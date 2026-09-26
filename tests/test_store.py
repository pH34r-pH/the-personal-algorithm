import sqlite3
from datetime import UTC, datetime

import personal_algorithm.store as store_module
from personal_algorithm.models import PersonalEvent


class LockedThenReady:
    def __init__(self) -> None:
        self.calls = 0
        self.busy_timeout = None

    def execute(self, sql: str):
        self.busy_timeout = sql
        return self

    def executescript(self, _sql: str):
        self.calls += 1
        if self.calls < 3:
            raise sqlite3.OperationalError("database is locked")
        return self


def test_initialize_schema_retries_transient_lock(monkeypatch):
    connection = LockedThenReady()
    sleeps = []
    monkeypatch.setattr(store_module.time, "sleep", sleeps.append)

    store_module._initialize_schema(connection, attempts=3)

    assert connection.calls == 3
    assert connection.busy_timeout == "PRAGMA busy_timeout = 30000"
    assert sleeps == [0.25, 0.5]


def test_initialize_schema_does_not_hide_other_sqlite_errors(monkeypatch):
    class Broken(LockedThenReady):
        def executescript(self, _sql: str):
            raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store_module.time, "sleep", lambda _: None)

    try:
        store_module._initialize_schema(Broken(), attempts=3)
    except sqlite3.OperationalError as exc:
        assert "disk I/O error" in str(exc)
    else:
        raise AssertionError("expected OperationalError")


def test_commit_writes_atomic_snapshot_and_restore_uses_it(tmp_path):
    live = tmp_path / "live.sqlite3"
    snapshot = tmp_path / "persistent" / "state.sqlite3"
    first = store_module.Store(live, snapshot_path=snapshot)
    first.connection.execute("CREATE TABLE durable_probe (value TEXT NOT NULL)")
    first.connection.execute("INSERT INTO durable_probe(value) VALUES ('kept')")
    first.connection.commit()

    assert snapshot.exists()

    restored = store_module.Store(tmp_path / "restored.sqlite3", snapshot_path=snapshot)
    row = restored.connection.execute("SELECT value FROM durable_probe").fetchone()
    assert row == ("kept",)



def test_personal_events_are_upserted_without_becoming_candidates():
    store = store_module.Store()
    event = PersonalEvent(
        id="spotify:stream:1",
        source="spotify",
        event_type="stream",
        occurred_at=datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
        subject="Example Track",
        canonical_url="spotify:track:example",
        payload={"ms_played": 12345},
        provenance={"archive_sha256": "abc", "member": "history.json", "index": 1},
    )

    store.put_personal_event(event)
    store.put_personal_event(event)

    assert store.count_personal_events() == 1
    assert store.connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
    row = store.connection.execute(
        "SELECT source, event_type, subject FROM personal_events WHERE id = ?",
        (event.id,),
    ).fetchone()
    assert row == ("spotify", "stream", "Example Track")
