"""Lease-fencing behavior using synthetic bytes and a fake Blob service."""

import asyncio
import io
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from personal_algorithm.application import ApplicationSettings, create_private_app
from personal_algorithm.connections import ConnectionStore
from personal_algorithm.leased_snapshot import LeasedSnapshot, SnapshotUnavailable
from personal_algorithm.providers import Connection, ConnectionStatus
from personal_algorithm.store import Store


class FakeLease:
    def __init__(self, blob):
        self.blob = blob

    def renew(self):
        with self.blob.lock:
            if self.blob.active is not self:
                self.blob.renew_failed.set()
            self.blob.require(self)
            self.blob.renewed.set()

    def release(self):
        with self.blob.lock:
            self.blob.require(self)
            self.blob.active = None


class FakeDownload:
    def __init__(self, data):
        self.data = data

    def readinto(self, destination):
        destination.write(self.data)


class FakeBlob:
    def __init__(self, data=b"initial"):
        self.data = data
        self.lock = threading.Lock()
        self.active = None
        self.renewed = threading.Event()
        self.renew_failed = threading.Event()
        self.fail_after_write = False
        self.checks = 0

    def require(self, lease):
        if self.active is not lease:
            raise RuntimeError("lease expired or superseded")

    def acquire_lease(self, *, lease_duration):
        with self.lock:
            if self.data is None or self.active is not None:
                raise RuntimeError("blob missing or already leased")
            self.active = FakeLease(self)
            return self.active

    def download_blob(self, *, lease):
        with self.lock:
            self.require(lease)
            return FakeDownload(self.data)

    def upload_blob(self, stream, *, overwrite, lease, max_concurrency):
        assert overwrite and max_concurrency == 1
        with self.lock:
            self.require(lease)
            self.data = stream.read()
            if self.fail_after_write:
                raise RuntimeError("response lost after publication")

    def get_blob_properties(self, *, lease):
        with self.lock:
            self.checks += 1
            self.require(lease)

    def expire(self):
        with self.lock:
            self.active = None


def test_lease_excludes_other_writers_and_fences_an_expired_writer(tmp_path):
    blob = FakeBlob()
    first, second = LeasedSnapshot(blob), LeasedSnapshot(blob)
    first.start()
    with pytest.raises(SnapshotUnavailable):
        second.start()
    destination = io.BytesIO()
    first.download(destination)
    assert destination.getvalue() == b"initial"
    blob.expire()
    replacement = LeasedSnapshot(blob)
    replacement.start()
    source = tmp_path / "snapshot"
    source.write_bytes(b"new generation")
    replacement.publish(source)
    source.write_bytes(b"stale generation")
    with pytest.raises(SnapshotUnavailable):
        first.publish(source)
    assert blob.data == b"new generation"
    with pytest.raises(SnapshotUnavailable):
        first.download(io.BytesIO())
    with pytest.raises(SnapshotUnavailable):
        first.close()
    replacement.close()


def test_missing_blob_does_not_create_empty_state():
    blob = FakeBlob(None)
    session = LeasedSnapshot(blob)
    with pytest.raises(SnapshotUnavailable):
        session.start()
    assert blob.data is None
    with pytest.raises(SnapshotUnavailable):
        session.start()
    session.close()


def test_ambiguous_publication_poisons_session(tmp_path):
    blob = FakeBlob()
    session = LeasedSnapshot(blob)
    session.start()
    source = tmp_path / "snapshot"
    source.write_bytes(b"possibly committed")
    blob.fail_after_write = True
    with pytest.raises(SnapshotUnavailable, match="ambiguous"):
        session.publish(source)
    assert blob.data == b"possibly committed"
    with pytest.raises(SnapshotUnavailable):
        session.download(io.BytesIO())
    session.close()


def test_renewal_failure_poisoning_and_release():
    blob = FakeBlob()
    session = LeasedSnapshot(blob, renew_interval=0.01)
    session.start()
    assert blob.renewed.wait(2)
    blob.expire()
    assert blob.renew_failed.wait(2)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            session.download(io.BytesIO())
        except SnapshotUnavailable:
            break
        time.sleep(0.01)
    else:
        pytest.fail("lease failure did not stop snapshot access")
    with pytest.raises(SnapshotUnavailable):
        session.close()
    replacement = LeasedSnapshot(blob)
    replacement.start()
    replacement.close()


def _seed_database(path):
    store = Store(path)
    store.connection.execute("CREATE TABLE fence_probe (value TEXT)")
    store.connection.execute("INSERT INTO fence_probe VALUES ('initial')")
    store.connection.commit()
    store.connection.close()
    return path.read_bytes()


