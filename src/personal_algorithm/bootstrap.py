"""Persisted, resumable provider bootstrap coordination."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime

from .models import Candidate
from .providers import (
    BootstrapCheckpoint,
    BootstrapJob,
    BootstrapStatus,
    Connection,
    Provider,
)
from .store import Store


def _checkpoint_json(checkpoint: BootstrapCheckpoint) -> str:
    return json.dumps(asdict(checkpoint), sort_keys=True)


def _checkpoint_from_json(value: str) -> BootstrapCheckpoint:
    data = json.loads(value)
    return BootstrapCheckpoint(
        cursor=data.get("cursor"),
        imported=data.get("imported", 0),
        skipped=data.get("skipped", 0),
        metadata=data.get("metadata", {}),
    )


class BootstrapCoordinator:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.store.connection.execute(
            """CREATE TABLE IF NOT EXISTS bootstrap_jobs (
                id TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL,
                connection_ref TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                checkpoint_json TEXT NOT NULL,
                error TEXT
            )"""
        )
        self.store.connection.commit()

    def create(self, connection: Connection) -> BootstrapJob:
        job = BootstrapJob(
            id=str(uuid.uuid4()),
            provider_id=connection.provider_id,
            connection_ref=connection.credential_ref,
            status=BootstrapStatus.PENDING,
        )
        self._save(job)
        return job

    def step(
        self,
        job_id: str,
        *,
        connection: Connection,
        provider: Provider,
    ) -> tuple[BootstrapJob, tuple[Candidate, ...]]:
        job = self.get(job_id)
        if job.status in {BootstrapStatus.COMPLETE, BootstrapStatus.CANCELLED}:
            return job, ()

        started = job.started_at or datetime.now(UTC)
        try:
            checkpoint = provider.bootstrap(connection, job.checkpoint)
            payload = checkpoint.metadata.get("repositories", [])
            mapper = getattr(provider, "repository_candidates", None)
            candidates = tuple(mapper(payload)) if mapper and payload else ()
            # Large provider payloads are transient; checkpoints retain progress only.
            clean_checkpoint = BootstrapCheckpoint(
                cursor=checkpoint.cursor,
                imported=checkpoint.imported,
                skipped=checkpoint.skipped,
                metadata={
                    key: value
                    for key, value in checkpoint.metadata.items()
                    if key != "repositories"
                },
            )
            for candidate in candidates:
                self.store.put_candidate(candidate)

            complete = clean_checkpoint.cursor is None
            updated = BootstrapJob(
                id=job.id,
                provider_id=job.provider_id,
                connection_ref=job.connection_ref,
                status=BootstrapStatus.COMPLETE if complete else BootstrapStatus.RUNNING,
                started_at=started,
                completed_at=datetime.now(UTC) if complete else None,
                checkpoint=clean_checkpoint,
            )
            self._save(updated)
            return updated, candidates
        except Exception as exc:
            failed = BootstrapJob(
                id=job.id,
                provider_id=job.provider_id,
                connection_ref=job.connection_ref,
                status=BootstrapStatus.FAILED,
                started_at=started,
                checkpoint=job.checkpoint,
                error=str(exc),
            )
            self._save(failed)
            raise

    def get(self, job_id: str) -> BootstrapJob:
        row = self.store.connection.execute(
            """SELECT provider_id, connection_ref, status, started_at,
                      completed_at, checkpoint_json, error
               FROM bootstrap_jobs WHERE id = ?""",
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown bootstrap job: {job_id}")
        return BootstrapJob(
            id=job_id,
            provider_id=row[0],
            connection_ref=row[1],
            status=BootstrapStatus(row[2]),
            started_at=datetime.fromisoformat(row[3]) if row[3] else None,
            completed_at=datetime.fromisoformat(row[4]) if row[4] else None,
            checkpoint=_checkpoint_from_json(row[5]),
            error=row[6],
        )

    def _save(self, job: BootstrapJob) -> None:
        self.store.connection.execute(
            """INSERT INTO bootstrap_jobs
               (id, provider_id, connection_ref, status, started_at,
                completed_at, checkpoint_json, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 status=excluded.status,
                 started_at=excluded.started_at,
                 completed_at=excluded.completed_at,
                 checkpoint_json=excluded.checkpoint_json,
                 error=excluded.error""",
            (
                job.id,
                job.provider_id,
                job.connection_ref,
                job.status.value,
                job.started_at.isoformat() if job.started_at else None,
                job.completed_at.isoformat() if job.completed_at else None,
                _checkpoint_json(job.checkpoint),
                job.error,
            ),
        )
        self.store.connection.commit()
