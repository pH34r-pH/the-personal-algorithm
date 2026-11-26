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
import weakref
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .leased_snapshot import LeasedSnapshot, SnapshotUnavailable
from .models import (
    Candidate,
    Explanation,
    Impression,
    Interaction,
    PersonalEvent,
    RankedCandidate,
    ScoreContribution,
)
from .ranking_runs import RankingInput, RankingMember, RankingRun, replay_ranking_run
from .serialization import candidate_from_dict, ranked_to_dict
from .snapshot_validation import validate_snapshot

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

CREATE TABLE IF NOT EXISTS ranking_inputs (
    sha256 TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('candidate', 'policy')),
    content_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ranking_runs (
    id TEXT PRIMARY KEY,
    evaluated_at TEXT NOT NULL,
    ranker TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL REFERENCES ranking_inputs(sha256),
    context_version TEXT,
    context_sha256 TEXT,
    candidate_count INTEGER NOT NULL CHECK (candidate_count > 0)
);

CREATE TABLE IF NOT EXISTS ranking_run_items (
    run_id TEXT NOT NULL REFERENCES ranking_runs(id),
    position INTEGER NOT NULL CHECK (position > 0),
    ranking_id INTEGER NOT NULL UNIQUE REFERENCES rankings(id),
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    input_sha256 TEXT NOT NULL REFERENCES ranking_inputs(sha256),
    PRIMARY KEY (run_id, position),
    UNIQUE (run_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS impressions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES ranking_runs(id),
    presented_at TEXT NOT NULL,
    candidate_count INTEGER NOT NULL CHECK (candidate_count > 0)
);

CREATE TABLE IF NOT EXISTS impression_items (
    impression_id TEXT NOT NULL REFERENCES impressions(id),
    position INTEGER NOT NULL CHECK (position > 0),
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    PRIMARY KEY (impression_id, position),
    UNIQUE (impression_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS interaction_impressions (
    interaction_id INTEGER PRIMARY KEY REFERENCES interactions(id),
    impression_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    FOREIGN KEY (impression_id, candidate_id) REFERENCES impression_items(impression_id, candidate_id)
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
        validate_snapshot(staged)
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

    @property
    def lastrowid(self):
        with self._connection._lock:
            self._connection._check()
            return self._cursor.lastrowid


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
        if self._failed:
            raise SnapshotUnavailable("local state requires recovery")

    def check_available(self) -> None:
        """Check snapshot and local-failure state without waiting on a request."""
        self._check()

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

    @contextmanager
    def idle_lock(self, operation: str):
        """Hold the connection after any existing transaction has finished."""
        deadline = time.monotonic() + 30
        while True:
            with self._lock:
                self._check()
                if not self._connection.in_transaction:
                    yield
                    return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"database remained busy before {operation}")
            time.sleep(0.01)

    @contextmanager
    def ranking_transaction(self, *, write: bool):
        with self.idle_lock("ranking"):
            row_factory = self._connection.row_factory
            self._connection.row_factory = sqlite3.Row
            try:
                self.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield self
                if write:
                    self.commit()
                else:
                    self._check()
                    self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
            finally:
                self._connection.row_factory = row_factory

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
        self._upsert_candidate(self.connection, candidate)
        self.connection.commit()

    @staticmethod
    def _upsert_candidate(connection, candidate: Candidate) -> None:
        payload = {
            "author": candidate.author,
            "summary": candidate.summary,
            "published_at": candidate.published_at.isoformat() if candidate.published_at else None,
            "topics": candidate.topics,
            "metadata": candidate.metadata,
            "provenance": candidate.provenance,
        }
        connection.execute(
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
        with self.connection.idle_lock("import"):
            return self._merge_staged_locked(path, count)

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
        self._insert_ranking(self.connection, ranked)
        self.connection.commit()

    @staticmethod
    def _insert_ranking(connection, ranked: RankedCandidate) -> int:
        cursor = connection.execute(
            """INSERT INTO rankings
               (candidate_id, score, ranker, policy_version, ranked_at, explanation_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                ranked.candidate.id,
                ranked.score,
                ranked.ranker,
                ranked.policy_version,
                ranked.ranked_at.isoformat(),
                json.dumps(ranked_to_dict(ranked)["explanation"], sort_keys=True, allow_nan=False),
            ),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    @staticmethod
    def _insert_ranking_input(connection, snapshot: RankingInput) -> None:
        connection.execute(
            """INSERT INTO ranking_inputs (sha256, kind, content_json) VALUES (?, ?, ?)
               ON CONFLICT(sha256) DO NOTHING""",
            (snapshot.sha256, snapshot.kind, snapshot.content),
        )
        row = connection.execute(
            "SELECT kind, content_json FROM ranking_inputs WHERE sha256 = ?", (snapshot.sha256,),
        ).fetchone()
        if row is None or tuple(row) != (snapshot.kind, snapshot.content):
            raise ValueError("stored ranking input differs from its content-addressed snapshot")

    def put_ranking_run(self, run: RankingRun) -> None:
        observed = replay_ranking_run(run)
        with self.connection.ranking_transaction(write=True) as connection:
            self._insert_ranking_input(connection, run.policy)
            connection.execute(
                """INSERT INTO ranking_runs
                   (id, evaluated_at, ranker, policy_sha256, context_version, context_sha256, candidate_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run.id, run.evaluated_at.isoformat(), run.ranker, run.policy.sha256,
                 run.context_version, run.context_sha256, len(run.members)),
            )
            for position, (member, ranked) in enumerate(zip(run.members, observed, strict=True), 1):
                self._insert_ranking_input(connection, member.snapshot)
                self._upsert_candidate(connection, ranked.candidate)
                identifier = self._insert_ranking(connection, ranked)
                connection.execute(
                    """INSERT INTO ranking_run_items
                       (run_id, position, ranking_id, candidate_id, input_sha256)
                       VALUES (?, ?, ?, ?, ?)""",
                    (run.id, position, identifier, ranked.candidate.id, member.snapshot.sha256),
                )

    def get_ranking_run(self, identifier: str) -> RankingRun:
        with self.connection.ranking_transaction(write=False) as connection:
            header = connection.execute(
                """SELECT run.*, policy.kind, policy.content_json FROM ranking_runs AS run
                   LEFT JOIN ranking_inputs AS policy ON policy.sha256 = run.policy_sha256
                   WHERE run.id = ?""",
                (identifier,),
            ).fetchone()
            if header is None:
                raise KeyError(identifier)
            records = connection.execute(
                """SELECT item.position, item.input_sha256, item.candidate_id AS member_candidate_id,
                          input.kind, input.content_json, ranking.*
                   FROM ranking_run_items AS item
                   LEFT JOIN ranking_inputs AS input ON input.sha256 = item.input_sha256
                   LEFT JOIN rankings AS ranking ON ranking.id = item.ranking_id
                   WHERE item.run_id = ? ORDER BY item.position""",
                (identifier,),
            ).fetchall()
        _require_contiguous_positions(records, header["candidate_count"], "ranking run")
        run = RankingRun(
            identifier, datetime.fromisoformat(header["evaluated_at"]), header["ranker"],
            RankingInput(header["kind"], header["policy_sha256"], header["content_json"]),
            tuple(_ranking_member(row) for row in records),
            header["context_version"], header["context_sha256"],
        )
        replay_ranking_run(run)
        return run

    def record_impression(self, run_id: str, candidate_ids: tuple[str, ...], *,
                          presented_at: datetime, identifier: str | None = None) -> Impression:
        if presented_at.utcoffset() is None:
            raise ValueError("presentation time must include a timezone")
        if not candidate_ids or not all(isinstance(value, str) and value.strip() for value in candidate_ids):
            raise ValueError("a presentation requires an ordered nonempty candidate list")
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("a presentation cannot repeat a candidate")
        if identifier is not None and (not isinstance(identifier, str) or not identifier.strip()):
            raise ValueError("impression ID must be a nonempty string")
        run = self.get_ranking_run(run_id)
        if presented_at < run.evaluated_at:
            raise ValueError("presentation predates the ranking run")
        eligible = {member.ranked.candidate.id for member in run.members}
        if not set(candidate_ids) <= eligible:
            raise ValueError("presented candidates must belong to the ranking run")
        impression = Impression(identifier or uuid4().hex, run_id, presented_at.astimezone(UTC),
                                tuple(candidate_ids))
        with self.connection.ranking_transaction(write=True) as connection:
            previous = _read_impression(connection, impression.id)
            if previous is not None:
                if previous != impression:
                    raise ValueError("impression ID already names different presentation evidence")
                return previous
            connection.execute(
                """INSERT INTO impressions (id, run_id, presented_at, candidate_count)
                   VALUES (?, ?, ?, ?)""",
                (impression.id, run_id, impression.presented_at.isoformat(), len(candidate_ids)),
            )
            connection.executemany(
                "INSERT INTO impression_items (impression_id, position, candidate_id) VALUES (?, ?, ?)",
                [(impression.id, index, candidate) for index, candidate in enumerate(candidate_ids, 1)],
            )
        return impression

    def get_impression(self, identifier: str) -> Impression:
        with self.connection.ranking_transaction(write=False) as connection:
            impression = _read_impression(connection, identifier)
        if impression is None:
            raise KeyError(identifier)
        return impression

    def put_interaction(self, interaction: Interaction) -> None:
        if interaction.impression_id is None:
            self._insert_interaction(self.connection, interaction)
            self.connection.commit()
            return
        with self.connection.ranking_transaction(write=True) as connection:
            impression = _read_impression(connection, interaction.impression_id)
            if impression is None or interaction.candidate_id not in impression.candidate_ids:
                raise ValueError("interaction candidate was not presented in this impression")
            if interaction.occurred_at.utcoffset() is None or interaction.occurred_at < impression.presented_at:
                raise ValueError("interaction must follow the recorded presentation")
            identifier = self._insert_interaction(connection, interaction)
            connection.execute(
                """INSERT INTO interaction_impressions (interaction_id, impression_id, candidate_id)
                   VALUES (?, ?, ?)""",
                (identifier, impression.id, interaction.candidate_id),
            )

    @staticmethod
    def _insert_interaction(connection, interaction: Interaction) -> int:
        cursor = connection.execute(
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
        assert cursor.lastrowid is not None
        return cursor.lastrowid


def _require_contiguous_positions(rows, count: int, description: str) -> None:
    if (type(count) is not int or count <= 0 or len(rows) != count
            or any(row["position"] != index for index, row in enumerate(rows, 1))):
        raise ValueError(f"{description} membership is incomplete or out of order")


def _read_impression(connection, identifier: str) -> Impression | None:
    header = connection.execute("SELECT * FROM impressions WHERE id = ?", (identifier,)).fetchone()
    if header is None:
        return None
    rows = connection.execute(
        "SELECT position, candidate_id FROM impression_items WHERE impression_id = ? ORDER BY position",
        (identifier,),
    ).fetchall()
    _require_contiguous_positions(rows, header["candidate_count"], "impression")
    return Impression(
        identifier, header["run_id"], datetime.fromisoformat(header["presented_at"]),
        tuple(row["candidate_id"] for row in rows),
    )


def _ranking_member(row: sqlite3.Row) -> RankingMember:
    snapshot = RankingInput(row["kind"], row["input_sha256"], row["content_json"])
    candidate = candidate_from_dict(snapshot.load("candidate"))
    if not (candidate.id == row["member_candidate_id"] == row["candidate_id"]):
        raise ValueError("ranking member identity differs from its frozen candidate")
    try:
        value = json.loads(row["explanation_json"])
        explanation = Explanation(
            value["final_score"], tuple(ScoreContribution(**item) for item in value["contributions"]),
        )
        ranked = RankedCandidate(
            candidate, row["score"], row["ranker"], row["policy_version"], explanation,
            datetime.fromisoformat(row["ranked_at"]),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("ranking member has malformed retained evidence") from exc
    return RankingMember(snapshot, ranked)
