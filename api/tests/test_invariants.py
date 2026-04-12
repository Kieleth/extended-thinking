"""Engineering invariant tests.

Structural guarantees that prevent entire classes of bugs. Runs against the
silk-free core (mcp_server, pipeline_v2, providers, algorithms). Graph-store
invariants over a realistic fixture live in
`tests/acceptance/test_invariants_at_scale.py`.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest


# ── Invariant 1: No silent exception swallowing ─────────────────────────


class BareExceptVisitor(ast.NodeVisitor):
    """AST visitor that finds `except ...: pass` blocks."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.violations: list[str] = []

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            self.violations.append(
                f"{self.filepath}:{node.lineno} bare `except: pass` swallows errors silently"
            )
        self.generic_visit(node)


def test_no_silent_exception_swallowing():
    """Every except block must log or re-raise. No bare `except: pass`."""
    src_dir = Path(__file__).parent.parent / "src" / "extended_thinking"
    violations = []

    for py_file in src_dir.rglob("*.py"):
        source = py_file.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        visitor = BareExceptVisitor(str(py_file.relative_to(src_dir.parent.parent)))
        visitor.visit(tree)
        violations.extend(visitor.violations)

    assert violations == [], (
        f"Found {len(violations)} silent exception swallowing:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ── Invariant 2: API key validation at startup ──────────────────────────


def test_provider_registry_raises_without_keys():
    """Requesting a provider when no API keys are set must raise, not return None."""
    from extended_thinking.ai.registry import _providers, get_provider

    _providers.clear()

    old_anthropic = os.environ.pop("ANTHROPIC_API_KEY", None)
    old_openai = os.environ.pop("OPENAI_API_KEY", None)

    import extended_thinking.ai.registry as reg
    orig_settings = reg.settings
    try:
        from extended_thinking.config import Settings
        from extended_thinking.config.schema import CredentialsConfig
        empty_settings = Settings(credentials=CredentialsConfig(
            anthropic_api_key="", openai_api_key=""))

        reg.settings = empty_settings
        _providers.clear()

        with pytest.raises(RuntimeError, match="No AI providers configured"):
            get_provider()
    finally:
        reg.settings = orig_settings
        _providers.clear()
        if old_anthropic:
            os.environ["ANTHROPIC_API_KEY"] = old_anthropic
        if old_openai:
            os.environ["OPENAI_API_KEY"] = old_openai


# ── Invariant 3: Concept extraction output is structured ────────────────


def test_extraction_parser_rejects_bad_categories():
    """Extracted concepts with invalid categories must be rejected."""
    from extended_thinking.processing.extractor import _parse_extraction_response

    response = json.dumps([
        {"name": "good", "category": "topic", "description": "valid"},
        {"name": "bad", "category": "INVALID", "description": "should be rejected"},
        {"name": "also good", "category": "tension", "description": "valid"},
    ])
    concepts = _parse_extraction_response(response)
    assert len(concepts) == 2
    assert all(
        c.category in {"topic", "theme", "entity", "question", "decision", "tension"}
        for c in concepts
    )


def test_extraction_parser_handles_garbage():
    """Parser must return empty list on unparseable input, not crash."""
    from extended_thinking.processing.extractor import _parse_extraction_response

    assert _parse_extraction_response("not json at all") == []
    assert _parse_extraction_response("") == []
    assert _parse_extraction_response("null") == []
    assert _parse_extraction_response("42") == []
