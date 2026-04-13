"""AT: ProjectsProvider — recursive CLAUDE.md / AGENTS.md / README hunt.

Each git-gated project becomes its own namespace (memory:project:<name>).
Files without a .git ancestor are skipped by default; the same pattern
set can include `docs/**/*.md` for architecture memory. Pipeline.sync
routes each chunk's concepts into the per-project namespace read off
the chunk metadata.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from extended_thinking.processing.pipeline_v2 import Pipeline
from extended_thinking.providers.projects import (
    ProjectsProvider,
    namespace_for_project,
)
from extended_thinking.storage import StorageLayer
from tests.helpers.fake_llm import DummyLM

pytestmark = pytest.mark.acceptance


# ── Helpers ──────────────────────────────────────────────────────────

def _make_git_project(root, name: str, files: dict[str, str]) -> None:
    """Create a fake git project at root/name with the given file tree.

    `files` maps relative path → content. Creates a .git/ dir so the
    require_git gate passes.
    """
    project = root / name
    project.mkdir(parents=True, exist_ok=True)
    (project / ".git").mkdir(exist_ok=True)
    for rel, content in files.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _make_non_git_folder(root, name: str, files: dict[str, str]) -> None:
    """Same but without .git/ — should be skipped when require_git=True."""
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


# ── 1. Namespace derivation ──────────────────────────────────────────

class TestNamespaceDerivation:

    def test_basename_becomes_slug(self, tmp_path):
        # Underscores preserved (Python-style repo names); uppercase
        # lowercased; spaces collapsed to hyphens.
        assert namespace_for_project(tmp_path / "extended_thinking") == \
            "memory:project:extended_thinking"
        assert namespace_for_project(tmp_path / "autoresearch-ET") == \
            "memory:project:autoresearch-et"
        assert namespace_for_project(tmp_path / "malleus") == \
            "memory:project:malleus"
        assert namespace_for_project(tmp_path / "My Cool Project") == \
            "memory:project:my-cool-project"


# ── 2. Recursive discovery with .git gate ────────────────────────────

class TestDiscovery:

    def test_finds_claude_md_in_every_git_project(self, tmp_path):
        _make_git_project(tmp_path, "alpha", {
            "CLAUDE.md": "alpha claude context",
            "README.md": "alpha readme",
        })
        _make_git_project(tmp_path, "beta", {
            "CLAUDE.md": "beta claude context",
        })

        provider = ProjectsProvider(roots=[tmp_path])
        chunks = provider.get_recent()

        filenames = sorted(c.metadata["filename"] for c in chunks)
        assert filenames.count("CLAUDE.md") == 2
        assert filenames.count("README.md") == 1

    def test_non_git_folders_skipped_by_default(self, tmp_path):
        _make_git_project(tmp_path, "alpha", {"CLAUDE.md": "git project"})
        _make_non_git_folder(tmp_path, "scratch", {"CLAUDE.md": "not a project"})

        provider = ProjectsProvider(roots=[tmp_path])
        chunks = provider.get_recent()

        sources = [c.source for c in chunks]
        assert any("alpha" in s for s in sources)
        assert not any("scratch" in s for s in sources)

    def test_require_git_false_ingests_everything(self, tmp_path):
        _make_non_git_folder(tmp_path / "scratch-root", "scratch", {
            "CLAUDE.md": "no git here",
        })

        provider = ProjectsProvider(
            roots=[tmp_path / "scratch-root"], require_git=False,
        )
        chunks = provider.get_recent()
        assert len(chunks) == 1
        assert "scratch" in chunks[0].source

    def test_skip_dirs_never_descended(self, tmp_path):
        """node_modules + .venv + __pycache__ must be invisible even if
        they contain matching filenames."""
        _make_git_project(tmp_path, "alpha", {
            "CLAUDE.md": "real claude",
            "node_modules/fake-pkg/README.md": "npm garbage",
            ".venv/lib/README.md": "pip garbage",
            "__pycache__/README.md": "python garbage",
        })
        provider = ProjectsProvider(roots=[tmp_path])
        chunks = provider.get_recent()
        contents = " ".join(c.content for c in chunks)
        assert "real claude" in contents
        assert "garbage" not in contents

    def test_docs_glob_pattern(self, tmp_path):
        _make_git_project(tmp_path, "alpha", {
            "docs/ADR/001.md": "ADR 001",
            "docs/guide.md": "guide",
            "CLAUDE.md": "claude",
        })
        provider = ProjectsProvider(
            roots=[tmp_path],
            patterns=["CLAUDE.md", "docs/**/*.md"],
        )
        chunks = provider.get_recent()
        names = {c.metadata["filename"] for c in chunks}
        assert names == {"CLAUDE.md", "001.md", "guide.md"}


# ── 3. Chunks carry per-project namespace ────────────────────────────

class TestNamespaceStamping:

    def test_every_chunk_has_project_namespace(self, tmp_path):
        _make_git_project(tmp_path, "extended_thinking", {
            "CLAUDE.md": "et context",
        })
        _make_git_project(tmp_path, "malleus", {
            "CLAUDE.md": "malleus context",
        })
        provider = ProjectsProvider(roots=[tmp_path])
        chunks = provider.get_recent()

        by_ns = {c.metadata["namespace"] for c in chunks}
        assert by_ns == {
            "memory:project:extended_thinking",
            "memory:project:malleus",
        }

    def test_provider_stat_reports_project_count(self, tmp_path):
        _make_git_project(tmp_path, "alpha", {"CLAUDE.md": "a"})
        _make_git_project(tmp_path, "beta", {"CLAUDE.md": "b"})
        _make_git_project(tmp_path, "gamma", {"CLAUDE.md": "c"})

        provider = ProjectsProvider(roots=[tmp_path])
        stats = provider.get_stats()
        assert stats["total_memories"] == 3
        assert stats["total_projects"] == 3


# ── 4. End-to-end: concepts land in per-project namespaces ──────────

class TestPipelineRouting:

    @pytest.mark.asyncio
    async def test_concepts_land_in_per_project_namespaces(
        self, tmp_data_dir, tmp_path,
    ):
        """Two git projects each sync separately; concepts land in
        per-project namespaces, same-name concepts stay distinct."""
        _make_git_project(tmp_path, "alpha", {
            "CLAUDE.md": "Alpha uses Kuzu as its graph store.",
        })
        _make_git_project(tmp_path, "beta", {
            "CLAUDE.md": "Beta uses Kuzu too, but differently.",
        })

        storage = StorageLayer.lite(tmp_data_dir / "kg")

        # One sync per project. Scope each provider to just that
        # project's directory — the project root is `tmp_path/<name>`
        # which contains the .git dir we created, so require_git=True
        # finds exactly one project.
        for project_dir, quote in [
            ("alpha", "Alpha uses Kuzu"),
            ("beta",  "Beta uses Kuzu too"),
        ]:
            provider = ProjectsProvider(roots=[tmp_path / project_dir])
            pipeline = Pipeline.from_storage(provider, storage)
            fake = DummyLM(
                {"CONVERSATION:": json.dumps([{
                    "name": "Kuzu",
                    "category": "entity",
                    "description": f"graph db used by {project_dir}",
                    "source_quote": quote,
                }])},
                default="[]",
            )
            with patch(
                "extended_thinking.processing.extractor.get_provider",
                return_value=fake,
            ):
                await pipeline.sync()

        rows = storage.kg._query_all(
            "MATCH (c:Concept) RETURN c.id, c.namespace ORDER BY c.id"
        )
        namespaces = {r[1] for r in rows}
        assert "memory:project:alpha" in namespaces
        assert "memory:project:beta" in namespaces
        # Same concept name, two distinct rows — one per namespace.
        assert len(rows) >= 2
