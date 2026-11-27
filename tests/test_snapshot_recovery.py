"""Read-only version recovery uses synthetic SQLite data and Blob doubles."""

import hashlib
import sqlite3
from types import SimpleNamespace

import pytest
from azure.core import MatchConditions

from personal_algorithm.snapshot_recovery import RecoveryError, staged_versioned_snapshot
from personal_algorithm.store import Store

VERSION = '2026-11-27T00:00:00.0000000Z'


class Download:
    def __init__(self, data):
        self.data = data

    def chunks(self):
        yield self.data[:17]
        yield self.data[17:]


class VersionedBlob:
    def __init__(self, data):
        self.version_id = VERSION
        self.reported_version = VERSION
        self.etag = '"synthetic-version"'
        self.data = data
        self.reported_size = len(data)
        self.downloads = 0

    def get_blob_properties(self):
        return SimpleNamespace(version_id=self.reported_version, etag=self.etag, size=self.reported_size)

    def download_blob(self, *, etag, match_condition):
        assert etag == self.etag and match_condition == MatchConditions.IfNotModified
        self.downloads += 1
        return Download(self.data)


def synthetic_database(path):
    store = Store(path)
    store.connection.execute("CREATE TABLE synthetic_probe (value TEXT)")
    store.connection.execute("INSERT INTO synthetic_probe VALUES ('fixture')")
    store.connection.commit()
    store.connection.close()
    return path.read_bytes()


def test_stages_only_requested_version_and_removes_private_copy(tmp_path):
    expected = synthetic_database(tmp_path / 'source.sqlite3')
    blob = VersionedBlob(expected)
    with staged_versioned_snapshot(blob, version_id=VERSION, staging_dir=tmp_path, max_bytes=len(expected)) as (path, receipt):
        assert path.read_bytes() == expected
        assert receipt.version_id == VERSION and receipt.etag == blob.etag and receipt.size == len(expected)
        assert receipt.sha256 == hashlib.sha256(expected).hexdigest()
        assert receipt.sha256 not in repr(receipt)
        assert path != tmp_path / 'source.sqlite3'
    assert not path.exists()
    assert (tmp_path / 'source.sqlite3').read_bytes() == expected
    assert not list(tmp_path.glob('tpa-recovery-*'))


@pytest.mark.parametrize('version', ['', '../snapshot', 'x?versionid=other', 'has space', 'x%2fy'])
def test_rejects_invalid_version_or_unbound_client_before_network(tmp_path, version):
    blob = VersionedBlob(b'not used')
    blob.version_id = version
    with pytest.raises(RecoveryError), staged_versioned_snapshot(blob, version_id=version, staging_dir=tmp_path, max_bytes=1024):
        pytest.fail('invalid version must not be staged')
    assert blob.downloads == 0


def test_base_blob_or_mismatched_client_cannot_silently_fall_back(tmp_path):
    blob = VersionedBlob(b'not used')
    for actual in (None, 'another-version'):
        blob.version_id = actual
        with pytest.raises(RecoveryError), staged_versioned_snapshot(blob, version_id=VERSION, staging_dir=tmp_path, max_bytes=1024):
            pytest.fail('unbound blob must not be staged')
    assert blob.downloads == 0


@pytest.mark.parametrize('limit', [0, -1, True, 1.5, '1024'])
def test_rejects_invalid_size_limit_before_download(tmp_path, limit):
    blob = VersionedBlob(b'not used')
    with pytest.raises(RecoveryError), staged_versioned_snapshot(
        blob, version_id=VERSION, staging_dir=tmp_path, max_bytes=limit
    ):
        pytest.fail('invalid size limit must not be used')
    assert blob.downloads == 0


@pytest.mark.parametrize('failure', ['version', 'etag', 'size', 'too_large'])
def test_rejects_bad_metadata_before_download(tmp_path, failure):
    data = synthetic_database(tmp_path / 'source.sqlite3')
    blob = VersionedBlob(data)
    if failure == 'version': blob.reported_version = 'different'
    if failure == 'etag': blob.etag = ''
    if failure == 'size': blob.reported_size = 0
    limit = len(data) - 1 if failure == 'too_large' else len(data)
    with pytest.raises(RecoveryError), staged_versioned_snapshot(blob, version_id=VERSION, staging_dir=tmp_path, max_bytes=limit):
        pytest.fail('invalid metadata must not be staged')
    assert blob.downloads == 0
    assert not list(tmp_path.glob('tpa-recovery-*'))


@pytest.mark.parametrize('failure', ['short', 'long', 'invalid_sqlite', 'missing_tables'])
def test_rejects_incomplete_or_invalid_snapshot_and_cleans_up(tmp_path, failure):
    data = synthetic_database(tmp_path / 'source.sqlite3')
    blob = VersionedBlob(data)
    if failure == 'short': blob.data = data[:-1]
    if failure == 'long': blob.data = data + b'x'
    if failure == 'invalid_sqlite': blob.data = b'x' * len(data)
    if failure == 'missing_tables':
        empty = tmp_path / 'empty.sqlite3'
        with sqlite3.connect(empty) as connection:
            connection.execute('CREATE TABLE wrong (value TEXT)')
        blob.data = empty.read_bytes()
        blob.reported_size = len(blob.data)
    with pytest.raises(RecoveryError), staged_versioned_snapshot(blob, version_id=VERSION, staging_dir=tmp_path, max_bytes=len(data) * 2):
        pytest.fail('invalid snapshot must not be staged')
    assert not list(tmp_path.glob('tpa-recovery-*'))


def test_caller_failure_propagates_and_removes_staging(tmp_path):
    data = synthetic_database(tmp_path / 'source.sqlite3')
    with pytest.raises(LookupError, match='caller'), staged_versioned_snapshot(
        VersionedBlob(data), version_id=VERSION, staging_dir=tmp_path, max_bytes=len(data)
    ):
        raise LookupError('caller')
    assert not list(tmp_path.glob('tpa-recovery-*'))


def test_interrupted_download_removes_partial_private_copy(tmp_path):
    data = synthetic_database(tmp_path / 'source.sqlite3')
    blob = VersionedBlob(data)
    class InterruptedDownload:
        def chunks(self):
            yield data[:1024]
            raise TimeoutError('synthetic network interruption')
    blob.download_blob = lambda **kwargs: InterruptedDownload()
    with pytest.raises(RecoveryError), staged_versioned_snapshot(
        blob, version_id=VERSION, staging_dir=tmp_path, max_bytes=len(data)
    ):
        pytest.fail('interrupted download must not be yielded')
    assert not list(tmp_path.glob('tpa-recovery-*'))
    assert (tmp_path / 'source.sqlite3').read_bytes() == data
