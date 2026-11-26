"""Hermetic tests for the non-production local SQLite reservation prototype."""
import sqlite3
from contextlib import closing

import pytest

from personal_algorithm.locked_snapshot import LockSnapshotError, locked_snapshot


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
