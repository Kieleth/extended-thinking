"""CLI output vocabulary — Dieter Rams via autoresearch-et.

The Rams move, stolen from autoresearch-et: the TUI is a KG query. Every
command renders data the graph already holds; nothing is invented at the
view layer. Stats come from `get_stats`, lists come from `list_concepts`,
diffs come from `GraphStore.diff`. The CLI's job is legibility, not
computation.

Design vocabulary (copied from the autoresearch-et TUI mockup):

  - Horizontal rules (─) for section boundaries. No boxes, no columns
    of vertical bars. The rule is the one load-bearing decoration.
  - Two tones: normal for content, dim for structure. One accent for
    identity (muted cyan), used only on the left side of the header.
  - Lowercase. Punctuation minimal. A sentence either ends in `.` or
    doesn't need one.
  - Block characters (▓░) for progress bars. Sparkline chars
    (▁▂▃▄▅▆▇█) for trends. ✓ / · / ✗ for row status.
  - Dense-but-aligned metric grids, not bullet lists.
  - Hints at the bottom. Parenthetical.

Everything here is pure-stdlib. No `rich`, no click. One import surface
for the entire CLI. Colour only when stdout is a TTY; piped output stays
plain text so it pipes into grep/awk without escape codes.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Iterable, Sequence

# ── Palette ──────────────────────────────────────────────────────────

# ANSI SGR codes. Two tones + one accent. That's it.
_RESET = "\033[0m"
_DIM = "\033[38;5;244m"     # neutral grey for rules, labels, meta
_ACCENT = "\033[38;5;80m"   # muted cyan — ET's signature, header only
_WARN = "\033[38;5;179m"    # soft amber for non-fatal notices
_ERR = "\033[38;5;167m"     # muted rose for hard errors
_OK = "\033[38;5;107m"      # muted moss for ✓ / success markers
_RED = "\033[38;5;202m"     # ET's fingertip — orange-red glow, not fire-alarm red


def _tty() -> bool:
    """Colour output only when stdout is a terminal and NO_COLOR is unset."""
    return (
        sys.stdout.isatty()
        and os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM") != "dumb"
    )


def _sgr(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}" if _tty() else text


def dim(text: str) -> str:
    return _sgr(_DIM, text)


def accent(text: str) -> str:
    return _sgr(_ACCENT, text)


def warn_tone(text: str) -> str:
    return _sgr(_WARN, text)


def err_tone(text: str) -> str:
    return _sgr(_ERR, text)


def ok_tone(text: str) -> str:
    return _sgr(_OK, text)


def red_tone(text: str) -> str:
    """ET's fingertip red. Reserved for the phone-home glow."""
    return _sgr(_RED, text)


# ── ET mascot ────────────────────────────────────────────────────────
# One-character-wide sprite. Eyes are fixed; expressions and hand state
# are composed from small frame vocabularies. Rendered inline, always
# with the accent-cyan `et` to its right.

FACES = {
    "open":    "◉‿◉",   # default, eyes wide
    "blink":   "⁃‿⁃",   # both eyes closed
    "wink_l":  "-‿◉",   # left eye closed
    "wink_r":  "◉‿-",   # right eye closed
    "look_l":  "◐‿◐",   # both looking left
    "look_r":  "◑‿◑",   # both looking right
    "narrow":  "◔‿◔",   # squinting, focused
    "up":      "◓‿◓",   # looking up
}

# Hand states (one-line sprites — used by et reset, mood footer, etc.)
# The outer `╭╮` is structural (always dim); the inner glyph, when
# present, is the glowing fingertip — red in phone-home mode.
# `none` hides the hand entirely — the phase-opening frame where only
# eyes are visible, before the finger reaches out.
HANDS = {
    "none":       ("",  "",  ""),    # no hand at all — wake_up only
    # All visible hands are 3 cells wide — `rest` gets an invisible
    # space between the corners so the label column after the sprite
    # doesn't jitter left-right as the glow cycle animates through
    # rest → spark → small → lit → burn → back.
    "rest":       ("╭", " ", "╮"),   # no glow, 3 cells
    "spark":      ("╭", "·", "╮"),   # warming up
    "small":      ("╭", "•", "╮"),
    "lit":        ("╭", "●", "╮"),
    "burn":       ("╭", "⦿", "╮"),   # peak
}


# ── Mood signature ───────────────────────────────────────────────────
# One-line ET sprite appended to command output. Reactive — each caller
# picks a (face, hand, note) that matches what just happened.

