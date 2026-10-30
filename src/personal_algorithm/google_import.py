"""Import bounded Google Takeout activity JSON without extracting archives."""

from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .models import PersonalEvent

if TYPE_CHECKING:
    from .archives import ArchiveInbox, ArchiveRecord
    from .store import Store

MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class GoogleImportSummary:
    archive_sha256: str
    files_scanned: int
    rows_seen: int
    events_written: int
    unknown_timestamps: int
    scope: str = "my_activity_and_youtube_history_json_only"


def _supported(name: str) -> bool:
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if not parts or '..' in parts or PurePosixPath(name.replace("\\", "/")).is_absolute():
        raise ValueError("unsafe archive member path")
    lower = [p.lower() for p in parts]
    if "my activity" in lower and lower[-1] == "myactivity.json":
        return True
    return ("youtube and youtube music" in lower and "history" in lower
            and lower[-1] in {"watch-history.json", "search-history.json"})


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in {"http", "https"} and parsed.hostname else None
    except ValueError:
        return None


def _event(row: object, archive_sha: str, member: str, index: int) -> PersonalEvent:
    if not isinstance(row, dict) or not isinstance(row.get("title"), str):
        raise TypeError("invalid activity row")
    digest = hashlib.sha256(
        json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return PersonalEvent(
        id=f"google:activity:{digest}", source="google", event_type="activity_snapshot",
        occurred_at=_time(row.get("time")), subject=row["title"],
        canonical_url=_url(row.get("titleUrl")), payload=dict(row),
        provenance={"archive_sha256": archive_sha, "member": member,
                    "index": index, "schema": "google_activity_json_v1"},
    )


def import_google_activity(inbox: ArchiveInbox, store: Store, record: ArchiveRecord):
    if (record.detected_provider or record.provider_hint) != "google":
        raise ValueError("archive is not identified as Google")
    scanned = seen = written = unknown = total = 0
    try:
        with zipfile.ZipFile(inbox.path_for(record)) as archive:
            for member in archive.infolist():
                if member.is_dir() or not _supported(member.filename):
                    continue
                if stat.S_ISLNK(member.external_attr >> 16) or member.flag_bits & 1:
                    raise ValueError("linked or encrypted member")
                total += member.file_size
                if member.file_size > MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES:
                    raise ValueError("selected Google activity data exceeds safety limits")
                with archive.open(member) as stream:
                    rows = json.load(stream)
                if not isinstance(rows, list):
                    raise TypeError("activity JSON must be an array")
                scanned += 1
                batch = []
                for index, row in enumerate(rows):
                    event = _event(row, record.sha256, member.filename, index)
                    seen += 1
                    unknown += event.occurred_at is None
                    batch.append(event)
                    if len(batch) >= 500:
                        written += store.put_personal_events(batch)
                        batch = []
                written += store.put_personal_events(batch)
    except (ValueError, TypeError, UnicodeError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
        inbox.mark_status(record.sha256, "failed", import_summary={
            "error": "Google import failed validation; the original archive is retained",
            "events_written": written,
        })
        raise ValueError("Google import failed validation; the original archive is retained") from exc
    summary = GoogleImportSummary(record.sha256, scanned, seen, written, unknown)
    inbox.mark_status(record.sha256, "imported" if scanned else "stored", import_summary=asdict(summary))
    return summary
