"""Local import of explicitly sent messages from a structured device export."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .models import PersonalEvent
from .store import Store

if TYPE_CHECKING:
    from .archives import ArchiveInbox, ArchiveRecord


@dataclass(frozen=True, slots=True)
class MessageImportSummary:
    rows_seen: int
    sent_seen: int
    events_written: int


def import_sent_messages(path: str | Path, store: Store) -> dict[str, int]:
    """Import text from direction-labeled sent records; never retain other records.

    This accepts the structured Pixel export, not arbitrary messaging formats. It does
    not import recipient details, attachments, or classify messages as preferences.
    """
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    source_sha = digest.hexdigest()
    seen = sent = written = 0
    batch: list[PersonalEvent] = []
    with path.open(encoding="utf-8") as source:
        for index, line in enumerate(source):
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("direction") not in {"sent", "received"}:
                raise ValueError("message record has an unknown direction")
            seen += 1
            if row["direction"] != "sent":
                continue
            sent += 1
            identifier = row.get("source_id")
            text = row.get("text")
            if not isinstance(identifier, str) or not identifier or not isinstance(text, str):
                raise ValueError("sent message is missing its source ID or text")
            timestamp = row.get("timestamp_utc")
            occurred = datetime.fromisoformat(timestamp) if timestamp else None
            if occurred is not None and occurred.tzinfo is None:
                raise ValueError("message timestamp must include a timezone")
            event_id = hashlib.sha256(f"{source_sha}:{identifier}".encode()).hexdigest()
            batch.append(PersonalEvent(
                id=f"messages:sent:{event_id}", source="messages", event_type="sent_message",
                occurred_at=occurred, subject=text,
                payload={"provider": row.get("provider"), "direction": "sent"},
                provenance={"source_sha256": source_sha, "row_index": index,
                            "schema": "pixel_structured_messages_v1"},
            ))
            if len(batch) >= 500:
                written += store.put_personal_events(batch)
                batch = []
        written += store.put_personal_events(batch)
    return {"rows_seen": seen, "sent_seen": sent, "events_written": written}


def import_message_archive(
    inbox: ArchiveInbox, store: Store, record: ArchiveRecord,
) -> MessageImportSummary:
    if (record.detected_provider or record.provider_hint) != "messages":
        raise ValueError("archive is not identified as messages")
    try:
        if not record.filename.lower().endswith(".jsonl"):
            raise ValueError("expected a JSONL file")
        summary = MessageImportSummary(**import_sent_messages(inbox.path_for(record), store))
    except (ValueError, UnicodeError, OSError) as exc:
        inbox.mark_status(record.sha256, "failed", import_summary={
            "error": "Message import failed validation; the original file is retained",
        })
        raise ValueError("Message import failed validation; the original file is retained") from exc
    inbox.mark_status(record.sha256, "imported", import_summary=asdict(summary))
    return summary
