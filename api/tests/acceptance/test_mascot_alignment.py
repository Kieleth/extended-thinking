"""AT: ET mascot sprite column alignment.

The two-line lollipop only works when the top row and bottom row have
the same visible width at every column — otherwise brows don't sit
above eyes and the finger ball drifts off the stem.

Classic failure mode: a "CJK wide" character sneaks into a BROWS or
FACES entry. `＾` looks like `^` but is fullwidth (U+FF3E, 2 terminal
cells). `◉‿◉` is 3 cells; `＾ ＾` is 5. The test catches this before
the user ever sees it.

Uses stdlib `unicodedata.east_asian_width` — no new deps.
"""

from __future__ import annotations

import re
import unicodedata

import pytest

from extended_thinking.cli_style import (
    BROWS,
    FACES,
    GLOW_BALL,
    STEM,
    mascot_tall,
)

pytestmark = pytest.mark.acceptance


# ── Width measurement ────────────────────────────────────────────────

_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_SGR.sub("", s)


def _cell_width(s: str) -> int:
    """Number of terminal cells `s` occupies, ANSI-stripped.

    CJK Wide + Fullwidth chars count as 2; everything else counts as 1.
    Zero-width combining marks count 0.
    """
    s = _strip_ansi(s)
    total = 0
    for ch in s:
        w = unicodedata.east_asian_width(ch)
        if w in ("W", "F"):
            total += 2
        elif unicodedata.category(ch).startswith("M"):
            # Combining marks are zero-width.
            continue
        else:
            total += 1
    return total


# ── Per-glyph width checks ───────────────────────────────────────────

class TestVocabularyWidths:
    """Every face and brow entry must be exactly 3 terminal cells so
    they stack cleanly. The layout assumes brow glyphs sit directly
    above eye glyphs at the same column positions."""

    @pytest.mark.parametrize("name,glyph", list(BROWS.items()))
    def test_brow_is_three_cells(self, name, glyph):
        w = _cell_width(glyph)
        assert w == 3, (
            f"BROWS[{name!r}] = {glyph!r} is {w} cells, expected 3. "
            f"Likely a CJK-wide character (east_asian_width='W' or 'F') — "
            f"replace with its narrow-width equivalent."
        )

    @pytest.mark.parametrize("name,glyph", list(FACES.items()))
    def test_face_is_three_cells(self, name, glyph):
        w = _cell_width(glyph)
        assert w == 3, (
            f"FACES[{name!r}] = {glyph!r} is {w} cells, expected 3. "
            f"Likely a CJK-wide character — use a narrow-width glyph."
        )

    @pytest.mark.parametrize("name,glyph", list(GLOW_BALL.items()))
    def test_glow_ball_is_one_cell(self, name, glyph):
        w = _cell_width(glyph)
        assert w == 1, (
            f"GLOW_BALL[{name!r}] = {glyph!r} is {w} cells, expected 1 — "
            f"the ball must align in a single column above the stem."
        )

    def test_stem_is_one_cell(self):
        assert _cell_width(STEM) == 1, STEM


# ── Composed-sprite alignment ────────────────────────────────────────

class TestSpriteAlignment:
    """Rendered two-line sprites: top and bottom must be identical in
    width at every column. Enumerate the cross-product of brow × face ×
    glow so no combination can quietly misalign."""

    @pytest.mark.parametrize("brow", list(BROWS.keys()))
    @pytest.mark.parametrize("face", list(FACES.keys()))
    @pytest.mark.parametrize("glow", list(GLOW_BALL.keys()))
    def test_top_and_bottom_same_width(self, brow, face, glow):
        top, bottom = mascot_tall(face=face, brow=brow, glow=glow, glowing=True)
        tw, bw = _cell_width(top), _cell_width(bottom)
        assert tw == bw, (
            f"misaligned: brow={brow!r} face={face!r} glow={glow!r}\n"
            f"  top    = {top!r}  ({tw} cells)\n"
            f"  bottom = {bottom!r}  ({bw} cells)"
        )

    @pytest.mark.parametrize("brow", list(BROWS.keys()))
    @pytest.mark.parametrize("face", list(FACES.keys()))
    def test_ball_column_equals_stem_column(self, brow, face):
        """The finger ball on the top row must sit directly above the
        stem on the bottom row. Both are the last visible character;
        their column positions must match."""
        top, bottom = mascot_tall(face=face, brow=brow, glow="lit", glowing=True)
        top_plain = _strip_ansi(top)
        bot_plain = _strip_ansi(bottom)

        # The ball and stem are the final non-space characters on each row.
        # If widths match, last-char columns match.
        ball_col = _last_char_col(top_plain)
        stem_col = _last_char_col(bot_plain)
        assert ball_col == stem_col, (
            f"finger column mismatch: brow={brow!r} face={face!r}\n"
            f"  ball @ col {ball_col} on {top_plain!r}\n"
            f"  stem @ col {stem_col} on {bot_plain!r}"
        )


def _last_char_col(s: str) -> int:
    """Column (0-indexed, cell-aware) of the last non-space character."""
    col = 0
    last = -1
    for ch in s:
        w = unicodedata.east_asian_width(ch)
        cells = 2 if w in ("W", "F") else (0 if unicodedata.category(ch).startswith("M") else 1)
        if ch != " " and cells > 0:
            last = col
        col += cells
    return last
