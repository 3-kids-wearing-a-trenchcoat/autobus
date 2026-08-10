"""
The meta-test that makes ``DispatchPolicy.params()`` trustworthy.

``params()`` feeds the results store's identity hash, so a knob a policy
accepts but does not declare makes two differently configured runs collide
under one identity. That is data corruption rather than a missing feature, and
it is the kind nobody could later pinpoint: the store would report the second
run as already recorded and quietly skip it.

Rather than trust eleven policies to stay in step by hand, this walks every
concrete policy in ``src.algorithm`` and compares what each constructor
accepts against what it declares. Discovery is dynamic on purpose — the
failure being guarded against is a *new* policy, and a hand-written list would
not contain it.
"""

import importlib
import inspect
import json
import pkgutil

import pytest

import src.algorithm
from src.algorithm.DispatchPolicy import BusState, DispatchPolicy
from src.map.PlanarMapProvider import PlanarMapProvider
from src.map.TransitGraph import TransitGraph
from src.map.graph import PlanarStreetGraph

# Not knobs: every policy takes the graph, and the map is identified
# separately as part of a run's world.
_NOT_A_KNOB = {"self", "graph"}


def _concrete_policies() -> list[type[DispatchPolicy]]:
    """
    Find every instantiable DispatchPolicy under src.algorithm.
    :return: The policy classes, sorted by name so failures read stably.
    """
    found: dict[str, type[DispatchPolicy]] = {}
    for module in pkgutil.iter_modules(src.algorithm.__path__):
        imported = importlib.import_module(f"src.algorithm.{module.name}")
        for _, obj in inspect.getmembers(imported, inspect.isclass):
            if (issubclass(obj, DispatchPolicy)
                    and not inspect.isabstract(obj)
                    and obj.__module__.startswith("src.algorithm")):
                found[obj.__name__] = obj
    return [found[name] for name in sorted(found)]


_POLICIES = _concrete_policies()


@pytest.fixture(scope="module")
def graph() -> TransitGraph:
    """A small transit graph every policy can be constructed against."""
    street = PlanarStreetGraph.generate(
        n_seeds=12, bounds=(0.0, 0.0, 1000.0, 1000.0), seed=3
    )
    return TransitGraph(PlanarMapProvider(street))


def test_discovery_found_the_policies() -> None:
    """
    Guard the guard: a broken walk would make every test below vacuous.
    """
    names = {cls.__name__ for cls in _POLICIES}
    assert {"FixedRoutePolicy", "WindowInsertionPolicy",
            "ReorderingInsertionPolicy"} <= names
    assert len(_POLICIES) >= 10


@pytest.mark.parametrize(
    "policy_class", _POLICIES, ids=lambda cls: cls.__name__
)
def test_every_constructor_knob_is_declared(
    policy_class: type[DispatchPolicy], graph: TransitGraph
) -> None:
    """Anything that changes behaviour has to reach the identity hash."""
    parameters = inspect.signature(policy_class.__init__).parameters
    knobs = {
        name for name, parameter in parameters.items()
        if name not in _NOT_A_KNOB
        and parameter.kind not in (
            parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD
        )
    }
    declared = set(policy_class(graph).params())

    assert knobs <= declared, (
        f"{policy_class.__name__} accepts {sorted(knobs - declared)} but "
        f"does not declare it in params(). Two runs differing only in that "
        f"argument would share one identity in the results store, and the "
        f"second would be silently skipped as already recorded."
    )


@pytest.mark.parametrize(
    "policy_class", _POLICIES, ids=lambda cls: cls.__name__
)
def test_declared_params_survive_json(
    policy_class: type[DispatchPolicy], graph: TransitGraph
) -> None:
    """
    The identity is hashed from canonical JSON, so a value that cannot be
    serialised would fail at the end of a trial rather than at its start.
    """
    params = policy_class(graph).params()
    assert json.loads(json.dumps(params, sort_keys=True)) == params


@pytest.mark.parametrize(
    "policy_class", _POLICIES, ids=lambda cls: cls.__name__
)
def test_a_subclass_declares_at_least_its_parent(
    policy_class: type[DispatchPolicy], graph: TransitGraph
) -> None:
    """
    Subclasses must extend ``params()`` rather than replace it — dropping an
    inherited knob would collide a subclass's runs with each other.
    """
    parents = [
        base for base in policy_class.__bases__
        if base in _POLICIES
    ]
    declared = set(policy_class(graph).params())
    for parent in parents:
        assert set(parent(graph).params()) <= declared, (
            f"{policy_class.__name__} drops parameters inherited from "
            f"{parent.__name__} instead of extending them"
        )


def test_params_is_required_of_a_new_policy() -> None:
    """
    The whole reason it is abstract: forgetting it must fail loudly at
    construction, not silently three weeks into a sweep.
    """
    class _Forgetful(DispatchPolicy):
        """A policy that implements plan() and nothing else."""

        def plan(self, state: BusState, n_stops: int) -> list[str]:
            """Return an empty route."""
            return []

    with pytest.raises(TypeError, match="params"):
        _Forgetful(graph=None)  # type: ignore[arg-type]
