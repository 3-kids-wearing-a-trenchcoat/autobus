"""Tests for RequestPool and related data model."""

import pytest

from src.algorithm.RequestPool import (
    Request,
    RequestPool,
    RequestState,
)


def make_request(id: str = "r1") -> Request:
    """Helper: build a minimal PENDING request."""
    return Request(id=id, pickup_stop_id="A", dropoff_stop_id="B")


# ---------------------------------------------------------------------------
# Request defaults
# ---------------------------------------------------------------------------

def test_request_defaults_to_pending() -> None:
    r = make_request()
    assert r.state is RequestState.PENDING


def test_request_created_at_is_set() -> None:
    r = make_request()
    assert r.created_at > 0


# ---------------------------------------------------------------------------
# add / get
# ---------------------------------------------------------------------------

def test_add_and_get() -> None:
    pool = RequestPool()
    r = make_request("r1")
    pool.add(r)
    assert pool.get("r1") is r


def test_add_duplicate_raises() -> None:
    pool = RequestPool()
    pool.add(make_request("r1"))
    with pytest.raises(ValueError):
        pool.add(make_request("r1"))


def test_get_missing_raises() -> None:
    pool = RequestPool()
    with pytest.raises(KeyError):
        pool.get("nonexistent")


# ---------------------------------------------------------------------------
# Filtered queries
# ---------------------------------------------------------------------------

def test_get_all_returns_only_active() -> None:
    pool = RequestPool()
    pool.add(make_request("pending"))
    pool.add(make_request("on_bus"))
    pool.add(make_request("fulfilled"))
    pool.add(make_request("cancelled"))

    pool.update_state("on_bus", RequestState.ON_BUS)
    pool.update_state("fulfilled", RequestState.FULFILLED)
    pool.update_state("cancelled", RequestState.CANCELLED)

    active_ids = {r.id for r in pool.get_all()}
    assert active_ids == {"pending", "on_bus"}


def test_get_pending_filters_correctly() -> None:
    pool = RequestPool()
    pool.add(make_request("p1"))
    pool.add(make_request("p2"))
    pool.add(make_request("o1"))
    pool.update_state("o1", RequestState.ON_BUS)

    pending_ids = {r.id for r in pool.get_pending()}
    assert pending_ids == {"p1", "p2"}


def test_get_on_bus_filters_correctly() -> None:
    pool = RequestPool()
    pool.add(make_request("p1"))
    pool.add(make_request("o1"))
    pool.update_state("o1", RequestState.ON_BUS)

    on_bus_ids = {r.id for r in pool.get_on_bus()}
    assert on_bus_ids == {"o1"}


# ---------------------------------------------------------------------------
# update_state / cancel
# ---------------------------------------------------------------------------

def test_update_state_changes_state() -> None:
    pool = RequestPool()
    pool.add(make_request("r1"))
    pool.update_state("r1", RequestState.ON_BUS)
    assert pool.get("r1").state is RequestState.ON_BUS


def test_cancel_sets_cancelled_state() -> None:
    pool = RequestPool()
    pool.add(make_request("r1"))
    pool.cancel("r1")
    assert pool.get("r1").state is RequestState.CANCELLED


def test_cancelled_request_not_in_get_all() -> None:
    pool = RequestPool()
    pool.add(make_request("r1"))
    pool.cancel("r1")
    assert pool.get_all() == []


def test_update_state_missing_raises() -> None:
    pool = RequestPool()
    with pytest.raises(KeyError):
        pool.update_state("nonexistent", RequestState.ON_BUS)


# ---------------------------------------------------------------------------
# __len__
# ---------------------------------------------------------------------------

def test_len_counts_active_only() -> None:
    pool = RequestPool()
    assert len(pool) == 0

    pool.add(make_request("r1"))
    pool.add(make_request("r2"))
    assert len(pool) == 2

    pool.update_state("r1", RequestState.FULFILLED)
    assert len(pool) == 1

    pool.cancel("r2")
    assert len(pool) == 0