def signature(
    face: str = "open",
    hand: str = "rest",
    *,
    glowing: bool = False,
    note: str = "",
) -> str:
    """Render a one-line ET signature for end-of-command moods.

        ◉‿◉ ╭●╮  signal received.

    Caller is responsible for the newlines around it. Returns a string
    starting with two leading spaces to align under the command's
    header + rule indent.
    """
    sprite = mascot(face, hand, glowing=glowing)
    if note:
        return f"  {sprite}  {dim(note)}"
    return f"  {sprite}"


def mascot(face: str = "open", hand: str = "rest", *, glowing: bool = False) -> str:
    """Compose a mascot sprite. `glowing=True` turns the inner hand
    glyph red; otherwise the whole hand is dim."""
    f = FACES.get(face, FACES["open"])
    l, mid, r = HANDS.get(hand, HANDS["rest"])
    # "none" hand: hide the hand entirely so only the face shows. Lets
    # the phase-open animation pop the eyes before the finger reaches.
    if not (l or mid or r):
        return f
    if glowing and mid:
        hand_str = f"{dim(l)}{red_tone(mid)}{dim(r)}"
    else:
        hand_str = dim(f"{l}{mid}{r}")
    return f"{f} {hand_str}"


# Named animation scripts. Each entry is a list of (face, hand, glowing)
# frame tuples. Used by `et mascot` for the live reel.
ANIMATIONS: dict[str, list[tuple[str, str, bool]]] = {
    "idle": [
        ("open", "rest", False),
    ],
    "blink": [
        ("open", "rest", False),
        ("open", "rest", False),
        ("open", "rest", False),
        ("blink", "rest", False),
        ("open", "rest", False),
    ],
    "wink": [
        ("open", "rest", False),
        ("open", "rest", False),
        ("wink_l", "rest", False),
        ("open", "rest", False),
    ],
    "look around": [
        ("open", "rest", False),
        ("look_l", "rest", False),
        ("look_l", "rest", False),
        ("open", "rest", False),
        ("look_r", "rest", False),
        ("look_r", "rest", False),
        ("open", "rest", False),
    ],
    "phone home": [
        ("open", "rest", False),
        ("open", "spark", True),
        ("open", "small", True),
        ("open", "lit", True),
        ("open", "burn", True),
        ("open", "lit", True),
        ("open", "small", True),
        ("open", "spark", True),
    ],
    "burn steady": [
        ("open", "lit", True),
    ],
    "focus + burn": [
        ("narrow", "lit", True),
        ("narrow", "burn", True),
        ("narrow", "lit", True),
    ],
    "look up at work": [
        ("up", "spark", True),
        ("up", "small", True),
        ("up", "lit", True),
        ("up", "burn", True),
        ("up", "lit", True),
        ("up", "small", True),
    ],
}


# ── Width ────────────────────────────────────────────────────────────

