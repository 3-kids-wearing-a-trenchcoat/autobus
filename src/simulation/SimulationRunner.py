"""SimulationRunner: orchestrates the simulation tick loop."""

from dataclasses import dataclass
from typing import Callable

from src.algorithm.BusAgent import BusAgent
from src.algorithm.RequestPool import RequestPool
from src.metrics.MetricsTracker import KPISnapshot, MetricsTracker
from src.simulation.AnimationRecorder import AnimationRecorder
from src.simulation.DemandGenerator import DemandGenerator
from src.simulation.SimulationBackend import SimulationBackend


@dataclass(frozen=True)
class TickProgress:
    """
    What a run looks like from outside, one tick in.

    Everything a caller needs to say how far along a run is, without
    reaching into the pool or the clock itself. Both of a run's two ending
    conditions are visible here: it stops when ``sim_time`` reaches the
    ceiling, or when demand is exhausted and nothing is left in play.

    :param sim_time: Simulated seconds elapsed.
    :param created: Requests generated so far.
    :param pending: Requests waiting at a stop.
    :param on_bus: Requests currently riding.
    :param fulfilled: Requests delivered.
    :param cancelled: Requests whose passenger gave up waiting.
    :param demand_exhausted: Whether the generator has nothing more to emit.
    """

    sim_time: float
    created: int
    pending: int
    on_bus: int
    fulfilled: int
    cancelled: int
    demand_exhausted: bool

    @property
    def settled(self) -> int:
        """How many requests have reached a final state."""
        return self.fulfilled + self.cancelled


