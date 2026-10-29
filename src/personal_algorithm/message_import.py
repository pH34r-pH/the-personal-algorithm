"""Local import of explicitly sent messages from a structured device export."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from .models import PersonalEvent
from .store import Store


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
