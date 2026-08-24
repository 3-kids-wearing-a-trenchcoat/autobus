"""Tests for the demo entry point's argument handling.

Only the pure parts: `main.py` defers its `carla` and pygame imports into
`build_carla_factory`, so the parser and its helpers can be exercised with
nothing installed and no server running.
"""

import main


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
