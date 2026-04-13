#!/usr/bin/env python3
"""Extended-thinking CLI.

Usage:
  et insight              # sync + generate wisdom
  et concepts             # list concepts
  et sync                 # pull from provider
  et stats                # show stats
  et init                 # register ET as an MCP server with CC / Claude Desktop
  et reset [--go-home]    # wipe all ET state (dry-run unless --go-home)
  et mcp-serve            # run the MCP server (usually invoked by a client, not humans)
"""

from __future__ import annotations

# ── Silence the noise floor BEFORE any heavy import ──────────────────
# Must happen at module top so the environment is set before chromadb,
# huggingface/tokenizers, or any transitive import sees it. These are
# not our warnings — they leak from dependencies and make ET output
# look unfinished.
import os as _os
_os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")   # HF fork warning
_os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")     # chromadb posthog
_os.environ.setdefault("CHROMA_TELEMETRY_IMPL", "none")

import warnings as _warnings
_warnings.filterwarnings("ignore", module=r"chromadb\..*")
_warnings.filterwarnings("ignore", module=r"posthog\..*")
_warnings.filterwarnings("ignore", module=r"transformers\..*")

import logging as _logging
# Chromadb logs telemetry errors at ERROR level, not WARNING, so a filter
# on warnings alone doesn't catch them. Raise the floor for those loggers
# specifically — we don't want their internal problems on our stdout.
for _name in ("chromadb.telemetry", "chromadb.telemetry.product.posthog",
              "posthog", "httpx"):
    _logging.getLogger(_name).setLevel(_logging.CRITICAL + 1)

# ── Real imports ─────────────────────────────────────────────────────

import argparse
import asyncio
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from extended_thinking import cli_style as style


def _get_pipeline():
    """Construct the pipeline. Raises DataDirConflict if both legacy and
    XDG data dirs exist — the caller renders a notice."""
    from extended_thinking.config import migrate_data_dir, settings
    from extended_thinking.processing.pipeline_v2 import Pipeline
    from extended_thinking.providers import get_provider
    from extended_thinking.storage import StorageLayer

    data_dir = migrate_data_dir(settings)
    storage = StorageLayer.default(data_dir)
    return Pipeline.from_storage(get_provider(), storage)


