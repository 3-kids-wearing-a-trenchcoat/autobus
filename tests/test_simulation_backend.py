"""
Tests for the SimulationBackend interface.

Covers the abstract base class itself, and the part of the contract
every concrete backend has to honour the same way — which is tested
here, once, rather than separately in each backend's own file.
"""

import pytest
from unittest.mock import MagicMock

from src.algorithm.RequestPool import RequestPool
from src.map.StaticMapProvider import StaticMapProvider, StopInput
from src.map.map_data import RoadNodeRecord
from src.simulation.MockBackend import MockBackend
from src.simulation.ScriptedBackend import ScriptedBackend
from src.simulation.SimulationBackend import AdvanceResult, SimulationBackend


class _MinimalBackend(SimulationBackend):
    """The least a backend can implement: the three abstract methods."""

    def set_destination(self, stop_id: str) -> None:
        """Record the commanded stop."""
        self._destination = stop_id

    def advance(self, dt: float) -> AdvanceResult:
        """Never arrive; consume the whole budget."""
        return AdvanceResult(None, dt)

    def current_location(self) -> str:
        """Return a fixed position."""
        return "stop_A"


# ---------------------------------------------------------------------------
# Abstractness
# ---------------------------------------------------------------------------

def test_cannot_instantiate_abstract_class() -> None:
    with pytest.raises(TypeError):
        SimulationBackend()  # type: ignore[abstract]


def test_partial_implementation_is_still_abstract() -> None:
    """Implementing only some of the three does not make a backend."""

    class _Partial(SimulationBackend):
        def set_destination(self, stop_id: str) -> None:
            pass

    with pytest.raises(TypeError):
        _Partial()  # type: ignore[abstract]


def test_three_abstract_methods_are_enough() -> None:
    """The optional hooks must not be abstract, or every world pays for
    passengers and lifecycle it may not have."""
    assert _MinimalBackend() is not None


# ---------------------------------------------------------------------------
# Optional hooks default to no-ops
# ---------------------------------------------------------------------------

def test_start_defaults_to_noop() -> None:
    assert _MinimalBackend().start() is None


def test_close_defaults_to_noop() -> None:
    assert _MinimalBackend().close() is None


def test_sync_passengers_defaults_to_noop() -> None:
    pool = MagicMock(spec=RequestPool)
    assert _MinimalBackend().sync_passengers(pool) is None


def test_sync_passengers_does_not_touch_the_pool() -> None:
    """The default must be inert, not merely silent."""
    pool = MagicMock(spec=RequestPool)
    _MinimalBackend().sync_passengers(pool)
    assert pool.mock_calls == []


# ---------------------------------------------------------------------------
# The contract every concrete backend honours
# ---------------------------------------------------------------------------

# The smallest world MockBackend can be built over: one road edge, which is
# one TransitGraph node, which is somewhere to stand.
_LINE = StaticMapProvider(
    stops=[StopInput("s1", "n1", "n2", 0.5)],
    nodes=[RoadNodeRecord("n1", x=0.0, y=0.0),
           RoadNodeRecord("n2", x=1300.0, y=0.0)],
    edges=[("n1", "n2")],
)

# Built lazily, because a factory lets each test have an untouched backend.
_CONCRETE = {
    "MockBackend": lambda: MockBackend(position="n1_n2", provider=_LINE),
    "ScriptedBackend": lambda: ScriptedBackend("n1_n2"),
}


@pytest.mark.parametrize("build", _CONCRETE.values(), ids=_CONCRETE.keys())
def test_a_backend_starts_where_it_was_placed(build) -> None:
    """Position is whatever the constructor was handed, before any leg."""
    assert build().current_location() == "n1_n2"


@pytest.mark.parametrize("build", _CONCRETE.values(), ids=_CONCRETE.keys())
def test_advance_without_a_destination_consumes_the_budget(build) -> None:
    """
    A backend with nowhere to go must still let the clock run, or the
    runner's movement loop would spin without ever advancing time.
    """
    result = build().advance(42.0)
    assert (result.arrived_at, result.consumed) == (None, 42.0)


# ---------------------------------------------------------------------------
# AdvanceResult
# ---------------------------------------------------------------------------

def test_advance_result_is_immutable() -> None:
    result = AdvanceResult("stop_B", 10.0)
    with pytest.raises(Exception):
        result.consumed = 5.0  # type: ignore[misc]


def test_advance_result_fields() -> None:
    result = AdvanceResult("stop_B", 10.0)
    assert (result.arrived_at, result.consumed) == ("stop_B", 10.0)
