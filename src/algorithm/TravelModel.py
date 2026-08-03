"""Travel-time and dwell-time estimates used when projecting a route."""

import math
import random
from abc import ABC, abstractmethod

from src.map.TransitGraph import TransitGraph


class TravelModel(ABC):
    """
    Abstract estimate of how long driving and stopping take.

    This is the *planning* view of the world — what a policy believes will
    happen — and is deliberately distinct from the simulation's ground truth
    (``SimulationBackend._travel_time``, which subclasses supply). In the mock
    simulation the two coincide exactly, so a projected schedule is not an
    approximation: it is precisely what the simulation will execute. Under
    CARLA they will diverge, because traffic makes real driving times
    unpredictable.

    Keeping the two apart means a policy can never silently mistake its own
    projections for facts. A policy must re-anchor on the real position, clock
    and passenger set every time it is asked to plan.
    """

    @abstractmethod
    def travel_time(self, from_id: str, to_id: str) -> float:
        """
        Estimate the driving time between two TransitGraph nodes.
        :param from_id: TransitGraph node ID of the origin.
        :param to_id: TransitGraph node ID of the destination.
        :return: Estimated travel time in seconds.
        """

    @abstractmethod
    def dwell_time(
        self, stop_id: str, n_boarding: int, n_alighting: int
    ) -> float:
        """
        Estimate how long the bus is held at a stop.

        The passenger counts are part of the signature so that a future model
        can charge boarding and alighting time per passenger; the current
        implementation ignores them.
        :param stop_id: TransitGraph node ID of the stop being visited.
        :param n_boarding: Passengers expected to board at this stop.
        :param n_alighting: Passengers expected to alight at this stop.
        :return: Estimated dwell time in seconds.
        """


class GraphTravelModel(TravelModel):
    """
    Travel estimates read straight off the TransitGraph, with constant dwell.

    Travel time is the shortest-path cost through the graph, whose edge
    weights are already expressed in seconds. Dwell is a single constant
    charged once per stop *visit*, independent of how many passengers board or
    alight.
    """

    def __init__(self, graph: TransitGraph, dwell_s: float = 0.0) -> None:
        """
        :param graph: Transit graph representing the road network.
        :param dwell_s: Seconds the bus is held at each stop it visits.
            Defaults to 0.0, which reproduces the original dwell-free
            behaviour exactly.
        """
        self._graph = graph
        self._dwell_s = dwell_s

    @property
    def graph(self) -> TransitGraph:
        """The transit graph these estimates are read from."""
        return self._graph

    @property
    def dwell_s(self) -> float:
        """Constant dwell time in seconds charged per stop visit."""
        return self._dwell_s

    def travel_time(self, from_id: str, to_id: str) -> float:
        """
        Return the shortest-path travel time between two nodes.
        :param from_id: TransitGraph node ID of the origin.
        :param to_id: TransitGraph node ID of the destination.
        :return: Travel time in seconds.
        """
        return self._graph.best_path_cost(from_id, to_id)

    def dwell_time(
        self, stop_id: str, n_boarding: int, n_alighting: int
    ) -> float:
        """
        Return the constant dwell time, ignoring the passenger counts.
        :param stop_id: TransitGraph node ID of the stop being visited.
        :param n_boarding: Ignored.
        :param n_alighting: Ignored.
        :return: Dwell time in seconds.
        """
        return self._dwell_s


class NoisyTravelModel(GraphTravelModel):
    """
    Graph travel times perturbed by a random factor on every query.

    Exists to break the mock simulation's most convenient assumption. Today
    one ``GraphTravelModel`` answers both the policy's projections and the
    backend's physics, so a schedule certified feasible is executed exactly as
    projected and the only way to fail a passenger is to refuse them up front.
    Under CARLA that identity dissolves: traffic and signals make the drive
    take a time nobody predicted, and a request can be accepted, promised, and
    then delivered late. Handing this model to the *backend alone* reproduces
    that failure mode on the planar map, years before the integration lands.

    The factor is lognormal with median 1.0, so a typical leg takes the time
    the graph predicts while the distribution stays positive and skews right —
    a bus loses more time to congestion than it can ever win back by driving
    fast. Its mean therefore sits above 1.0 and delay accumulates over a route
    rather than cancelling out, which is the behaviour worth stress-testing.
    ``sigma`` reads roughly as the proportional spread: 0.2 keeps about
    two thirds of legs within ±20% of prediction.

    Randomness comes from a private generator, never the module-level one.
    ``run_trial`` seeds the global generator for demand, and drawing from it
    here would shift the demand stream, silently making a noisy run
    incomparable with the baseline it is supposed to be measured against.
    """

    def __init__(
        self,
        graph: TransitGraph,
        dwell_s: float = 0.0,
        sigma: float = 0.0,
        seed: int | None = None,
    ) -> None:
        """
        :param graph: Transit graph representing the road network.
        :param dwell_s: Seconds the bus is held at each stop it visits.
        :param sigma: Standard deviation of the underlying normal, so the
            multiplier is ``exp(N(0, sigma))``. 0.0 disables perturbation and
            reproduces GraphTravelModel exactly.
        :param seed: Seed for this model's private generator. None draws
            from system entropy, which makes the run irreproducible — pass a
            seed for anything being compared.
        :raises ValueError: If sigma is negative.
        """
        super().__init__(graph, dwell_s)
        if sigma < 0.0:
            raise ValueError(f"sigma must not be negative (got {sigma})")
        self._sigma = sigma
        self._rng = random.Random(seed)

    @property
    def sigma(self) -> float:
        """Spread of the lognormal multiplier; 0.0 means no perturbation."""
        return self._sigma

    def travel_time(self, from_id: str, to_id: str) -> float:
        """
        Return the graph's travel time scaled by a fresh random factor.

        Drawn per call, which the simulation turns into one draw per leg
        driven: SimulationBackend asks once when a leg begins and caches the
        answer until the bus arrives, so a leg never re-rolls underneath it.
        :param from_id: TransitGraph node ID of the origin.
        :param to_id: TransitGraph node ID of the destination.
        :return: Perturbed travel time in seconds.
        """
        base = super().travel_time(from_id, to_id)
        if self._sigma == 0.0:
            # Short-circuited so sigma=0 leaves the generator untouched and
            # is bit-identical to the unperturbed model, not merely equal.
            return base
        return base * math.exp(self._rng.normalvariate(0.0, self._sigma))
