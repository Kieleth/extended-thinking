"""Snapshot the `et` CLI's --help output so UX changes are visible in PR diffs.

ANSI escapes are stripped because cli_style emits them when stdout is a TTY,
and we want the snapshot to be terminal-independent.

If you intentionally change a help message, regenerate with:
    make at-update-snapshots
"""

from __future__ import annotations

import re
import sys

import pytest

pytestmark = pytest.mark.acceptance


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _help_text(*argv: str) -> str:
    """Invoke `et <argv...> --help` in-process and return stripped output."""
    import io
    import contextlib
    from extended_thinking.cli import _build_parser

    parser = _build_parser()
    buf = io.StringIO()
    # `--help` raises SystemExit(0); catch and return what was printed.
    with contextlib.redirect_stdout(buf):
        try:
            parser.parse_args(list(argv) + ["--help"])
        except SystemExit:
            pass
    return _strip_ansi(buf.getvalue())


def test_top_level_help_lists_every_command(snapshot):
    text = _help_text()
    # Every advertised command must appear in the help.
    for cmd in ("insight", "concepts", "sync", "stats", "doctor",
                "wizard", "mcp-serve", "init", "reset", "config"):
        assert cmd in text, f"--help missing command: {cmd}"
    assert text == snapshot


def test_top_level_help_includes_workflow_examples(snapshot):
    text = _help_text()
    assert "common workflows" in text
    assert "et wizard" in text
    assert "daily loop" in text
    assert text == snapshot


@pytest.mark.parametrize("subcommand", [
    "insight", "concepts", "sync", "stats", "doctor",
    "wizard", "init", "reset",
])
def test_subcommand_help_renders(snapshot, subcommand):
    text = _help_text(subcommand)
    assert "usage: et " + subcommand in text
    # Every interactive-or-action command should carry an examples block.
    if subcommand not in ("stats",):
        assert "examples:" in text, f"{subcommand} --help missing examples block"
    assert text == snapshot(name=subcommand)


def test_config_help_renders(snapshot):
    text = _help_text("config")
    assert "et config init" in text
    assert "et config show" in text
    assert text == snapshot
