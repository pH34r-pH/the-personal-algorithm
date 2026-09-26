import io
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_algorithm.archive_api import create_archive_router
from personal_algorithm.archives import ArchiveInbox
from personal_algorithm.store import Store


def _takeout_zip() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            "Takeout/YouTube and YouTube Music/history/watch-history.html",
            "<html></html>",
        )
        archive.writestr("Takeout/My Activity/Search/MyActivity.json", "[]")
    return payload.getvalue()


def test_archive_upload_is_content_addressed_and_detects_provider(tmp_path):
    store = Store()
    inbox = ArchiveInbox(store, tmp_path)
    app = FastAPI()
    app.include_router(create_archive_router(inbox))
    client = TestClient(app)

    payload = _takeout_zip()
    first = client.post(
        "/archives/upload",
        content=payload,
        headers={"x-archive-filename": "takeout.zip"},
    )
    assert first.status_code == 201
    body = first.json()
    assert body["detected_provider"] == "google"
    assert body["manifest"]["format"] == "zip"
    assert body["manifest"]["members"] == 2
    assert (tmp_path / body["relative_path"]).exists()

    duplicate = client.post(
        "/archives/upload",
        content=payload,
        headers={"x-archive-filename": "same-data.zip", "x-provider-hint": "google"},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["sha256"] == body["sha256"]
    assert len(inbox.list()) == 1


def test_archive_upload_respects_streaming_size_limit(tmp_path):
    inbox = ArchiveInbox(Store(), tmp_path, max_bytes=3)
    app = FastAPI()
    app.include_router(create_archive_router(inbox))
    client = TestClient(app)

    response = client.post(
        "/archives/upload",
        content=b"four",
        headers={"x-archive-filename": "too-large.json"},
    )

    assert response.status_code == 413
    assert "configured upload limit" in response.json()["detail"]


def test_archive_manifest_flags_unsafe_member_names_without_extracting(tmp_path):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../outside.json", "{}")

    inbox = ArchiveInbox(Store(), tmp_path)
    app = FastAPI()
    app.include_router(create_archive_router(inbox))
    response = TestClient(app).post(
        "/archives/upload",
        content=payload.getvalue(),
        headers={"x-archive-filename": "odd.zip"},
    )

    assert response.status_code == 201
    assert response.json()["manifest"]["suspicious_members"] == 1
    assert not (tmp_path.parent / "outside.json").exists()
