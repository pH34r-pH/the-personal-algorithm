"""Hermetic tests for the non-production local SQLite reservation prototype."""
import sqlite3
import threading
from contextlib import closing

import pytest

from personal_algorithm.locked_snapshot import LockSnapshotError, locked_snapshot
from personal_algorithm.store import Store


def source_db(path):
    with closing(sqlite3.connect(path)) as conn:
        for table in ("candidates", "personal_events", "rankings", "interactions"):
            conn.execute(f"CREATE TABLE {table} (value INTEGER)")
        conn.execute("INSERT INTO candidates VALUES (1)")
        conn.commit()


def test_writer_is_blocked_and_latest_commit_is_captured(tmp_path):
    source = tmp_path / "live.sqlite3"
    source_db(source)
    # Model a local commit not yet published to a separate older snapshot.
    with closing(sqlite3.connect(source)) as writer:
        writer.execute("INSERT INTO candidates VALUES (2)")
        writer.commit()
    with locked_snapshot(source, staging_dir=tmp_path, timeout=1) as locked:
        locked.verify()
        with closing(sqlite3.connect(source, timeout=0.05)) as writer:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                writer.execute("INSERT INTO candidates VALUES (3)")
            writer.rollback()
        with closing(sqlite3.connect(locked.path)) as reader:
            assert reader.execute("SELECT count(*) FROM candidates").fetchone()[0] == 2
        staged = locked.path
        assert staged.exists()
    assert not staged.exists()
    with pytest.raises(LockSnapshotError):
        locked.verify()
    with closing(sqlite3.connect(source, timeout=1)) as writer:
        writer.execute("INSERT INTO candidates VALUES (3)")
        writer.commit()


def test_bad_snapshot_releases_lock_and_removes_staging(tmp_path):
    source = tmp_path / "incomplete.sqlite3"
    with closing(sqlite3.connect(source)) as conn:
        conn.execute("CREATE TABLE other(value INTEGER)")
        conn.commit()
    with pytest.raises(RuntimeError), locked_snapshot(source, staging_dir=tmp_path, timeout=1):
        pytest.fail("incomplete snapshot must not be yielded")
    assert not list(tmp_path.glob("tpa-locked-*"))
    with closing(sqlite3.connect(source, timeout=1)) as conn:
        conn.execute("INSERT INTO other VALUES (1)")
        conn.commit()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), 121])
def test_invalid_timeout_is_rejected_before_open(tmp_path, timeout):
    with pytest.raises(ValueError), locked_snapshot(
        tmp_path / "missing", staging_dir=tmp_path, timeout=timeout
    ):
        pass


def test_captures_store_commit_paused_before_file_snapshot(tmp_path, monkeypatch):
    live, snapshot = tmp_path / "live.sqlite3", tmp_path / "snapshot.sqlite3"
    store = Store(live, snapshot_path=snapshot)
    store.connection.execute("CREATE TABLE probe (value INTEGER)")
    store.connection.execute("INSERT INTO probe VALUES (1)")
    store.connection.commit()
    entered, resume = threading.Event(), threading.Event()
    original = store.connection._write_snapshot

    def paused_snapshot():
        entered.set()
        if not resume.wait(5):
            raise TimeoutError("test did not release paused snapshot")
        original()

    monkeypatch.setattr(store.connection, "_write_snapshot", paused_snapshot)
    errors = []

    def commit_second():
        try:
            store.connection.execute("INSERT INTO probe VALUES (2)")
            store.connection.commit()
        except Exception as exc:  # noqa: BLE001 - propagate background failure to the test
            errors.append(exc)

    worker = threading.Thread(target=commit_second)
    worker.start()
    try:
        assert entered.wait(5)
        with locked_snapshot(live, staging_dir=tmp_path, timeout=1) as locked:
            with closing(sqlite3.connect(snapshot)) as old, closing(sqlite3.connect(locked.path)) as copy:
                assert old.execute("SELECT count(*) FROM probe").fetchone()[0] == 1
                assert copy.execute("SELECT count(*) FROM probe").fetchone()[0] == 2
            locked.verify()
    finally:
        resume.set()
        worker.join(10)
        store.connection.close()
    assert not worker.is_alive()
    assert not errors