def test_fenced_store_publishes_and_restores_sqlite(tmp_path):
    blob = FakeBlob(_seed_database(tmp_path / "seed.sqlite"))
    store = Store(tmp_path / "first.sqlite", leased_snapshot=LeasedSnapshot(blob))
    store.connection.execute("INSERT INTO fence_probe VALUES ('durable')")
    store.connection.commit()
    store.connection.close()
    restored = Store(tmp_path / "second.sqlite", leased_snapshot=LeasedSnapshot(blob))
    assert restored.connection.execute("SELECT value FROM fence_probe ORDER BY value").fetchall() == [
        ("durable",), ("initial",)
    ]
    restored.connection.close()


def test_failed_publication_stops_store_and_keeps_local_evidence(tmp_path):
    blob = FakeBlob(_seed_database(tmp_path / "seed.sqlite"))
    local = tmp_path / "local.sqlite"
    store = Store(local, leased_snapshot=LeasedSnapshot(blob))
    store.connection.execute("INSERT INTO fence_probe VALUES ('uncertain')")
    blob.fail_after_write = True
    with pytest.raises(SnapshotUnavailable, match="ambiguous"):
        store.connection.commit()
    with pytest.raises(SnapshotUnavailable):
        store.connection.execute("SELECT * FROM fence_probe")
    store.connection.close()
    with sqlite3.connect(local) as check:
        assert check.execute("SELECT COUNT(*) FROM fence_probe").fetchone() == (2,)
    with pytest.raises(SnapshotUnavailable, match="recovery required"):
        Store(local, leased_snapshot=LeasedSnapshot(blob))
    assert local.exists()


def test_fenced_store_rejects_corruption_and_lost_lease(tmp_path):
    corrupt = FakeBlob(b"not a SQLite database")
    with pytest.raises(sqlite3.DatabaseError):
        Store(tmp_path / "corrupt.sqlite", leased_snapshot=LeasedSnapshot(corrupt))
    assert not (tmp_path / "corrupt.sqlite").exists()
    assert corrupt.active is None

    blob = FakeBlob(_seed_database(tmp_path / "seed.sqlite"))
    store = Store(tmp_path / "local.sqlite", leased_snapshot=LeasedSnapshot(blob))
    cursor = store.connection.execute("SELECT * FROM fence_probe")
    blob.expire()
    with pytest.raises(SnapshotUnavailable):
        cursor.fetchone()
    with pytest.raises(SnapshotUnavailable):
        store.connection.execute("SELECT * FROM fence_probe")
    with pytest.raises(SnapshotUnavailable):
        store.connection.close()


def test_connection_listing_batches_results_and_preserves_fields(tmp_path):
    blob = FakeBlob(_seed_database(tmp_path / "seed.sqlite"))
    store = Store(tmp_path / "local.sqlite", leased_snapshot=LeasedSnapshot(blob))
    connections = ConnectionStore(store)
    entries = [
        Connection(
            provider_id=name, account_label=f"{name} account", status=ConnectionStatus.CONNECTED,
            granted_scopes=("read", "write"), connected_at=datetime(2026, 1, 1, tzinfo=UTC),
            credential_ref=f"secret://{name}", metadata={"name": name},
        )
        for name in ("zeta", "alpha", "beta")
    ]
    for entry in entries:
        connections.put(entry)
    blob.checks = 0
    assert connections.list() == tuple(sorted(entries, key=lambda item: item.provider_id))
    assert blob.checks == 4
    store.connection.close()


def test_fenced_application_auth_write_recovery_and_unfinished_request(tmp_path):
    blob = FakeBlob(_seed_database(tmp_path / "seed.sqlite"))
    settings = ApplicationSettings(database=str(tmp_path / "first.sqlite"),
                                   owner_object_id="owner", archive_dir=str(tmp_path / "archives"))
    app = create_private_app(settings, leased_snapshot=LeasedSnapshot(blob))

    @app.post("/synthetic-write")
    def write():
        app.state.store.connection.execute("INSERT INTO fence_probe VALUES ('durable')")
        app.state.store.connection.commit()
        return {"ok": True}

    @app.post("/synthetic-uncommitted")
    def unfinished():
        app.state.store.connection.execute("INSERT INTO fence_probe VALUES ('not-durable')")
        return {"ok": True}

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/connections").status_code == 401
        assert client.get("/connections", headers={"x-ms-client-principal-id": "owner"}).status_code == 200
        assert client.post("/synthetic-write").status_code == 200
        assert client.post("/synthetic-uncommitted").status_code == 503
        assert app.state.store.connection.execute("SELECT value FROM fence_probe ORDER BY value").fetchall() == [
            ("durable",), ("initial",)
        ]
    assert blob.active is None

    second = ApplicationSettings(database=str(tmp_path / "second.sqlite"),
                                 owner_object_id="owner", archive_dir=str(tmp_path / "archives"))
    restored = create_private_app(second, leased_snapshot=LeasedSnapshot(blob))
    with TestClient(restored) as client:
        assert client.get("/health").status_code == 200
        assert restored.state.store.connection.execute("SELECT value FROM fence_probe ORDER BY value").fetchall() == [
            ("durable",), ("initial",)
        ]


