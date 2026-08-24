"""Tests for the demo entry point's argument handling.

Only the pure parts: `main.py` defers its `carla` and pygame imports into
`build_carla_factory`, so the parser and its helpers can be exercised with
nothing installed and no server running.
"""

import main
from src.simulation.SimulationRunner import TickProgress


def _parsed(*argv: str):
    """Parse a demo command line."""
    return main.build_parser().parse_args(list(argv))


# ---------------------------------------------------------------------------
# Render resolution
# ---------------------------------------------------------------------------

def test_render_size_defaults_to_following_the_window():
    """
    The default must be indistinguishable from the viewer's own size.

    `CarlaViewer` reads `render_size or size`, so None is what makes the
    camera match the window exactly and cost no scaling at all.
    """
    assert main.render_size(_parsed()) is None
    assert main.DEFAULT_RENDER_WIDTH is None
    assert main.DEFAULT_RENDER_HEIGHT is None


def test_render_size_follows_a_resized_window_too():
    """Changing only the window must change what is rendered with it."""
    args = _parsed("--viewer-size", "2560", "1440")
    assert tuple(args.viewer_size) == (2560, 1440)
    assert main.render_size(args) is None


def test_render_size_can_be_set_apart_from_the_window():
    """Render below the window to buy back tick rate, or above for detail."""
    args = _parsed("--viewer-size", "1920", "1080",
                   "--render-size", "1280", "720")
    assert main.render_size(args) == (1280, 720)
    assert tuple(args.viewer_size) == (1920, 1080)


def test_render_size_is_a_pair_of_ints():
    """argparse hands back strings nowhere; the camera wants integers."""
    args = _parsed("--render-size", "800", "600")
    width, height = main.render_size(args)
    assert isinstance(width, int) and isinstance(height, int)


# ---------------------------------------------------------------------------
# Scenery
# ---------------------------------------------------------------------------

def test_scenery_is_on_by_default():
    """A demo with a window open should show the campus."""
    assert _parsed().no_scenery is False


def test_scenery_can_be_turned_off():
    assert _parsed("--no-scenery").no_scenery is True


# ---------------------------------------------------------------------------
# Why a run ended
# ---------------------------------------------------------------------------

def _progress(**overrides) -> TickProgress:
    """A TickProgress with sensible defaults."""
    fields = dict(sim_time=100.0, created=10, pending=0, on_bus=0,
                  fulfilled=10, cancelled=0, demand_exhausted=True)
    fields.update(overrides)
    return TickProgress(**fields)


def test_a_run_that_served_everyone_says_so():
    args = _parsed("--max-requests", "10", "--max-time", "40000")
    assert "on its own" in main.why_it_ended(_progress(), args)


def test_hitting_the_ceiling_is_named_with_what_was_left():
    """The distinction that matters: unserved demand, or no clock left."""
    args = _parsed("--max-requests", "40", "--max-time", "900")
    text = main.why_it_ended(
        _progress(sim_time=900.0, created=11, pending=8, on_bus=2,
                  fulfilled=1, demand_exhausted=False),
        args)
    assert "--max-time" in text
    assert "10 passenger(s) still in play" in text   # 8 pending + 2 aboard
    assert "29 never created" in text                # 40 - 11


def test_a_run_that_stopped_before_creating_its_demand_says_that():
    args = _parsed("--max-requests", "30", "--max-time", "40000")
    text = main.why_it_ended(
        _progress(sim_time=500.0, created=12, fulfilled=12), args)
    assert "only 12 of 30" in text


def test_no_observed_tick_still_produces_a_sentence():
    """A run that failed before its first tick must not crash the report."""
    assert main.why_it_ended(None, _parsed()).endswith(".")


# ---------------------------------------------------------------------------
# The progress line
# ---------------------------------------------------------------------------

def test_the_progress_line_tracks_settled_requests():
    line = main.ProgressLine(total_requests=4, max_time_s=1000.0)
    try:
        line.observe(_progress(created=2, pending=2, fulfilled=0))
        line.observe(_progress(created=4, pending=1, on_bus=1,
                               fulfilled=1, cancelled=1))
        assert line.last is not None
        assert line.last.settled == 2
    finally:
        line.close()


def test_the_postfix_names_the_clock_and_who_is_in_play():
    line = main.ProgressLine(total_requests=4, max_time_s=1000.0)
    try:
        text = line._postfix(_progress(sim_time=250.0, pending=3, on_bus=2))
        assert "t=250/1000s" in text
        assert "waiting 3" in text and "aboard 2" in text
        # Nobody has given up, so that clause stays out of the way.
        assert "gave up" not in text
    finally:
        line.close()


def test_the_postfix_mentions_abandonment_only_when_it_happens():
    line = main.ProgressLine(total_requests=4, max_time_s=1000.0)
    try:
        assert "gave up 2" in line._postfix(_progress(cancelled=2))
    finally:
        line.close()
