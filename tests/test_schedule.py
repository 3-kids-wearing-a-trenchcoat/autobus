"""Tests for Schedule: visit collapsing, timing, slack and feasibility."""

import pytest

from src.algorithm.Schedule import Event, Schedule
from src.algorithm.TravelModel import GraphTravelModel, TravelModel
from src.map.TransitGraph import TransitGraph
from test_transit_graph import _PROVIDER

_TRAVEL_S = 10.0
_POSITION = "start"


class _FixedTravelModel(TravelModel):
    """Constant travel and dwell, so timings are exact and hand-checkable."""

    def __init__(self, travel_s: float = _TRAVEL_S, dwell_s: float = 0.0):
        self._travel_s = travel_s
        self._dwell_s = dwell_s
        self.dwell_calls: list[tuple[str, int, int]] = []

    def travel_time(self, from_id: str, to_id: str) -> float:
        """Return the constant travel time."""
        return self._travel_s

    def dwell_time(
        self, stop_id: str, n_boarding: int, n_alighting: int
    ) -> float:
        """Record the call and return the constant dwell time."""
        self.dwell_calls.append((stop_id, n_boarding, n_alighting))
        return self._dwell_s


def pickup(owner: str, node: str) -> Event:
    """Build a boarding event."""
    return Event(node_id=node, load_delta=1, owner_id=owner)


def dropoff(owner: str, node: str) -> Event:
    """Build an alighting event."""
    return Event(node_id=node, load_delta=-1, owner_id=owner)


# ---------------------------------------------------------------------------
# Visit collapsing
# ---------------------------------------------------------------------------

def test_empty_schedule_has_no_stops() -> None:
    assert Schedule().stop_sequence(_POSITION) == []


def test_distinct_stops_are_kept_in_order() -> None:
    schedule = Schedule([pickup("r1", "A"), dropoff("r1", "B")])
    assert schedule.stop_sequence(_POSITION) == ["A", "B"]


def test_consecutive_events_at_one_stop_collapse() -> None:
    """Two riders boarding together is one pull-over, not two."""
    schedule = Schedule([pickup("r1", "A"), pickup("r2", "A")])
    assert schedule.stop_sequence(_POSITION) == ["A"]


def test_non_consecutive_repeat_stays_two_visits() -> None:
    """Leaving and coming back is genuinely two stops."""
    schedule = Schedule([
        pickup("r1", "A"), pickup("r2", "B"), dropoff("r1", "A"),
    ])
    assert schedule.stop_sequence(_POSITION) == ["A", "B", "A"]


def test_leading_stop_at_current_position_is_dropped() -> None:
    """A zero-length self-transition would stall the bus."""
    schedule = Schedule([pickup("r1", _POSITION), dropoff("r1", "B")])
    assert schedule.stop_sequence(_POSITION) == ["B"]


# ---------------------------------------------------------------------------
# Arrival times
# ---------------------------------------------------------------------------

def test_arrival_times_accumulate_travel() -> None:
    schedule = Schedule([pickup("r1", "A"), dropoff("r1", "B")])
    times = schedule.arrival_times(_POSITION, 0.0, _FixedTravelModel())
    assert times == pytest.approx([10.0, 20.0])


def test_arrival_times_offset_by_start_time() -> None:
    schedule = Schedule([pickup("r1", "A"), dropoff("r1", "B")])
    times = schedule.arrival_times(_POSITION, 100.0, _FixedTravelModel())
    assert times == pytest.approx([110.0, 120.0])


def test_events_sharing_a_visit_share_an_arrival() -> None:
    schedule = Schedule([pickup("r1", "A"), pickup("r2", "A")])
    times = schedule.arrival_times(_POSITION, 0.0, _FixedTravelModel())
    assert times == pytest.approx([10.0, 10.0])