def _humanize_bytes(n: int) -> str:
    """`12 MB` / `842 KB` / `7 B`. Three significant figures max."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{n} {unit}"
            return f"{n:.0f} {unit}" if n >= 10 else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.0f} TB"


def _render_data_dir_conflict(exc) -> str:
    """Turn a DataDirConflict into the redesigned notice."""
    legacy_size = _humanize_bytes(exc.legacy_size)
    xdg_size = _humanize_bytes(exc.xdg_size)
    legacy_str = str(exc.legacy).replace(str(Path.home()), "~")
    xdg_str = str(exc.xdg).replace(str(Path.home()), "~")

    # Pad paths to the same column so the sizes line up
    path_w = max(len(legacy_str), len(xdg_str))

    rows = [
        f"  {style.dim('legacy')}  {legacy_str:<{path_w}}  {legacy_size:>8}",
        f"  {style.dim('xdg')}     {xdg_str:<{path_w}}  {xdg_size:>8}",
    ]
    # ET looking sideways-confused for conflict states.
    sprite = style.mascot("wink_l", "rest")
    return (
        f"  {sprite}\n"
        + style.notice(
            "two data directories hold data. merge manually before continuing.",
            *rows,
            "",
            "to merge legacy into xdg:",
            f"  {style.dim('$')} rsync -a {legacy_str}/ {xdg_str}/",
            f"  {style.dim('$')} rm -rf {legacy_str}",
            "",
            "or keep one, remove the other, and run et sync again.",
            tone="warn",
        )
    )


# ── Commands ─────────────────────────────────────────────────────────

def cmd_insight(force: bool = False) -> int:
    from extended_thinking.mcp_server import _render_insight
    pipeline = _get_pipeline()

    print(style.header("insight"))

    sync_result = asyncio.run(pipeline.sync())
    insight = asyncio.run(pipeline.get_insight())

    if insight["type"] == "nothing_new" and force:
        wisdom = asyncio.run(pipeline.generate_wisdom(force=True))
        if wisdom:
            insight = {"type": "wisdom"}

    concepts = pipeline.store.list_concepts(order_by="frequency", limit=50)
    wisdoms = pipeline.store.list_wisdoms(limit=1)

    print()
    if wisdoms:
        print(_render_insight(wisdoms[0], concepts))
    else:
        title = insight.get("insight", {}).get("title", "no insight available")
        print(f"  {title}")
    return 0


def cmd_concepts(limit: int = 20) -> int:
    from extended_thinking.mcp_server import _render_concepts
    pipeline = _get_pipeline()
    concepts = pipeline.store.list_concepts(order_by="frequency", limit=limit)

    print(style.header("concepts", right=f"top {len(concepts)}"))
    print()
    print(_render_concepts(concepts))
    return 0


class _SyncReporter:
    """Live phase reporter for `Pipeline.sync(on_progress=...)`.

    One live ET sprite walks through the phases. Completed phases
    print above him as plain `· label detail 1.4s` rows; ET stays at
    the bottom, his face and fingertip matching whatever phase is
    currently active. When a phase ends, his line is overwritten with
    the completion row (+ newline), and a fresh live line is drawn
    below for the next phase.

        · reading provider          67 chunks                   1.8s
        · filtering content         67 thinking                 0.0s
        ◔‿◔ ╭●╮  extracting concepts     batch 2/4 · haiku      ← live ET

    One ET, walking down the transcript. Unix semantics — piped
    output skips ANSI and prints only the completion rows.
    """

    _LABELS = {
        "read":    "reading provider",
        "filter":  "filtering content",
        "index":   "indexing vectors",
        "extract": "extracting concepts",
        "resolve": "resolving entities",
        "relate":  "detecting relationships",
        "enrich":  "enriching with knowledge",
    }

    # Each phase gets a face + brow that match what ET's doing.
    # `extract` is the longest + most Haiku-heavy, so he narrows his
    # eyes + furrows his brow. `enrich` reaches outward, so he looks up
    # with raised brows. Other phases are left/right scanning.
    _PHASE_FACES = {
        "read":    "open",
        "filter":  "look_l",
        "index":   "look_r",
        "extract": "narrow",
        "resolve": "look_l",
        "relate":  "look_r",
        "enrich":  "up",
    }
    _PHASE_BROWS = {
        "read":    "neutral",
        "filter":  "raised",
        "index":   "raised",
        "extract": "furrow",
        "resolve": "tilt",
        "relate":  "tilt",
        "enrich":  "arch",
    }

    # Fingertip pulse during a phase — travels up the intensity ladder
    # and back. The lit/burn frames carry the orange-red glow.
    _GLOW_CYCLE = ["spark", "small", "lit", "burn", "lit", "small"]

    # Twitch faces — micro-animations that briefly override the phase
    # face to make ET feel alive. Blinks weighted highest because a
    # living creature blinks more often than it winks or scans.
    _TWITCH_CHOICES = [
        "blink", "blink", "blink",    # 3x weight
        "wink_l", "wink_r",
        "look_l", "look_r",
    ]

    def __init__(self):
        import random
        import time
        self._random = random
        self._time = time
        self._t_start = time.monotonic()
        self._t_phase = self._t_start
        self._label_w = max(len(v) for v in self._LABELS.values())
        self._active_phase: str | None = None
        self._active_detail: str = ""
        self._frame = 0
        self._tty = sys.stdout.isatty() and _os.environ.get("NO_COLOR") is None
        self._sprite_drawn = False
        # Twitch state — counts frames since last twitch, picks a random
        # interval for the next one so it feels non-metronomic.
        self._twitch_counter = 0
        self._twitch_every = random.randint(8, 14)
        self._twitch_face: str | None = None

    # ── Event callback ────────────────────────────────────────────────

    def __call__(self, event: str, phase: str, detail: str = "") -> None:
        if event == "start":
            self._active_phase = phase
            self._active_detail = ""
            self._t_phase = self._time.monotonic()
            self._paint_spinner()
        elif event == "tick":
            if self._active_phase != phase:
                # Event arrived out of order; normalize
                self._active_phase = phase
                self._t_phase = self._time.monotonic()
            self._active_detail = detail
            self._paint_spinner()
        elif event == "done":
            self._finalize(detail)

    # ── Spinner loop (called by a background asyncio task) ────────────

    async def spin(self):
        """Tick ~180ms (slower, breathing). On each tick, advance the
        glow frame, possibly trigger a one-frame face twitch, redraw."""
        import asyncio
        while True:
            await asyncio.sleep(0.18)
            self._frame += 1
            self._twitch_counter += 1
            if self._twitch_counter >= self._twitch_every:
                self._twitch_face = self._random.choice(self._TWITCH_CHOICES)
                self._twitch_counter = 0
                self._twitch_every = self._random.randint(8, 14)
            else:
                self._twitch_face = None
            if self._active_phase is not None:
                self._paint_spinner()

    # ── Rendering ─────────────────────────────────────────────────────

    def _paint_spinner(self) -> None:
        """Draw ET's two-line lollipop sprite.

        Row 1 = brows + finger ball. Row 2 = eye face + stem + label +
        detail. Persistent across ticks — we move cursor up 1, redraw
        both rows, end cursor on row 2. First paint emits both rows
        without the cursor-up dance.
        """
        if self._active_phase is None or not self._tty:
            return
        phase_face = self._PHASE_FACES.get(self._active_phase, "open")
        face = self._twitch_face or phase_face
        brow = self._PHASE_BROWS.get(self._active_phase, "neutral")
        glow = self._GLOW_CYCLE[self._frame % len(self._GLOW_CYCLE)]
        top, bottom = style.mascot_tall(face, brow, glow, glowing=True)

        label = self._LABELS.get(self._active_phase, self._active_phase)
        detail = self._active_detail or "…"
        top_line = f"  {top}"
        bottom_line = (
            f"  {bottom}  "
            f"{label:<{self._label_w + 2}}"
            f"{style.dim(detail)}"
        )

        if self._sprite_drawn:
            # Walk up to row 1, rewrite both rows, land on row 2.
            sys.stdout.write("\033[1A\r\033[K")
            sys.stdout.write(top_line)
            sys.stdout.write("\n\r\033[K")
            sys.stdout.write(bottom_line)
        else:
            # First paint of this phase — print both rows fresh.
            sys.stdout.write(top_line)
            sys.stdout.write("\n")
            sys.stdout.write(bottom_line)
            self._sprite_drawn = True
        sys.stdout.flush()

    def _finalize(self, detail: str) -> None:
        """End the active phase. Collapse the two-line sprite into a
        single `· label detail elapsed` completion row, then leave the
        cursor on a fresh row for the next phase's sprite.
        """
        if self._active_phase is None:
            return
        now = self._time.monotonic()
        elapsed = now - self._t_phase
        label = self._LABELS.get(self._active_phase, self._active_phase)
        completion = (
            f"  {style.dim('·')}  "
            f"{label:<{self._label_w + 2}}"
            f"{detail:<42}"
            f"{style.dim(f'{elapsed:>5.1f}s')}"
        )
        if self._tty:
            if self._sprite_drawn:
                # Walk up to row 1, clear both rows, print completion in
                # row 1's place, leave cursor on fresh row 2 for next sprite.
                sys.stdout.write("\033[1A\r\033[K")
                sys.stdout.write(completion)
                sys.stdout.write("\n\r\033[K")
            else:
                sys.stdout.write(f"\r{completion}\033[K\n")
            sys.stdout.flush()
        else:
            print(completion)
        self._sprite_drawn = False
        self._active_phase = None
        self._active_detail = ""

    def finish(self) -> None:
        """Called once sync() returns. Clears any trailing two-line sprite."""
        if self._tty and self._sprite_drawn:
            # Walk up, clear row 1, clear row 2, leave cursor on fresh line.
            sys.stdout.write("\033[1A\r\033[K")
            sys.stdout.write("\n\r\033[K")
            sys.stdout.flush()
        self._active_phase = None
        self._sprite_drawn = False

    def total(self) -> float:
        return self._time.monotonic() - self._t_start


def _confirm_sources(pipeline, assume_yes: bool) -> bool:
    """Show detected sources before sync runs; interactively pick which
    participate.

    Returns True to proceed, False to bail. Mutates
    `pipeline.provider._providers` in place to reflect the user's
    selection — ephemeral to this command run; persistent toggles live
    in `[providers.<name>].enabled` (ADR 012).

    AutoProvider's sub-providers each carry `name` + `get_stats()`;
    one row per provider with its memory count. Non-AutoProviders
    render as a single row and skip the picker (nothing to pick).
    """
    provider = pipeline.provider
    sub = getattr(provider, "_providers", None)

    rows: list[tuple[str, str, str]] = []  # (label, path, count)
    if sub is not None:
        for p in sub:
            count = p.get_stats().get("total_memories", 0)
            path = _describe_provider_path(p)
            rows.append((p.name, path, f"{count:,} memories"))
    else:
        count = provider.get_stats().get("total_memories", 0)
        rows.append((provider.name, _describe_provider_path(provider), f"{count:,} memories"))

    if not rows:
        print(style.notice("no providers detected. configure one in ~/.config/extended-thinking/config.toml.", tone="warn"))
        return False

    print(f"  {style.dim('found')}  {len(rows)} source{'s' if len(rows) != 1 else ''}:")
    print()

    # Static path: --yes or non-TTY. Just list + proceed.
    if assume_yes or not sys.stdout.isatty() or not sys.stdin.isatty():
        label_w = max(len(r[0]) for r in rows)
        path_w = max(len(r[1]) for r in rows)
        for name, path, count in rows:
            print(f"    {style.ok_tone('✓')}  {name:<{label_w}}   {style.dim(path):<{path_w}}   {count}")
        print()
        print(style.hint("  proceeding (--yes)" if assume_yes else "  proceeding (non-interactive)"))
        print()
        return True

    # Interactive path: arrow-key selector. Only meaningful when sub is a
    # list we can filter; single-provider case skips the picker.
    if sub is None:
        print(f"    {style.ok_tone('✓')}  {rows[0][0]}   {style.dim(rows[0][1])}   {rows[0][2]}")
        print()
        try:
            answer = input(f"  {style.dim('proceed?')} [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        print()
        return answer in ("", "y", "yes")

    selected = _interactive_source_picker(rows)
    if selected is None:
        return False

    # Apply selection: filter sub-providers in place.
    if not any(selected):
        print(style.notice("no sources selected. nothing to sync.", tone="warn"))
        return False

    original = list(sub)
    sub[:] = [p for p, keep in zip(original, selected) if keep]
    print()
    return True


def _interactive_source_picker(rows) -> list[bool] | None:
    """Arrow-key multi-select. Returns list[bool] of kept rows, or None.

    Pure stdlib: tty.setraw + termios. Unix only (posix). The caller
    should only invoke this when both stdin and stdout are TTYs.

    Keys:
      ↑/↓   move cursor
      space toggle current row
      a     select all
      n     select none
      enter proceed with the current selection
      q     cancel (returns None)
    """
    import termios
    import tty

    n = len(rows)
    cursor = 0
    selected = [True] * n
    label_w = max(len(r[0]) for r in rows)
    path_w = max(len(r[1]) for r in rows)

    def frame() -> list[str]:
        lines = []
        for i, (label, path, count) in enumerate(rows):
            pointer = style.accent("▸") if i == cursor else " "
            mark = style.ok_tone("✓") if selected[i] else style.dim("·")
            lines.append(
                f"    {pointer}  [{mark}]  {label:<{label_w}}   "
                f"{style.dim(path):<{path_w}}   {count}"
            )
        lines.append("")
        lines.append(
            "    " + style.dim(
                "↑/↓ move   space toggle   a all   n none   enter proceed   q cancel"
            )
        )
        return lines

    # Initial paint.
    lines = frame()
    for line in lines:
        print(line)
    sys.stdout.write("\033[?25l")  # hide cursor
    sys.stdout.flush()

    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == "\x1b":  # ESC → arrow key sequence
                seq = sys.stdin.read(2)
                if seq == "[A":
                    cursor = (cursor - 1) % n
                elif seq == "[B":
                    cursor = (cursor + 1) % n
                else:
                    continue
            elif ch in ("\r", "\n"):
                break
            elif ch == " ":
                selected[cursor] = not selected[cursor]
            elif ch in ("a", "A"):
                selected = [True] * n
            elif ch in ("n", "N"):
                selected = [False] * n
            elif ch in ("q", "Q", "\x03"):  # q or Ctrl+C
                return None
            else:
                continue

            # Redraw: move cursor up to the top of the block and rewrite.
            sys.stdout.write(f"\033[{len(lines)}A")
            for line in frame():
                sys.stdout.write(f"\r\033[K{line}\n")
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
        sys.stdout.write("\033[?25h")  # show cursor
        sys.stdout.flush()

    return selected


def _describe_provider_path(p) -> str:
    """Best-effort one-line description of where a provider reads from.

    Providers store their roots under private attrs (_projects_dir,
    _root, _workspace_dir, etc.) and also surface them via get_stats().
    Check both, prefer the attr over the stats round-trip.
    """
    for attr in (
        "projects_dir", "_projects_dir",
        "root", "_root",
        "path", "_path",
        "export_path", "_export_path",
        "workspace_dir", "_workspace_dir",
        "folder", "_folder",
    ):
        v = getattr(p, attr, None)
        if v:
            return str(v).replace(str(Path.home()), "~")
    try:
        stats = p.get_stats()
    except Exception:  # noqa: BLE001
        return "(configured)"
    for key in ("projects_dir", "root", "path", "export_path"):
        v = stats.get(key)
        if v:
            return str(v).replace(str(Path.home()), "~")
    return "(configured)"


def cmd_sync(yes: bool = False) -> int:
    pipeline = _get_pipeline()

    provider = pipeline.provider if hasattr(pipeline, "provider") else None
    provider_name = getattr(provider, "name", "?") if provider else "?"

    print(style.header("sync", right=provider_name))
    print()

    # Pre-flight: show what we're about to ingest from, confirm.
    if not _confirm_sources(pipeline, assume_yes=yes):
        print(style.hint("  cancelled."))
        return 0

    result, total_time = asyncio.run(_run_sync_with_reporter(pipeline))
    total = pipeline.store.get_stats()["total_concepts"]

    delta = result["concepts_extracted"]
    chunks = result["chunks_processed"]
    status = result.get("status", "synced")

    print()
    print(style.rule())
    print()
    grid_rows = [
        [("chunks", str(chunks)), ("concepts", f"{total:,}")],
        [("+ concepts", f"+{delta}"), ("status", status)],
    ]
    enrichment = result.get("enrichment")
    if enrichment:
        grid_rows.append([
            ("+ enriched", str(enrichment.get("knowledge_nodes_created", 0))),
            ("runs", str(enrichment.get("runs_recorded", 0))),
        ])
    grid_rows.append([("elapsed", f"{total_time:.1f}s"), ("", "")])
    print(style.grid(grid_rows))

    # Mood signature: ET's face + hand intensity scale with how much
    # just happened. Zero new concepts → bored ET with resting hand.
    # Small haul → curious. Big haul → wide-eyed + finger burning.
    mood = _sync_mood(delta, enrichment)
    print()
    print(f"  {mood}")
    return 0


def _sync_mood(delta: int, enrichment: dict | None) -> str:
    """One-line mascot signature encoding the size of the sync delta."""
    if delta == 0 and not enrichment:
        # Nothing new. ET closes his eyes. No phone-home.
        return style.mascot("blink", "rest") + f"  {style.dim('nothing new.')}"
    if delta < 5:
        return style.mascot("open", "spark", glowing=True) + f"  {style.dim('a few new concepts.')}"
    if delta < 20:
        return style.mascot("open", "lit", glowing=True) + f"  {style.dim('signal received.')}"
    # Big haul — eyes widen, finger burns. Something actually happened.
    return style.mascot("narrow", "burn", glowing=True) + f"  {style.dim('big haul. phoning home.')}"


async def _run_sync_with_reporter(pipeline):
    """Run sync() with the live spinner reporter in parallel.

    The reporter is itself a callback; a sibling asyncio task just keeps
    the spinner frame ticking at ~80ms so the active phase line looks
    alive during long awaits (Haiku extraction in particular).
    """
    import contextlib

    reporter = _SyncReporter()
    spin_task = asyncio.create_task(reporter.spin())
    try:
        result = await pipeline.sync(on_progress=reporter)
    finally:
        reporter.finish()
        spin_task.cancel()
        # CancelledError is the expected outcome of cancelling an infinite
        # spinner loop; suppress() expresses that intent without tripping
        # the no-silent-swallow invariant.
        with contextlib.suppress(asyncio.CancelledError):
            await spin_task
    return result, reporter.total()


def cmd_stats() -> int:
    pipeline = _get_pipeline()
    stats = pipeline.get_stats()
    p = stats["provider"]
    c = stats["concepts"]

    provider_name = p.get("detected_provider", p.get("provider", "?"))
    print(style.header("stats", right=provider_name))
    print()

    grid_rows = [
        [("memories", f"{p.get('total_memories', 0):,}"),
         ("concepts", f"{c['total_concepts']:,}")],
        [("relationships", f"{c['total_relationships']:,}"),
         ("wisdoms", f"{c['total_wisdoms']:,}")],
    ]
    print(style.grid(grid_rows))
    return 0


def cmd_mcp_serve() -> int:
    """Run the MCP server. Usually invoked by a client, not directly by humans."""
    from extended_thinking.mcp_server import run_mcp_server
    run_mcp_server()
    return 0


# ── et reset ─────────────────────────────────────────────────────────
# Nukes every trace of ET on the machine. Dry-run by default — actual
# deletion requires --go-home (the intent is "you're going home; start
# over"). No partial modes: all three locations go together or the
# command does nothing. Anything subtler is a config edit, not a reset.

def _reset_targets() -> list[tuple[str, Path]]:
    """The three locations ET occupies on disk."""
    from extended_thinking.config import settings
    from extended_thinking.config.paths import LEGACY_DATA_DIR, user_config_dir

    return [
        ("data", settings.data.root),
        ("config", user_config_dir()),
        ("legacy", LEGACY_DATA_DIR),
    ]


def cmd_reset(go_home: bool = False) -> int:
    """`et reset` — wipe all ET state. Dry-run unless --go-home is set."""
    from extended_thinking.config.migrate import _dir_size

    targets = _reset_targets()
    present = [(label, path, _dir_size(path)) for label, path, *_ in
               [(lbl, p) for lbl, p in targets] if path.exists()]

    mode = "going home" if go_home else "dry-run"
    print(style.header("reset", right=mode))
    print()

    if not present:
        print(style.notice(
            "nothing to reset — no ET state found on this machine.",
            tone="warn",
        ))
        return 0

    if not go_home:
        # Preview: list what would be deleted, with sizes.
        rows = []
        total = 0
        path_w = max(len(str(p).replace(str(Path.home()), "~")) for _, p, _ in present)
        for label, path, size in present:
            path_str = str(path).replace(str(Path.home()), "~")
            total += size
            rows.append(
                f"  {style.dim(label):<14}  {path_str:<{path_w}}  {_humanize_bytes(size):>10}"
            )

        print(style.notice(
            "about to remove every trace of ET on this machine.",
            *rows,
            "",
            f"  {style.dim('total')}         {_humanize_bytes(total):>10}  across {len(present)} location{'s' if len(present) != 1 else ''}.",
            "",
            f"run {style.accent('et reset --go-home')} to actually do it.",
            tone="warn",
        ))
        return 0

    # Real deletion. ET's face fades through half-closed to closed as
    # each location gets wiped — "going home" rendered in three frames.
    fade_faces = ["open", "narrow", "blink"]
    failures: list[str] = []
    for i, (label, path, size) in enumerate(present):
        face_key = fade_faces[min(i, len(fade_faces) - 1)]
        sprite = style.mascot(face_key, "rest")
        try:
            shutil.rmtree(path)
            print(f"  {sprite}  {style.ok_tone('✓')}  {label:<10}  removed  ({_humanize_bytes(size)})")
        except OSError as e:
            failures.append(f"{label}: {e}")
            print(f"  {sprite}  {style.err_tone('✗')}  {label:<10}  {e}")

    print()
    if failures:
        print(style.hint(f"  {len(failures)} location{'s' if len(failures) != 1 else ''} could not be removed; see above"))
        return 1

    # Final frame — ET fully home, eyes closed.
    print(f"  {style.mascot('blink', 'rest')}  {style.dim('clean slate. run et sync to start over.')}")
    return 0


# ── et init ──────────────────────────────────────────────────────────────────

MCP_SERVER_KEY = "extended-thinking"


def _mcp_entry() -> dict:
    """The MCP server entry to register. Uses sys.executable so it works across
    pip, pipx, and editable installs without relying on PATH resolution at spawn time."""
    return {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "extended_thinking.mcp_server"],
        "env": {},
    }


def _client_configs() -> list[tuple[str, Path]]:
    """Known MCP-client config locations on this machine. Returns (name, path) pairs."""
    home = Path.home()
    return [
        ("Claude Code", home / ".claude.json"),
        ("Claude Desktop", home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"),
        ("opencode", home / ".config" / "opencode" / "config.json"),
        ("Codex CLI", home / ".codex" / "config.json"),
    ]


def _backup(path: Path) -> Path:
    """Timestamped backup sibling. Returns the backup path."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak-{ts}")
    shutil.copy2(path, bak)
    return bak


