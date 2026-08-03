"""ScriptedBackend: a world with no map, driven by a timetable."""

from collections.abc import Sequence

from src.simulation.SimulationBackend import AdvanceResult, SimulationBackend


class ScriptedBackend(SimulationBackend):
    """
    A world with no graph, no travel model and no geometry.

    Every leg takes however long the timetable says, regardless of where the
    bus is or where it was told to go. The bus then "arrives" at whatever
    stop it was commanded to.

    Its purpose is to demonstrate that nothing above the backend depends on
    where the world comes from: a full simulation runs against this and
    produces ordinary KPIs, with no map involved anywhere below the runner.
    It doubles as a cheap stand-in for CARLA while the server is unavailable,
    and as the backend the runner's own tests drive.

    :param position: TransitGraph node ID the bus starts at.
    :param leg_durations: Seconds each successive leg takes. The sequence is
        **cycled**, so a single-element timetable means a constant leg time
        and a trial never runs out of schedule. Defaults to one minute per
        leg.
    :raises ValueError: If ``leg_durations`` is empty or holds a
        non-positive duration, either of which would stall or loop the
        runner's movement pass.
    """

    def __init__(
        self,
        position: str,
        leg_durations: Sequence[float] = (60.0,),
    ) -> None:
        if not leg_durations:
            raise ValueError("leg_durations must not be empty")
        if any(duration <= 0.0 for duration in leg_durations):
            raise ValueError("leg durations must be strictly positive")
        self._leg_durations = tuple(leg_durations)
        self._position = position
        self._destination: str | None = None
        self._leg_index = 0
        self._elapsed_toward_next: float = 0.0

    def set_destination(self, stop_id: str) -> None:
        """
        Aim the bus at a stop, preserving progress if already heading there.
        :param stop_id: TransitGraph node ID of the stop to drive to.
        """
        if stop_id != self._destination:
            self._destination = stop_id
            self._elapsed_toward_next = 0.0

    def advance(self, dt: float) -> AdvanceResult:
        """
        Advance along the current leg by up to dt seconds.
        :param dt: Time budget in seconds.
        :return: AdvanceResult naming the stop reached, if any, and the time
            spent.
        """
        if self._destination is None:
            return AdvanceResult(None, dt)

        duration = self._leg_durations[
            self._leg_index % len(self._leg_durations)
        ]
        time_left = duration - self._elapsed_toward_next
        if dt >= time_left:
            self._position = self._destination
            self._elapsed_toward_next = 0.0
            self._leg_index += 1
            return AdvanceResult(self._position, time_left)

        self._elapsed_toward_next += dt
        return AdvanceResult(None, dt)

    def current_location(self) -> str:
        """Return the last stop the bus reached."""
        return self._position
