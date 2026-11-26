"""SQLite event and recommendation store."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import weakref
from pathlib import Path
from typing import Any

from .leased_snapshot import LeasedSnapshot, SnapshotUnavailable
from .models import Candidate, Interaction, PersonalEvent, RankedCandidate

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    content_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS personal_events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT,
    subject TEXT,
    canonical_url TEXT,
    payload_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_personal_events_source_type
ON personal_events(source, event_type);

CREATE INDEX IF NOT EXISTS idx_personal_events_occurred_at
ON personal_events(occurred_at);

CREATE TABLE IF NOT EXISTS rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    score REAL NOT NULL,
    ranker TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    ranked_at TEXT NOT NULL,
    explanation_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    kind TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    explicit INTEGER NOT NULL,
    metadata_json TEXT NOT NULL
);
"""


def _initialize_schema(connection: sqlite3.Connection, *, attempts: int = 8) -> None:
    connection.execute("PRAGMA busy_timeout = 30000")
    for attempt in range(attempts):
        try:
            connection.executescript(SCHEMA)
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(min(0.25 * (attempt + 1), 2.0))


def _restore_snapshot(database: Path, snapshot: Path | None) -> None:
    if snapshot is None or not snapshot.exists():
        return
    database.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(snapshot, database)


def _restore_leased_snapshot(database: Path, snapshot: LeasedSnapshot) -> None:
    """Restore only into an empty local path, preserving any recovery evidence."""
    database.parent.mkdir(parents=True, exist_ok=True)
    if any(Path(str(database) + suffix).exists() for suffix in ("", "-wal", "-shm", "-journal")):
        raise SnapshotUnavailable("local database or journal already exists; recovery required")
    fd, name = tempfile.mkstemp(prefix=".restore-", suffix=".sqlite3", dir=database.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as output:
            snapshot.download(output)
        check = sqlite3.connect(f"{staged.resolve().as_uri()}?mode=ro", uri=True)
        try:
            if check.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise SnapshotUnavailable("snapshot integrity check failed")
            tables = {row[0] for row in check.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
            if not {"candidates", "personal_events", "rankings", "interactions"} <= tables:
                raise SnapshotUnavailable("snapshot is missing required tables")
        finally:
            check.close()
        snapshot.verify()
        # Hard-link creation fails if another file appeared at the target.
        os.link(staged, database)
    finally:
        staged.unlink(missing_ok=True)


class _FencedCursor:
    """Do not return buffered SQLite results after observing lease loss."""

    def __init__(self, cursor: sqlite3.Cursor, connection: _DurableConnection) -> None:
        self._cursor = cursor
        self._connection = connection

    def _fetch(self, name: str, *args):
        with self._connection._lock:
            self._connection._check()
            result = getattr(self._cursor, name)(*args)
            self._connection._check()
            return result

    def fetchone(self):
        return self._fetch("fetchone")

    def fetchmany(self, *args):
        return self._fetch("fetchmany", *args)

    def fetchall(self):
        return self._fetch("fetchall")

    def close(self) -> None:
        with self._connection._lock:
            self._cursor.close()

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row

    @property
    def rowcount(self):
        with self._connection._lock:
            self._connection._check()
            return self._cursor.rowcount


class _DurableConnection:
    def __init__(self, connection: sqlite3.Connection, snapshot: Path | None,
                 leased_snapshot: LeasedSnapshot | None = None) -> None:
        self._connection = connection
        self._snapshot = snapshot
        self._leased_snapshot = leased_snapshot
        self._failed = False
        self._lock = threading.RLock()
        self._cursors: weakref.WeakSet[_FencedCursor] = weakref.WeakSet()

    def __getattr__(self, name: str) -> Any:
        if self._leased_snapshot is not None and name != "in_transaction":
            raise AttributeError(f"{name} is not available on a fenced connection")
        return getattr(self._connection, name)

    def _check(self) -> None:
        if self._failed:
            raise SnapshotUnavailable("local state requires recovery")
        if self._leased_snapshot is not None:
            self._leased_snapshot.verify()

    def execute(self, *args, **kwargs):
        with self._lock:
            self._check()
            cursor = self._connection.execute(*args, **kwargs)
            if self._leased_snapshot is not None:
                self._check()
                fenced = _FencedCursor(cursor, self)
                self._cursors.add(fenced)
                return fenced
            return cursor

    def executemany(self, *args, **kwargs):
        with self._lock:
            self._check()
            cursor = self._connection.executemany(*args, **kwargs)
            if self._leased_snapshot is not None:
                self._check()
                fenced = _FencedCursor(cursor, self)
                self._cursors.add(fenced)
                return fenced
            return cursor

    def executescript(self, *args, **kwargs):
        with self._lock:
            if self._leased_snapshot is not None:
                raise SnapshotUnavailable("executescript is unsafe on a fenced connection")
            return self._connection.executescript(*args, **kwargs)

    def commit(self) -> None:
        with self._lock:
            self._check()
            try:
                self._connection.commit()
                if self._snapshot is not None or self._leased_snapshot is not None:
                    self._write_snapshot()
            except Exception:
                if self._leased_snapshot is not None:
                    self._failed = True
                raise

    def rollback(self) -> None:
        with self._lock:
            self._connection.rollback()

    def finish_request(self) -> None:
        """Reject an unfinished transaction, retaining committed recovery data."""
        with self._lock:
            if self._connection.in_transaction:
                self._connection.rollback()
                raise SnapshotUnavailable("request ended with an unfinished transaction")

    def close(self) -> None:
        with self._lock:
            for cursor in list(self._cursors):
                cursor.close()
            self._connection.close()
            if self._leased_snapshot is not None:
                self._leased_snapshot.close()

    def _write_snapshot(self) -> None:
        snapshot = self._snapshot
        if snapshot is not None:
            snapshot.parent.mkdir(parents=True, exist_ok=True)

        fd, local_name = tempfile.mkstemp(prefix="personal-algorithm-", suffix=".sqlite3")
        os.close(fd)
        local_copy = Path(local_name)
        remote_tmp = None
        try:
            backup = sqlite3.connect(str(local_copy))
            try:
                self._connection.backup(backup)
                backup.commit()
            finally:
                backup.close()
            if self._leased_snapshot is not None:
                self._leased_snapshot.publish(local_copy)
                return
            assert snapshot is not None
            remote_fd, remote_name = tempfile.mkstemp(
                prefix=f".{snapshot.name}.", suffix=".tmp", dir=snapshot.parent
            )
            remote_tmp = Path(remote_name)
            with os.fdopen(remote_fd, "wb") as destination, local_copy.open("rb") as source:
                shutil.copyfileobj(source, destination)
            os.replace(remote_tmp, snapshot)
        finally:
            try:
                local_copy.unlink(missing_ok=True)
            finally:
                if remote_tmp is not None:
                    remote_tmp.unlink(missing_ok=True)


class Store:
    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        snapshot_path: str | Path | None = None,
        leased_snapshot: LeasedSnapshot | None = None,
    ) -> None:
        database_path = Path(path) if str(path) != ":memory:" else None
        snapshot = Path(snapshot_path) if snapshot_path else None
        if leased_snapshot is not None:
            if database_path is None or snapshot is not None:
                raise ValueError("fenced snapshots require a local path and no file snapshot")
            leased_snapshot.start()
            try:
                _restore_leased_snapshot(database_path, leased_snapshot)
            except Exception:
                leased_snapshot.close()
                raise
        elif database_path is not None:
            _restore_snapshot(database_path, snapshot)
        try:
            raw_connection = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
            try:
                _initialize_schema(raw_connection)
            except Exception:
                raw_connection.close()
                raise
        except Exception:
            if leased_snapshot is not None:
                leased_snapshot.close()
            raise
        self.connection = _DurableConnection(raw_connection, snapshot, leased_snapshot)

    def put_candidate(self, candidate: Candidate) -> None:
        payload = {
            "author": candidate.author,
            "summary": candidate.summary,
            "published_at": candidate.published_at.isoformat() if candidate.published_at else None,
            "topics": candidate.topics,
            "metadata": candidate.metadata,
            "provenance": candidate.provenance,
        }
        self.connection.execute(
            """INSERT INTO candidates
               (id, source, source_id, canonical_url, title, discovered_at, content_type, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 canonical_url=excluded.canonical_url,
                 title=excluded.title,
                 discovered_at=excluded.discovered_at,
                 content_type=excluded.content_type,
                 payload_json=excluded.payload_json""",
            (
                candidate.id,
                candidate.source,
                candidate.source_id,
                candidate.canonical_url,
                candidate.title,
                candidate.discovered_at.isoformat(),
                candidate.content_type,
                json.dumps(payload, sort_keys=True),
            ),
        )
        self.connection.commit()

    def put_personal_event(self, event: PersonalEvent) -> None:
        self.put_personal_events((event,))

    def put_personal_events(self, events) -> int:
        rows = [
            (
                event.id,
                event.source,
                event.event_type,
                event.occurred_at.isoformat() if event.occurred_at else None,
                event.subject,
                event.canonical_url,
                json.dumps(event.payload, sort_keys=True),
                json.dumps(event.provenance, sort_keys=True),
            )
            for event in events
        ]
        if not rows:
            return 0
        self.connection.executemany(
            """INSERT INTO personal_events
               (id, source, event_type, occurred_at, subject, canonical_url,
                payload_json, provenance_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 source=excluded.source,
                 event_type=excluded.event_type,
                 occurred_at=excluded.occurred_at,
                 subject=excluded.subject,
                 canonical_url=excluded.canonical_url,
                 payload_json=excluded.payload_json,
                 provenance_json=excluded.provenance_json""",
            rows,
        )
        self.connection.commit()
        return len(rows)

    def merge_staged_personal_events(self, path: Path, count: int) -> int:
        """Commit one fully validated disk-backed batch in one SQL statement."""
        if count == 0:
            return 0
        deadline = time.monotonic() + 30
        connection = self.connection
        while True:
            with connection._lock:
                if not connection.in_transaction:
                    return self._merge_staged_locked(path, count)
            if time.monotonic() >= deadline:
                raise TimeoutError("database remained busy before import")
            time.sleep(0.01)

    def _merge_staged_locked(self, path: Path, count: int) -> int:
        connection = self.connection
        connection.execute("ATTACH DATABASE ? AS spotify_import_stage", (str(path),))
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO personal_events
                   (id, source, event_type, occurred_at, subject, canonical_url,
                    payload_json, provenance_json)
                   SELECT id, source, event_type, occurred_at, subject, canonical_url,
                          payload_json, provenance_json
                   FROM spotify_import_stage.events WHERE 1 ORDER BY rowid
                   ON CONFLICT(id) DO UPDATE SET
                     source=excluded.source, event_type=excluded.event_type,
                     occurred_at=excluded.occurred_at, subject=excluded.subject,
                     canonical_url=excluded.canonical_url, payload_json=excluded.payload_json,
                     provenance_json=excluded.provenance_json"""
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("DETACH DATABASE spotify_import_stage")
        return count

    def count_personal_events(self) -> int:
        return int(
            self.connection.execute("SELECT COUNT(*) FROM personal_events").fetchone()[0]
        )

    def put_ranking(self, ranked: RankedCandidate) -> None:
        explanation = {
            "final_score": ranked.explanation.final_score,
            "contributions": [
                {
                    "feature": c.feature,
                    "value": c.value,
                    "weight": c.weight,
                    "contribution": c.contribution,
                    "reason": c.reason,
                }
                for c in ranked.explanation.contributions
            ],
        }
        self.connection.execute(
            """INSERT INTO rankings
               (candidate_id, score, ranker, policy_version, ranked_at, explanation_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                ranked.candidate.id,
                ranked.score,
                ranked.ranker,
                ranked.policy_version,
                ranked.ranked_at.isoformat(),
                json.dumps(explanation, sort_keys=True),
            ),
        )
        self.connection.commit()

    def put_interaction(self, interaction: Interaction) -> None:
        self.connection.execute(
            """INSERT INTO interactions
               (candidate_id, kind, occurred_at, explicit, metadata_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                interaction.candidate_id,
                interaction.kind.value,
                interaction.occurred_at.isoformat(),
                int(interaction.explicit),
                json.dumps(interaction.metadata, sort_keys=True),
            ),
        )
        self.connection.commit()
