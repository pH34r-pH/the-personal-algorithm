import json

import pytest

from personal_algorithm.message_import import import_sent_messages
from personal_algorithm.store import Store


def test_sent_only_idempotent_and_minimal(tmp_path):
    source = tmp_path / "messages.jsonl"
    rows = [
        {"direction": "sent", "source_id": "device:sms:1", "text": "hello",
         "timestamp_utc": "2024-01-01T00:00:00+00:00", "provider": "sms",
         "participant_addresses": ["private"]},
        {"direction": "received", "source_id": "device:sms:2", "text": "other"},
    ]
    source.write_text("\n".join(map(json.dumps, rows)) + "\n", encoding="utf-8")
    store = Store()
    assert import_sent_messages(source, store)["sent_seen"] == 1
    import_sent_messages(source, store)
    assert store.count_personal_events() == 1
    result = store.connection.execute(
        "SELECT subject, payload_json, provenance_json FROM personal_events"
    ).fetchone()
    assert result[0] == "hello"
    assert "private" not in result[1] + result[2]


def test_unknown_direction_fails_closed(tmp_path):
    source = tmp_path / "messages.jsonl"
    source.write_text(json.dumps({"direction": "unknown", "text": "secret"}), encoding="utf-8")
    store = Store()
    with pytest.raises(ValueError, match="direction"):
        import_sent_messages(source, store)
    assert store.count_personal_events() == 0
