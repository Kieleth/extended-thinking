"""Friendly-error path: known exceptions render as styled notices, not tracebacks.

Tested in-process (importing main and calling it with patched argv) so we
can exercise the exception path deterministically without depending on
the user's machine state.
"""

from __future__ import annotations

import io
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.acceptance


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip(s: str) -> str:
    return _ANSI.sub("", s)


def _run_main(*argv: str) -> tuple[int, str, str]:
    """Invoke `cli.main()` with patched argv; return (rc, stdout, stderr)."""
    from extended_thinking import cli

    out, err = io.StringIO(), io.StringIO()
    with patch.object(sys, "argv", ["et", *argv]):
        with redirect_stdout(out), redirect_stderr(err):
            try:
                rc = cli.main()
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else 1
    return rc, _strip(out.getvalue()), _strip(err.getvalue())


def test_typo_renders_did_you_mean(capsys):
    rc, out, err = _run_main("inisght")
    assert rc == 2
    combined = out + err
    assert "did you mean" in combined
    assert "insight" in combined


def test_no_args_returns_zero():
    """`et` with no args is informational, never an error."""
    rc, _out, _err = _run_main()
    assert rc == 0


def test_no_ai_provider_renders_friendly_message():
    """If the registry raises 'No AI providers configured', main() should
    catch it and print a styled fix hint instead of a traceback."""
    from extended_thinking import cli

    def _boom(*_a, **_kw):
        raise RuntimeError("No AI providers configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.")

    out, err = io.StringIO(), io.StringIO()
    with patch.object(sys, "argv", ["et", "stats"]):
        with patch.object(cli, "_dispatch", side_effect=_boom):
            with redirect_stdout(out), redirect_stderr(err):
                rc = cli.main()

    assert rc == 2
    combined = _strip(out.getvalue()) + _strip(err.getvalue())
    assert "ANTHROPIC_API_KEY" in combined
    assert "et config set" in combined
    # Crucially: no Python traceback signature.
    assert "Traceback" not in combined


def test_keyboard_interrupt_exits_130():
    """Ctrl-C during a command should produce exit 130, not a traceback."""
    from extended_thinking import cli

    def _boom(*_a, **_kw):
        raise KeyboardInterrupt()

    out, err = io.StringIO(), io.StringIO()
    with patch.object(sys, "argv", ["et", "stats"]):
        with patch.object(cli, "_dispatch", side_effect=_boom):
            with redirect_stdout(out), redirect_stderr(err):
                rc = cli.main()
    assert rc == 130
    combined = _strip(out.getvalue()) + _strip(err.getvalue())
    assert "Traceback" not in combined


def test_unknown_subcommand_with_no_close_match_does_not_crash():
    """`et zzzz` should exit cleanly, not raise."""
    rc, _out, _err = _run_main("zzzz-no-such-thing")
    assert rc != 0
    # Either our did-you-mean fired or argparse's invalid-choice did.