def test_dwell_charged_once_per_visit_not_per_event() -> None:
    # Two riders board at A (one dwell), then one alights at B.
    # A: travel 10 -> arrive 10, dwell 5 -> depart 15.
    # B: travel 10 -> arrive 25.  A per-event dwell would give 30.
    schedule = Schedule([
        pickup("r1", "A"), pickup("r2", "A"), dropoff("r1", "B"),
    ])
    model = _FixedTravelModel(dwell_s=5.0)
    times = schedule.arrival_times(_POSITION, 0.0, model)
    assert times == pytest.approx([10.0, 10.0, 25.0])


def test_dwell_receives_the_visit_passenger_counts() -> None:
    """Counts come from the visit, so proportional dwell needs no plumbing."""
    schedule = Schedule([
        pickup("r1", "A"), pickup("r2", "A"), dropoff("r1", "B"),
    ])
    model = _FixedTravelModel(dwell_s=5.0)
    schedule.arrival_times(_POSITION, 0.0, model)
    assert model.dwell_calls == [("A", 2, 0), ("B", 0, 1)]


def test_events_at_current_position_are_served_immediately() -> None:
    """The bus is already parked there: no leg driven, no extra dwell."""
    schedule = Schedule([pickup("r1", _POSITION), dropoff("r1", "B")])
    model = _FixedTravelModel(dwell_s=5.0)
    times = schedule.arrival_times(_POSITION, 100.0, model)
    assert times == pytest.approx([100.0, 110.0])


def test_timeline_composes_with_real_graph_costs() -> None:
    """
    Guard against a units or semantics mismatch against the real graph.

    Uses the hand-authored test map rather than a stub, so the seconds the
    schedule accumulates are the same seconds the simulation would spend.
    """
    graph = TransitGraph(_PROVIDER)
    model = GraphTravelModel(graph)
    schedule = Schedule([pickup("r1", "n1_n2"), dropoff("r1", "n3_n4")])

    times = schedule.arrival_times("n4_n1", 0.0, model)

    first_leg = graph.best_path_cost("n4_n1", "n1_n2")
    second_leg = graph.best_path_cost("n1_n2", "n3_n4")
    assert times == pytest.approx([first_leg, first_leg + second_leg])


def test_real_graph_timeline_shifts_by_dwell() -> None:
    graph = TransitGraph(_PROVIDER)
    schedule = Schedule([pickup("r1", "n1_n2"), dropoff("r1", "n3_n4")])

    plain = schedule.arrival_times("n4_n1", 0.0, GraphTravelModel(graph))
    dwelt = schedule.arrival_times(
        "n4_n1", 0.0, GraphTravelModel(graph, dwell_s=7.0)
    )

    # One dwell is charged at the pickup, before driving on to the dropoff.
    assert dwelt[0] == pytest.approx(plain[0])
    assert dwelt[1] == pytest.approx(plain[1] + 7.0)


# ---------------------------------------------------------------------------
# Forward slack
# ---------------------------------------------------------------------------

def test_slack_is_spare_time_at_a_lone_event() -> None:
    slack = Schedule().forward_slack([10.0], [25.0])
    assert slack == pytest.approx([15.0])


def test_slack_is_limited_by_the_tightest_downstream_event() -> None:
    # Event 0 has 40s spare, but event 1 only has 5s; delaying the bus at
    # event 0 pushes event 1 too, so event 0's usable slack is 5s.
    slack = Schedule().forward_slack([10.0, 20.0], [50.0, 25.0])
    assert slack == pytest.approx([5.0, 5.0])


def test_slack_ignores_upstream_tightness() -> None:
    """A tight early event must not constrain a roomy later one."""
    slack = Schedule().forward_slack([10.0, 20.0], [12.0, 100.0])
    assert slack == pytest.approx([2.0, 80.0])


def test_deadline_free_events_have_infinite_slack() -> None:
    slack = Schedule().forward_slack(
        [10.0, 20.0], [float("inf"), float("inf")]
    )
    assert slack[0] == float("inf")


