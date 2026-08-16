"""Correcting the traffic manager's junction decisions as the bus drives.

`TrafficManager.set_route` takes a **positional** list of turns and spends
one entry per junction it considers a decision point. That makes the list
only as good as our prediction of which junctions those are — and an entry
too many is exactly as fatal as an entry too few, because either throws
every later turn onto the wrong junction. Traced on Town03, one such slip
sent a 417 m leg 1649 m without arriving, silently: CARLA complains only
when it can find *no* candidate for the turn requested, so a turn that
lands on the wrong junction sets ``route_drops=0``.

Five attempts to predict that set failed, and the prediction was then shown
not to exist — see ``carla_routing._is_labelled`` for what it asks and why
it asks the wrong question. This module removes the need for it by closing
the loop instead:

* the plan is keyed by **junction id**, not by list position, so an entry
  can only ever be spent at the junction it was meant for, and a route that
  crosses one junction twice is expressible;
* **CARLA is the oracle** — a junction its action buffer labels ``Left``,
  ``Right`` or ``Straight`` is a decision point, one left at ``LaneFollow``
  is not, so nothing here has to guess;
* only **one entry is ever outstanding**, aimed at a junction currently in
  the buffer, so a mistake cannot compound into the rest of the leg.

A junction the traffic manager is deciding at that our plan does not
contain means the bus has left its route, which this reports so the caller
can re-plan. That is an off-route trigger for free, and it fires on the
divergence rather than 232 s later, which is when the periodic re-issue in
``CarlaBackend._track_replan`` got there.

**Measured** on the Town03 leg ``s05 -> s11`` (seed 0, no traffic, no
periodic re-issue), each arm from a fresh spawn on a fresh server:

===============  ========  =========  =========  =======
arm              arrived   time       driven     ratio
===============  ========  =========  =========  =======
positional list  no        420 s cap  1641.8 m   3.94x
closed loop      yes       144.4 s     685.8 m   1.64x
===============  ========  =========  =========  =======

Unlike ``carla_routing`` this module does not import carla; it only ever
handles objects handed to it.
"""

# Buffer labels that mean the traffic manager has a choice to make here.
# `LaneFollow` means it has not, and issuing an entry against it would have
# that entry consumed at some later junction instead.
DECISION_LABELS = frozenset({"Left", "Right", "Straight"})


def buffered_junction(
    traffic_manager: object, bus: object
) -> tuple[int, str, float] | None:
    """
    Find the first junction the traffic manager has decided on ahead.

    Reads `get_all_actions`, which is the traffic manager's own lookahead
    buffer. Measured at 1-3 entries spanning about 19 m, and 0.003 ms a
    call, so it is affordable every tick.
    :param traffic_manager: The traffic manager steering the bus.
    :param bus: The bus actor.
    :return: (junction id, the label CARLA gave it, metres ahead), or None
        if no junction is in the buffer yet.
    """
    here = bus.get_location()
    for action, waypoint in traffic_manager.get_all_actions(bus):
        if not waypoint.is_junction:
            continue
        junction = waypoint.get_junction()
        if junction is None:
            continue
        gap = here.distance(waypoint.transform.location)
        return int(junction.id), str(action), gap
    return None


class ClosedLoopSteering:
    """
    Hold a junction-keyed plan and correct the bus onto it, one turn at a
    time.

    Owns no CARLA objects: the traffic manager and the bus are passed to
    ``step`` so the caller keeps control of their lifetimes.
    """

    def __init__(self) -> None:
        self._pending: list[tuple[int, str]] = []
        # The junction already acted on, and only while it is still the one
        # in the buffer. A *set* of ids would be wrong: a route may cross
        # the same junction twice -- the Town03 reference leg crosses 861
        # twice -- and the second visit needs deciding again.
        self._acted: int | None = None
        self.corrections = 0
        self.agreements = 0

    def set_plan(self, pairs: list[tuple[int, str]]) -> None:
        """
        Adopt a freshly planned route.

        :param pairs: (junction id, turn) in travel order, from
            ``carla_routing.junction_turns``.
        """
        self._pending = list(pairs)
        self._acted = None

    def step(self, traffic_manager: object, bus: object) -> str | None:
        """
        Check the junction ahead and correct it if it is wrong.

        :param traffic_manager: The traffic manager steering the bus.
        :param bus: The bus actor.
        :return: A reason to re-plan, or None if nothing is needed. The
            caller should re-plan from the bus's current position and call
            ``set_plan`` with the result.
        """
        ahead = buffered_junction(traffic_manager, bus)
        if ahead is None:
            # Nothing buffered: whatever we acted on has been passed, so
            # meeting that junction again counts as a fresh decision.
            self._acted = None
            return None

        junction, label, _gap = ahead
        if junction == self._acted:
            return None
        if label not in DECISION_LABELS:
            return None

        wanted = self._take(junction)
        if wanted is None:
            return f"junction {junction} is not on the planned route"

        self._acted = junction
        if wanted == label:
            self.agreements += 1
            return None

        # Takes effect on the next world tick, not this one -- the traffic
        # manager re-decides when it ticks. ``empty_buffer=True`` is what
        # forces that re-decision on a junction it has already labelled.
        traffic_manager.set_route(bus, [wanted], True)
        self.corrections += 1
        return None

    def _take(self, junction: int) -> str | None:
        """
        Claim the turn the plan wants at a junction, and drop what precedes
        it.

        Earlier junctions are discarded rather than kept: the bus has passed
        them, and a route that revisits a junction must match the *next*
        occurrence rather than the first.
        :param junction: Junction the traffic manager is deciding at.
        :return: The turn to make, or None if the junction is not ahead on
            the plan.
        """
        for index, (candidate, turn) in enumerate(self._pending):
            if candidate == junction:
                self._pending = self._pending[index + 1:]
                return turn
        return None
