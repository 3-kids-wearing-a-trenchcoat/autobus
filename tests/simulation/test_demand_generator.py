"""Tests for GenerationPolicy implementations and DemandGenerator."""

import pytest

from src.algorithm.RequestPool import RequestPool
from src.map.map_data import RoadEdgeRecord, StopRecord
from src.simulation.DemandGenerator import (
    DemandGenerator,
    PeriodicPolicy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_edge(edge_id: str) -> RoadEdgeRecord:
    """Build a minimal RoadEdgeRecord."""
    return RoadEdgeRecord(
        id=edge_id, from_node_id="n0", to_node_id="n1", length_m=100.0
    )


def make_stop(stop_id: str) -> StopRecord:
    """Build a minimal StopRecord."""
    return StopRecord(
        id=stop_id, x=0.0, y=0.0,
        edge=make_edge(f"e_{stop_id}"), pos_on_edge=0.5
    )


def make_stops(n: int) -> list[StopRecord]:
    """Build a list of n distinct stops."""
    return [make_stop(f"s{i}") for i in range(n)]


def make_generator(
    n_stops: int = 3,
    interval_s: float = 10.0,
    max_requests: int = 5,
    weights: dict | None = None,
) -> tuple[DemandGenerator, RequestPool]:
    pool = RequestPool()
    policy = PeriodicPolicy(interval_s=interval_s, max_requests=max_requests)
    gen = DemandGenerator(
        pool=pool,
        policy=policy,
        stops=make_stops(n_stops),
        weights=weights,
    )
    return gen, pool


# ---------------------------------------------------------------------------
# PeriodicPolicy
# ---------------------------------------------------------------------------

def test_periodic_policy_fires_on_first_tick() -> None:
    policy = PeriodicPolicy(interval_s=10.0, max_requests=5)
    assert policy.should_generate(0.0) == 1


def test_periodic_policy_does_not_fire_before_interval() -> None:
    policy = PeriodicPolicy(interval_s=10.0, max_requests=5)
    policy.should_generate(0.0)   # first fire
    assert policy.should_generate(5.0) == 0


def test_periodic_policy_fires_after_interval() -> None:
    policy = PeriodicPolicy(interval_s=10.0, max_requests=5)
    policy.should_generate(0.0)
    assert policy.should_generate(10.0) == 1


def test_periodic_policy_respects_max_requests() -> None:
    policy = PeriodicPolicy(interval_s=10.0, max_requests=3)
    for tick in range(3):
        assert policy.should_generate(float(tick * 10)) == 1
    assert policy.should_generate(30.0) == 0


def test_periodic_policy_exactly_max_requests_generated() -> None:
    policy = PeriodicPolicy(interval_s=5.0, max_requests=2)
    results = [policy.should_generate(float(i * 5)) for i in range(5)]
    assert sum(results) == 2


# ---------------------------------------------------------------------------
# DemandGenerator construction
# ---------------------------------------------------------------------------

def test_constructor_raises_with_fewer_than_two_stops() -> None:
    pool = RequestPool()
    policy = PeriodicPolicy(interval_s=10.0, max_requests=5)
    with pytest.raises(ValueError):
        DemandGenerator(pool=pool, policy=policy, stops=make_stops(1))


def test_constructor_raises_with_zero_stops() -> None:
    pool = RequestPool()
    policy = PeriodicPolicy(interval_s=10.0, max_requests=5)
    with pytest.raises(ValueError):
        DemandGenerator(pool=pool, policy=policy, stops=[])


# ---------------------------------------------------------------------------
# DemandGenerator.update
# ---------------------------------------------------------------------------

def test_update_does_nothing_when_policy_returns_zero() -> None:
    gen, pool = make_generator(interval_s=10.0)
    gen.update(0.0)    # fires
    gen.update(5.0)    # too soon — policy returns 0
    assert len(pool) == 1


def test_update_adds_request_when_policy_fires() -> None:
    gen, pool = make_generator(interval_s=10.0, max_requests=3)
    gen.update(0.0)
    assert len(pool) == 1
    gen.update(10.0)
    assert len(pool) == 2


def test_update_adds_all_requests_up_to_max() -> None:
    gen, pool = make_generator(interval_s=10.0, max_requests=4)
    for i in range(4):
        gen.update(float(i * 10))
    assert len(pool) == 4
    gen.update(40.0)   # policy exhausted
    assert len(pool) == 4


def test_request_ids_are_unique() -> None:
    gen, pool = make_generator(interval_s=10.0, max_requests=5)
    for i in range(5):
        gen.update(float(i * 10))
    ids = [r.id for r in pool.get_all()]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Stop selection
# ---------------------------------------------------------------------------

def test_pickup_and_dropoff_always_differ() -> None:
    gen, pool = make_generator(n_stops=2, interval_s=1.0, max_requests=20)
    for i in range(20):
        gen.update(float(i))
    for r in pool.get_all():
        assert r.pickup_stop_id != r.dropoff_stop_id


def test_stops_are_drawn_from_available_set() -> None:
    stops = make_stops(3)
    stop_ids = {s.id for s in stops}
    pool = RequestPool()
    policy = PeriodicPolicy(interval_s=1.0, max_requests=10)
    gen = DemandGenerator(pool=pool, policy=policy, stops=stops)
    for i in range(10):
        gen.update(float(i))
    for r in pool.get_all():
        assert r.pickup_stop_id in stop_ids
        assert r.dropoff_stop_id in stop_ids


# ---------------------------------------------------------------------------
# Biased (weighted) mode — smoke test
# ---------------------------------------------------------------------------

def test_biased_mode_produces_valid_requests() -> None:
    stops = make_stops(3)
    weights = {stops[0].id: 10.0, stops[1].id: 1.0, stops[2].id: 1.0}
    pool = RequestPool()
    policy = PeriodicPolicy(interval_s=1.0, max_requests=10)
    gen = DemandGenerator(
        pool=pool, policy=policy, stops=stops, weights=weights
    )
    for i in range(10):
        gen.update(float(i))
    assert len(pool) == 10
    for r in pool.get_all():
        assert r.pickup_stop_id != r.dropoff_stop_id


# ---------------------------------------------------------------------------
# created_at uses simulation time
# ---------------------------------------------------------------------------

def test_request_created_at_matches_sim_time() -> None:
    gen, pool = make_generator(interval_s=10.0, max_requests=1)
    gen.update(sim_time=100.0)
    request = pool.get_all()[0]
    assert request.created_at == 100.0
