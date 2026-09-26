"""SQLite event and recommendation store."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
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


def _restore_snapshot(database: Path, snapshot: Path | None) -> None:
    if snapshot is None or not snapshot.exists():
        return
    database.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(snapshot, database)


class _DurableConnection:
    def __init__(self, connection: sqlite3.Connection, snapshot: Path | None) -> None:
        self._connection = connection
        self._snapshot = snapshot

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def commit(self) -> None:
        self._connection.commit()
        if self._snapshot is not None:
            self._write_snapshot()

    def rollback(self) -> None:
        self._connection.rollback()

    def _write_snapshot(self) -> None:
        snapshot = self._snapshot
        assert snapshot is not None
        snapshot.parent.mkdir(parents=True, exist_ok=True)

        fd, local_name = tempfile.mkstemp(prefix="personal-algorithm-", suffix=".sqlite3")
        os.close(fd)
        local_copy = Path(local_name)
        remote_tmp = snapshot.with_suffix(snapshot.suffix + ".tmp")
        try:
            backup = sqlite3.connect(str(local_copy))
            try:
                self._connection.backup(backup)
                backup.commit()
            finally:
                backup.close()
            shutil.copyfile(local_copy, remote_tmp)
            os.replace(remote_tmp, snapshot)
        finally:
            local_copy.unlink(missing_ok=True)
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
        self.connection.execute(
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
            (
                event.id,
                event.source,
                event.event_type,
                event.occurred_at.isoformat() if event.occurred_at else None,
                event.subject,
                event.canonical_url,
                json.dumps(event.payload, sort_keys=True),
                json.dumps(event.provenance, sort_keys=True),
            ),
        )
        self.connection.commit()

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
