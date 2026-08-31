"""Tests for the stroke font used to draw readable text in a CARLA world.

No server: ``carla.Location`` and ``carla.Vector3D`` are client-side value
types, so a glyph can be drawn against a recording stand-in and measured.
"""

import pytest

# Guarded rather than imported bare: an ImportError at module scope is a
# *collection* error, and pytest abandons the whole run on the first one --
# so a missing optional dependency reports this project as entirely broken
# rather than as one suite that cannot run here.
pytest.importorskip("carla", reason="the CARLA client wheel is not installed")
import carla

from src.simulation import carla_debug_text
from src.simulation.carla_debug_text import (
    GLYPHS,
    advance,
    draw_text,
    stroke_count,
    text_width,
)

RIGHT = carla.Vector3D(x=1.0, y=0.0, z=0.0)
UP = carla.Vector3D(x=0.0, y=0.0, z=1.0)
WHITE = carla.Color(r=255, g=255, b=255)


class _Recorder:
    """Collects the segments that would have been drawn."""

    def __init__(self) -> None:
        self.lines: list[tuple[object, object]] = []
        self.thicknesses: list[float] = []

    def draw_line(self, begin, end, **kwargs) -> None:
        self.lines.append((begin, end))
        self.thicknesses.append(kwargs.get("thickness"))


def _drawn(text: str, height: float = 1.0, **kwargs) -> _Recorder:
    """Draw a string at the origin and return what was recorded."""
    recorder = _Recorder()
    draw_text(recorder, text, carla.Location(x=0.0, y=0.0, z=0.0),
              RIGHT, UP, height, WHITE, 1.0, **kwargs)
    return recorder


def test_a_space_costs_nothing() -> None:
    assert _drawn(" ").lines == []
    assert stroke_count(" ") == 0


def test_stroke_count_matches_what_is_drawn() -> None:
    """The budget helper has to agree with the drawing, or it is a lie."""
    for text in ("A", "HELLO", "n_s15_n_s13", "waiting 4", "0123456789"):
        assert stroke_count(text) == len(_drawn(text).lines), text


def test_lowercase_is_folded_to_uppercase() -> None:
    assert stroke_count("abc") == stroke_count("ABC")
    assert len(_drawn("abc").lines) == len(_drawn("ABC").lines)


def test_an_unknown_character_is_drawn_as_the_fallback() -> None:
    """A surprise should be visible, not silently missing."""
    assert stroke_count("é") == stroke_count("?")
    assert len(_drawn("é").lines) > 0


def test_every_glyph_is_drawable() -> None:
    """A malformed table entry would be an exception at draw time."""
    for character in GLYPHS:
        _drawn(character)


def test_text_grows_along_the_right_vector() -> None:
    recorder = _drawn("HH", height=1.0)
    xs = [point.x for pair in recorder.lines for point in pair]
    # The second H sits exactly one advance further along.
    assert max(xs) == pytest.approx(advance(1.0) + 2.0 / 4.0, abs=1e-6)


def test_height_scales_the_drawing() -> None:
    small = _drawn("E", height=1.0)
    large = _drawn("E", height=3.0)
    assert len(small.lines) == len(large.lines)
    small_top = max(point.z for pair in small.lines for point in pair)
    large_top = max(point.z for pair in large.lines for point in pair)
    assert large_top == pytest.approx(small_top * 3.0)


def test_cap_height_is_the_height_asked_for() -> None:
    """A caller placing lines needs the number to mean something."""
    top = max(point.z for pair in _drawn("E", height=2.0).lines
              for point in pair)
    assert top == pytest.approx(2.0)


def test_the_underscore_hangs_below_the_baseline() -> None:
    """Stop ids are mostly underscores; they must not read as letters."""
    zs = [point.z for pair in _drawn("_").lines for point in pair]
    assert max(zs) < 0.0


def test_stroke_width_follows_the_height_by_default() -> None:
    thin = _drawn("E", height=1.0).thicknesses[0]
    thick = _drawn("E", height=4.0).thicknesses[0]
    assert thick == pytest.approx(thin * 4.0)


def test_an_explicit_thickness_wins() -> None:
    assert _drawn("E", height=2.0, thickness=0.5).thicknesses[0] == 0.5


def test_width_accounts_for_every_character() -> None:
    assert text_width("AAA", 1.0) == pytest.approx(3 * advance(1.0))
    assert text_width("", 1.0) == 0.0


def test_draw_text_reports_what_it_drew() -> None:
    """Callers budget in strokes, so the return value has to be the count."""
    recorder = _Recorder()
    drawn = draw_text(recorder, "HELLO",
                      carla.Location(x=0.0, y=0.0, z=0.0), RIGHT, UP,
                      1.0, WHITE, 1.0)
    assert drawn == len(recorder.lines) == stroke_count("HELLO")


def test_text_can_be_laid_in_any_plane() -> None:
    """The caller supplies the plane, so labels can face a moving camera."""
    recorder = _Recorder()
    draw_text(recorder, "E", carla.Location(x=0.0, y=0.0, z=0.0),
              carla.Vector3D(x=0.0, y=1.0, z=0.0), UP, 1.0, WHITE, 1.0)
    # Laid along +y, nothing should have moved in x.
    assert all(point.x == pytest.approx(0.0)
               for pair in recorder.lines for point in pair)


def test_the_font_covers_what_the_overlay_writes() -> None:
    """Every character the backend's labels can contain must be drawable."""
    used = set("abcdefghijklmnopqrstuvwxyz0123456789_ :->.".upper())
    assert used <= set(GLYPHS)
