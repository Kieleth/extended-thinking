"""Shared helpers for the acceptance test suite.

Import surface kept minimal. Tests import exactly what they need:

    from tests.helpers.fake_llm import FakeListLLM, DummyLM
    from tests.helpers.dummy_embed import DummyVectorizer
    from tests.helpers.fixture_loader import load_graph_from_json
    from tests.helpers.assertions import assert_entity_exists, assert_top_k_contains
    from tests.helpers.snapshot_matchers import FloatArraySerializer
"""
