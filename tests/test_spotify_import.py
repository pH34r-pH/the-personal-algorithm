import io
import json
import tarfile
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_algorithm import spotify_import
from personal_algorithm.archive_api import create_archive_router
from personal_algorithm.archives import ArchiveInbox
from personal_algorithm.spotify_import import import_spotify_history
from personal_algorithm.store import Store


def _spotify_archive() -> bytes:
    rows = [
        {
            "ts": "2024-07-25T03:52:19Z",
            "username": "fixture",
            "platform": "ios",
            "ms_played": 15673,
            "conn_country": "US",
            "ip_addr_decrypted": "192.0.2.10",
            "user_agent_decrypted": "fixture-agent",
            "master_metadata_track_name": "Example Track",
            "master_metadata_album_artist_name": "Example Artist",
            "master_metadata_album_album_name": "Example Album",
            "spotify_track_uri": "spotify:track:abc123",
            "episode_name": None,
            "episode_show_name": None,
            "spotify_episode_uri": None,
            "reason_start": "trackdone",
            "reason_end": "fwdbtn",
            "shuffle": True,
            "skipped": True,
            "offline": False,
            "offline_timestamp": None,
            "incognito_mode": False,
        },
        {
            "ts": "2024-07-25T04:00:00Z",
            "platform": "android",
            "ms_played": 240000,
            "conn_country": "US",
            "master_metadata_track_name": None,
            "master_metadata_album_artist_name": None,
            "master_metadata_album_album_name": None,
            "spotify_track_uri": None,
            "episode_name": "Episode 1",
            "episode_show_name": "Fixture Show",
            "spotify_episode_uri": "spotify:episode:def456",
            "reason_start": "clickrow",
            "reason_end": "endplay",
            "shuffle": False,
            "skipped": False,
            "offline": False,
            "offline_timestamp": None,
            "incognito_mode": False,
        },
        {
            "ts": "2024-07-25T05:00:00Z",
            "platform": "web",
            "ms_played": 600000,
            "conn_country": "US",
            "master_metadata_track_name": None,
            "spotify_track_uri": None,
            "episode_name": None,
            "spotify_episode_uri": None,
            "audiobook_title": "Fixture Book",
            "audiobook_uri": "spotify:audiobook:book123",
            "audiobook_chapter_title": "Chapter 2",
            "audiobook_chapter_uri": "spotify:audiobook:book123",
            "reason_start": "clickrow",
            "reason_end": "endplay",
            "shuffle": False,
            "skipped": False,
            "offline": True,
            "offline_timestamp": 1721883600,
            "incognito_mode": True,
        },
    ]
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            "Spotify Extended Streaming History/endsong_0.json",
            json.dumps(rows),
        )
        archive.writestr(
            "Spotify Extended Streaming History/Read Me First.json",
            json.dumps({"not": "stream rows"}),
        )
    return payload.getvalue()


def _client(tmp_path):
    store = Store()
    inbox = ArchiveInbox(store, tmp_path)
    app = FastAPI()
    app.include_router(create_archive_router(inbox))
    return TestClient(app), store, inbox


def test_spotify_lifetime_history_import_preserves_full_rows(tmp_path):
    client, store, inbox = _client(tmp_path)
    uploaded = client.post(
        "/archives/upload",
        content=_spotify_archive(),
        headers={"x-archive-filename": "spotify.zip"},
    )
    assert uploaded.status_code == 201
    archive = uploaded.json()
    assert archive["detected_provider"] == "spotify"

    imported = client.post(f"/archives/{archive['sha256']}/import")
    assert imported.status_code == 200
    summary = imported.json()["summary"]
    assert summary["rows_seen"] == 3
    assert summary["events_written"] == 3
    assert summary["unsupported_json_files"] == 1
    assert store.count_personal_events() == 3

    rows = store.connection.execute(
        """SELECT event_type, subject, canonical_url, payload_json, provenance_json
           FROM personal_events ORDER BY occurred_at"""
    ).fetchall()
    assert rows[0][0:3] == (
        "track_stream",
        "Example Artist — Example Track",
        "https://open.spotify.com/track/abc123",
    )
    assert '"ip_addr_decrypted": "192.0.2.10"' in rows[0][3]
    assert '"member": "Spotify Extended Streaming History/endsong_0.json"' in rows[0][4]
    assert rows[1][0:3] == (
        "podcast_stream",
        "Fixture Show — Episode 1",
        "https://open.spotify.com/episode/def456",
    )
    assert rows[2][0] == "audiobook_stream"
    assert rows[2][1] == "Chapter 2"

    retained = inbox.get(archive["sha256"])
    assert retained is not None
    assert retained.status == "imported"
    assert retained.manifest["import_summary"]["events_written"] == 3


def test_spotify_reimport_is_idempotent(tmp_path):
    client, store, _ = _client(tmp_path)
    uploaded = client.post(
        "/archives/upload",
        content=_spotify_archive(),
        headers={"x-archive-filename": "spotify.zip"},
    ).json()

    assert client.post(f"/archives/{uploaded['sha256']}/import").status_code == 200
    assert store.count_personal_events() == 3
    assert client.post(f"/archives/{uploaded['sha256']}/import").status_code == 200
    assert store.count_personal_events() == 3