def test_missed_deadline_yields_negative_slack() -> None:
    """Negative slack is how the caller detects infeasibility."""
    slack = Schedule().forward_slack([30.0], [25.0])
    assert slack[0] == pytest.approx(-5.0)


def test_slack_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        Schedule().forward_slack([1.0, 2.0], [1.0])


# ---------------------------------------------------------------------------
# Insertion
# ---------------------------------------------------------------------------

def test_insert_into_empty_schedule() -> None:
    schedule = Schedule().insert(pickup("r1", "A"), dropoff("r1", "B"), 0, 0)
    assert schedule.stop_sequence(_POSITION) == ["A", "B"]


def test_insert_keeps_pickup_before_dropoff() -> None:
    base = Schedule([pickup("r1", "A"), dropoff("r1", "B")])
    schedule = base.insert(pickup("r2", "C"), dropoff("r2", "D"), 0, 2)
    assert schedule.stop_sequence(_POSITION) == ["C", "A", "B", "D"]


def test_insert_returns_a_new_schedule() -> None:
    """Immutability is what lets the search try candidates freely."""
    base = Schedule([pickup("r1", "A"), dropoff("r1", "B")])
    base.insert(pickup("r2", "C"), dropoff("r2", "D"), 0, 2)
    assert base.stop_sequence(_POSITION) == ["A", "B"]


def test_insert_rejects_dropoff_before_pickup() -> None:
    with pytest.raises(ValueError):
        Schedule([pickup("r1", "A")]).insert(
            pickup("r2", "C"), dropoff("r2", "D"), 1, 0
        )


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------

def test_without_owner_drops_both_events() -> None:
    schedule = Schedule([
        pickup("r1", "A"), pickup("r2", "B"),
        dropoff("r1", "C"), dropoff("r2", "D"),
    ]).without_owner("r1")
    assert schedule.stop_sequence(_POSITION) == ["B", "D"]


def test_without_pickup_keeps_the_dropoff() -> None:
    """A boarded passenger still has to be delivered."""
    schedule = Schedule([
        pickup("r1", "A"), dropoff("r1", "C"),
    ]).without_pickup("r1")
    assert schedule.stop_sequence(_POSITION) == ["C"]
    assert schedule.dropoff_owners() == {"r1"}


# ---------------------------------------------------------------------------
# Feasibility
# ---------------------------------------------------------------------------

def test_capacity_ok_within_limit() -> None:
    schedule = Schedule([pickup("r1", "A"), pickup("r2", "B")])
    assert schedule.capacity_ok(capacity_total=2, initial_load=0) is True


def test_capacity_violated_by_one_too_many() -> None:
    schedule = Schedule([pickup("r1", "A"), pickup("r2", "B")])
    assert schedule.capacity_ok(capacity_total=1, initial_load=0) is False


def test_capacity_counts_passengers_already_aboard() -> None:
    schedule = Schedule([pickup("r1", "A")])
    assert schedule.capacity_ok(capacity_total=2, initial_load=2) is False


def test_capacity_freed_by_an_earlier_dropoff() -> None:
    schedule = Schedule([dropoff("r0", "A"), pickup("r1", "B")])
    assert schedule.capacity_ok(capacity_total=1, initial_load=1) is True


def test_precedence_ok_when_pickup_leads() -> None:
    schedule = Schedule([pickup("r1", "A"), dropoff("r1", "B")])
    assert schedule.precedence_ok() is True


def test_precedence_violated_when_dropoff_leads() -> None:
    schedule = Schedule([dropoff("r1", "B"), pickup("r1", "A")])
    assert schedule.precedence_ok() is False


def test_onboard_passenger_needs_no_pickup() -> None:
    """Riders already aboard have no scheduled pickup; that is not a fault."""
    schedule = Schedule([dropoff("r0", "A")])
    assert schedule.precedence_ok() is True
