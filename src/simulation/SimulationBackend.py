"""Abstract simulation backend: the seam between the project and a world."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.algorithm.RequestPool import RequestPool


@dataclass(frozen=True)
class AdvanceResult:
    """
    Outcome of one ``advance`` call.

    :param arrived_at: Stop ID the bus reached, or None if it was still en
        route when the time budget ran out.
    :param consumed: Seconds of simulation time actually spent. Equals the
        requested budget unless an arrival cut the call short.
    """

    arrived_at: str | None
    consumed: float


class SimulationBackend(ABC):
    """
    Adapter between the simulation and whatever world the bus drives in.

    The backend answers questions and takes commands; it never orchestrates.
    Everything that is not world-specific — the tick order, the clock, demand,
    abandonment, metrics — belongs to ``SimulationRunner``, which is what
    keeps the rest of the project indifferent to whether the world is a
    synthetic graph, CARLA, or a scripted timetable.

    Three methods are abstract because only the world can answer them. The
    other three have no-op defaults, because a world that needs no setup and
    has no passengers to render should not have to say so.
    """

    #: Whether several of these may run at once in separate processes.
    #:
    #: True for any world that lives entirely inside its own process — the
    #: dead-reckoning mock, a scripted timetable — which is every world the
    #: experiment harness sweeps over. A world backed by a **shared external
    #: resource** must say False: one CARLA server cannot host a campaign per
    #: core, and trials would contend for it rather than run.
    #:
    #: Declared rather than inferred from the map, and the distinction is
    #: worth keeping: a CARLA *town* driven by the mock backend uses no server
    #: at all, and is both parallel-safe and genuinely useful — real geometry
    #: and long legs at seconds per trial.
    parallel_safe: bool = True

    # ------------------------------------------------------------------
    # Abstract interface — genuinely world-specific
    # ------------------------------------------------------------------

    @abstractmethod
    def set_destination(self, stop_id: str) -> None:
        """
        Command the bus to drive to a stop.

        Must be **idempotent**: re-commanding the stop the bus is already
        heading for leaves the leg untouched, which is how partial progress
        survives across ticks. The runner re-issues the destination on every
        pass of its movement loop rather than tracking what it last said.

        A destination equal to the bus's current location is legal — a policy
        may re-issue the stop the bus is standing on — and must start a fresh
        leg rather than being ignored as unchanged.
        :param stop_id: TransitGraph node ID of the stop to drive to.
        """

    @abstractmethod
    def advance(self, dt: float) -> AdvanceResult:
        """
        Advance the world until ``dt`` is spent or one stop is reached.

        **One arrival at most, never a batch.** Arrivals within a single tick
        are sequentially dependent: the agent boards, alights and re-plans on
        each one, and the leg that follows is not known until it has. A
        backend that reported several arrivals at once would be answering for
        a route the agent had not yet chosen.

        When no arrival occurs the whole budget must be consumed, so the
        runner's clock and the world's stay in step. For a tick-based world
        that means ``dt`` should be a whole multiple of its tick length.
        :param dt: Time budget in seconds.
        :return: AdvanceResult naming the stop reached, if any, and the time
            actually spent.
        """

    @abstractmethod
    def current_location(self) -> str:
        """
        Return where the bus is now.

        Between stops this is the last stop reached, so it is only exact at
        arrivals. That is enough for today, where the bus is stationary
        whenever it plans; a world that supports re-planning mid-leg would
        report a finer position here.
        :return: TransitGraph node ID of the bus's position.
        """

    # ------------------------------------------------------------------
    # Optional hooks — no-ops unless the world needs them
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        Prepare the world for a run; called once before the first tick.

        This is where a world with actors creates them. Deliberately separate
        from ``__init__`` so that constructing a backend stays cheap and
        failure-free — CARLA has no vehicle to place until it has a server.
        """

    def close(self) -> None:
        """
        Release the world's resources; called once after the last tick.

        Called from a ``finally`` block, so it runs even when a trial fails
        part-way. Leaked actors are the classic cross-trial contaminant.
        """

    def sync_passengers(self, pool: RequestPool) -> None:
        """
        Mirror the request pool onto whatever the world renders passengers as.

        Takes the pool and diffs it rather than subscribing to it, which keeps
        the algorithm layer free of observer hooks: ``RequestPool`` does not
        know that anything is watching.
        :param pool: The shared request pool, read-only from here.
        """
