"""Tests for ReorderingInsertionPolicy's relocate-repair pass."""

import unittest

from src.algorithm.DispatchPolicy import BusState
from src.algorithm.ReorderingInsertionPolicy import (
    _MAX_CONVERGE_PASSES,
    ReorderingInsertionPolicy,
)
from src.algorithm.RequestPool import Request, RequestState
from src.algorithm.Schedule import Schedule
from src.algorithm.WindowInsertionPolicy import WindowInsertionPolicy
from src.map.TransitGraph import TransitGraph
from maps import SQUARE_MAP

# TransitGraph node IDs (road edges) in SQUARE_MAP, matching the names used in
# test_window_insertion_policy. Travel between them costs fractions of a
# second, so a window of 1000s never binds.
_START = "n4_n1"
_S1 = "n1_n2"
_S2 = "n3_n4"
_S3 = "n2_n3"
_S4 = "n1_n3"

_WIDE_WINDOW = 1000.0


def _req(
    req_id: str,
    pickup: str,
    dropoff: str,
    created_at: float = 0.0,
    state: RequestState = RequestState.PENDING,
    boarded_at: float | None = None,
) -> Request:
    """Build a request in the given lifecycle state."""
    return Request(
        id=req_id, pickup_stop_id=pickup, dropoff_stop_id=dropoff,
        created_at=created_at, state=state, boarded_at=boarded_at,
    )


def _state(
    pending: list[Request] | None = None,
    onboard: list[Request] | None = None,
    remaining_capacity: int = 4,
    now: float = 0.0,
    position: str = _START,
) -> BusState:
    """Build a BusState with sensible defaults."""
    return BusState(
        position=position,
        onboard=onboard or [],
        pending_requests=pending or [],
        remaining_capacity=remaining_capacity,
        now=now,
    )


def _improvable_requests() -> list[Request]:
    """
    Return a request set whose FIFO insertion order is demonstrably poor.

    Found by exhaustive search over the test map: inserting these three in
    creation order leaves the bus doubling back, and relocating one of them
    cuts total passenger time by about 20%. Three distinct pickups, all
    created at the same instant, so the improvement is purely a matter of
    visit order rather than of timing.
    :return: Three pending requests.
    """
    return [
        _req("r0", _S1, _START),
        _req("r1", _S2, _S1),
        _req("r2", _S4, _START),
    ]


class TestParity(unittest.TestCase):
    """With repair switched off, the subclass must not perturb its parent."""

    def setUp(self) -> None:
        self.graph = TransitGraph(SQUARE_MAP)

    def _both(self) -> tuple[WindowInsertionPolicy,
                             ReorderingInsertionPolicy]:
        """Build a parent policy and a repair-disabled subclass."""
        return (
            WindowInsertionPolicy(self.graph, max_wait_s=_WIDE_WINDOW),
            ReorderingInsertionPolicy(
                self.graph, max_wait_s=_WIDE_WINDOW,
                max_relocations=0, retry_rejected=False,
            ),
        )

    def test_disabled_repair_matches_parent_over_a_call_sequence(
        self,
    ) -> None:
        parent, child = self._both()
        requests = _improvable_requests()
        for step in range(3):
            state = _state(pending=requests[step:])
            self.assertEqual(parent.plan(state, 10), child.plan(state, 10))
            self.assertEqual(parent.schedule, child.schedule)

    def test_zero_n_stops_returns_empty(self) -> None:
        _, child = self._both()
        state = _state(pending=_improvable_requests())
        self.assertEqual(child.plan(state, 0), [])

    def test_fallback_route_matches_parent(self) -> None:
        # A full bus cannot schedule anything, so both policies fall through
        # to "drive to the nearest pending pickup".
        parent, child = self._both()
        state = _state(pending=[_req("r0", _S1, _S2)], remaining_capacity=0)
        self.assertEqual(parent.plan(state, 5), child.plan(state, 5))
        self.assertFalse(child.schedule)