def test_fenced_application_fails_closed_after_lease_loss(tmp_path):
    blob = FakeBlob(_seed_database(tmp_path / "seed.sqlite"))
    settings = ApplicationSettings(database=str(tmp_path / "local.sqlite"),
                                   owner_object_id="owner", archive_dir=str(tmp_path / "archives"))
    app = create_private_app(settings, leased_snapshot=LeasedSnapshot(blob))
    # Shutdown reports the lost lease.
    with pytest.raises(SnapshotUnavailable), TestClient(app) as client:
        blob.expire()
        assert client.get("/health").status_code == 503


def test_health_rejects_ambiguous_snapshot_publication(tmp_path):
    blob = FakeBlob(_seed_database(tmp_path / "seed.sqlite"))
    settings = ApplicationSettings(database=str(tmp_path / "local.sqlite"),
                                   owner_object_id="owner", archive_dir=str(tmp_path / "archives"))
    app = create_private_app(settings, leased_snapshot=LeasedSnapshot(blob))

    @app.post("/synthetic-write")
    def write():
        app.state.store.connection.execute("INSERT INTO fence_probe VALUES ('uncertain')")
        app.state.store.connection.commit()
        return {"ok": True}

    with TestClient(app) as client:
        blob.fail_after_write = True
        assert client.post("/synthetic-write").status_code == 503
        assert client.get("/health").status_code == 503


def test_fenced_application_serializes_transactions_across_requests(tmp_path):
    blob = FakeBlob(_seed_database(tmp_path / "seed.sqlite"))
    settings = ApplicationSettings(database=str(tmp_path / "local.sqlite"),
                                   owner_object_id="owner", archive_dir=str(tmp_path / "archives"))
    app = create_private_app(settings, leased_snapshot=LeasedSnapshot(blob))
    entered, release = threading.Event(), threading.Event()

    @app.post("/synthetic-slow-write")
    def slow_write():
        app.state.store.connection.execute("INSERT INTO fence_probe VALUES ('pending')")
        entered.set()
        assert release.wait(5)
        app.state.store.connection.commit()
        return {"ok": True}

    @app.get("/synthetic-read")
    def read():
        rows = app.state.store.connection.execute("SELECT value FROM fence_probe ORDER BY value").fetchall()
        return {"values": [row[0] for row in rows]}

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(client.post, "/synthetic-slow-write")
        assert entered.wait(2)
        second = executor.submit(client.get, "/synthetic-read")
        try:
            time.sleep(0.1)
            assert not second.done()
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 200
        assert second.result(timeout=5).json() == {"values": ["initial", "pending"]}


def test_cancelled_fastapi_worker_finishes_before_next_transaction(tmp_path):
    blob = FakeBlob(_seed_database(tmp_path / "seed.sqlite"))
    settings = ApplicationSettings(database=str(tmp_path / "local.sqlite"),
                                   owner_object_id="owner", archive_dir=str(tmp_path / "archives"))
    app = create_private_app(settings, leased_snapshot=LeasedSnapshot(blob))
    entered, release = threading.Event(), threading.Event()
    second_entered = threading.Event()

    @app.post("/slow")
    def slow():
        app.state.store.connection.execute("INSERT INTO fence_probe VALUES ('first')")
        entered.set()
        assert release.wait(5)
        app.state.store.connection.commit()
        return {"ok": True}

    @app.post("/second")
    def second():
        second_entered.set()
        app.state.store.connection.execute("INSERT INTO fence_probe VALUES ('second')")
        app.state.store.connection.commit()
        return {"ok": True}

    async def scenario():
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message): pass
        def scope(path):
            return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                    "method": "POST", "scheme": "http", "path": path,
                    "raw_path": path.encode(), "query_string": b"", "root_path": "",
                    "headers": [], "client": ("127.0.0.1", 1), "server": ("localhost", 80)}
        first = asyncio.create_task(app(scope("/slow"), receive, send))
        assert await asyncio.to_thread(entered.wait, 2)
        first.cancel()
        second_task = asyncio.create_task(app(scope("/second"), receive, send))
        try:
            await asyncio.sleep(0.1)
            assert not second_entered.is_set()
        finally:
            release.set()
            outcomes = await asyncio.gather(first, second_task, return_exceptions=True)
        assert isinstance(outcomes[0], asyncio.CancelledError)
        assert outcomes[1] is None
        assert app.state.store.connection.execute("SELECT value FROM fence_probe ORDER BY value").fetchall() == [
            ("first",), ("initial",), ("second",)
        ]
    try:
        asyncio.run(scenario())
    finally:
        app.state.store.connection.close()


