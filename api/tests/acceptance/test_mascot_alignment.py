"""AT: ET mascot width + column invariants.

Every face and hand glyph must hold a predictable number of terminal
cells. A fullwidth Unicode char (east_asian_width = W or F) slipping
into FACES or HANDS would push adjacent glyphs one column right, which
cascades into every header and spinner that uses the sprite.

This AT enumerates the full cross-product of faces × hands and asserts
that `mascot()` renders at a consistent width per hand. Pure stdlib —
`unicodedata.east_asian_width` + ANSI stripping, no new deps.
"""

from __future__ import annotations

import re
import unicodedata

import pytest

from extended_thinking.cli_style import FACES, HANDS, mascot, signature

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
            continue
        else:
            total += 1
    return total


# ── Per-glyph widths ─────────────────────────────────────────────────

class TestVocabularyWidths:
    """Every face must be exactly 3 terminal cells. Hand glyph widths
    depend on the hand: `rest` is 2 (`╭╮`), the rest are 3 (`╭X╮`)."""

    @pytest.mark.parametrize("name,glyph", list(FACES.items()))
    def test_face_is_three_cells(self, name, glyph):
        w = _cell_width(glyph)
        assert w == 3, (
            f"FACES[{name!r}] = {glyph!r} is {w} cells, expected 3. "
            f"Likely a CJK-wide character — use a narrow-width glyph."
        )

    @pytest.mark.parametrize("name", list(HANDS.keys()))
    def test_hand_width_matches_outer_plus_inner(self, name):
        l, mid, r = HANDS[name]
        expected = _cell_width(l) + _cell_width(mid) + _cell_width(r)
        actual = _cell_width(f"{l}{mid}{r}")
        assert actual == expected, (
            f"HANDS[{name!r}] = {(l, mid, r)!r} width mismatch."
        )

    @pytest.mark.parametrize("name", ["rest", "spark", "small", "lit", "burn"])
    def test_visible_hands_are_three_cells(self, name):
        """Every visible hand renders at exactly 3 cells. Without this
        the label column shifts left/right as the glow cycle animates
        between `rest` (would be 2 cells) and the glowing frames (3)."""
        l, mid, r = HANDS[name]
        assert _cell_width(f"{l}{mid}{r}") == 3, (
            f"hand {name!r} should be 3 cells ({l!r}+{mid!r}+{r!r})"
        )

    def test_none_hand_is_zero_cells(self):
        l, mid, r = HANDS["none"]
        assert _cell_width(f"{l}{mid}{r}") == 0


# ── Composed one-line sprite ─────────────────────────────────────────

class TestSpriteRendering:
    """mascot(face, hand) returns a `<face> <hand>` one-liner. Width is
    deterministic: face(3) + space(1) + hand(2 or 3) = 6 or 7 cells."""

    @pytest.mark.parametrize("face", list(FACES.keys()))
    @pytest.mark.parametrize("hand", list(HANDS.keys()))
    def test_mascot_width_is_face_plus_hand_plus_one(self, face, hand):
        sprite = mascot(face, hand, glowing=True)
        w = _cell_width(sprite)
        l, mid, r = HANDS[hand]
        hand_w = _cell_width(f"{l}{mid}{r}")
        if hand_w == 0:
            # "none" hand — face only, no separator.
            expected = 3
        else:
            expected = 3 + 1 + hand_w
        assert w == expected, (
            f"mascot({face!r}, {hand!r}) = {sprite!r}, got {w} cells, "
            f"expected {expected}"
        )

    @pytest.mark.parametrize("face", list(FACES.keys()))
    @pytest.mark.parametrize("hand", list(HANDS.keys()))
    def test_signature_starts_with_two_spaces(self, face, hand):
        sig = signature(face, hand, glowing=True, note="")
        assert sig.startswith("  "), f"signature should be indented: {sig!r}"