class SimulationRunner:
    """
    Drives the simulation: owns the clock, the tick order and the components.

    The runner mediates between the agent and the world. It reads the agent's
    next stop, commands the backend to drive there, observes the arrival and
    hands it back to the agent. Nothing here is world-specific, which is
    precisely why it lives above the backend rather than inside it.

    :param backend: The world to drive. Any SimulationBackend will do; the
        runner never asks which kind it is.
    :param agent: The bus agent that manages routing and passenger state.
    :param pool: Shared request pool; read for termination and abandonment.
    :param demand_generator: Generates new passenger requests each tick.
    :param metrics_tracker: Passive observer that records per-tick snapshots.
    :param dt: Fixed step size in seconds for each tick.
    :param max_time: Safety ceiling in seconds; the loop exits when the pool
        empties or sim_time reaches this, whichever comes first.
    :param recorder: Optional animation recorder; when provided, captures a
        frame on each stop arrival. Defaults to None.
    :param max_wait_s: Maximum time a request may stay PENDING before the
        passenger gives up and the request is cancelled. None disables
        abandonment (requests wait indefinitely). Defaults to None.
    :param on_tick: Optional observer called once per tick with a
        ``TickProgress``, for a caller that wants to show progress while the
        run happens. Passive, like ``metrics_tracker``: it is called after
        the tick is complete and nothing reads what it returns, so it cannot
        influence the simulation. Defaults to None, which calls nothing.
    """

    def __init__(
        self,
        backend: SimulationBackend,
        agent: BusAgent,
        pool: RequestPool,
        demand_generator: DemandGenerator,
        metrics_tracker: MetricsTracker,
        dt: float,
        max_time: float,
        recorder: AnimationRecorder | None = None,
        max_wait_s: float | None = None,
        on_tick: Callable[["TickProgress"], None] | None = None,
    ) -> None:
        self._backend = backend
        self._agent = agent
        self._pool = pool
        self._demand_generator = demand_generator
        self._metrics_tracker = metrics_tracker
        self._dt = dt
        self._max_time = max_time
        self._recorder = recorder
        self._max_wait_s = max_wait_s
        self._on_tick = on_tick
        self._sim_time: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> KPISnapshot:
        """
        Run the simulation to completion and print a summary to stdout.

        :return: KPISnapshot computed from the final simulation state.
        """
        try:
            # Inside the guard, not before it: a failure part-way through
            # ``start`` leaves actors alive and the world synchronous.
            self._backend.start()
            while not self.is_done() and self._sim_time < self._max_time:
                self.step(self._dt)
        finally:
            self._backend.close()

        kpi = self._metrics_tracker.snapshot(self._sim_time)
        _print_results(kpi, self._sim_time)
        return kpi

    def step(self, dt: float) -> None:
        """
        Advance the simulation by dt seconds.

        Per-tick order: advance the clock → generate demand → expire
        long-waiting requests → sync the world's passengers → move the bus
        (firing on_arrival for each stop reached) → record a metrics snapshot.

        Demand is generated before movement so that passengers created in this
        tick can board at stops the bus visits within it. ``record_tick`` runs
        last so the snapshot reflects the final pool state.
        :param dt: Time delta in seconds.
        """
        self._sim_time += dt
        self._demand_generator.update(self._sim_time)
        self._expire_pending()
        self._backend.sync_passengers(self._pool)
        self._process_movement(dt)
        self._metrics_tracker.record_tick(self._sim_time)
        # Last, so an observer sees exactly the state the metrics snapshot
        # was taken from rather than a half-finished tick.
        if self._on_tick is not None:
            self._on_tick(self._progress())

    def _progress(self) -> TickProgress:
        """
        Describe the run as it stands, for ``on_tick``.

        :return: A snapshot of the clock and the pool.
        """
        pending = len(self._pool.get_pending())
        on_bus = len(self._pool.get_on_bus())
        fulfilled = len(self._pool.get_fulfilled())
        cancelled = len(self._pool.get_cancelled())
        return TickProgress(
            sim_time=self._sim_time,
            # Summed rather than taken from ``len(pool)``, which counts only
            # what is still in play -- it reads 0 once the last passenger is
            # delivered, which is the opposite of "created so far".
            created=pending + on_bus + fulfilled + cancelled,
            pending=pending,
            on_bus=on_bus,
            fulfilled=fulfilled,
            cancelled=cancelled,
            demand_exhausted=self._demand_generator.is_exhausted(),
        )

    def is_done(self) -> bool:
        """
        Return True when the run has nothing left to do.

        That means an empty pool **and** a demand generator with nothing more
        to emit. Both halves are necessary: an empty pool alone is the
        ordinary state of a bus between requests, and treating it as the end
        stops a trial the moment it happens to catch up with its own demand.

        Measured on a CARLA pilot before this was required: at a 150 s demand
        interval, 2 of 8 trials ended after serving **1 of 12** requests,
        because the bus completed that first trip in under 150 s and the
        second request had not been created yet. Both reported
        ``served_fraction`` of 1.0 — perfect service over the one request
        that existed — which is the kind of wrong number nobody queries.
        :return: True when no work remains and no more is coming.
        """
        return (
            self._demand_generator.is_exhausted()
            and len(self._pool.get_pending()) == 0
            and len(self._pool.get_on_bus()) == 0
        )

    @property
    def sim_time(self) -> float:
        """Current simulation time in seconds."""
        return self._sim_time

    @property
    def metrics(self) -> MetricsTracker:
        """The MetricsTracker for reading KPIs."""
        return self._metrics_tracker

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _expire_pending(self) -> None:
        """
        Cancel PENDING requests whose passengers have waited too long.

        A request is abandoned once it has been PENDING for at least
        ``max_wait_s`` seconds (measured from ``created_at``). Onboard
        (ON_BUS) requests are committed and never abandoned. No-op when
        ``max_wait_s`` is None. ``get_pending`` returns a fresh list, so
        cancelling under it is safe.
        """
        if self._max_wait_s is None:
            return
        for request in self._pool.get_pending():
            if self._sim_time - request.created_at >= self._max_wait_s:
                self._pool.cancel(request.id)

    def _process_movement(self, dt: float) -> None:
        """
        Spend a tick's time budget driving, one leg at a time.

        Each pass commands the backend to the agent's current next stop and
        advances it. The backend reports at most one arrival, because the leg
        after it is not known until the agent has boarded, alighted and
        re-planned — which is what ``on_arrival`` does before the loop goes
        round again.

        The arrival timestamp is derived from the leftover budget rather than
        from the tick boundary, so a stop reached mid-tick is stamped at the
        moment it was actually reached.
        :param dt: Time budget in seconds for this tick.
        """
        remaining = dt
        while remaining > 0.0:
            next_stop = self._agent.next_stop()
            if next_stop is None:
                # An empty route is not necessarily an idle world: demand may
                # have arrived since the bus last planned, and since it plans
                # only on arrival, a parked bus would otherwise never wake.
                # Gated on there being something to plan for, so an genuinely
                # idle bus does not re-run the policy every tick.
                if not self._pool.get_pending():
                    break
                self._agent.replan_if_idle(self._sim_time - remaining)
                next_stop = self._agent.next_stop()
                if next_stop is None:
                    break
            self._backend.set_destination(next_stop)
            # After set_destination, so the plan and the leg it starts with
            # are always handed over in the same state.
            self._backend.set_planned_route(self._agent.planned_route())
            result = self._backend.advance(remaining)
            remaining -= result.consumed
            if result.arrived_at is None:
                break
            arrival_time = self._sim_time - remaining
            self._agent.on_arrival(result.arrived_at, arrival_time)
            if self._recorder is not None:
                self._recorder.record_arrival(result.arrived_at, arrival_time)


def _print_results(kpi: KPISnapshot, sim_time: float) -> None:
    """Print a human-readable summary of a completed simulation run."""

    def fmt_float(value: float | None, unit: str = "s") -> str:
        if value is None:
            return "N/A"
        return f"{value:.2f} {unit}"

    print("=== Simulation Complete ===")
    print(f"Sim time:         {sim_time:.2f} s")
    print(f"Fulfilled:        {kpi.fulfilled_count}")
    print(f"Cancelled:        {kpi.cancelled_count}")
    print(f"Mean wait time:   {fmt_float(kpi.mean_wait_time)}")
    print(f"P90 wait time:    {fmt_float(kpi.p90_wait_time)}")
    print(f"Max wait time:    {fmt_float(kpi.max_wait_time)}")
    print(f"Mean ride time:   {fmt_float(kpi.mean_ride_time)}")
    print(f"Mean journey:     {fmt_float(kpi.mean_journey_time)}")
    if kpi.fulfillment_rate is None:
        print(f"Fulfillment rate: {fmt_float(None)}")
    else:
        print(f"Fulfillment rate: {kpi.fulfillment_rate * 100:.1f} %")
    print(f"Throughput:       {fmt_float(kpi.throughput, 'req/m')}")
