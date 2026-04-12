"""AutoProvider over CC sessions + markdown folder.

Verifies the merge-and-dedup behavior of AutoProvider without any LLM calls.
Fixture shape: `cc_session_small_projects_dir` + `NOTES_SMALL_DIR`.

Fast-path, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from extended_thinking.providers.auto import AutoProvider

pytestmark = pytest.mark.acceptance


@pytest.fixture
def auto_provider_home(tmp_path, cc_session_small_projects_dir):
    """A fake $HOME that contains both ~/.claude/projects/ and ~/Documents/
    wired to our fixture data. AutoProvider's detection walks this home."""
    home = tmp_path / "home"

    # Simulate ~/.claude/projects
    claude_projects = home / ".claude" / "projects"
    claude_projects.parent.mkdir(parents=True, exist_ok=True)
    claude_projects.symlink_to(cc_session_small_projects_dir)

    # Simulate ~/Documents populated from notes_small
    documents = home / "Documents"
    documents.mkdir(parents=True, exist_ok=True)
    notes_src = Path(__file__).resolve().parents[1] / "fixtures" / "folders" / "notes_small"
    for md in notes_src.glob("*.md"):
        (documents / md.name).write_text(md.read_text())

    return home


def test_auto_detects_both_cc_and_folder(auto_provider_home):
    provider = AutoProvider(home_dir=auto_provider_home)
    names = [p.name for p in provider._providers]
    assert "claude-code" in names, f"claude-code not detected; got {names}"
    # FolderProvider reports name 'folder'.
    assert any(n == "folder" for n in names), f"folder not detected; got {names}"


def test_get_recent_merges_chunks_across_providers(auto_provider_home):
    provider = AutoProvider(home_dir=auto_provider_home)
    chunks = provider.get_recent(limit=50)
    # 3 CC exchange pairs + 3 markdown notes = at least 6 chunks.
    assert len(chunks) >= 6, f"expected >=6 merged chunks, got {len(chunks)}"
    sources = {c.source for c in chunks}
    # Mix of CC session file + markdown files should be represented.
    assert any("session_small.jsonl" in s for s in sources), sources
    assert any(s.endswith(".md") for s in sources), sources


def test_get_recent_dedups_by_id(auto_provider_home):
    """No two chunks should share an ID after the merge."""
    provider = AutoProvider(home_dir=auto_provider_home)
    chunks = provider.get_recent(limit=50)
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids)), (
        f"duplicate chunk IDs detected: "
        f"{[x for x in set(ids) if ids.count(x) > 1]}"
    )


def test_get_recent_sorted_by_timestamp_descending(auto_provider_home):
    """Chunks should be returned newest-first after the merge sort."""
    provider = AutoProvider(home_dir=auto_provider_home)
    chunks = provider.get_recent(limit=50)
    timestamps = [c.timestamp or "" for c in chunks]
    assert timestamps == sorted(timestamps, reverse=True), (
        f"chunks not sorted newest-first: {timestamps}"
    )
