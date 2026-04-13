"""AT: GraphStore explicit close() contract (R11).

Kuzu's Python API has no `Database.close()` — handles release only via
GC, so two GraphStore instances on the same file produce two live
Kuzu Database handles whose page-allocation views diverge → file
corruption on write. Root cause of the 2026-04-12 autoresearch-ET
incident.

This AT pins the contract:

  1. close() releases the handle deterministically (file is reopenable
     in the same process).
  2. Constructing a second GraphStore on a path that's already open
     raises DuplicateGraphStoreError.
  3. After close(), reopen on the same path succeeds.
  4. Context manager (`with GraphStore(...) as kg:`) closes on exit.
  5. StorageLayer.close() cascades to the underlying GraphStore.
  6. The FastAPI route shares ONE GraphStore per resolved path —
     concurrent requests get the same instance, never two.
"""

from __future__ import annotations

import pytest

from extended_thinking.storage import StorageLayer
from extended_thinking.storage.graph_store import (
    DuplicateGraphStoreError,
    GraphStore,
    _LIVE_STORES,
)

pytestmark = pytest.mark.acceptance


# ── 1. close() releases the handle ───────────────────────────────────

class TestExplicitClose:
    """close() must drop the Kuzu Database reference and force gc so the
    OS file handle releases before the call returns. After close(), the
    same path can be opened again in this process."""

    def test_close_then_reopen_succeeds(self, tmp_path):
        kg = GraphStore(tmp_path / "kg")
        kg.close()

        # Same path, fresh GraphStore — must not raise.
        kg2 = GraphStore(tmp_path / "kg")
        try:
            stats = kg2.get_stats()
            assert stats["total_concepts"] == 0
        finally:
            kg2.close()

    def test_close_is_idempotent(self, tmp_path):
        kg = GraphStore(tmp_path / "kg")
        kg.close()
        # No second close should error.
        kg.close()
        kg.close()

    def test_registry_cleared_after_close(self, tmp_path):
        kg = GraphStore(tmp_path / "kg")
        key = kg._registry_key
        assert key in _LIVE_STORES
        kg.close()
        assert key not in _LIVE_STORES


# ── 2. Duplicate-handle guard ────────────────────────────────────────

class TestDuplicateGuard:
    """The bug class autoresearch-ET hit: a second GraphStore on a path
    that's already open. Must raise — not silently produce a second
    Kuzu Database handle."""

    def test_double_open_raises(self, tmp_path):
        kg1 = GraphStore(tmp_path / "kg")
        try:
            with pytest.raises(DuplicateGraphStoreError) as exc_info:
                GraphStore(tmp_path / "kg")
            assert "already open" in str(exc_info.value).lower()
            assert "close()" in str(exc_info.value)
        finally:
            kg1.close()

    def test_double_open_via_different_path_strings(self, tmp_path):
        """The guard resolves paths, so `./kg` and `/abs/.../kg` collide."""
        kg1 = GraphStore(tmp_path / "kg")
        try:
            absolute = (tmp_path / "kg").resolve()
            with pytest.raises(DuplicateGraphStoreError):
                GraphStore(absolute)
        finally:
            kg1.close()

    def test_different_paths_are_independent(self, tmp_path):
        """Opening different paths concurrently is fine — only same-path
        is a corruption hazard."""
        a = GraphStore(tmp_path / "a")
        b = GraphStore(tmp_path / "b")
        try:
            assert a._db_path != b._db_path
            assert a.get_stats()["total_concepts"] == 0
            assert b.get_stats()["total_concepts"] == 0
        finally:
            a.close()
            b.close()


# ── 3. Context manager ───────────────────────────────────────────────

class TestContextManager:
    """`with GraphStore(...) as kg:` blocks close on exit, even on
    exception — the only safe way to reopen later in the same scope."""

    def test_with_block_closes(self, tmp_path):
        with GraphStore(tmp_path / "kg") as kg:
            assert kg.get_stats()["total_concepts"] == 0
            key = kg._registry_key
        # After the block, registry is clear and the path reopens.
        assert key not in _LIVE_STORES
        with GraphStore(tmp_path / "kg") as kg2:
            assert kg2.get_stats()["total_concepts"] == 0

    def test_with_block_closes_on_exception(self, tmp_path):
        key = None
        with pytest.raises(RuntimeError, match="forced"):
            with GraphStore(tmp_path / "kg") as kg:
                key = kg._registry_key
                raise RuntimeError("forced")
        # Even after an exception inside the block, the close fired.
        assert key is not None and key not in _LIVE_STORES


# ── 4. StorageLayer cascades ─────────────────────────────────────────

class TestStorageLayerCascade:
    """StorageLayer.close() must reach all the way down to the
    GraphStore so consumers don't have to reach inside."""

    def test_lite_close_releases_handle(self, tmp_path):
        storage = StorageLayer.lite(tmp_path / "data")
        try:
            assert storage.kg.get_stats()["total_concepts"] == 0
        finally:
            storage.close()
        # Underlying GraphStore is closed — reopen the same path works.
        storage2 = StorageLayer.lite(tmp_path / "data")
        storage2.close()

    def test_storage_layer_context_manager(self, tmp_path):
        with StorageLayer.lite(tmp_path / "data") as storage:
            assert storage.kg is not None
        # Registry empty.
        with StorageLayer.lite(tmp_path / "data"):
            pass


# ── 5. FastAPI route shares one instance ─────────────────────────────

class TestRouteSingleton:
    """The HTTP route's _get_graph_store() must hand out the same
    GraphStore for every call on the same path. Two concurrent requests
    must not produce two Kuzu Database handles."""

    def test_repeated_calls_return_same_instance(self, tmp_path, monkeypatch):
        from extended_thinking.api.routes import graph_v2

        # Point the route at a tmp data dir.
        class _FakeData:
            def __init__(self, root):
                self.root = root
        class _FakeSettings:
            data = _FakeData(tmp_path)
        monkeypatch.setattr(graph_v2, "settings", _FakeSettings(),
                            raising=False)
        monkeypatch.setattr(
            "extended_thinking.config.migrate_data_dir",
            lambda s: tmp_path,
        )

        # Empty cache before the test.
        graph_v2._STORE_CACHE.clear()

        try:
            kg1 = graph_v2._get_graph_store()
            kg2 = graph_v2._get_graph_store()
            kg3 = graph_v2._get_graph_store()
            assert kg1 is kg2 is kg3, (
                "route must serve the same GraphStore for every call "
                "on the same path"
            )
        finally:
            graph_v2.close_graph_stores()

    def test_close_graph_stores_releases_path(self, tmp_path, monkeypatch):
        from extended_thinking.api.routes import graph_v2

        monkeypatch.setattr(
            "extended_thinking.config.migrate_data_dir",
            lambda s: tmp_path,
        )
        graph_v2._STORE_CACHE.clear()

        kg = graph_v2._get_graph_store()
        path_key = kg._registry_key
        graph_v2.close_graph_stores()

        assert path_key not in _LIVE_STORES
        # After shutdown, a future reopen on the same path is legal.
        graph_v2._STORE_CACHE.clear()
        kg2 = graph_v2._get_graph_store()
        try:
            assert kg2._registry_key == path_key
        finally:
            graph_v2.close_graph_stores()
