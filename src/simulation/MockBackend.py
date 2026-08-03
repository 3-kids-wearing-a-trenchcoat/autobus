"""MockBackend: SimulationBackend for non-CARLA map sources."""

from src.algorithm.TravelModel import GraphTravelModel
from src.map.MapProvider import MapProvider
from src.map.TransitGraph import TransitGraph
from src.simulation.SimulationBackend import AdvanceResult, SimulationBackend


class MockBackend(SimulationBackend):
    """
    A world made of shortest paths: the bus advances by dead reckoning.

    Travel times come from the TransitGraph built off the given provider, so
    a leg's duration is known the moment it starts and the bus covers it at a
    constant rate. There is no vehicle, no geometry and nothing to spawn,
    which is what makes this the fast test bed.

    :param position: TransitGraph node ID the bus starts at.
    :param provider: Map source used to build a TransitGraph, when no travel
        model is supplied. Ignored if ``travel_model`` is given.
    :param travel_model: Travel/dwell estimates to drive against. When None,
        one is built from ``provider``. Passing the *same* model the policy
        plans against makes the mock simulation's exactness structural rather
        than coincidental, and avoids building a second TransitGraph.
    :param dwell_s: Seconds the bus is held at every stop it visits,
        modelling the time a real bus spends pulled over while passengers
        board and alight. This is *ground truth*, as opposed to a policy's
        estimate of it (see ``TravelModel``). Ignored when ``travel_model``
        is supplied, which carries its own dwell. Defaults to 0.0, which
        reproduces the original dwell-free timing exactly.
    :raises ValueError: If neither ``provider`` nor ``travel_model`` is given.
    """

    def __init__(
        self,
        position: str,
        provider: MapProvider | None = None,
        travel_model: GraphTravelModel | None = None,
        dwell_s: float = 0.0,
    ) -> None:
        if travel_model is None:
            if provider is None:
                raise ValueError(
                    "MockBackend needs either a provider or a travel_model"
                )
            travel_model = GraphTravelModel(
                TransitGraph(provider), dwell_s=dwell_s
            )
        self._travel_model = travel_model
        self._dwell_s = travel_model.dwell_s
        self._position = position
        self._destination: str | None = None
        # None means "not yet computed for the current destination". Cleared
        # on arrival as well as on a change of destination, so a re-issued
        # stop still starts a fresh leg.
        self._leg_duration: float | None = None
        self._elapsed_toward_next: float = 0.0

    # ------------------------------------------------------------------
    # SimulationBackend interface
    # ------------------------------------------------------------------

    def set_destination(self, stop_id: str) -> None:
        """
        Aim the bus at a stop, preserving progress if it is already heading
        there.
        :param stop_id: TransitGraph node ID of the stop to drive to.
        """
        if stop_id != self._destination:
            self._destination = stop_id
            self._elapsed_toward_next = 0.0
            self._leg_duration = None

    def advance(self, dt: float) -> AdvanceResult:
        """
        Advance along the current leg by up to dt seconds.

        The leg's duration is computed lazily on the first advance toward a
        destination rather than when it is set, which keeps construction free
        of movement arithmetic and matches how a real world would behave: you
        find out how long the drive takes by starting it.
        :param dt: Time budget in seconds.
        :return: AdvanceResult naming the stop reached, if any, and the time
            spent.
        """
        if self._destination is None:
            return AdvanceResult(None, dt)
        if self._leg_duration is None:
            self._leg_duration = self._leg_duration_to(self._destination)

        time_left = self._leg_duration - self._elapsed_toward_next
        if dt >= time_left:
            self._position = self._destination
            self._elapsed_toward_next = 0.0
            self._leg_duration = None
            return AdvanceResult(self._position, time_left)

        self._elapsed_toward_next += dt
        return AdvanceResult(None, dt)

    def current_location(self) -> str:
        """Return the last stop the bus reached."""
        return self._position

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _leg_duration_to(self, stop_id: str) -> float:
        """
        Return how long the drive to a stop takes, dwell included.

        Dwell is folded into the *leading* edge of the leg — the bus first
        spends ``dwell_s`` pulled over where it currently is, then drives.
        That is equivalent to holding it at the stop it just reached, and it
        keeps the whole calculation to a single duration per leg.

        One consequence: the bus also dwells at its initial position, before
        it has served anyone. Harmless, and arguably right, since a real bus
        would be sitting at its first stop with the doors open.
        :param stop_id: TransitGraph node ID of the destination.
        :return: Dwell plus travel time in seconds.
        """
        return self._dwell_s + self._travel_model.travel_time(
            self._position, stop_id
        )