def _patch_client(name: str, path: Path, dry_run: bool = False) -> tuple[str, str]:
    """Register ET in one client's config. Returns (status, detail) for a row()."""
    if not path.exists():
        return ("pending", f"{name:<15} config not found")

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return ("fail", f"{name:<15} invalid JSON ({e})")
    except OSError as e:
        return ("fail", f"{name:<15} read failed ({e})")

    mcp = data.setdefault("mcpServers", {})
    entry = _mcp_entry()
    existing = mcp.get(MCP_SERVER_KEY)

    if existing == entry:
        return ("ok", f"{name:<15} already registered")

    action = "update" if existing else "add"
    if dry_run:
        return ("pending", f"{name:<15} would {action}")

    bak = _backup(path)
    mcp[MCP_SERVER_KEY] = entry
    path.write_text(json.dumps(data, indent=2) + "\n")
    return ("ok", f"{name:<15} {action} ({bak.name})")


def cmd_init(dry_run: bool = False) -> int:
    right = "dry-run" if dry_run else None
    print(style.header("init", right=right))
    print(style.subtitle(f"  register {MCP_SERVER_KEY!r} with local MCP clients"))
    print()

    for name, path in _client_configs():
        status, detail = _patch_client(name, path, dry_run=dry_run)
        print(f"  {style.row(status, [detail])}")

    print()
    print(style.hint("  restart the client to pick up the new MCP server"))
    if dry_run:
        print(style.hint("  (dry-run: no files were modified)"))
    return 0