def term_width(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except OSError:
        return default


def _ansi_strip(text: str) -> str:
    """Rough ANSI-code stripper for width calculations. Only handles the
    SGR codes we emit — good enough for rule-fill math."""
    out = []
    i = 0
    while i < len(text):
        if text[i] == "\033":
            end = text.find("m", i)
            if end == -1:
                break
            i = end + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


# ── Header + rules ───────────────────────────────────────────────────

RULE_CHAR = "─"


def header(left: str, right: str | None = None, width: int | None = None) -> str:
    """`  et ─ sync ────────────────────── claude-haiku-4-5`

    Left side carries the accent colour (ET's signature); the fill + right
    side are dim. Matches the autoresearch-et mockup shape.
    """
    if width is None:
        width = term_width()
    width = max(40, width)

    left_text = f"{accent('et')} {dim(RULE_CHAR)} {left}"
    right_text = dim(right) if right else ""

    left_w = len(_ansi_strip(left_text))
    right_w = len(_ansi_strip(right_text)) if right_text else 0
    # at least one space on either side of the fill
    fill_w = max(3, width - left_w - right_w - 2)
    fill = dim(RULE_CHAR * fill_w)

    if right_text:
        return f"{left_text} {fill} {right_text}"
    return f"{left_text} {fill}"


def subtitle(text: str) -> str:
    """Second line under the header. Dim, no prefix."""
    return dim(text)


def rule(width: int | None = None) -> str:
    """Full-width horizontal rule. Dim."""
    if width is None:
        width = term_width()
    return dim(RULE_CHAR * max(40, width))


# ── Row markers ──────────────────────────────────────────────────────

OK = "✓"
PENDING = "·"
FAIL = "✗"


def marker(status: str) -> str:
    """Render `ok` / `pending` / `fail` as its glyph, coloured."""
    if status == "ok":
        return ok_tone(OK)
    if status == "fail":
        return err_tone(FAIL)
    return dim(PENDING)


# ── Progress + sparkline ─────────────────────────────────────────────

_BLOCK_FULL = "▓"
_BLOCK_EMPTY = "░"
_SPARK = "▁▂▃▄▅▆▇█"


def progress_bar(current: int, total: int, *, width: int = 32) -> str:
    """`[▓▓▓▓▓░░░░░] 23 / 100` — solid/hollow blocks, count on the right."""
    if total <= 0:
        ratio = 0.0
    else:
        ratio = max(0.0, min(1.0, current / total))
    filled = int(round(ratio * width))
    bar = _BLOCK_FULL * filled + _BLOCK_EMPTY * (width - filled)
    return f"[{bar}] {current} / {total}"


def sparkline(values: Sequence[float]) -> str:
    """`▁▂▃▄▅▆▇█` — each value maps to one of eight heights."""
    if not values:
        return ""
    lo = min(values)
    hi = max(values)
    span = hi - lo or 1.0
    out = []
    for v in values:
        idx = int((v - lo) / span * (len(_SPARK) - 1))
        out.append(_SPARK[max(0, min(len(_SPARK) - 1, idx))])
    return "".join(out)


# ── Grids + rows ─────────────────────────────────────────────────────

def grid(
    rows: list[list[tuple[str, str]]],
    *,
    col_gap: int = 4,
    label_dim: bool = True,
) -> str:
    """Dense metric grid, one row per input row, N columns per cell.

    Each cell is a (label, value) tuple. Labels render dim, values normal.
    Columns are aligned so labels and values line up across rows.

        elapsed  04 : 17        solved    13 / 23   56.5%
        spend    $ 0.21         avg cost  $ 0.009 / problem
        tokens   22,184         throughput 86 tok / s
    """
    if not rows:
        return ""

    n_cols = max(len(r) for r in rows)
    # Normalise row lengths
    padded = [r + [("", "")] * (n_cols - len(r)) for r in rows]

    label_widths = [
        max(len(padded[r][c][0]) for r in range(len(padded)))
        for c in range(n_cols)
    ]
    value_widths = [
        max(len(padded[r][c][1]) for r in range(len(padded)))
        for c in range(n_cols)
    ]

    lines: list[str] = []
    for row in padded:
        parts = []
        for c, (label, value) in enumerate(row):
            lbl = dim(label.ljust(label_widths[c])) if label_dim else label.ljust(label_widths[c])
            val = value.ljust(value_widths[c])
            parts.append(f"{lbl}  {val}" if label else " " * (label_widths[c] + 2 + value_widths[c]))
        lines.append((" " * col_gap).join(parts).rstrip())
    return "\n".join(lines)


def row(status: str, cells: list[str], *, widths: list[int] | None = None) -> str:
    """A status-marked table row: `✓  p0923   2 4 7 12  (12 − 4) × 2 + 7`.

    `cells` are rendered normally; widths pad each cell to a min column width.
    """
    glyph = marker(status)
    if widths is None:
        body = "  ".join(cells)
    else:
        body = "  ".join(c.ljust(w) for c, w in zip(cells, widths + [0] * len(cells)))
    return f"{glyph}  {body}"


# ── Notices (errors, warnings, soft stops) ───────────────────────────

def notice(summary: str, *body: str, tone: str = "warn") -> str:
    """A soft notice. Used for migration refusals, misconfig, etc.

    Layout matches the rest of the language: a header-less rule bracket,
    the summary line in tone colour, body content beneath, a closing rule.

        ──────────────────────────────────────────────────────────────
        two data directories found. merge manually before continuing.

          legacy   ~/.extended-thinking                 12 MB
          xdg      ~/.local/share/extended-thinking      7 MB

          to merge legacy into xdg:
            rsync -a ~/.extended-thinking/ ~/.local/share/extended-thinking/
            rm -rf ~/.extended-thinking
        ──────────────────────────────────────────────────────────────

    `tone`: 'warn' (amber) for resolvable; 'err' (rose) for hard stops.
    """
    painter = warn_tone if tone == "warn" else err_tone
    out: list[str] = [rule(), painter(summary)]
    if body:
        out.append("")
        out.extend(body)
    out.append(rule())
    return "\n".join(out)


# ── Hint (bottom parenthetical) ──────────────────────────────────────

def hint(text: str) -> str:
    """Dim parenthetical at the bottom of a view: `(q)uit   any key: pause`."""
    return dim(text)
