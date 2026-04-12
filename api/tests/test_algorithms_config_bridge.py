"""ADR 012 step 4: bridge between TOML `[algorithms.*.*]` and the
algorithm registry's get_active() config shape."""

from __future__ import annotations

import pytest

from extended_thinking.algorithms import build_config_from_settings, get_active


class TestEmptyInput:

    def test_none_returns_empty_dict(self):
        assert build_config_from_settings(None) == {}

    def test_empty_dict_returns_empty(self):
        assert build_config_from_settings({}) == {}


class TestFamilyOrder:

    def test_explicit_order_key_preserved(self):
        tree = {"resolution": {"order": ["sequence_matcher", "embedding_cosine"]}}
        cfg = build_config_from_settings(tree)
        assert cfg["algorithms"]["resolution"] == ["sequence_matcher", "embedding_cosine"]

    def test_order_ignores_non_string_entries(self):
        """Garbage order falls back to plugin-table scan."""
        tree = {"resolution": {
            "order": [123, None],  # invalid
            "sequence_matcher": {"active": True},
        }}
        cfg = build_config_from_settings(tree)
        assert cfg["algorithms"]["resolution"] == ["sequence_matcher"]


class TestPluginTables:

    def test_plugin_with_active_true_enabled(self):
        tree = {"decay": {"physarum": {"active": True, "decay_rate": 0.9}}}
        cfg = build_config_from_settings(tree)
        assert cfg["algorithms"]["decay"] == ["physarum"]
        assert cfg["parameters"]["physarum"]["decay_rate"] == 0.9
        # `active` key is stripped from params
        assert "active" not in cfg["parameters"]["physarum"]

    def test_plugin_with_active_false_skipped(self):
        tree = {"decay": {
            "physarum": {"active": True},
            "disabled_plugin": {"active": False, "some_param": 1},
        }}
        cfg = build_config_from_settings(tree)
        assert cfg["algorithms"]["decay"] == ["physarum"]
        assert "disabled_plugin" not in cfg["parameters"]

    def test_plugin_without_active_key_defaults_to_enabled(self):
        """Listing a plugin at all is opt-in; `active` defaults to True."""
        tree = {"decay": {"physarum": {"decay_rate": 0.85}}}
        cfg = build_config_from_settings(tree)
        assert cfg["algorithms"]["decay"] == ["physarum"]

    def test_plugin_with_no_params_contributes_no_parameters_entry(self):
        tree = {"decay": {"physarum": {"active": True}}}
        cfg = build_config_from_settings(tree)
        # Empty params → no parameter entry (registry will use AlgorithmMeta defaults)
        assert "physarum" not in cfg.get("parameters", {})


class TestMixed:

    def test_order_wins_when_both_order_and_subtables_present(self):
        tree = {"resolution": {
            "order": ["embedding_cosine"],
            "sequence_matcher": {"active": True},  # should be ignored for order
            "embedding_cosine": {"active": True, "threshold": 0.9},
        }}
        cfg = build_config_from_settings(tree)
        assert cfg["algorithms"]["resolution"] == ["embedding_cosine"]
        # Params from subtable still collected
        assert cfg["parameters"]["embedding_cosine"]["threshold"] == 0.9


class TestEndToEndWithRegistry:
    """Bridge must produce a dict the registry actually accepts."""

    def test_physarum_config_round_trip(self):
        tree = {"decay": {"physarum": {"active": True, "decay_rate": 0.8}}}
        cfg = build_config_from_settings(tree)
        algs = get_active("decay", cfg)
        assert any(a.meta.name == "physarum" for a in algs)
        phys = [a for a in algs if a.meta.name == "physarum"][0]
        assert phys.decay_rate == 0.8

    def test_active_false_removes_plugin_from_result(self):
        tree = {"decay": {"physarum": {"active": False}}}
        cfg = build_config_from_settings(tree)
        # empty family list → registry still treats it as "user specified none"
        # which returns zero plugins for that family
        algs = get_active("decay", cfg)
        assert algs == []

    def test_unknown_family_is_tolerated(self):
        """A future plugin family listed in config shouldn't crash anything;
        get_active() just returns nothing for a family with no registered plugins."""
        tree = {"nonexistent_family": {"some_plugin": {"active": True}}}
        cfg = build_config_from_settings(tree)
        algs = get_active("nonexistent_family", cfg)
        assert algs == []


class TestResolutionDefaultFallback:
    """pipeline_v2._get_resolution_algorithms supplies a default order when
    the user hasn't configured the family. That default must still work."""

    def test_default_order_used_when_family_absent(self):
        from extended_thinking.processing.pipeline_v2 import _get_resolution_algorithms
        algs = _get_resolution_algorithms(has_vectors=True)
        names = [a.meta.name for a in algs]
        assert "sequence_matcher" in names
        # embedding_cosine included when vectors are available
        assert "embedding_cosine" in names

    def test_no_vectors_filters_embedding_cosine(self):
        from extended_thinking.processing.pipeline_v2 import _get_resolution_algorithms
        algs = _get_resolution_algorithms(has_vectors=False)
        names = [a.meta.name for a in algs]
        assert "embedding_cosine" not in names
