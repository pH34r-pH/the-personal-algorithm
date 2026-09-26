"""Durable raw-archive inbox for personal-data onboarding."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
import tempfile
import zipfile
from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import anyio

from .store import Store


@dataclass(frozen=True, slots=True)
class ArchiveRecord:
    sha256: str
    filename: str
    provider_hint: str | None
    detected_provider: str | None
    size_bytes: int
    received_at: datetime
    relative_path: str
    status: str
    manifest: dict


class ArchiveInbox:
    def __init__(
        self,
        store: Store,
        root: str | Path,
        *,
        max_bytes: int = 50 * 1024 * 1024 * 1024,
    ) -> None:
        self.store = store
        self.root = Path(root)
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        self.store.connection.execute(
            """CREATE TABLE IF NOT EXISTS archive_uploads (
                sha256 TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                provider_hint TEXT,
                detected_provider TEXT,
                size_bytes INTEGER NOT NULL,
                received_at TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                status TEXT NOT NULL,
                manifest_json TEXT NOT NULL
            )"""
        )
        self.store.connection.commit()

    async def store_stream(
        self,
        filename: str,
        chunks: AsyncIterable[bytes],
        *,
        provider_hint: str | None = None,
    ) -> ArchiveRecord:
        safe_name = _safe_filename(filename)
        fd, tmp_name = tempfile.mkstemp(prefix="archive-", dir=self.root)
        os.close(fd)
        tmp = Path(tmp_name)
        digest = hashlib.sha256()
        size = 0
        try:
            async with await anyio.open_file(tmp, "wb") as handle:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ValueError("archive exceeds configured upload limit")
                    digest.update(chunk)
                    await handle.write(chunk)

            sha = digest.hexdigest()
            existing = self.get(sha)
            if existing is not None:
                tmp.unlink(missing_ok=True)
                return existing

            relative = Path(sha[:2]) / sha / safe_name
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, target)

            manifest = _inspect(target)
            record = ArchiveRecord(
                sha256=sha,
                filename=safe_name,
                provider_hint=provider_hint,
                detected_provider=_detect_provider(manifest, provider_hint),
                size_bytes=size,
                received_at=datetime.now(UTC),
                relative_path=relative.as_posix(),
                status="stored",
                manifest=manifest,
            )
            self._save(record)
            return record
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def get(self, sha256: str) -> ArchiveRecord | None:
        row = self.store.connection.execute(
            """SELECT filename, provider_hint, detected_provider, size_bytes,
                      received_at, relative_path, status, manifest_json
               FROM archive_uploads WHERE sha256 = ?""",
            (sha256,),
        ).fetchone()
        if row is None:
            return None
        return ArchiveRecord(
            sha256=sha256,
            filename=row[0],
            provider_hint=row[1],
            detected_provider=row[2],
            size_bytes=row[3],
            received_at=datetime.fromisoformat(row[4]),
            relative_path=row[5],
            status=row[6],
            manifest=json.loads(row[7]),
        )

    def path_for(self, record: ArchiveRecord) -> Path:
        path = (self.root / record.relative_path).resolve()
        root = self.root.resolve()
        if path != root and root not in path.parents:
            raise ValueError("archive path escaped configured root")
        return path

    def mark_status(
        self,
        sha256: str,
        status: str,
        *,
        import_summary: dict | None = None,
    ) -> ArchiveRecord:
        record = self.get(sha256)
        if record is None:
            raise KeyError(f"unknown archive: {sha256}")
        manifest = dict(record.manifest)
        if import_summary is not None:
            manifest["import_summary"] = import_summary
        self.store.connection.execute(
            """UPDATE archive_uploads
               SET status = ?, manifest_json = ?
               WHERE sha256 = ?""",
            (status, json.dumps(manifest, sort_keys=True), sha256),
        )
        self.store.connection.commit()
        updated = self.get(sha256)
        assert updated is not None
        return updated

    def list(self) -> tuple[ArchiveRecord, ...]:
        rows = self.store.connection.execute(
            """SELECT sha256, filename, provider_hint, detected_provider,
                      size_bytes, received_at, relative_path, status, manifest_json
               FROM archive_uploads ORDER BY received_at DESC"""
        ).fetchall()
        return tuple(
            ArchiveRecord(
                sha256=row[0],
                filename=row[1],
                provider_hint=row[2],
                detected_provider=row[3],
                size_bytes=row[4],
                received_at=datetime.fromisoformat(row[5]),
                relative_path=row[6],
                status=row[7],
                manifest=json.loads(row[8]),
            )
            for row in rows
        )

    def _save(self, record: ArchiveRecord) -> None:
        self.store.connection.execute(
            """INSERT INTO archive_uploads
               (sha256, filename, provider_hint, detected_provider, size_bytes,
                received_at, relative_path, status, manifest_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.sha256,
                record.filename,
                record.provider_hint,
                record.detected_provider,
                record.size_bytes,
                record.received_at.isoformat(),
                record.relative_path,
                record.status,
                json.dumps(record.manifest, sort_keys=True),
            ),
        )
        self.store.connection.commit()


def _safe_filename(value: str) -> str:
    name = Path(value).name.strip()
    name = re.sub(r"[^A-Za-z0-9._() +\-]", "_", name)
    if not name or name in {".", ".."}:
        raise ValueError("archive filename is invalid")
    return name[:240]


def _inspect(path: Path) -> dict:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            names = [item.filename for item in members]
            return _manifest(
                "zip",
                names,
                sum(item.file_size for item in members),
            )

    if tarfile.is_tarfile(path):
        with tarfile.open(path, "r:*") as archive:
            members = archive.getmembers()
            names = [item.name for item in members]
            return _manifest(
                "tar",
                names,
                sum(item.size for item in members if item.isfile()),
            )

    suffix = path.suffix.lower().lstrip(".") or "binary"
    return {
        "format": suffix,
        "members": 1,
        "uncompressed_bytes": path.stat().st_size,
        "sample_members": [path.name],
        "suspicious_members": 0,
    }


def _manifest(kind: str, names: list[str], uncompressed_bytes: int) -> dict:
    suspicious = 0
    for name in names:
        pure = PurePosixPath(name.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts:
            suspicious += 1
    return {
        "format": kind,
        "members": len(names),
        "uncompressed_bytes": uncompressed_bytes,
        "sample_members": names[:200],
        "suspicious_members": suspicious,
    }


def _detect_provider(manifest: dict, provider_hint: str | None) -> str | None:
    if provider_hint:
        return provider_hint.strip().lower() or None
    haystack = "\n".join(manifest.get("sample_members", [])).lower()
    rules = (
        ("google", ("takeout/", "youtube and youtube music", "my activity")),
        ("spotify", ("streaming_history", "endsong")),
        ("x", ("data/tweets.js", "data/like.js", "your archive")),
        ("linkedin", ("connections.csv", "searchqueries.csv", "reactions.csv")),
        ("meta", ("facebook", "instagram", "your_facebook_activity")),
        ("reddit", ("comments.csv", "posts.csv", "saved_posts.csv")),
    )
    for provider, markers in rules:
        if any(marker in haystack for marker in markers):
            return provider
    return None
