import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_algorithm.archive_api import create_archive_router
from personal_algorithm.archives import ArchiveInbox
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


def test_upload_and_import_sent_messages(tmp_path):
    store = Store()
    app = FastAPI()
    app.include_router(create_archive_router(ArchiveInbox(store, tmp_path)))
    client = TestClient(app)
    rows = [
        {"direction": "sent", "source_id": "sms:1", "text": "hello"},
        {"direction": "received", "source_id": "sms:2", "text": "private reply"},
    ]
    response = client.post(
        "/archives/upload", content="\n".join(map(json.dumps, rows)),
        headers={"x-archive-filename": "messages.jsonl", "x-provider-hint": "messages"},
    )
    assert response.status_code == 201
    assert response.json()["import_available"] is True
    url = f'/archives/{response.json()["sha256"]}/import'
    assert client.post(url).json()["summary"]["sent_seen"] == 1
    assert client.post(url).status_code == 200
    assert store.count_personal_events() == 1