class TestRepair(unittest.TestCase):
    """The repair pass must improve the order without breaking it."""

    def setUp(self) -> None:
        self.graph = TransitGraph(SQUARE_MAP)
        self.policy = ReorderingInsertionPolicy(
            self.graph, max_wait_s=_WIDE_WINDOW, retry_rejected=False,
        )

    def test_repair_lowers_total_passenger_time(self) -> None:
        requests = _improvable_requests()
        state = _state(pending=requests)

        parent = WindowInsertionPolicy(self.graph, max_wait_s=_WIDE_WINDOW)
        parent_route = parent.plan(state, 10)
        repaired_route = self.policy.plan(state, 10)

        parent_cost = parent._cost(parent.schedule, state)
        repaired_cost = self.policy._cost(self.policy.schedule, state)
        self.assertLess(repaired_cost, parent_cost)
        self.assertNotEqual(parent_route, repaired_route)
        self.assertGreater(self.policy.stats()["pair_accepted"], 0)

    def test_repair_serves_exactly_the_same_requests(self) -> None:
        state = _state(pending=_improvable_requests())
        self.policy.plan(state, 10)

        before = self.policy.schedule
        after = self.policy._repair(before, state)
        self.assertEqual(before.owners(), after.owners())

    def test_unplaceable_request_keeps_its_slot(self) -> None:
        """
        The guarantee that outranks every cost saving.

        Repair works by ejecting a request and putting it back, so a request
        that cannot be put back must leave the schedule untouched rather than
        vanish. Reproduced here by letting the clock run past every deadline,
        which is what an insertion made in good faith looks like once the bus
        has fallen behind — the case CARLA traffic will make routine.
        """
        policy = ReorderingInsertionPolicy(
            self.graph, max_wait_s=0.5, retry_rejected=False,
        )
        requests = [_req("r0", _S1, _S2), _req("r1", _S3, _S4)]
        policy.plan(_state(pending=requests), 10)
        scheduled = policy.schedule
        self.assertEqual(scheduled.owners(), {"r0", "r1"})

        # Every deadline is now in the past, so nothing can be re-placed.
        late = _state(pending=requests, now=5.0)
        self.assertIsNone(
            policy._relocate_pair(scheduled, requests[0], late)
        )
        self.assertEqual(policy._repair(scheduled, late).owners(),
                         scheduled.owners())

    def test_repair_preserves_precedence(self) -> None:
        state = _state(pending=_improvable_requests())
        self.policy.plan(state, 10)
        self.assertTrue(self.policy.schedule.precedence_ok())

    def test_repair_preserves_the_service_window(self) -> None:
        # Tight enough to bind on this map, so feasibility is a real test
        # rather than a formality.
        policy = ReorderingInsertionPolicy(
            self.graph, max_wait_s=0.6, retry_rejected=False,
        )
        state = _state(pending=_improvable_requests())
        policy.plan(state, 10)
        self.assertTrue(policy._feasible(policy.schedule, state))

    def test_repair_respects_capacity(self) -> None:
        # A single-seat bus serving three riders: the schedule can only be
        # reordered in ways that keep them from overlapping. This particular
        # set was picked because repair *does* accept a move here, so the
        # capacity assertion below is testing the repair rather than the
        # parent's construction.
        state = _state(
            pending=[
                _req("r0", _START, _S1),
                _req("r1", _START, _S3),
                _req("r2", _S1, _START),
            ],
            remaining_capacity=1,
        )
        self.policy.plan(state, 10)
        self.assertGreater(self.policy.stats()["pair_accepted"], 0)
        self.assertTrue(self.policy.schedule.capacity_ok(1, 0))

    def test_extra_passes_never_worsen_the_schedule(self) -> None:
        state = _state(pending=_improvable_requests())
        single = ReorderingInsertionPolicy(
            self.graph, max_wait_s=_WIDE_WINDOW, n_passes=1,
            retry_rejected=False,
        )
        triple = ReorderingInsertionPolicy(
            self.graph, max_wait_s=_WIDE_WINDOW, n_passes=3,
            retry_rejected=False,
        )
        single.plan(state, 10)
        triple.plan(state, 10)
        self.assertLessEqual(
            triple._cost(triple.schedule, state),
            single._cost(single.schedule, state),
        )

    def test_max_relocations_caps_the_sweep(self) -> None:
        state = _state(pending=_improvable_requests())
        policy = ReorderingInsertionPolicy(
            self.graph, max_wait_s=_WIDE_WINDOW, max_relocations=1,
            retry_rejected=False,
        )
        policy.plan(state, 10)
        stats = policy.stats()
        attempts = stats["pair_attempts"] + stats["dropoff_attempts"]
        self.assertEqual(attempts, 1)


