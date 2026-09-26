"""Provider-specific archive import into neutral PersonalEvent history."""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .archives import ArchiveInbox, ArchiveRecord
from .models import PersonalEvent
from .store import Store

SPOTIFY_IMPORTER = "spotify.streaming-history.v1"
MAX_SPOTIFY_JSON_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ArchiveImportResult:
    archive_sha256: str
    provider: str
    importer: str
    status: str
    imported: int
    skipped: int
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class ArchiveImportService:
    def __init__(self, store: Store, inbox: ArchiveInbox) -> None:
        self.store = store
        self.inbox = inbox
        self.store.connection.execute(
            """CREATE TABLE IF NOT EXISTS archive_imports (
                archive_sha256 TEXT NOT NULL,
                provider TEXT NOT NULL,
                importer TEXT NOT NULL,
                status TEXT NOT NULL,
                imported INTEGER NOT NULL,
                skipped INTEGER NOT NULL,
                error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (archive_sha256, importer)
            )"""
        )
        self.store.connection.commit()

    def import_if_supported(self, record: ArchiveRecord) -> ArchiveImportResult | None:
        provider = record.detected_provider or record.provider_hint
        if provider == "spotify":
            return self.import_spotify(record)
        return None

    def import_spotify(self, record: ArchiveRecord) -> ArchiveImportResult:
        existing = self._get(record.sha256, SPOTIFY_IMPORTER)
        if existing is not None and existing.status == "complete":
            return existing
        self.inbox.set_status(record.sha256, "importing")
        try:
            imported, skipped = _import_spotify_path(
                self.inbox.path_for(record),
                archive_sha256=record.sha256,
                store=self.store,
            )
            result = ArchiveImportResult(
                archive_sha256=record.sha256,
                provider="spotify",
                importer=SPOTIFY_IMPORTER,
                status="complete",
                imported=imported,
                skipped=skipped,
            )
            self._save(result)
            self.inbox.set_status(record.sha256, "imported")
            return result
        except Exception as exc:
            result = ArchiveImportResult(
                archive_sha256=record.sha256,
                provider="spotify",
                importer=SPOTIFY_IMPORTER,
                status="failed",
                imported=0,
                skipped=0,
                error=str(exc),
            )
            self._save(result)
            self.inbox.set_status(record.sha256, "import_failed")
            return result

    def get_for_archive(self, sha256: str) -> tuple[ArchiveImportResult, ...]:
        rows = self.store.connection.execute(
            """SELECT provider, importer, status, imported, skipped, error
               FROM archive_imports WHERE archive_sha256 = ? ORDER BY importer""",
            (sha256,),
        ).fetchall()
        return tuple(
            ArchiveImportResult(
                archive_sha256=sha256,
                provider=row[0],
                importer=row[1],
                status=row[2],
                imported=row[3],
                skipped=row[4],
                error=row[5],
            )
            for row in rows
        )

    def _get(self, sha256: str, importer: str) -> ArchiveImportResult | None:
        row = self.store.connection.execute(
            """SELECT provider, status, imported, skipped, error
               FROM archive_imports WHERE archive_sha256 = ? AND importer = ?""",
            (sha256, importer),
        ).fetchone()
        if row is None:
            return None
        return ArchiveImportResult(
            archive_sha256=sha256,
            provider=row[0],
            importer=importer,
            status=row[1],
            imported=row[2],
            skipped=row[3],
            error=row[4],
        )

    def _save(self, result: ArchiveImportResult) -> None:
        self.store.connection.execute(
            """INSERT INTO archive_imports
               (archive_sha256, provider, importer, status, imported, skipped, error, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(archive_sha256, importer) DO UPDATE SET
                 provider=excluded.provider, status=excluded.status,
                 imported=excluded.imported, skipped=excluded.skipped,
                 error=excluded.error, updated_at=excluded.updated_at""",
            (
                result.archive_sha256,
                result.provider,
                result.importer,
                result.status,
                result.imported,
                result.skipped,
                result.error,
                datetime.now(UTC).isoformat(),
            ),
        )
        self.store.connection.commit()


