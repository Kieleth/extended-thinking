"""Acceptance test: the research-backbone demo runs end-to-end.

Guards `examples/research_backbone_demo.py` against silent rot. If a
future refactor breaks any of the shipped ADR 013 capabilities, the
demo breaks here and CI catches it before a consumer runs into it.

The demo itself lives at `examples/research_backbone_demo.py` and is
also intended to be run by hand.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEMO = REPO_ROOT / "examples" / "research_backbone_demo.py"
API_SRC = REPO_ROOT / "api" / "src"


def test_demo_script_exists():
    assert DEMO.exists(), f"expected demo at {DEMO}"


def test_demo_runs_to_completion():
    """Run the full demo as a subprocess. It should exit 0 and print
    every step marker."""
    result = subprocess.run(
        [sys.executable, str(DEMO)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        env={
            "PYTHONPATH": f"{API_SRC}:{REPO_ROOT}",
            "PATH": __import__("os").environ.get("PATH", ""),
            "HOME": __import__("os").environ.get("HOME", ""),
            # Suppress the chromadb telemetry noise
            "ANONYMIZED_TELEMETRY": "False",
        },
    )
    if result.returncode != 0:
        pytest.fail(
            f"demo exited {result.returncode}\n"
            f"--- stdout ---\n{result.stdout[-2000:]}\n"
            f"--- stderr ---\n{result.stderr[-2000:]}"
        )

    out = result.stdout
    expected_markers = [
        "Step 1: Typed writes",
        "Step 2: Namespace isolation",
        "Step 3: Typed vector similarity",
        "Step 4: Grounded rationale",
        "Step 5: Algorithm write-back",
        "Step 6: Filtered bitemporal diff",
        "Step 7: Non-extraction ingest mode",
        "Step 8: Summary stats",
        "Demo complete",
    ]
    missing = [m for m in expected_markers if m not in out]
    assert not missing, (
        f"demo output missing expected markers: {missing}\n"
        f"stdout tail:\n{out[-1500:]}"
    )


def test_demo_produces_vector_hits():
    """The vector-similarity step must actually return results.
    If indexing-on-insert silently breaks, this catches it."""
    result = subprocess.run(
        [sys.executable, str(DEMO)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        env={
            "PYTHONPATH": f"{API_SRC}:{REPO_ROOT}",
            "PATH": __import__("os").environ.get("PATH", ""),
            "HOME": __import__("os").environ.get("HOME", ""),
            "ANONYMIZED_TELEMETRY": "False",
        },
    )
    # Step 3 prints lines like "0.738  h-1: ..." — at least one must appear
    assert " h-1:" in result.stdout or " h-2:" in result.stdout or " h-3:" in result.stdout


def test_demo_persists_proposal_edges():
    """Algorithm write-back must actually write at least one ProposalBy."""
    result = subprocess.run(
        [sys.executable, str(DEMO)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        env={
            "PYTHONPATH": f"{API_SRC}:{REPO_ROOT}",
            "PATH": __import__("os").environ.get("PATH", ""),
            "HOME": __import__("os").environ.get("HOME", ""),
            "ANONYMIZED_TELEMETRY": "False",
        },
    )
    assert "ProposalBy edges persisted" in result.stdout
    # Walk to the line and check the count isn't zero
    for line in result.stdout.splitlines():
        if "ProposalBy edges persisted" in line:
            # Format: "  ✓ N ProposalBy edges persisted"
            count = int(line.strip().split()[1])
            assert count >= 1, f"demo persisted 0 proposals; line: {line!r}"
            return
    pytest.fail("persistence line not found")
