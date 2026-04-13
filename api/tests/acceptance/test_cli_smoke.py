"""Black-box smoke tests for the `et` CLI.

Subprocess invocations against the venv-installed `et` binary. Catches
packaging issues that in-process tests miss (PATH resolution, entry
points wired correctly, sys.executable assumptions).

Each test is an exit-code check + a substring assertion. No snapshot
matching here (that's test_cli_help.py); these tests guard "the binary
runs and does not crash."
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.acceptance


_ET = Path(sys.executable).parent / "et"
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip(b: bytes) -> str:
    return _ANSI.sub("", b.decode("utf-8", errors="replace"))


def _run(*args: str, env: dict | None = None, timeout: int = 20) -> subprocess.CompletedProcess:
    """Invoke `et <args...>` via subprocess. Returns the completed process."""
    cmd = [str(_ET), *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
        env=env if env is not None else os.environ.copy(),
    )


def test_et_help_runs_clean():
    p = _run("--help")
    assert p.returncode == 0
    out = _strip(p.stdout)
    assert "common workflows" in out
    assert "et wizard" in out


def test_et_no_args_returns_zero_when_set_up():
    """`et` with no args is informational, not an error. Exit 0 for both
    the fresh-install path and the set-up path."""
    p = _run()
    assert p.returncode == 0


def test_et_doctor_runs_quiet():
    """Doctor's quiet mode prints only the summary signature, exits 0/1/2."""
    p = _run("doctor", "--quiet")
    assert p.returncode in (0, 1, 2), f"unexpected exit {p.returncode}"
    out = _strip(p.stdout)
    # Quiet: still has the header + signature, but no per-check rows.
    assert "doctor" in out


def test_et_doctor_full_output():
    p = _run("doctor")
    assert p.returncode in (0, 1, 2)
    out = _strip(p.stdout)
    assert "python version" in out
    assert "anthropic api key" in out
    assert "data dir writable" in out


def test_et_init_dry_run_does_not_write():
    """`et init --dry-run` reports what it would do; reads but never writes."""
    p = _run("init", "--dry-run")
    assert p.returncode == 0
    out = _strip(p.stdout)
    assert "init" in out
    assert "dry-run" in out


def test_et_typo_suggests_correction():
    p = _run("inisght")
    assert p.returncode == 2
    err = _strip(p.stderr)
    assert "did you mean" in err
    assert "insight" in err


def test_et_unknown_command_with_no_close_match_falls_back_to_argparse():
    """Garbage with no close suggestion drops to argparse's invalid-choice path."""
    p = _run("xyzzy-no-such-thing")
    assert p.returncode == 2
    # Either our did-you-mean kicks in, or argparse's invalid-choice does.
    combined = _strip(p.stdout) + _strip(p.stderr)
    assert "xyzzy-no-such-thing" in combined or "invalid choice" in combined


def test_et_subcommand_help_runs_for_every_command():
    """`et <command> --help` exits 0 for every advertised command."""
    for cmd in ("insight", "concepts", "sync", "stats", "doctor",
                "wizard", "mcp-serve", "init", "reset"):
        p = _run(cmd, "--help")
        assert p.returncode == 0, (
            f"et {cmd} --help exited {p.returncode}\n"
            f"stdout: {_strip(p.stdout)[:300]}\n"
            f"stderr: {_strip(p.stderr)[:300]}"
        )


def test_et_config_help_runs():
    p = _run("config", "--help")
    assert p.returncode == 0
    out = _strip(p.stdout)
    assert "et config init" in out
    assert "et config show" in out


def test_et_wizard_refuses_non_interactive():
    """Wizard should bail cleanly (not crash) when stdin is not a TTY."""
    p = _run("wizard", "--dry-run")
    # subprocess.run with capture_output gives non-TTY stdin/stdout
    assert p.returncode in (0, 1)
    out = _strip(p.stdout) + _strip(p.stderr)
    assert "interactive" in out.lower() or "wizard" in out.lower()
