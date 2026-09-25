import sqlite3

import personal_algorithm.store as store_module


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
