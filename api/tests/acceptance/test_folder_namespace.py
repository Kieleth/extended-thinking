"""AT: per-folder namespacing (option A from the project-awareness design).

Narrative: a user has two markdown folders — `~/vault/notes` and
`~/writing`. ET should keep concepts from each in its own namespace
(`memory:notes` / `memory:writing`) so they don't blend, including
auto-discovered folders, not just those listed in config.

Asserts end-to-end:

  1. FolderProvider stamps each chunk with `namespace = memory:<basename>`
     derived from the folder's name.
  2. Pipeline.sync extracts those chunks into the folder's namespace.
  3. Concept ids are namespace-prefixed for non-default namespaces so
     same-name concepts in different folders stay as distinct rows.
  4. Entity resolution stays inside the namespace — a `Kuzu` concept in
     `memory:notes` does not merge with a `Kuzu` concept in
     `memory:writing`.
  5. Stats + listings scoped by namespace return clean per-project
     slices; the union still sees everything.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from extended_thinking.processing.pipeline_v2 import Pipeline
from extended_thinking.providers.folder import FolderProvider, namespace_for_root
from extended_thinking.storage import StorageLayer
from tests.helpers.fake_llm import DummyLM

pytestmark = pytest.mark.acceptance


# ── Helpers ──────────────────────────────────────────────────────────

def _write_md(folder, name: str, text: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def _extraction_for(name: str, quote: str) -> str:
    """Scripted Haiku response shaped to match the extractor contract."""
    return json.dumps([{
        "name": name,
        "category": "entity",
        "description": f"concept extracted from {name}",
        "source_quote": quote,
    }])


# ── 1. Namespace derivation ──────────────────────────────────────────

class TestNamespaceDerivation:
    """Pure function: folder name → memory:<slug> string."""

    def test_basename_becomes_slug(self, tmp_path):
        assert namespace_for_root(tmp_path / "notes") == "memory:notes"
        assert namespace_for_root(tmp_path / "My Writing") == "memory:my-writing"
        assert namespace_for_root(tmp_path / "Documents") == "memory:documents"

    def test_empty_basename_falls_back_to_memory(self):
        from pathlib import Path
        # Root "/" has no basename; should degrade to plain "memory".
        assert namespace_for_root(Path("/")) == "memory"

    def test_non_ascii_stripped(self, tmp_path):
        # Characters outside [a-z0-9_-] collapse to hyphens.
        result = namespace_for_root(tmp_path / "café · notes")
        assert result == "memory:caf-notes"


# ── 2. FolderProvider stamps chunks ──────────────────────────────────

class TestChunkStamping:
    """Every chunk emitted by a FolderProvider carries its namespace in
    metadata, so downstream Pipeline.sync can route concepts correctly."""

    def test_default_namespace_from_basename(self, tmp_path):
        root = tmp_path / "notes"
        _write_md(root, "a.md", "alpha content")
        _write_md(root, "b.md", "beta content")

        provider = FolderProvider(root)
        chunks = provider.get_recent()

        assert len(chunks) == 2
        assert provider.namespace == "memory:notes"
        assert all(c.metadata["namespace"] == "memory:notes" for c in chunks)

    def test_explicit_namespace_override(self, tmp_path):
        """A caller can force a namespace, ignoring the basename."""
        root = tmp_path / "anything"
        _write_md(root, "a.md", "content")

        provider = FolderProvider(root, namespace="memory:research")
        chunks = provider.get_recent()

        assert provider.namespace == "memory:research"
        assert chunks[0].metadata["namespace"] == "memory:research"


# ── 3. Pipeline routes concepts by chunk namespace ──────────────────

class TestPipelineRouting:
    """sync() reads `metadata["namespace"]` off the source chunk and
    routes the extracted concept to that namespace, prefixing the
    concept id so same-name concepts don't collide across projects."""

    @pytest.mark.asyncio
    async def test_concept_lands_in_folder_namespace(self, tmp_data_dir, tmp_path):
        root = tmp_path / "notes"
        _write_md(root, "intro.md", "I love working with Kuzu for graph storage.")
        provider = FolderProvider(root)

        storage = StorageLayer.lite(tmp_data_dir / "kg")
        pipeline = Pipeline.from_storage(provider, storage)

        fake = DummyLM(
            {"CONVERSATION:": _extraction_for("Kuzu", "working with Kuzu")},
            default=_extraction_for("Kuzu", "working with Kuzu"),
        )
        with patch(
            "extended_thinking.processing.extractor.get_provider",
            return_value=fake,
        ):
            await pipeline.sync()

        # Concept landed in the folder's namespace
        rows = pipeline.store._query_all(
            "MATCH (c:Concept) RETURN c.id, c.namespace, c.name"
        )
        assert rows, "expected at least one concept row"
        assert all(r[1] == "memory:notes" for r in rows), rows
        # Id is prefixed with the namespace
        assert any(r[0].startswith("memory:notes:") for r in rows), rows


