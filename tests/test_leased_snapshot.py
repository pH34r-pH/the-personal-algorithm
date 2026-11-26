"""Lease-fencing behavior using synthetic bytes and a fake Blob service."""

import io
import sqlite3
import threading
import time
from datetime import UTC, datetime

import pytest

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
