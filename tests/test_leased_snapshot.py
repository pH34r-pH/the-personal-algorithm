"""Lease-fencing behavior using synthetic bytes and a fake Blob service."""

import io
import threading
import time

import pytest

from personal_algorithm.leased_snapshot import LeasedSnapshot, SnapshotUnavailable


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
