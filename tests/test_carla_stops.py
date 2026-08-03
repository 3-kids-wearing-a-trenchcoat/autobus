"""Tests for the authored CARLA stop schema.

Runs entirely offline: the module under test never imports carla, which is
the property that lets CarlaMapProvider be built and tested without a live
server.
"""

import json
from pathlib import Path

import pytest

from src.map.carla_stops import (
    CarlaStop,
    available_towns,
    load_stops,
    save_stops,
    stops_path,
    town_name,
)


def _stop(identifier: str = "s01", **overrides: object) -> CarlaStop:
    """Build a stop with sensible defaults."""
    fields = dict(
        id=identifier, road_id=8, lane_id=-1, s=296.0,
        x=396.4, y=22.5, z=0.0, name=None,
    )
    fields.update(overrides)
    return CarlaStop(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# town_name
# ---------------------------------------------------------------------------

def test_strips_carla_path_prefix() -> None:
    assert town_name("Carla/Maps/Town01") == "Town01"


def test_leaves_a_bare_town_alone() -> None:
    assert town_name("Town01") == "Town01"


def test_a_live_name_and_a_typed_name_agree() -> None:
    """The whole point: one file serves both spellings."""
    assert town_name("Carla/Maps/Town10HD_Opt") == town_name("Town10HD_Opt")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def test_path_is_named_after_the_town(tmp_path: Path) -> None:
    assert stops_path("Carla/Maps/Town02", tmp_path).name == (
        "Town02.stops.json"
    )


def test_path_defaults_to_the_packaged_directory() -> None:
    assert stops_path("Town01").parent.name == "carla_maps"


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

def test_round_trip_preserves_every_field(tmp_path: Path) -> None:
    written = [
        _stop("s01", name="Depot"),
        _stop("s02", road_id=15, lane_id=1, s=134.5, x=-2.0, y=144.0),
    ]
    save_stops("Town01", written, tmp_path)
    assert load_stops("Town01", tmp_path) == written


def test_coordinates_are_stored_to_the_millimetre(tmp_path: Path) -> None:
    """
    Files are meant to be read and reviewed by hand, so coordinates are
    rounded rather than dumped at full float precision. That makes the round
    trip millimetre-exact rather than bit-exact, which callers must expect.
    """
    save_stops("Town01", [_stop(x=396.39999389648438, s=1.23456)], tmp_path)
    loaded = load_stops("Town01", tmp_path)[0]
    assert loaded.x == pytest.approx(396.4, abs=1e-3)
    assert loaded.s == pytest.approx(1.235, abs=1e-3)


def test_round_trip_accepts_the_carla_spelling(tmp_path: Path) -> None:
    save_stops("Carla/Maps/Town01", [_stop()], tmp_path)
    assert len(load_stops("Town01", tmp_path)) == 1


def test_order_is_preserved(tmp_path: Path) -> None:
    """Stop order is authored, so reading must not sort it."""
    stops = [_stop("s03"), _stop("s01"), _stop("s02")]
    save_stops("Town01", stops, tmp_path)
    assert [s.id for s in load_stops("Town01", tmp_path)] == [
        "s03", "s01", "s02"
    ]


def test_saving_records_the_town_and_schema(tmp_path: Path) -> None:
    path = save_stops("Carla/Maps/Town01", [_stop()], tmp_path)
    document = json.loads(path.read_text())
    assert document["map"] == "Town01"
    assert document["schema_version"] == 1


def test_note_is_stored(tmp_path: Path) -> None:
    path = save_stops("Town01", [_stop()], tmp_path, note="authored by hand")
    assert json.loads(path.read_text())["note"] == "authored by hand"


def test_saving_creates_the_directory(tmp_path: Path) -> None:
    target = tmp_path / "does" / "not" / "exist"
    save_stops("Town01", [_stop()], target)
    assert (target / "Town01.stops.json").exists()


def test_z_defaults_when_absent(tmp_path: Path) -> None:
    path = stops_path("Town01", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "map": "Town01",
        "stops": [{"id": "s01", "road_id": 8, "lane_id": -1,
                   "s": 1.0, "x": 2.0, "y": 3.0}],
    }))
    assert load_stops("Town01", tmp_path)[0].z == 0.0


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

def test_missing_file_is_an_explicit_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Town07"):
        load_stops("Town07", tmp_path)


def test_a_file_naming_another_town_is_rejected(tmp_path: Path) -> None:
    """
    Catches a file renamed by hand without its contents being updated, which
    would otherwise load stops from the wrong map and fail much later.
    """
    save_stops("Town01", [_stop()], tmp_path)
    (tmp_path / "Town01.stops.json").rename(
        tmp_path / "Town02.stops.json"
    )
    with pytest.raises(ValueError, match="Town02"):
        load_stops("Town02", tmp_path)


def test_duplicate_ids_are_rejected_on_save(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="s01"):
        save_stops("Town01", [_stop("s01"), _stop("s01")], tmp_path)


def test_duplicate_ids_are_rejected_on_load(tmp_path: Path) -> None:
    """A hand-edited file can hold duplicates that save never saw."""
    path = stops_path("Town01", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "map": "Town01",
        "stops": [
            {"id": "s01", "road_id": 8, "lane_id": -1,
             "s": 1.0, "x": 2.0, "y": 3.0},
            {"id": "s01", "road_id": 9, "lane_id": -1,
             "s": 4.0, "x": 5.0, "y": 6.0},
        ],
    }))
    with pytest.raises(ValueError, match="s01"):
        load_stops("Town01", tmp_path)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_available_towns_lists_what_was_written(tmp_path: Path) -> None:
    save_stops("Town02", [_stop()], tmp_path)
    save_stops("Town01", [_stop()], tmp_path)
    assert available_towns(tmp_path) == ["Town01", "Town02"]


def test_available_towns_is_empty_without_a_directory(
    tmp_path: Path,
) -> None:
    assert available_towns(tmp_path / "nothing here") == []


# ---------------------------------------------------------------------------
# The packaged Town01 file
# ---------------------------------------------------------------------------

def test_packaged_town01_loads() -> None:
    """The authored file ships with the code, so it must stay readable."""
    stops = load_stops("Town01")
    assert len(stops) >= 5
    assert all(stop.lane_id != 0 for stop in stops)


def test_packaged_town01_is_listed() -> None:
    assert "Town01" in available_towns()