def _import_spotify_path(path: Path, *, archive_sha256: str, store: Store) -> tuple[int, int]:
    if zipfile.is_zipfile(path):
        return _import_spotify_zip(path, archive_sha256=archive_sha256, store=store)
    if path.suffix.lower() == ".json":
        return _store_spotify_rows(
            _load_json_file(path),
            archive_sha256=archive_sha256,
            member=path.name,
            store=store,
        )
    raise ValueError("Spotify import expects a JSON file or ZIP archive")


def _import_spotify_zip(path: Path, *, archive_sha256: str, store: Store) -> tuple[int, int]:
    imported = 0
    skipped = 0
    matched = 0
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir() or not _spotify_history_member(info.filename):
                continue
            matched += 1
            if info.file_size > MAX_SPOTIFY_JSON_BYTES:
                raise ValueError(f"Spotify history member is too large: {info.filename}")
            with archive.open(info) as handle:
                payload = json.load(handle)
            added, ignored = _store_spotify_rows(
                payload,
                archive_sha256=archive_sha256,
                member=info.filename,
                store=store,
            )
            imported += added
            skipped += ignored
    if matched == 0:
        raise ValueError("no Spotify streaming-history JSON members were found")
    return imported, skipped


def _load_json_file(path: Path) -> Any:
    if path.stat().st_size > MAX_SPOTIFY_JSON_BYTES:
        raise ValueError("Spotify history JSON file exceeds the parser limit")
    with path.open("rb") as handle:
        return json.load(handle)


def _spotify_history_member(name: str) -> bool:
    lower = name.lower()
    if not lower.endswith(".json"):
        return False
    return any(
        marker in lower
        for marker in ("streaming_history", "streaminghistory", "endsong", "streaming history")
    )


def _store_spotify_rows(
    payload: Any,
    *,
    archive_sha256: str,
    member: str,
    store: Store,
) -> tuple[int, int]:
    if not isinstance(payload, list):
        raise ValueError(f"Spotify history member is not a JSON array: {member}")
    events: list[PersonalEvent] = []
    skipped = 0
    for index, row in enumerate(payload):
        if not isinstance(row, dict) or not _looks_like_spotify_stream(row):
            skipped += 1
            continue
        events.append(
            _spotify_event(row, archive_sha256=archive_sha256, member=member, index=index)
        )
    return store.put_personal_events(events), skipped


def _looks_like_spotify_stream(row: dict[str, Any]) -> bool:
    timestamp = row.get("ts") or row.get("endTime")
    played = row.get("ms_played")
    if played is None:
        played = row.get("msPlayed")
    return isinstance(timestamp, str) and isinstance(played, int)


def _spotify_event(
    row: dict[str, Any],
    *,
    archive_sha256: str,
    member: str,
    index: int,
) -> PersonalEvent:
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    event_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    uri = row.get("spotify_track_uri") or row.get("spotify_episode_uri")
    subject = (
        row.get("master_metadata_track_name")
        or row.get("episode_name")
        or row.get("trackName")
    )
    return PersonalEvent(
        id=f"spotify:stream:{event_hash}",
        source="spotify",
        event_type="stream",
        occurred_at=_spotify_time(row.get("ts") or row.get("endTime")),
        subject=subject if isinstance(subject, str) else None,
        canonical_url=_spotify_url(uri),
        payload=row,
        provenance={
            "archive_sha256": archive_sha256,
            "member": member,
            "index": index,
            "importer": SPOTIFY_IMPORTER,
        },
    )


def _spotify_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _spotify_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) == 3 and parts[0] == "spotify" and parts[1] in {"track", "episode"}:
        return f"https://open.spotify.com/{parts[1]}/{parts[2]}"
    return None
