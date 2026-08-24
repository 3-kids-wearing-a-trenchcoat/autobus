"""Tests for the scenery-block schema and its file format.

No server and no ``carla`` import: these blocks are map data, read and
written the same way authored stops are.
"""

import json

import pytest

from src.map.carla_props import (
    SCHEMA_VERSION,
    SceneryBlock,
    has_props,
    load_props,
    props_path,
    save_props,
)

_BLOCKS = [
    SceneryBlock(x=10.0, y=-20.0, edge_m=12.0, height_m=8.0, yaw_deg=30.0),
    SceneryBlock(x=40.5, y=-60.25, edge_m=6.5, height_m=17.5),
]


def test_props_path_is_keyed_on_the_bare_town(tmp_path):
    """A prefixed CARLA map name and a bare town name agree."""
    assert (props_path("Carla/Maps/TAU", tmp_path)
            == props_path("TAU", tmp_path))
    assert props_path("TAU", tmp_path).name == "TAU.props.json"


def test_has_props_is_false_when_the_town_has_none(tmp_path):
    """Most towns have no scenery, and that is not an error."""
    assert has_props("Town01", tmp_path) is False


def test_round_trip_preserves_every_block(tmp_path):
    """What is written is what comes back."""
    save_props("TAU", _BLOCKS, note="test", directory=tmp_path)
    assert has_props("TAU", tmp_path) is True
    assert load_props("TAU", tmp_path) == _BLOCKS


def test_yaw_defaults_to_zero_when_absent(tmp_path):
    """An older or hand-written file need not carry a rotation."""
    path = props_path("TAU", tmp_path)
    path.write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "map": "TAU",
        "blocks": [{"x": 1.0, "y": 2.0, "edge_m": 3.0, "height_m": 4.0}],
    }), encoding="utf-8")
    assert load_props("TAU", tmp_path)[0].yaw_deg == 0.0


def test_a_foreign_schema_is_refused(tmp_path):
    """A file from a different schema is a bug, not something to guess at."""
    path = props_path("TAU", tmp_path)
    path.write_text(json.dumps({
        "schema_version": SCHEMA_VERSION + 1, "map": "TAU", "blocks": [],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        load_props("TAU", tmp_path)


def test_the_note_is_kept_in_the_file(tmp_path):
    """A reader has to be able to tell what produced the blocks."""
    save_props("TAU", _BLOCKS, note="derived from OSM", directory=tmp_path)
    payload = json.loads(
        props_path("TAU", tmp_path).read_text(encoding="utf-8")
    )
    assert payload["note"] == "derived from OSM"
    assert payload["map"] == "TAU"


# ---------------------------------------------------------------------------
# The packaged TAU file
# ---------------------------------------------------------------------------

def test_the_packaged_tau_scenery_loads_and_is_sane():
    """The committed file is readable and holds plausible geometry."""
    blocks = load_props("TAU")
    assert len(blocks) > 100
    for block in blocks:
        assert block.edge_m >= 4.0
        assert 3.0 <= block.height_m <= 80.0
        assert -180.0 <= block.yaw_deg <= 180.0


def test_shipped_towns_have_no_scenery():
    """CARLA's own towns bring their own art and must be left alone."""
    assert has_props("Town01") is False
    assert has_props("Town03") is False