class TestOnboardRiders(unittest.TestCase):
    """A rider already aboard relocates by their dropoff alone."""

    def setUp(self) -> None:
        self.graph = TransitGraph(SQUARE_MAP)
        self.policy = ReorderingInsertionPolicy(
            self.graph, max_wait_s=_WIDE_WINDOW, retry_rejected=False,
        )

    def test_onboard_rider_is_considered_for_relocation(self) -> None:
        onboard = [
            _req("on", _S1, _S3, state=RequestState.ON_BUS, boarded_at=0.0)
        ]
        state = _state(
            pending=[_req("r0", _S2, _S4)], onboard=onboard,
            remaining_capacity=3,
        )
        self.policy.plan(state, 10)
        self.assertGreater(self.policy.stats()["dropoff_attempts"], 0)

    def test_dropoff_relocation_respects_capacity(self) -> None:
        """
        Why relocate-dropoff cannot simply reuse ``_adopt_dropoff``.

        A single-seat bus already carrying someone, with two more wanting the
        same trip. Judged on cost alone the onboard rider's dropoff belongs
        much later — it scores 0.17 against the 0.55 of the best legal
        placement — but getting there means two people aboard at once. Moving
        a dropoff later raises the load carried in between, so the capacity
        check is doing real work rather than restating the parent's.
        """
        policy = ReorderingInsertionPolicy(
            self.graph, max_wait_s=0.5, retry_rejected=False,
        )
        onboard = [
            _req("on", _START, _S1, state=RequestState.ON_BUS, boarded_at=0.0)
        ]
        state = _state(
            pending=[_req("r0", _START, _S1), _req("r1", _START, _S1)],
            onboard=onboard,
            remaining_capacity=0,
        )
        policy.plan(state, 10)

        relocated = policy._relocate_dropoff(
            policy.schedule, onboard[0], state
        )
        self.assertIsNotNone(relocated)
        self.assertTrue(relocated.capacity_ok(1, 1))
        self.assertTrue(policy.schedule.capacity_ok(1, 1))

    def test_dropoff_relocation_respects_other_riders_windows(self) -> None:
        """
        The onboard rider has no deadline; everyone behind them does.

        A dropoff carries no window of its own, so the only thing stopping it
        from moving is the delay it inflicts downstream. Here the cheapest
        placement that capacity allows would push a waiting rider's pickup
        past their deadline — and it is cheaper than the status quo (0.94
        against 1.16), so the improvement gate would wave it through if
        feasibility were not checked separately.
        """
        policy = ReorderingInsertionPolicy(
            self.graph, max_wait_s=0.25, retry_rejected=False,
        )
        onboard = [
            _req("on", _START, _S1, state=RequestState.ON_BUS, boarded_at=0.0)
        ]
        state = _state(
            pending=[_req("r0", _START, _S1), _req("r1", _S2, _START)],
            onboard=onboard,
            remaining_capacity=3,
        )
        policy.plan(state, 10)

        relocated = policy._relocate_dropoff(
            policy.schedule, onboard[0], state
        )
        if relocated is not None:
            self.assertTrue(policy._feasible(relocated, state))
        self.assertTrue(policy._feasible(policy.schedule, state))

    def test_relocation_never_recreates_a_boarded_rider_pickup(self) -> None:
        onboard = [
            _req("on", _S1, _S3, state=RequestState.ON_BUS, boarded_at=0.0)
        ]
        state = _state(
            pending=[_req("r0", _S2, _S4)], onboard=onboard,
            remaining_capacity=3,
        )
        self.policy.plan(state, 10)
        self.assertNotIn("on", self.policy.schedule.pickup_owners())
        self.assertIn("on", self.policy.schedule.dropoff_owners())


