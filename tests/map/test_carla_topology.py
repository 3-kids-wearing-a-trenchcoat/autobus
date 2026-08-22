"""Tests for the dumped-CARLA-topology schema.

Runs offline: the module under test imports no carla, which is what lets
CarlaMapProvider be built and tested without a server.
"""

import json
from pathlib import Path

import pytest

from src.map.carla_topology import (
    DEFAULT_TOWN_SPEED_LIMIT_KPH,
    CarlaTopology,
    RoadSegment,
    SegmentPoint,
    load_topology,
    save_topology,
    topology_path,
)


def _segment(
    road_id: int = 1,
    lane_id: int = -1,
    is_junction: bool = False,
    points: tuple[tuple[float, float, float], ...] = (
        (0.0, 0.0, 0.0), (10.0, 0.0, 10.0), (20.0, 0.0, 20.0),
    ),
) -> RoadSegment:
    """Build a straight segment along x from (x, s) pairs."""
    return RoadSegment(
        road_id=road_id,
        lane_id=lane_id,
        is_junction=is_junction,
        points=tuple(
            SegmentPoint(x=x, y=y, z=0.0, s=s) for x, y, s in points
        ),
    )


def _topology(*segments: RoadSegment, **kwargs: object) -> CarlaTopology:
    """Build a topology around the given segments."""
    return CarlaTopology(
        map=str(kwargs.get("map", "TownTest")),
        default_speed_limit_kph=float(
            kwargs.get("default_speed_limit_kph",
                       DEFAULT_TOWN_SPEED_LIMIT_KPH)
        ),
        speed_limit_overrides=dict(
            kwargs.get("speed_limit_overrides", {})  # type: ignore[arg-type]
        ),
        segments=segments,
    )


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def test_length_sums_the_polyline() -> None:
    assert _segment().length_m == pytest.approx(20.0)


def test_length_follows_bends_not_the_straight_line() -> None:
    """A curved segment is longer than the distance between its ends."""
    bent = RoadSegment(1, -1, False, (
        SegmentPoint(0.0, 0.0, 0.0, 0.0),
        SegmentPoint(0.0, 10.0, 0.0, 10.0),
        SegmentPoint(10.0, 10.0, 0.0, 20.0),
    ))
    assert bent.length_m == pytest.approx(20.0)


def test_cumulative_lengths_start_at_zero() -> None:
    assert _segment().cumulative_lengths()[0] == 0.0


def test_start_and_end_are_the_outer_points() -> None:
    segment = _segment()
    assert (segment.start.s, segment.end.s) == (0.0, 20.0)


# ---------------------------------------------------------------------------
# Locating a stop by its OpenDRIVE s
# ---------------------------------------------------------------------------

def test_covers_s_within_range() -> None:
    assert _segment().covers_s(15.0)


def test_covers_s_excludes_outside() -> None:
    assert not _segment().covers_s(25.0)


def test_covers_s_includes_the_endpoints() -> None:
    segment = _segment()
    assert segment.covers_s(0.0) and segment.covers_s(20.0)


def test_distance_at_s_interpolates_between_samples() -> None:
    """
    A stop must land where it was authored, not at the nearest sample.
    s=15 sits halfway between the 10 m and 20 m samples.
    """
    assert _segment().distance_at_s(15.0) == pytest.approx(15.0)


def test_distance_at_s_at_the_start_is_zero() -> None:
    assert _segment().distance_at_s(0.0) == pytest.approx(0.0)


def test_distance_at_s_at_the_end_is_the_length() -> None:
    assert _segment().distance_at_s(20.0) == pytest.approx(20.0)


def test_outside_s_is_an_error_not_a_clamp() -> None:
    """Silently clamping would place a stop on the wrong road."""
    with pytest.raises(ValueError, match="outside"):
        _segment().distance_at_s(99.0)


# ---------------------------------------------------------------------------
# Positive lanes run backwards along s
# ---------------------------------------------------------------------------

def test_covers_s_handles_a_descending_lane() -> None:
    """
    The sign of lane_id gives direction relative to the reference line, so s
    decreases along a positive lane. Range checks must not assume ordering.
    """
    descending = _segment(lane_id=1, points=(
        (0.0, 0.0, 20.0), (10.0, 0.0, 10.0), (20.0, 0.0, 0.0),
    ))
    assert descending.covers_s(15.0)
    assert not descending.covers_s(25.0)


