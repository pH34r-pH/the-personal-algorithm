"""Spotify Extended Streaming History archive importer."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tarfile
import tempfile
import zipfile
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .archives import ArchiveInbox, ArchiveRecord
from .json_array_stream import iter_json_array, iter_json_array_text
from .models import PersonalEvent
from .store import Store

MAX_UNSUPPORTED_JSON_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SpotifyImportSummary:
    archive_sha256: str
    files_scanned: int
    rows_seen: int
    events_written: int
    unsupported_json_files: int


def import_spotify_history(
    inbox: ArchiveInbox,
    store: Store,
    record: ArchiveRecord,
) -> SpotifyImportSummary:
    if (record.detected_provider or record.provider_hint) != "spotify":
        raise ValueError("archive is not identified as Spotify")

    path = inbox.path_for(record)
    files_scanned = 0
    rows_seen = 0
    events_written = 0
    unsupported = 0

    try:
        for member_name, open_member, binary in _json_members(path):
            files_scanned += 1
            if not _is_array(open_member, binary):
                _validate_unsupported_json(open_member, binary)
                unsupported += 1
                continue
            with tempfile.TemporaryDirectory(prefix="spotify-import-") as directory:
                staging = Path(directory) / "events.sqlite3"
                supported, count, written = _stage_member(
                    staging, open_member, binary, record.sha256, member_name
                )
                if not supported:
                    unsupported += 1
                    continue
                rows_seen += count
                events_written += store.merge_staged_personal_events(staging, written)

        if rows_seen == 0:
            raise ValueError("no Spotify Extended Streaming History rows found")

        summary = SpotifyImportSummary(
            archive_sha256=record.sha256,
            files_scanned=files_scanned,
            rows_seen=rows_seen,
            events_written=events_written,
            unsupported_json_files=unsupported,
        )
        inbox.mark_status(
            record.sha256,
            "imported",
            import_summary=asdict(summary),
        )
        return summary
    except Exception as exc:
        inbox.mark_status(
            record.sha256,
            "failed",
            import_summary={"error": str(exc)},
        )
        raise


def _json_members(path: Path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if not member.is_dir() and member.filename.lower().endswith(".json"):
                    yield member.filename, lambda member=member: archive.open(member), True
        return
    if tarfile.is_tarfile(path):
        with tarfile.open(path, "r:*") as archive:
            for member in archive.getmembers():
                if member.isfile() and member.name.lower().endswith(".json"):
                    yield member.name, lambda member=member: archive.extractfile(member), True
        return
    if path.suffix.lower() == ".json":
        yield path.name, lambda: path.open(encoding="utf-8"), False


def _validate_unsupported_json(
    open_member, binary: bool, *, max_bytes: int | None = None,
) -> None:
    limit = MAX_UNSUPPORTED_JSON_BYTES if max_bytes is None else max_bytes
    if limit <= 0:
        raise ValueError("unsupported JSON limit must be positive")
    chunks: list[bytes] | list[str] = []
    total = 0
    with open_member() as stream:
        while chunk := stream.read(65536):
            size = len(chunk) if binary else len(chunk.encode("utf-8"))
            total += size
            if total > limit:
                raise ValueError("unsupported Spotify JSON member exceeds size limit")
            chunks.append(chunk)
    raw = b"".join(chunks) if binary else "".join(chunks)
    json.loads(raw)


def _is_array(open_member, binary: bool) -> bool:
    encoding = None
    if binary:
        with open_member() as stream:
            encoding = json.detect_encoding(io.BufferedReader(stream).read(4))
    with open_member() as stream:
        reader = (io.TextIOWrapper(stream, encoding=encoding, errors="surrogatepass")
                  if binary else stream)
        while char := reader.read(1):
            if char not in " \t\r\n":
                return char == "["
        return False


def _rows(open_member, binary: bool):
    with open_member() as stream:
        values = iter_json_array(stream) if binary else iter_json_array_text(stream)
        yield from (row for row in values if isinstance(row, dict))


def _supported_sample(sample: dict) -> bool:
    return ("ts" in sample and "ms_played" in sample and any(
        key in sample for key in ("spotify_track_uri", "spotify_episode_uri", "audiobook_uri",
                                  "master_metadata_track_name", "episode_name", "audiobook_chapter_title")
    ))


def _stage_member(path, open_member, binary, archive_sha, member_name):
    supported = None
    rows_seen = written = 0

    def serialized():
        nonlocal supported, rows_seen, written
        for row in _rows(open_member, binary):
            if supported is None:
                supported = _supported_sample(row)
            index = rows_seen
            rows_seen += 1
            if not supported:
                continue
            event = _event_from_row(archive_sha, member_name, index, row)
            if event is not None:
                written += 1
                yield (event.id, event.source, event.event_type,
                       event.occurred_at.isoformat() if event.occurred_at else None,
                       event.subject, event.canonical_url, json.dumps(event.payload, sort_keys=True),
                       json.dumps(event.provenance, sort_keys=True))

    with closing(sqlite3.connect(path)) as stage:
        stage.execute("""CREATE TABLE events (id TEXT, source TEXT, event_type TEXT, occurred_at TEXT,
                      subject TEXT, canonical_url TEXT, payload_json TEXT, provenance_json TEXT)""")
        stage.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)", serialized())
        stage.commit()
    return bool(supported), rows_seen, written


def _event_from_row(
    archive_sha256: str,
    member_name: str,
    index: int,
    row: dict,
) -> PersonalEvent | None:
    timestamp = _parse_timestamp(row.get("ts"))
    if timestamp is None:
        return None

    event_type, subject, uri = _identity(row)
    canonical = _spotify_url(uri)
    stable_payload = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    identity = "\0".join((archive_sha256, member_name, str(index), stable_payload))
    event_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()

    return PersonalEvent(
        id=f"spotify:stream:{event_digest}",
        source="spotify",
        event_type=event_type,
        occurred_at=timestamp,
        subject=subject,
        canonical_url=canonical,
        payload=dict(row),
        provenance={
            "archive_sha256": archive_sha256,
            "member": member_name,
            "index": index,
            "schema": "spotify_extended_streaming_history",
        },
    )


def _identity(row: dict) -> tuple[str, str | None, str | None]:
    track_uri = row.get("spotify_track_uri")
    if track_uri or row.get("master_metadata_track_name"):
        artist = row.get("master_metadata_album_artist_name")
        track = row.get("master_metadata_track_name")
        subject = " — ".join(part for part in (artist, track) if part) or None
        return "track_stream", subject, track_uri

    episode_uri = row.get("spotify_episode_uri")
    if episode_uri or row.get("episode_name"):
        show = row.get("episode_show_name")
        episode = row.get("episode_name")
        subject = " — ".join(part for part in (show, episode) if part) or None
        return "podcast_stream", subject, episode_uri

    audiobook_uri = row.get("audiobook_chapter_uri") or row.get("audiobook_uri")
    title = row.get("audiobook_chapter_title") or row.get("audiobook_title")
    if audiobook_uri or title:
        return "audiobook_stream", title, audiobook_uri

    return "unknown_stream", None, None


def _spotify_url(uri: object) -> str | None:
    if not isinstance(uri, str):
        return None
    parts = uri.split(":")
    if len(parts) != 3 or parts[0] != "spotify":
        return None
    kind, item_id = parts[1], parts[2]
    if kind not in {"track", "episode", "show", "album", "artist", "audiobook"}:
        return None
    return f"https://open.spotify.com/{kind}/{item_id}"


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
