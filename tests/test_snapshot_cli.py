"""Tests for the non-destructive operator snapshot CLI."""

import hashlib
import io
import json
import sqlite3

import personal_algorithm.snapshot_cli as cli
from personal_algorithm.store import SCHEMA


def _make_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    return path.read_bytes()


class _FakeBlob:
    def __init__(self, payload: bytes = b"", version_id: str | None = None, etag: str = '"e1"'):
        self.payload = payload
        self.version_id = version_id
        self.etag = etag
        self.uploaded = None
        self.closed = False

    def upload_blob(self, data, *, overwrite=True, max_concurrency=1):
        assert overwrite is False
        self.uploaded = data.read()
        self.payload = self.uploaded
        return {"etag": self.etag}

    def get_blob_properties(self):
        class Props:
            pass
        props = Props()
        props.version_id = self.version_id
        props.etag = self.etag
        props.size = len(self.payload)
        return props

    def download_blob(self, *, etag=None, match_condition=None):
        assert etag == self.etag
        stream = io.BytesIO(self.payload)
        class Download:
            def chunks(self):
                while chunk := stream.read(4096):
                    yield chunk
        return Download()


def test_backup_locked_writes_new_file_and_refuses_overwrite(tmp_path, capsys):
    src = tmp_path / "live.sqlite3"
    _make_db(src)
    out = tmp_path / "backup.sqlite3"
    staging = tmp_path / "stage"
    staging.mkdir()

    rc = cli.main(["backup-locked", "--source", str(src), "--output", str(out), "--staging-dir", str(staging)])
    assert rc == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["output"] == str(out)
    assert receipt["size"] == out.stat().st_size
    assert receipt["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()

    rc2 = cli.main(["backup-locked", "--source", str(src), "--output", str(out), "--staging-dir", str(staging)])
    assert rc2 == 1
    assert "overwrite" in capsys.readouterr().err


def test_seed_and_verify_seed_cli(tmp_path, monkeypatch, capsys):
    src = tmp_path / "frozen.sqlite3"
    payload = _make_db(src)
    staging = tmp_path / "stage"
    staging.mkdir()
    fake = _FakeBlob()
    closed = []
    monkeypatch.setattr(cli, "configured_blob_client", lambda url, cid, version_id=None: (fake, lambda: closed.append(True)))

    url = "https://acct123.blob.core.windows.net/state/snap.sqlite3"
    cid = "11111111-2222-3333-4444-555555555555"
    assert cli.main(["seed", "--source", str(src), "--blob-url", url, "--identity-client-id", cid, "--staging-dir", str(staging)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["size"] == len(payload)
    assert receipt["sha256"] == hashlib.sha256(payload).hexdigest()

    assert cli.main(["verify-seed", "--source", str(src), "--blob-url", url, "--identity-client-id", cid, "--staging-dir", str(staging)]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified == receipt
    assert len(closed) == 2


def test_stage_version_cli_verifies_and_refuses_overwrite(tmp_path, monkeypatch, capsys):
    src = tmp_path / "v1.sqlite3"
    payload = _make_db(src)
    out = tmp_path / "recovered.sqlite3"
    staging = tmp_path / "stage"
    staging.mkdir()
    fake = _FakeBlob(payload=payload, version_id="2026-11-27T00:00:00Z", etag='"v1"')
    closed = []
    monkeypatch.setattr(cli, "configured_blob_client", lambda url, cid, version_id=None: (fake, lambda: closed.append(True)))

    url = "https://acct123.blob.core.windows.net/state/snap.sqlite3"
    cid = "11111111-2222-3333-4444-555555555555"
    argv = [
        "stage-version", "--blob-url", url, "--identity-client-id", cid,
        "--version-id", "2026-11-27T00:00:00Z", "--output", str(out),
        "--staging-dir", str(staging), "--max-bytes", str(len(payload) + 1024),
    ]
    assert cli.main(argv) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["version_id"] == "2026-11-27T00:00:00Z"
    assert receipt["sha256"] == hashlib.sha256(payload).hexdigest()
    assert out.read_bytes() == payload
    assert closed == [True]

    assert cli.main(argv) == 1
    assert "overwrite" in capsys.readouterr().err
