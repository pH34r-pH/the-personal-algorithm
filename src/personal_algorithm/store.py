"""SQLite event and recommendation store."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

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


def _logical_database_digest(path: Path) -> bytes:
    """Compare SQLite contents without exposing records or creating a database."""
    digest = hashlib.sha256()
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=30.0)) as connection:
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise sqlite3.DatabaseError("database failed SQLite quick_check")
        for line in connection.iterdump():
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
    return digest.digest()


def _restore_snapshot(database: Path, snapshot: Path | None) -> None:
    if snapshot is None or not snapshot.exists():
        return
    if database.exists():
        if _logical_database_digest(database) != _logical_database_digest(snapshot):
            raise RuntimeError("local database and persistent snapshot differ; recovery required")
        return
    database.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(
        prefix=f".{database.name}.", suffix=".restore", dir=database.parent,
    )
    staged = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as destination, snapshot.open("rb") as source:
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        snapshot_digest = _logical_database_digest(staged)
        try:
            os.link(staged, database)
        except FileExistsError:
            if _logical_database_digest(database) != snapshot_digest:
                raise RuntimeError("local database and persistent snapshot differ; recovery required") from None
    finally:
        staged.unlink(missing_ok=True)


class _DurableConnection:
    def __init__(self, connection: sqlite3.Connection, snapshot: Path | None) -> None:
        self._connection = connection
        self._snapshot = snapshot
        self._lock = threading.RLock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def execute(self, *args, **kwargs):
        with self._lock:
            return self._connection.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        with self._lock:
            return self._connection.executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        with self._lock:
            return self._connection.executescript(*args, **kwargs)

    def commit(self) -> None:
        with self._lock:
            self._connection.commit()
            if self._snapshot is not None:
                self._write_snapshot()

    def rollback(self) -> None:
        with self._lock:
            self._connection.rollback()

    def _write_snapshot(self) -> None:
        snapshot = self._snapshot
        assert snapshot is not None
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
    ) -> None:
        database_path = Path(path) if str(path) != ":memory:" else None
        snapshot = Path(snapshot_path) if snapshot_path else None
        if database_path is not None:
            _restore_snapshot(database_path, snapshot)
        raw_connection = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
        _initialize_schema(raw_connection)
        self.connection = _DurableConnection(raw_connection, snapshot)

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