class TestRetry(unittest.TestCase):
    """
    The retry step's contract.

    No test here asserts that retry actually admits a rejected request. On
    this five-node map a window tight enough to reject anything also leaves a
    schedule too small to reorder usefully, so no admission is reachable —
    that is a property of the fixture, not of the policy. What is testable is
    that retry is attempted when it should be, skipped when disabled, and
    never costs anyone their place.
    """

    def setUp(self) -> None:
        self.graph = TransitGraph(SQUARE_MAP)

    # Narrow enough that r1 is genuinely refused. Widening this to 0.2 lets
    # both requests in, at which point every test below passes vacuously.
    _REJECTING_WINDOW = 0.15

    def _rejecting_state(self) -> BusState:
        """Build a state whose tight window leaves someone unscheduled."""
        return _state(pending=[
            _req("r0", _S1, _S2),
            _req("r1", _S3, _S4, created_at=0.0),
        ])

    def test_the_fixture_really_rejects_someone(self) -> None:
        # Guards every other test in this class against going hollow.
        policy = ReorderingInsertionPolicy(
            self.graph, max_wait_s=self._REJECTING_WINDOW,
            retry_rejected=False,
        )
        state = self._rejecting_state()
        policy.plan(state, 10)
        unscheduled = {
            r.id for r in state.pending_requests
        } - policy.schedule.owners()
        self.assertTrue(unscheduled)

    def test_retry_is_attempted_when_a_request_is_unscheduled(self) -> None:
        policy = ReorderingInsertionPolicy(
            self.graph, max_wait_s=self._REJECTING_WINDOW,
            retry_rejected=True,
        )
        policy.plan(self._rejecting_state(), 10)
        self.assertGreater(policy.stats()["retry_attempts"], 0)

    def test_retry_disabled_makes_no_attempts(self) -> None:
        policy = ReorderingInsertionPolicy(
            self.graph, max_wait_s=self._REJECTING_WINDOW,
            retry_rejected=False,
        )
        policy.plan(self._rejecting_state(), 10)
        self.assertEqual(policy.stats()["retry_attempts"], 0)

    def test_retry_never_drops_a_scheduled_request(self) -> None:
        policy = ReorderingInsertionPolicy(
            self.graph, max_wait_s=self._REJECTING_WINDOW,
            retry_rejected=True,
        )
        state = self._rejecting_state()
        policy.plan(state, 10)
        before = policy.schedule
        after = policy._retry(before, state)
        self.assertTrue(before.owners().issubset(after.owners()))


class TestStatsAndConstruction(unittest.TestCase):
    """Counter reporting and constructor validation."""

    def setUp(self) -> None:
        self.graph = TransitGraph(SQUARE_MAP)

    def test_stats_start_at_zero_with_every_key_present(self) -> None:
        policy = ReorderingInsertionPolicy(
            self.graph, max_wait_s=_WIDE_WINDOW
        )
        stats = policy.stats()
        self.assertEqual(set(stats), {
            "repair_calls", "repair_passes", "pair_attempts",
            "pair_accepted", "dropoff_attempts", "dropoff_accepted",
            "cost_improvement", "retry_attempts", "retry_admitted",
        })
        self.assertEqual(set(stats.values()), {0.0})

    def test_stats_returns_a_copy(self) -> None:
        policy = ReorderingInsertionPolicy(
            self.graph, max_wait_s=_WIDE_WINDOW
        )
        policy.stats()["repair_calls"] = 99.0
        self.assertEqual(policy.stats()["repair_calls"], 0.0)

    def test_cost_improvement_accumulates(self) -> None:
        policy = ReorderingInsertionPolicy(
            self.graph, max_wait_s=_WIDE_WINDOW, retry_rejected=False,
        )
        policy.plan(_state(pending=_improvable_requests()), 10)
        self.assertGreater(policy.stats()["cost_improvement"], 0.0)

    def test_negative_max_relocations_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ReorderingInsertionPolicy(
                self.graph, max_wait_s=_WIDE_WINDOW, max_relocations=-1
            )

    def test_negative_n_passes_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ReorderingInsertionPolicy(
                self.graph, max_wait_s=_WIDE_WINDOW, n_passes=-1
            )


