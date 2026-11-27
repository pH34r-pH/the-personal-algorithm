"""Offline seed tests use synthetic databases and in-memory Blob doubles."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from azure.core import MatchConditions

import personal_algorithm.snapshot_seed as module
from personal_algorithm.snapshot_seed import SeedError, seed_frozen_snapshot, verify_frozen_seed
from personal_algorithm.store import Store


class Download:
    def __init__(self, data):
        self.data = data

    def chunks(self):
        yield self.data[:17]
        yield self.data[17:]


class Blob:
    def __init__(self, initial=None):
        self.data = initial
        self.lost_response = False
        self.corrupt_download = False
        self.etag = '"synthetic-version"'

    def upload_blob(self, data, *, overwrite, max_concurrency):
        assert not overwrite and max_concurrency == 1
        if self.data is not None:
            raise RuntimeError("already exists")
        self.data = data.read()
        if self.lost_response:
            raise RuntimeError("response lost after upload")
        return {"etag": self.etag}

    def download_blob(self, *, etag, match_condition):
        assert etag == self.etag and match_condition == MatchConditions.IfNotModified
        return Download(b"incorrect" if self.corrupt_download else self.data)

    def get_blob_properties(self):
        if self.data is None:
            raise RuntimeError("missing blob")
        return SimpleNamespace(etag=self.etag)


def _source(path: Path) -> bytes:
    store = Store(path)
    store.connection.execute("CREATE TABLE synthetic_probe (value TEXT)")
    store.connection.execute("INSERT INTO synthetic_probe VALUES ('fixture')")
    store.connection.commit()
    store.connection.close()
    return path.read_bytes()


def test_seed_verifies_exact_content_and_keeps_source(tmp_path):
    source = tmp_path / "source.sqlite3"
    expected = _source(source)
    blob = Blob()
    receipt = seed_frozen_snapshot(source, blob, staging_dir=tmp_path)
    assert source.read_bytes() == expected == blob.data
    assert receipt.size == len(expected) and len(receipt.sha256) == 64
    assert receipt.sha256 not in repr(receipt)
    assert not list(tmp_path.glob("tpa-seed-*"))


def test_existing_destination_is_never_overwritten(tmp_path):
    source = tmp_path / "source.sqlite3"
    expected = _source(source)
    blob = Blob(b"existing destination")
    with pytest.raises(SeedError, match="ambiguous"):
        seed_frozen_snapshot(source, blob, staging_dir=tmp_path)
    assert blob.data == b"existing destination"
    assert source.read_bytes() == expected
    assert not list(tmp_path.glob("tpa-seed-*"))


@pytest.mark.parametrize("failure", ["lost-response", "bad-readback"])
def test_ambiguous_upload_is_retained_for_reconciliation(tmp_path, failure):
    source = tmp_path / "source.sqlite3"
    expected = _source(source)
    blob = Blob()
    blob.lost_response = failure == "lost-response"
    blob.corrupt_download = failure == "bad-readback"
    with pytest.raises(SeedError, match="reconcile"):
        seed_frozen_snapshot(source, blob, staging_dir=tmp_path)
    assert blob.data == expected
    assert source.read_bytes() == expected
    assert not list(tmp_path.glob("tpa-seed-*"))


def test_read_only_reconciliation_accepts_only_identical_seed(tmp_path):
    source = tmp_path / "source.sqlite3"
    expected = _source(source)
    blob = Blob()
    blob.lost_response = True
    with pytest.raises(SeedError):
        seed_frozen_snapshot(source, blob, staging_dir=tmp_path)
    receipt = verify_frozen_seed(source, blob, staging_dir=tmp_path)
    assert receipt.size == len(expected)
    assert source.read_bytes() == expected == blob.data
    blob.data = b"other data"
    with pytest.raises(SeedError, match="could not be verified"):
        verify_frozen_seed(source, blob, staging_dir=tmp_path)
    assert blob.data == b"other data"


def test_sidecars_or_invalid_database_never_create_destination(tmp_path):
    source = tmp_path / "source.sqlite3"
    _source(source)
    sidecar = Path(str(source) + "-wal")
    sidecar.write_bytes(b"synthetic sidecar")
    blob = Blob()
    with pytest.raises(SeedError, match="sidecars"):
        seed_frozen_snapshot(source, blob, staging_dir=tmp_path)
    assert blob.data is None
    sidecar.unlink()
    source.write_bytes(b"not sqlite")
    with pytest.raises(sqlite3.DatabaseError):
        seed_frozen_snapshot(source, blob, staging_dir=tmp_path)
    assert blob.data is None


def test_source_sidecar_appearing_during_copy_is_rejected(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    _source(source)
    blob = Blob()
    original = module._digest
    def inject_sidecar(reader, destination=None):
        result = original(reader, destination)
        if destination is not None:
            Path(str(source) + "-wal").write_bytes(b"synthetic sidecar")
        return result
    monkeypatch.setattr(module, "_digest", inject_sidecar)
    with pytest.raises(SeedError, match="sidecars"):
        seed_frozen_snapshot(source, blob, staging_dir=tmp_path)
    assert blob.data is None


def test_source_mutation_during_copy_is_rejected(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    _source(source)
    blob = Blob()
    original = module._digest
    def mutate_source(reader, destination=None):
        result = original(reader, destination)
        if destination is not None:
            with source.open("r+b") as handle:
                handle.seek(0)
                handle.write(b"synthetic change")
        return result
    monkeypatch.setattr(module, "_digest", mutate_source)
    with pytest.raises(SeedError, match="changed"):
        seed_frozen_snapshot(source, blob, staging_dir=tmp_path)
    assert blob.data is None
