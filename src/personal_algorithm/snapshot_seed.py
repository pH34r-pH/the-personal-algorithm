"""Seed a new Blob from a previously frozen standalone SQLite snapshot.

This helper does not stop or fence the old application. A protected deployment
must independently establish and maintain that freeze through cutover.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from azure.core import MatchConditions

from .snapshot_validation import validate_snapshot


class SeedError(RuntimeError):
    """Seeding failed; the destination may already contain data."""


@dataclass(frozen=True, slots=True)
class SeedReceipt:
    size: int
    etag: str
    sha256: str = field(repr=False)


def _digest(stream: BinaryIO, destination: BinaryIO | None = None) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        if destination is not None:
            destination.write(chunk)
        digest.update(chunk)
        size += len(chunk)
    return size, digest.hexdigest()


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    # Windows may report different ctime values for path and handle metadata.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _check_sidecars(source: Path) -> None:
    if any(Path(str(source) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise SeedError("source has SQLite sidecars; offline recovery is required")


def _stage_source(source: Path, destination: Path) -> tuple[int, str]:
    _check_sidecars(source)
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise SeedError("source must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(source, flags), "rb") as reader, destination.open("xb") as writer:
        if _identity(os.fstat(reader.fileno())) != _identity(before):
            raise SeedError("source changed before copy")
        size, digest = _digest(reader, writer)
        writer.flush()
        os.fsync(writer.fileno())
        reader.seek(0)
        second = _digest(reader)
        if (size, digest) != second or _identity(os.fstat(reader.fileno())) != _identity(before):
            raise SeedError("source changed during copy")
    if _identity(source.lstat()) != _identity(before):
        raise SeedError("source was replaced during copy")
    _check_sidecars(source)
    validate_snapshot(destination)
    return size, digest


def _verify_blob(blob: Any, etag: str | None, size: int, digest: str) -> SeedReceipt:
    if not isinstance(etag, str) or not etag:
        raise SeedError("destination has no verifiable version")
    download = blob.download_blob(etag=etag, match_condition=MatchConditions.IfNotModified)
    remote_hash = hashlib.sha256()
    remote_size = 0
    for chunk in download.chunks():
        remote_hash.update(chunk)
        remote_size += len(chunk)
    if (remote_size, remote_hash.hexdigest()) != (size, digest):
        raise SeedError("destination did not match the staged source")
    return SeedReceipt(size=size, etag=etag, sha256=digest)


def seed_frozen_snapshot(source: Path, blob: Any, *, staging_dir: Path) -> SeedReceipt:
    """Create and verify an absent Blob; never replace or delete a destination.

    The caller is responsible for proving the source writer is stopped and
    stays stopped until cutover. On any error, including a lost upload response,
    the Blob may exist and must be reconciled explicitly; it is never deleted.
    Temporary files contain private data and need secure handling after crashes.
    """
    with tempfile.TemporaryDirectory(prefix="tpa-seed-", dir=staging_dir) as directory:
        staged = Path(directory) / "snapshot.sqlite3"
        size, digest = _stage_source(source, staged)
        try:
            with staged.open("rb") as data:
                result = blob.upload_blob(data, overwrite=False, max_concurrency=1)
            return _verify_blob(blob, result.get("etag"), size, digest)
        except Exception as exc:
            raise SeedError("seed failed or is ambiguous; retain and reconcile destination") from exc


def verify_frozen_seed(source: Path, blob: Any, *, staging_dir: Path) -> SeedReceipt:
    """Compare a previously created Blob, without modifying source or target.

    Equality is only a point-in-time observation. The caller must still hold
    the old writer stopped and prevent target changes through cutover.
    """
    with tempfile.TemporaryDirectory(prefix="tpa-seed-", dir=staging_dir) as directory:
        staged = Path(directory) / "snapshot.sqlite3"
        size, digest = _stage_source(source, staged)
        try:
            properties = blob.get_blob_properties()
            return _verify_blob(blob, properties.etag, size, digest)
        except Exception as exc:
            raise SeedError("existing seed could not be verified; retain it for recovery") from exc