class TestConvergence(unittest.TestCase):
    """n_passes=None must sweep to a fixed point and then stop."""

    def setUp(self) -> None:
        self.graph = TransitGraph(SQUARE_MAP)

    def _policy(self, n_passes: int | None) -> ReorderingInsertionPolicy:
        """Build a policy with repair configured to the given pass count."""
        return ReorderingInsertionPolicy(
            self.graph, max_wait_s=_WIDE_WINDOW,
            n_passes=n_passes, retry_rejected=False,
        )

    def test_converged_schedule_is_a_fixed_point(self) -> None:
        policy = self._policy(None)
        state = _state(pending=_improvable_requests())
        policy.plan(state, 10)
        # A further sweep over a converged schedule must find nothing, and
        # _repair signals "nothing moved" by handing back the same object.
        self.assertIs(policy._repair(policy.schedule, state), policy.schedule)

    def test_convergence_costs_exactly_one_confirming_pass(self) -> None:
        # This fixture improves on the first sweep and has nothing left for
        # the second, so converging must cost exactly two passes. Asserted
        # exactly rather than as a range: a loop that never detects its fixed
        # point still lands inside any range bounded by _MAX_CONVERGE_PASSES.
        policy = self._policy(None)
        policy.plan(_state(pending=_improvable_requests()), 10)
        stats = policy.stats()
        self.assertEqual(stats["repair_calls"], 1.0)
        self.assertEqual(stats["repair_passes"], 2.0)

    def test_cap_bounds_a_repair_that_never_settles(self) -> None:
        # Convergence is guaranteed by strictly decreasing cost, so force the
        # pathology the cap exists for: a repair that reports a change every
        # time. Without the cap this would not terminate.
        class NeverSettles(ReorderingInsertionPolicy):
            def _repair(self, schedule, state):  # type: ignore[no-untyped-def]
                return Schedule(schedule.events)

        policy = NeverSettles(
            self.graph, max_wait_s=_WIDE_WINDOW,
            n_passes=None, retry_rejected=False,
        )
        policy.plan(_state(pending=_improvable_requests()), 10)
        self.assertEqual(
            policy.stats()["repair_passes"], float(_MAX_CONVERGE_PASSES)
        )

    def test_fixed_count_runs_exactly_that_many_passes(self) -> None:
        policy = self._policy(2)
        policy.plan(_state(pending=_improvable_requests()), 10)
        stats = policy.stats()
        self.assertEqual(stats["repair_passes"], 2.0 * stats["repair_calls"])

    def test_zero_passes_runs_no_sweeps(self) -> None:
        policy = self._policy(0)
        policy.plan(_state(pending=_improvable_requests()), 10)
        self.assertEqual(policy.stats()["repair_passes"], 0.0)

    def test_converging_is_never_worse_than_one_pass(self) -> None:
        state = _state(pending=_improvable_requests())
        one_pass, converging = self._policy(1), self._policy(None)
        one_pass.plan(state, 10)
        converging.plan(state, 10)
        self.assertLessEqual(
            converging._cost(converging.schedule, state),
            one_pass._cost(one_pass.schedule, state),
        )

    def test_converging_drops_nobody(self) -> None:
        policy = self._policy(None)
        requests = _improvable_requests()
        policy.plan(_state(pending=requests), 10)
        self.assertEqual(policy.schedule.owners(), {r.id for r in requests})

    def test_none_passes_is_accepted_by_validation(self) -> None:
        # None means "until converged", so it must survive the negativity
        # check that guards the integer form.
        self._policy(None)


if __name__ == "__main__":
    unittest.main()