# ── 4. Two folders, clean isolation ──────────────────────────────────

class TestCrossFolderIsolation:
    """The headline Rams promise: same concept name in two folders →
    two distinct rows, two namespaces, no merge."""

    @pytest.mark.asyncio
    async def test_same_name_stays_disjoint_across_folders(
        self, tmp_data_dir, tmp_path,
    ):
        notes_dir = tmp_path / "notes"
        writing_dir = tmp_path / "writing"
        _write_md(notes_dir, "x.md", "Kuzu appears here in notes.")
        _write_md(writing_dir, "y.md", "Kuzu also appears here in writing.")

        storage = StorageLayer.lite(tmp_data_dir / "kg")

        # Run sync once per folder with its own FolderProvider so each
        # batch carries a single namespace.
        for folder, quote in [
            (notes_dir, "Kuzu appears here in notes"),
            (writing_dir, "Kuzu also appears here in writing"),
        ]:
            provider = FolderProvider(folder)
            pipeline = Pipeline.from_storage(provider, storage)
            fake = DummyLM(
                {"CONVERSATION:": _extraction_for("Kuzu", quote)},
                default=_extraction_for("Kuzu", quote),
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
        ids = {r[0] for r in rows}

        # Two distinct namespaces, two distinct ids, one concept per side.
        assert namespaces == {"memory:notes", "memory:writing"}, rows
        assert ids == {"memory:notes:kuzu", "memory:writing:kuzu"}, rows
        assert len(rows) == 2


# ── 5. Scoped reads return clean slices ──────────────────────────────

class TestNamespaceScopedReads:
    """Consumers use namespace-scoped list/stats to get per-project
    views; unscoped sees the union."""

    @pytest.mark.asyncio
    async def test_list_and_stats_scope_cleanly(self, tmp_data_dir, tmp_path):
        notes_dir = tmp_path / "notes"
        writing_dir = tmp_path / "writing"
        _write_md(notes_dir, "a.md", "Kuzu graph database.")
        _write_md(writing_dir, "b.md", "ChromaDB vector store.")

        storage = StorageLayer.lite(tmp_data_dir / "kg")

        for folder, (name, quote) in [
            (notes_dir, ("Kuzu", "Kuzu graph database")),
            (writing_dir, ("ChromaDB", "ChromaDB vector store")),
        ]:
            provider = FolderProvider(folder)
            pipeline = Pipeline.from_storage(provider, storage)
            fake = DummyLM(
                {"CONVERSATION:": _extraction_for(name, quote)},
                default=_extraction_for(name, quote),
            )
            with patch(
                "extended_thinking.processing.extractor.get_provider",
                return_value=fake,
            ):
                await pipeline.sync()

        # Scoped listings return only that project's concepts
        notes = storage.kg.list_concepts(namespace="memory:notes")
        writing = storage.kg.list_concepts(namespace="memory:writing")
        assert {c["name"] for c in notes} == {"Kuzu"}
        assert {c["name"] for c in writing} == {"ChromaDB"}

        # Unscoped sees both
        all_concepts = storage.kg.list_concepts()
        assert {c["name"] for c in all_concepts} == {"Kuzu", "ChromaDB"}

        # Stats also scope
        notes_stats = storage.kg.get_stats(namespace="memory:notes")
        writing_stats = storage.kg.get_stats(namespace="memory:writing")
        assert notes_stats["total_concepts"] == 1
        assert writing_stats["total_concepts"] == 1
