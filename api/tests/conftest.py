"""Shared pytest fixtures for the ET test suite.

Scope split:
  session-scoped: pure-read fixtures (embeddings, provider over fixture JSONL,
                  fixture folder, pre-loaded graph shape). Reused across tests.
  function-scoped: anything that mutates state (tmp dirs, fake LLMs whose
                   call counters tests assert on).

Existing unit tests keep working unchanged; fixtures here are additive.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Make `tests.helpers.*` imports work both from api/ and from nested dirs.
_API_ROOT = Path(__file__).resolve().parent.parent
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

# Repo root on sys.path so the codegen-regenerability tests can
# `from scripts.gen_kuzu_types import ...` — the generator scripts live
# outside the wheel by design (build-time tooling, not runtime). Runtime
# `extended_thinking._schema.*` imports resolve via the package and do
# NOT need this hack.
_REPO_ROOT = _API_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.helpers.dummy_embed import DummyVectorizer  # noqa: E402
from tests.helpers.fake_llm import DummyLM, FakeListLLM  # noqa: E402
from tests.helpers.fixture_loader import load_graph_from_json  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SESSION_SMALL_JSONL = FIXTURES_DIR / "cc_sessions" / "session_small.jsonl"
NOTES_SMALL_DIR = FIXTURES_DIR / "folders" / "notes_small"
EXPECTED_GRAPH_SMALL = FIXTURES_DIR / "expected" / "graph_small.json"


# ── Session-scoped: pure reads ────────────────────────────────────────────


@pytest.fixture(scope="session")
def dummy_embed() -> DummyVectorizer:
    """Deterministic hash-based vectorizer. Safe to share across tests since
    it's stateless after construction."""
    return DummyVectorizer()


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def cc_session_small_projects_dir(tmp_path_factory) -> Path:
    """A Claude-Code-projects-style directory containing session_small.jsonl.

    ClaudeCodeProvider expects the JSONL inside a per-project subdirectory.
    We stage the fixture once per session into a session-lifetime tmp dir.
    """
    root = tmp_path_factory.mktemp("cc_projects")
    project = root / "-Users-luis-Projects-extended_thinking"
    project.mkdir(parents=True, exist_ok=True)
    shutil.copy(SESSION_SMALL_JSONL, project / "session_small.jsonl")
    return root


@pytest.fixture(scope="session")
def cc_session_small(cc_session_small_projects_dir):
    """Pre-loaded ClaudeCodeProvider over session_small.jsonl."""
    from extended_thinking.providers.claude_code import ClaudeCodeProvider
    return ClaudeCodeProvider(projects_dir=cc_session_small_projects_dir)


@pytest.fixture(scope="session")
def folder_notes_small():
    """FolderProvider over the notes_small fixture directory."""
    from extended_thinking.providers.folder import FolderProvider
    return FolderProvider(NOTES_SMALL_DIR)


# ── Function-scoped: mutating state ───────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path) -> Path:
    """Isolated `~/.extended-thinking`-shaped data dir per test."""
    d = tmp_path / "et_data"
    d.mkdir()
    return d


@pytest.fixture
def fake_llm() -> FakeListLLM:
    """Empty FakeListLLM. Test scripts responses via `fake_llm._responses.append(...)`
    or by constructing a new one: `fake_llm = FakeListLLM(['r1', 'r2'])`."""
    return FakeListLLM(responses=[])


@pytest.fixture
def dummy_lm_factory():
    """Factory that builds a DummyLM with a given responses dict."""
    def _factory(responses: dict, *, default: str = "{}") -> DummyLM:
        return DummyLM(responses=responses, default=default)
    return _factory


@pytest.fixture
def loaded_graph_small(tmp_data_dir):
    """Fresh GraphStore pre-populated from `expected/graph_small.json`.

    Function-scoped because algorithm tests mutate edge weights (decay,
    reinforcement). A session-scoped version would leak between tests.
    """
    from extended_thinking.storage.graph_store import GraphStore

    store = GraphStore(tmp_data_dir / "kg_small")
    load_graph_from_json(store, EXPECTED_GRAPH_SMALL)
    return store