def test_distance_at_s_handles_a_descending_lane() -> None:
    """s=15 is 5 m along a segment whose s runs 20 -> 0."""
    descending = _segment(lane_id=1, points=(
        (0.0, 0.0, 20.0), (10.0, 0.0, 10.0), (20.0, 0.0, 0.0),
    ))
    assert descending.distance_at_s(15.0) == pytest.approx(5.0)
    assert descending.distance_at_s(20.0) == pytest.approx(0.0)
    assert descending.distance_at_s(0.0) == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Speed limits
# ---------------------------------------------------------------------------

def test_default_speed_limit_applies_without_an_override() -> None:
    assert _topology(_segment()).speed_limit_for(1) == pytest.approx(30.0)


def test_override_wins_for_its_road() -> None:
    topology = _topology(_segment(), speed_limit_overrides={7: 90.0})
    assert topology.speed_limit_for(7) == pytest.approx(90.0)
    assert topology.speed_limit_for(1) == pytest.approx(30.0)


def test_the_default_is_not_carlas_130() -> None:
    """
    DEFAULT_SPEED_LIMIT in map_data is 130 km/h, which is a motorway figure.
    Falling through to it would make every urban estimate four times too
    fast, so the town default is deliberately its own constant.
    """
    assert DEFAULT_TOWN_SPEED_LIMIT_KPH == 30.0


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

def test_round_trip_preserves_geometry(tmp_path: Path) -> None:
    written = _topology(
        _segment(road_id=1),
        _segment(road_id=2, lane_id=1, is_junction=True),
        speed_limit_overrides={2: 50.0},
    )
    save_topology(written, tmp_path)
    loaded = load_topology("TownTest", tmp_path)

    assert loaded.map == "TownTest"
    assert len(loaded.segments) == 2
    assert loaded.segments[1].is_junction is True
    assert loaded.segments[1].lane_id == 1
    assert loaded.speed_limit_overrides == {2: 50.0}
    assert loaded.segments[0].length_m == pytest.approx(20.0)


def test_road_id_keys_survive_json(tmp_path: Path) -> None:
    """JSON object keys are strings; road IDs must come back as ints."""
    save_topology(
        _topology(_segment(), speed_limit_overrides={7: 90.0}), tmp_path
    )
    loaded = load_topology("TownTest", tmp_path)
    assert loaded.speed_limit_for(7) == pytest.approx(90.0)


def test_saving_accepts_the_carla_spelling(tmp_path: Path) -> None:
    save_topology(_topology(_segment(), map="Carla/Maps/Town01"), tmp_path)
    assert (tmp_path / "Town01.topology.json").exists()


def test_path_is_named_after_the_town(tmp_path: Path) -> None:
    assert topology_path("Carla/Maps/Town02", tmp_path).name == (
        "Town02.topology.json"
    )


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

def test_missing_dump_is_an_explicit_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Town09"):
        load_topology("Town09", tmp_path)


def test_a_dump_naming_another_town_is_rejected(tmp_path: Path) -> None:
    save_topology(_topology(_segment(), map="TownTest"), tmp_path)
    (tmp_path / "TownTest.topology.json").rename(
        tmp_path / "Town02.topology.json"
    )
    with pytest.raises(ValueError, match="Town02"):
        load_topology("Town02", tmp_path)


def test_a_single_point_segment_is_rejected(tmp_path: Path) -> None:
    """One point has no drivable length and no direction."""
    path = tmp_path / "TownTest.topology.json"
    path.write_text(json.dumps({
        "map": "TownTest",
        "segments": [{"road_id": 1, "lane_id": -1, "is_junction": False,
                      "points": [[0.0, 0.0, 0.0, 0.0]]}],
    }))
    with pytest.raises(ValueError, match="fewer than two points"):
        load_topology("TownTest", tmp_path)


# ---------------------------------------------------------------------------
# The packaged Town01 dump
# ---------------------------------------------------------------------------

def test_packaged_town01_loads() -> None:
    topology = load_topology("Town01")
    assert len(topology.segments) > 50
    assert topology.default_speed_limit_kph == pytest.approx(30.0)


def test_packaged_town01_segments_are_monotonic_in_s() -> None:
    """
    The dumper interpolates the terminus rather than taking it from the next
    road's waypoint. If that regressed, s would jump at the last point and
    distance_at_s could not interpolate.
    """
    for segment in load_topology("Town01").segments:
        values = [point.s for point in segment.points]
        ascending = all(a <= b for a, b in zip(values, values[1:]))
        descending = all(a >= b for a, b in zip(values, values[1:]))
        assert ascending or descending, (
            f"road {segment.road_id} lane {segment.lane_id} "
            f"has non-monotonic s"
        )
