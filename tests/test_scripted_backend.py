"""Tests for ScriptedBackend: a world with no map at all."""

import pytest

from src.simulation.ScriptedBackend import ScriptedBackend


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_starts_at_the_given_position() -> None:
    assert ScriptedBackend("stop_A").current_location() == "stop_A"


def test_empty_timetable_is_rejected() -> None:
    """An empty timetable has no leg to serve, so it could only stall."""
    with pytest.raises(ValueError):
        ScriptedBackend("stop_A", [])


def test_zero_duration_is_rejected() -> None:
    """
    A zero-length leg arrives without consuming anything, which would spin
    the runner's movement loop forever.
    """
    with pytest.raises(ValueError):
        ScriptedBackend("stop_A", [10.0, 0.0])


def test_negative_duration_is_rejected() -> None:
    with pytest.raises(ValueError):
        ScriptedBackend("stop_A", [-1.0])


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

def test_arrives_after_the_scripted_duration() -> None:
    backend = ScriptedBackend("stop_A", [10.0])
    backend.set_destination("stop_B")
    result = backend.advance(10.0)
    assert (result.arrived_at, result.consumed) == ("stop_B", 10.0)


def test_arrives_at_whatever_it_was_commanded() -> None:
    """There is no map: the destination is taken on trust."""
    backend = ScriptedBackend("stop_A", [10.0])
    backend.set_destination("somewhere_entirely_made_up")
    assert backend.advance(10.0).arrived_at == "somewhere_entirely_made_up"


def test_successive_legs_walk_the_timetable() -> None:
    backend = ScriptedBackend("stop_A", [10.0, 25.0, 5.0])
    consumed = []
    for index, destination in enumerate(["stop_B", "stop_C", "stop_D"]):
        backend.set_destination(destination)
        consumed.append(backend.advance(1000.0).consumed)
    assert consumed == pytest.approx([10.0, 25.0, 5.0])


def test_the_timetable_cycles() -> None:
    """
    A one-entry timetable means a constant leg time, so a trial can run to
    completion without anyone having to guess how many legs it will take.
    """
    backend = ScriptedBackend("stop_A", [10.0, 20.0])
    consumed = []
    for destination in ["b", "c", "d", "e"]:
        backend.set_destination(destination)
        consumed.append(backend.advance(1000.0).consumed)
    assert consumed == pytest.approx([10.0, 20.0, 10.0, 20.0])


def test_partial_progress_accumulates() -> None:
    backend = ScriptedBackend("stop_A", [10.0])
    backend.set_destination("stop_B")
    assert backend.advance(4.0).arrived_at is None
    assert backend.advance(4.0).arrived_at is None
    assert backend.advance(4.0).arrived_at == "stop_B"


def test_changing_destination_discards_progress() -> None:
    backend = ScriptedBackend("stop_A", [10.0])
    backend.set_destination("stop_B")
    backend.advance(9.0)
    backend.set_destination("stop_C")
    assert backend.advance(9.0).arrived_at is None


def test_recommanding_the_same_stop_keeps_progress() -> None:
    backend = ScriptedBackend("stop_A", [10.0])
    backend.set_destination("stop_B")
    backend.advance(9.0)
    backend.set_destination("stop_B")
    assert backend.advance(1.0).arrived_at == "stop_B"


def test_advance_without_a_destination_consumes_the_budget() -> None:
    result = ScriptedBackend("stop_A").advance(42.0)
    assert (result.arrived_at, result.consumed) == (None, 42.0)


def test_position_follows_arrivals() -> None:
    backend = ScriptedBackend("stop_A", [10.0])
    backend.set_destination("stop_B")
    backend.advance(10.0)
    assert backend.current_location() == "stop_B"


def test_arrival_consumes_only_what_the_leg_needed() -> None:
    backend = ScriptedBackend("stop_A", [10.0])
    backend.set_destination("stop_B")
    assert backend.advance(1000.0).consumed == pytest.approx(10.0)
