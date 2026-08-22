"""Tests for RequestPool and related data model."""

import pytest

from src.algorithm.RequestPool import (
    Request,
    RequestPool,
    RequestState,
)


def make_request(id: str = "r1") -> Request:
    """Helper: build a minimal PENDING request."""
    return Request(id=id, pickup_stop_id="A", dropoff_stop_id="B",
                   created_at=0.0)


# ---------------------------------------------------------------------------
# Request defaults
# ---------------------------------------------------------------------------

def test_request_defaults_to_pending() -> None:
    r = make_request()
    assert r.state is RequestState.PENDING


def test_request_created_at_is_set() -> None:
    r = Request(id="r1", pickup_stop_id="A", dropoff_stop_id="B",
                created_at=42.5)
    assert r.created_at == 42.5


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


# ---------------------------------------------------------------------------
# Request timestamp defaults
# ---------------------------------------------------------------------------

def test_request_boarded_at_defaults_to_none() -> None:
    r = make_request()
    assert r.boarded_at is None


def test_request_fulfilled_at_defaults_to_none() -> None:
    r = make_request()
    assert r.fulfilled_at is None


# ---------------------------------------------------------------------------
# get_fulfilled / get_cancelled
# ---------------------------------------------------------------------------

def test_get_fulfilled_returns_fulfilled_requests() -> None:
    pool = RequestPool()
    pool.add(make_request("r1"))
    pool.add(make_request("r2"))
    pool.update_state("r1", RequestState.FULFILLED)

    fulfilled_ids = {r.id for r in pool.get_fulfilled()}
    assert fulfilled_ids == {"r1"}


def test_get_fulfilled_excludes_other_states() -> None:
    pool = RequestPool()
    pool.add(make_request("pending"))
    pool.add(make_request("on_bus"))
    pool.add(make_request("cancelled"))
    pool.update_state("on_bus", RequestState.ON_BUS)
    pool.update_state("cancelled", RequestState.CANCELLED)

    assert pool.get_fulfilled() == []


def test_get_fulfilled_empty_when_none_fulfilled() -> None:
    pool = RequestPool()
    assert pool.get_fulfilled() == []


def test_get_cancelled_returns_cancelled_requests() -> None:
    pool = RequestPool()
    pool.add(make_request("r1"))
    pool.add(make_request("r2"))
    pool.cancel("r1")

    cancelled_ids = {r.id for r in pool.get_cancelled()}
    assert cancelled_ids == {"r1"}


def test_get_cancelled_excludes_other_states() -> None:
    pool = RequestPool()
    pool.add(make_request("pending"))
    pool.add(make_request("on_bus"))
    pool.add(make_request("fulfilled"))
    pool.update_state("on_bus", RequestState.ON_BUS)
    pool.update_state("fulfilled", RequestState.FULFILLED)

    assert pool.get_cancelled() == []


def test_get_cancelled_empty_when_none_cancelled() -> None:
    pool = RequestPool()
    assert pool.get_cancelled() == []


# ---------------------------------------------------------------------------
# State index consistency and ordering
# ---------------------------------------------------------------------------

def test_index_follows_transitions() -> None:
    """A request moves between state buckets as it transitions."""
    pool = RequestPool()
    pool.add(make_request("r1"))
    assert [r.id for r in pool.get_pending()] == ["r1"]

    pool.update_state("r1", RequestState.ON_BUS)
    assert pool.get_pending() == []
    assert [r.id for r in pool.get_on_bus()] == ["r1"]
    assert len(pool) == 1

    pool.cancel("r1")
    assert pool.get_on_bus() == []
    assert [r.id for r in pool.get_cancelled()] == ["r1"]
    assert len(pool) == 0


def test_get_pending_preserves_add_order() -> None:
    """get_pending returns requests in add-order, even after some board.

    This order drives BusAgent's capacity-limited boarding tie-break, so it
    must not change.
    """
    pool = RequestPool()
    for rid in ("r1", "r2", "r3"):
        pool.add(make_request(rid))
    assert [r.id for r in pool.get_pending()] == ["r1", "r2", "r3"]

    pool.update_state("r2", RequestState.ON_BUS)
    assert [r.id for r in pool.get_pending()] == ["r1", "r3"]


def test_update_state_noop_leaves_index_intact() -> None:
    """Transitioning to the current state is a harmless no-op."""
    pool = RequestPool()
    pool.add(make_request("r1"))
    pool.update_state("r1", RequestState.PENDING)
    assert [r.id for r in pool.get_pending()] == ["r1"]
    assert len(pool) == 1