# ── entry point ──────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="et", description="Extended-thinking CLI.")
    sub = parser.add_subparsers(dest="cmd", required=False)

    sub.add_parser("insight", help="sync + generate wisdom").add_argument("--force", action="store_true")
    sub.add_parser("concepts", help="list concepts").add_argument("--limit", type=int, default=20)
    p_sync = sub.add_parser("sync", help="pull from provider")
    p_sync.add_argument("-y", "--yes", action="store_true",
                        help="skip the source-confirmation prompt")
    sub.add_parser("stats", help="show stats")
    sub.add_parser("mcp-serve", help="run the MCP server (for clients)")

    p_init = sub.add_parser("init", help="register ET as an MCP server with CC / Claude Desktop")
    p_init.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")

    p_reset = sub.add_parser("reset", help="wipe all ET state (dry-run unless --go-home)")
    p_reset.add_argument("--go-home", action="store_true",
                         help="actually delete; without this flag, reset previews only")

    # `et config ...` — ADR 012
    p_cfg = sub.add_parser("config", help="inspect or edit ET configuration")
    cfg_sub = p_cfg.add_subparsers(dest="config_cmd", required=True)
    p_cfg_init = cfg_sub.add_parser("init", help="scaffold config.toml and secrets.toml under ~/.config/extended-thinking")
    p_cfg_init.add_argument("--force", action="store_true", help="overwrite existing files")
    cfg_sub.add_parser("path", help="print resolved config file paths")
    p_cfg_show = cfg_sub.add_parser("show", help="print resolved effective config")
    p_cfg_show.add_argument("--format", choices=["toml", "json"], default="toml")
    p_cfg_show.add_argument("--show-secrets", action="store_true", help="do not redact credential values")
    cfg_sub.add_parser("validate", help="load + validate config, exit nonzero on error")

    p_cfg_get = cfg_sub.add_parser("get", help="read a single config value (dotted path)")
    p_cfg_get.add_argument("key", help="e.g. extraction.model, algorithms.decay.physarum.decay_rate")

    p_cfg_set = cfg_sub.add_parser("set", help="write a single config value")
    p_cfg_set.add_argument("key", help="dotted path")
    p_cfg_set.add_argument("value", help="value (bool/int/float/list via commas/string)")
    p_cfg_set.add_argument("--scope", choices=["user", "project", "secrets"], default="user")

    p_cfg_edit = cfg_sub.add_parser("edit", help="open config in $EDITOR")
    p_cfg_edit.add_argument("--scope", choices=["user", "project", "secrets"], default="user")

    return parser


