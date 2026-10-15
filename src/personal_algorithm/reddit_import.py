"""Import posts and comments from a Reddit account export without extracting it."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import stat
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .models import PersonalEvent

if TYPE_CHECKING:
    from .archives import ArchiveInbox, ArchiveRecord
    from .store import Store

# Bound decompression independently of the upload limit. Other files remain retained.
MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
TABLES = {"comments.csv": ("comment", "body"), "posts.csv": ("post", "title")}


@dataclass(frozen=True, slots=True)
class RedditImportSummary:
    archive_sha256: str
    files_scanned: int
    rows_seen: int
    events_written: int
    unknown_timestamps: int
    scope: str = "posts_and_comments_only"


def _safe_member(name: str) -> str:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("unsafe archive member path")
    return path.name.lower()


def _csv_sources(path: Path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            total = 0
            for member in archive.infolist():
                if member.is_dir():
                    continue
                name = _safe_member(member.filename)
                if name not in TABLES:
                    continue
                if stat.S_ISLNK(member.external_attr >> 16) or member.flag_bits & 1:
                    raise ValueError("linked or encrypted CSV members are not supported")
                total += member.file_size
                if member.file_size > MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES:
                    raise ValueError("Reddit CSV data exceeds the parser safety limit")
                with (
                    archive.open(member) as handle,
                    io.TextIOWrapper(handle, encoding="utf-8-sig", newline="") as text,
                ):
                    yield member.filename, name, text
        return
    name = _safe_member(path.name)
    if name not in TABLES:
        raise ValueError("use a Reddit ZIP export, comments.csv, or posts.csv")
    if path.stat().st_size > MAX_MEMBER_BYTES:
        raise ValueError("Reddit CSV data exceeds the parser safety limit")
    with path.open(encoding="utf-8-sig", newline="") as text:
        yield path.name, name, text


def _timestamp(value: str) -> datetime | None:
    value = value.strip()
    if value.endswith(" UTC"):
        value = value[:-4] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Do not turn absent timezone information into a guessed UTC observation.
    return parsed if parsed.tzinfo is not None else None


def _reddit_url(value: str) -> str | None:
    value = value.strip()
    if value.startswith("/") and not value.startswith("//"):
        value = "https://www.reddit.com" + value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme in {"http", "https"}
        and (host == "reddit.com" or host.endswith(".reddit.com"))
        and not parsed.username
        and not parsed.password
    ):
        return value
    return None


def _events(text, table: str, archive_sha: str, member: str):
    kind, subject_key = TABLES[table]
    reader = csv.DictReader(text, strict=True)
    fields = reader.fieldnames or []
    if not {"id", subject_key}.issubset(fields) or len(fields) != len(set(fields)):
        raise ValueError("Reddit CSV has missing or duplicate required headers")
    for index, row in enumerate(reader):
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Reddit CSV row has an unexpected number of fields")
        identifier = row["id"].strip().lower()
        prefix = "t1_" if kind == "comment" else "t3_"
        identifier = identifier.removeprefix(prefix)
        if not re.fullmatch(r"[a-z0-9]+", identifier):
            raise ValueError("Reddit CSV row has an invalid record identifier")
        # Keep distinct edited observations, but deduplicate identical rows across exports.
        digest = hashlib.sha256(
            json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        yield PersonalEvent(
            id=f"reddit:{kind}:{identifier}:{digest}",
            source="reddit",
            event_type=f"{kind}_snapshot",
            occurred_at=_timestamp(row.get("date", "")),
            subject=row[subject_key],
            canonical_url=_reddit_url(row.get("permalink", "")),
            payload=dict(row),
            provenance={
                "archive_sha256": archive_sha,
                "member": member,
                "index": index,
                "schema": "reddit_account_export_csv_v1",
                "reddit_id": prefix + identifier,
            },
        )


def import_reddit_history(
    inbox: ArchiveInbox, store: Store, record: ArchiveRecord,
) -> RedditImportSummary:
    if (record.detected_provider or record.provider_hint) != "reddit":
        raise ValueError("archive is not identified as Reddit")
    scanned = seen = written = unknown = 0
    try:
        for member, table, text in _csv_sources(inbox.path_for(record)):
            scanned += 1
            batch = []
            for event in _events(text, table, record.sha256, member):
                seen += 1
                unknown += event.occurred_at is None
                batch.append(event)
                if len(batch) >= 500:
                    written += store.put_personal_events(batch)
                    batch = []
            written += store.put_personal_events(batch)
        if not scanned:
            raise ValueError("no supported Reddit comments.csv or posts.csv found")
    except (ValueError, UnicodeError, csv.Error, zipfile.BadZipFile, RuntimeError) as exc:
        # No row contents, filesystem paths, or provider credentials in diagnostics.
        inbox.mark_status(
            record.sha256, "failed",
            import_summary={
                "error": "Reddit import failed validation; the original archive is retained",
                "events_written": written,
                "scope": "posts_and_comments_only",
            },
        )
        raise ValueError("Reddit import failed validation; the original archive is retained") from exc
    summary = RedditImportSummary(record.sha256, scanned, seen, written, unknown)
    inbox.mark_status(record.sha256, "imported", import_summary=asdict(summary))
    return summary