def test_non_spotify_archive_waits_for_its_parser(tmp_path):
    client, _, _ = _client(tmp_path)
    uploaded = client.post(
        "/archives/upload",
        content=b"[]",
        headers={
            "x-archive-filename": "x-export.json",
            "x-provider-hint": "x",
        },
    ).json()

    response = client.post(f"/archives/{uploaded['sha256']}/import")
    assert response.status_code == 409


def _row(name="Example"):
    return {"ts": "2024-07-25T03:52:19Z", "ms_played": 10,
            "spotify_track_uri": "spotify:track:abc", "master_metadata_track_name": name}


def _upload(client, content, filename):
    response = client.post("/archives/upload", content=content,
                           headers={"x-archive-filename": filename, "x-provider-hint": "spotify"})
    assert response.status_code == 201
    return response.json()["sha256"]


def test_malformed_later_member_preserves_prior_member_without_partial_second(tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("endsong_0.json", json.dumps([_row("first")]))
        archive.writestr("endsong_1.json", json.dumps([_row("second")])[:-1] + ", invalid]")
    client, store, inbox = _client(tmp_path)
    sha = _upload(client, data.getvalue(), "spotify.zip")
    record = inbox.get(sha)
    with pytest.raises(ValueError):
        import_spotify_history(inbox, store, record)
    assert store.count_personal_events() == 1
    assert store.connection.execute("SELECT subject FROM personal_events").fetchone()[0] == "first"
    assert inbox.get(sha).status == "failed"


def test_bad_late_row_does_not_write_partial_member(tmp_path):
    rows = [_row("good") for _ in range(700)]
    rows.append({**_row("bad"), "master_metadata_album_artist_name": ["invalid"]})
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("endsong.json", json.dumps(rows))
    client, store, inbox = _client(tmp_path)
    sha = _upload(client, data.getvalue(), "spotify.zip")
    with pytest.raises(TypeError):
        import_spotify_history(inbox, store, inbox.get(sha))
    assert store.count_personal_events() == 0
    assert inbox.get(sha).status == "failed"


def test_filtered_rows_keep_previous_indexing_and_first_dict_classification(tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("endsong.json", json.dumps([7, _row("one"), None, _row("two")]))
        archive.writestr("unsupported.json", json.dumps([None, {"not": "spotify"}, _row("ignored")]))
    client, store, _ = _client(tmp_path)
    sha = _upload(client, data.getvalue(), "spotify.zip")
    response = client.post(f"/archives/{sha}/import")
    assert response.status_code == 200
    assert response.json()["summary"]["rows_seen"] == 2
    assert response.json()["summary"]["unsupported_json_files"] == 1
    indices = [json.loads(row[0])["index"] for row in store.connection.execute(
        "SELECT provenance_json FROM personal_events ORDER BY subject").fetchall()]
    assert indices == [0, 1]


@pytest.mark.parametrize("kind", ["zip-utf16", "zip-utf32", "tar", "raw"])
def test_supported_archive_formats_and_encoding(tmp_path, kind):
    encoding = {"zip-utf16": "utf-16", "zip-utf32": "utf-32"}.get(kind, "utf-8")
    raw = json.dumps([_row("format")]).encode(encoding)
    content = io.BytesIO()
    if kind in {"zip-utf16", "zip-utf32"}:
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("endsong.json", raw)
        filename = "spotify.zip"
    elif kind == "tar":
        with tarfile.open(fileobj=content, mode="w") as archive:
            member = tarfile.TarInfo("endsong.json")
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
        filename = "spotify.tar"
    else:
        content.write(raw)
        filename = "spotify.json"
    client, store, _ = _client(tmp_path)
    sha = _upload(client, content.getvalue(), filename)
    assert client.post(f"/archives/{sha}/import").status_code == 200
    assert store.count_personal_events() == 1


def test_failed_member_cleans_private_staging_directory(tmp_path, monkeypatch):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("endsong.json", json.dumps([_row("good")])[:-1] + ", invalid]")
    client, store, inbox = _client(tmp_path / "archives")
    sha = _upload(client, data.getvalue(), "spotify.zip")
    stage_root = tmp_path / "stage"
    stage_root.mkdir()
    original = spotify_import.tempfile.TemporaryDirectory
    monkeypatch.setattr(spotify_import.tempfile, "TemporaryDirectory",
                        lambda **kwargs: original(dir=stage_root, **kwargs))
    with pytest.raises(ValueError):
        import_spotify_history(inbox, store, inbox.get(sha))
    assert list(stage_root.iterdir()) == []
    assert store.count_personal_events() == 0


def test_binary_array_detection_handles_short_reads(short_reader):
    raw = json.dumps([_row("format")]).encode("utf-16")
    assert spotify_import._is_array(lambda: short_reader(raw), True)


def test_raw_utf8_bom_preserves_previous_rejection(tmp_path):
    client, store, inbox = _client(tmp_path)
    sha = _upload(client, b"\xef\xbb\xbf" + json.dumps([_row()]).encode(), "spotify.json")
    with pytest.raises(ValueError):
        import_spotify_history(inbox, store, inbox.get(sha))
    assert store.count_personal_events() == 0
    assert inbox.get(sha).status == "failed"