def _dispatch(args) -> int:
    if args.cmd == "insight":
        return cmd_insight(force=args.force)
    if args.cmd == "concepts":
        return cmd_concepts(limit=args.limit)
    if args.cmd == "sync":
        return cmd_sync(yes=args.yes)
    if args.cmd == "stats":
        return cmd_stats()
    if args.cmd == "init":
        return cmd_init(dry_run=args.dry_run)
    if args.cmd == "reset":
        return cmd_reset(go_home=args.go_home)
    if args.cmd == "mcp-serve":
        return cmd_mcp_serve()
    if args.cmd == "config":
        from extended_thinking.config.commands import (
            cmd_config_edit,
            cmd_config_get,
            cmd_config_init,
            cmd_config_path,
            cmd_config_set,
            cmd_config_show,
            cmd_config_validate,
        )
        if args.config_cmd == "init":
            return cmd_config_init(force=args.force)
        if args.config_cmd == "path":
            return cmd_config_path()
        if args.config_cmd == "show":
            return cmd_config_show(format=args.format, show_secrets=args.show_secrets)
        if args.config_cmd == "validate":
            return cmd_config_validate()
        if args.config_cmd == "get":
            return cmd_config_get(args.key)
        if args.config_cmd == "set":
            return cmd_config_set(args.key, args.value, scope=args.scope)
        if args.config_cmd == "edit":
            return cmd_config_edit(scope=args.scope)
    return 1


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.cmd is None:
        parser.print_help()
        return 1

    # Single place to catch expected, renderable error states. Anything
    # else bubbles up as a Python traceback (bug, not a UX concern).
    try:
        return _dispatch(args)
    except Exception as exc:
        from extended_thinking.config.migrate import DataDirConflict
        if isinstance(exc, DataDirConflict):
            print(_render_data_dir_conflict(exc), file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
