"""Spotify Extended Streaming History archive importer."""

from __future__ import annotations

import hashlib
import json
import tarfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .archives import ArchiveInbox, ArchiveRecord
from .models import PersonalEvent
from .store import Store


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
        for member_name, payload in _json_payloads(path):
            files_scanned += 1
            rows = _stream_rows(payload)
            if rows is None:
                unsupported += 1
                continue

            rows_seen += len(rows)
            events = tuple(
                event
                for index, row in enumerate(rows)
                if (event := _event_from_row(record.sha256, member_name, index, row))
                is not None
            )
            events_written += store.put_personal_events(events)

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


def _json_payloads(path: Path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if member.is_dir() or not member.filename.lower().endswith(".json"):
                    continue
                with archive.open(member) as handle:
                    yield member.filename, json.load(handle)
        return

    if tarfile.is_tarfile(path):
        with tarfile.open(path, "r:*") as archive:
            for member in archive.getmembers():
                if not member.isfile() or not member.name.lower().endswith(".json"):
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                with handle:
                    yield member.name, json.load(handle)
        return

    if path.suffix.lower() == ".json":
        with path.open(encoding="utf-8") as handle:
            yield path.name, json.load(handle)


def _stream_rows(payload) -> list[dict] | None:
    if not isinstance(payload, list) or not payload:
        return None
    rows = [row for row in payload if isinstance(row, dict)]
    if not rows:
        return None
    sample = rows[0]
    if "ts" not in sample or "ms_played" not in sample:
        return None
    if not any(
        key in sample
        for key in (
            "spotify_track_uri",
            "spotify_episode_uri",
            "audiobook_uri",
            "master_metadata_track_name",
            "episode_name",
            "audiobook_chapter_title",
        )
    ):
        return None
    return rows


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
    event_digest = hashlib.sha256(stable_payload.encode("utf-8")).hexdigest()

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
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