@pytest.mark.parametrize("disconnect_mode", ["cancel", "network"])
def test_cancelled_stalled_upload_cleans_up_and_health_remains_available(tmp_path, disconnect_mode):
    blob = FakeBlob(_seed_database(tmp_path / "seed.sqlite"))
    archive_dir = tmp_path / "archives"
    settings = ApplicationSettings(database=str(tmp_path / "local.sqlite"),
                                   owner_object_id="owner", archive_dir=str(archive_dir))
    app = create_private_app(settings, leased_snapshot=LeasedSnapshot(blob))

    async def scenario():
        waiting = asyncio.Event()
        never = asyncio.Event()
        first_chunk = True
        sent = []
        async def upload_receive():
            nonlocal first_chunk
            if first_chunk:
                first_chunk = False
                return {"type": "http.request", "body": b"synthetic partial", "more_body": True}
            waiting.set()
            await never.wait()
            return {"type": "http.disconnect"}
        async def send(message): sent.append(message)
        def scope(path, method, headers=None):
            return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                    "method": method, "scheme": "http", "path": path,
                    "raw_path": path.encode(), "query_string": b"", "root_path": "",
                    "headers": headers or [], "client": ("127.0.0.1", 1), "server": ("localhost", 80)}
        headers = [(b"x-ms-client-principal-id", b"owner"), (b"x-archive-filename", b"test.zip")]
        upload = asyncio.create_task(app(scope("/archives/upload", "POST", headers), upload_receive, send))
        try:
            await asyncio.wait_for(waiting.wait(), 2)
            await asyncio.wait_for(app(scope("/health", "GET"), None, send), 2)
            assert sent[0]["status"] == 200
            assert list(archive_dir.glob("archive-*"))
            if disconnect_mode == "cancel":
                upload.cancel()
            else:
                never.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(upload, 2)
            assert list(archive_dir.glob("archive-*")) == []
            assert app.state.store.connection.execute("SELECT COUNT(*) FROM archive_uploads").fetchone() == (0,)
        finally:
            if not upload.done():
                upload.cancel()
                await asyncio.gather(upload, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        app.state.store.connection.close()


@pytest.mark.parametrize("empty_frames", [False, True])
def test_idle_upload_times_out_and_cleans_up(tmp_path, empty_frames):
    blob = FakeBlob(_seed_database(tmp_path / "seed.sqlite"))
    archive_dir = tmp_path / "archives"
    settings = ApplicationSettings(database=str(tmp_path / "local.sqlite"),
                                   owner_object_id="owner", archive_dir=str(archive_dir))
    app = create_private_app(settings, leased_snapshot=LeasedSnapshot(blob), body_idle_timeout=0.1)

    async def scenario():
        first = True
        never = asyncio.Event()
        sent = []
        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": b"partial", "more_body": True}
            if empty_frames:
                await asyncio.sleep(0.02)
                return {"type": "http.request", "body": b"", "more_body": True}
            await never.wait()
        async def send(message): sent.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "POST", "scheme": "http", "path": "/archives/upload",
                 "raw_path": b"/archives/upload", "query_string": b"", "root_path": "",
                 "headers": [(b"x-ms-client-principal-id", b"owner"),
                             (b"x-archive-filename", b"test.zip")],
                 "client": ("127.0.0.1", 1), "server": ("localhost", 80)}
        await asyncio.wait_for(app(scope, receive, send), 2)
        assert sent[0]["status"] == 408
        assert list(archive_dir.glob("archive-*")) == []
        assert app.state.store.connection.execute("SELECT COUNT(*) FROM archive_uploads").fetchone() == (0,)
    try:
        asyncio.run(scenario())
    finally:
        app.state.store.connection.close()


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_idle_timeout_fails_before_acquiring_lease(tmp_path, timeout):
    blob = FakeBlob(_seed_database(tmp_path / "seed.sqlite"))
    settings = ApplicationSettings(database=str(tmp_path / "local.sqlite"),
                                   owner_object_id="owner", archive_dir=str(tmp_path / "archives"))
    with pytest.raises(ValueError, match="timeout"):
        create_private_app(settings, leased_snapshot=LeasedSnapshot(blob), body_idle_timeout=timeout)
    assert blob.active is None
    assert not (tmp_path / "local.sqlite").exists()
