"""Import selected Google Takeout history and subscription references."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import stat
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

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
    scope: str = "google_activity_json_html_and_youtube_subscriptions_csv"


def _supported(name: str) -> bool:
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if not parts or '..' in parts or PurePosixPath(name.replace("\\", "/")).is_absolute():
        raise ValueError("unsafe archive member path")
    lower = [p.lower() for p in parts]
    if "my activity" in lower and lower[-1] in {"myactivity.json", "myactivity.html"}:
        return True
    if "youtube and youtube music" not in lower:
        return False
    return (("history" in lower and lower[-1] in {
        "watch-history.json", "search-history.json", "watch-history.html", "search-history.html"
    }) or ("subscriptions" in lower and lower[-1] == "subscriptions.csv"))


_OFFSETS = {"UTC": 0, "GMT": 0, "PST": -8, "PDT": -7, "MST": -7, "MDT": -6,
            "CST": -6, "CDT": -5, "EST": -5, "EDT": -4}


def _html_time(value: str) -> datetime | None:
    value = " ".join(value.split())
    date_text, _, zone = value.rpartition(" ")
    if zone not in _OFFSETS:
        return None
    try:
        return datetime.strptime(date_text, "%b %d, %Y, %I:%M:%S %p").replace(
            tzinfo=timezone(timedelta(hours=_OFFSETS[zone]))
        )
    except ValueError:
        return None


class _ActivityHTML(HTMLParser):
    """Collect the first six-column content cell of each Takeout activity card."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self.depth = 0
        self.lines: list[str] = []
        self.url: str | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        if not self.depth and tag == "div" and {"content-cell", "mdl-cell--6-col"} <= classes:
            if "mdl-typography--text-right" not in classes:
                self.depth = 1
                self.lines = [""]
                self.url = None
        elif self.depth:
            if tag == "div":
                self.depth += 1
            elif tag == "br":
                self.lines.append("")
            elif tag == "a" and self.url is None:
                self.url = _url(attrs.get("href"))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self.depth:
            self.lines[-1] += data

    def handle_endtag(self, tag):
        if self.depth and tag == "div":
            self.depth -= 1
            if not self.depth:
                lines = [" ".join(line.split()) for line in self.lines if line.strip()]
                if lines:
                    timestamp = _html_time(lines[-1])
                    self.rows.append({"title": lines[0], "titleUrl": self.url,
                                      "time": timestamp.isoformat() if timestamp else None})


def _subscription(row: dict, archive_sha: str, member: str, index: int) -> PersonalEvent:
    channel_id = (row.get("Channel Id") or "").strip()
    title = (row.get("Channel Title") or "").strip()
    url = _url(row.get("Channel Url"))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", channel_id) or not title or not url:
        raise ValueError("invalid YouTube subscription")
    parsed = urlsplit(url)
    if (parsed.hostname not in {"www.youtube.com", "youtube.com"}
            or parsed.path.rstrip("/") != f"/channel/{channel_id}"
            or parsed.query or parsed.fragment or parsed.username or parsed.password
            or parsed.port is not None):
        raise ValueError("invalid YouTube channel URL")
    url = urlunsplit(("https", parsed.hostname, parsed.path, "", ""))
    return PersonalEvent(
        id=f"youtube:subscription:{channel_id}", source="youtube", event_type="subscription",
        subject=title, canonical_url=url, payload={"channel_id": channel_id},
        provenance={"archive_sha256": archive_sha, "member": member, "index": index,
                    "schema": "youtube_subscriptions_csv_v1"},
    )


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
                    "index": index, "schema": (
                        "google_activity_html_v1" if member.lower().endswith(".html")
                        else "google_activity_json_v1"
                    )},
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
                    if member.filename.lower().endswith(".csv"):
                        reader = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline=""))
                        if not {"Channel Id", "Channel Url", "Channel Title"} <= set(reader.fieldnames or []):
                            raise ValueError("invalid subscription CSV header")
                        rows = list(reader)
                    elif member.filename.lower().endswith(".html"):
                        parser = _ActivityHTML()
                        parser.feed(stream.read().decode("utf-8-sig"))
                        parser.close()
                        rows = parser.rows
                    else:
                        rows = json.load(stream)
                if not isinstance(rows, list):
                    raise TypeError("activity JSON must be an array")
                scanned += 1
                batch = []
                for index, row in enumerate(rows):
                    if member.filename.lower().endswith(".csv"):
                        event = _subscription(row, record.sha256, member.filename, index)
                    else:
                        event = _event(row, record.sha256, member.filename, index)
                    seen += 1
                    unknown += event.event_type != "subscription" and event.occurred_at is None
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
