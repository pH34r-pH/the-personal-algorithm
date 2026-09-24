from datetime import UTC, datetime, timedelta

import pytest

from personal_algorithm.oauth_state import DurableAuthorizationStateStore
from personal_algorithm.store import Store

NOW = datetime(2026, 9, 23, 20, tzinfo=UTC)


def test_state_survives_store_wrapper_recreation_and_is_one_time():
    store = Store()
    first = DurableAuthorizationStateStore(store, ttl=timedelta(minutes=10))
    state = first.create("https://example.invalid/callback", now=NOW)

    resumed = DurableAuthorizationStateStore(store, ttl=timedelta(minutes=10))
    pending = resumed.consume(state, now=NOW + timedelta(minutes=1))
    assert pending.redirect_uri == "https://example.invalid/callback"

    with pytest.raises(ValueError, match="already-consumed"):
        resumed.consume(state, now=NOW + timedelta(minutes=2))


def test_expired_state_is_rejected():
    store = DurableAuthorizationStateStore(Store(), ttl=timedelta(minutes=5))
    state = store.create("https://example.invalid/callback", now=NOW)

    with pytest.raises(ValueError, match="expired"):
        store.consume(state, now=NOW + timedelta(minutes=5))


def test_redirect_must_be_https():
    store = DurableAuthorizationStateStore(Store())
    with pytest.raises(ValueError, match="HTTPS"):
        store.create("http://example.invalid/callback", now=NOW)


def test_prune_removes_consumed_and_expired_rows():
    database = Store()
    states = DurableAuthorizationStateStore(database, ttl=timedelta(minutes=5))
    consumed = states.create("https://example.invalid/a", now=NOW)
    states.consume(consumed, now=NOW + timedelta(minutes=1))
    states.create("https://example.invalid/b", now=NOW)

    removed = states.prune(now=NOW + timedelta(minutes=6))
    assert removed == 2
    assert database.connection.execute("SELECT COUNT(*) FROM oauth_states").fetchone()[0] == 0
