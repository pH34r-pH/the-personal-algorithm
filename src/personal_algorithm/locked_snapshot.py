"""Create a temporary SQLite backup while holding a local writer reservation.

This is a building block, not a migration or a cross-replica fence. The caller
must establish exact process/path identity and maintain all external boundaries
until cutover. A crash can leave the private staged copy on disk.
"""
from __future__ import annotations

import math
import os
import sqlite3
import stat
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .snapshot_validation import validate_snapshot


class LockSnapshotError(RuntimeError):
    """A local writer reservation or its staged copy could not be verified."""


def _identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise LockSnapshotError("source must be a regular non-symlink file")
    return info.st_dev, info.st_ino


@dataclass(slots=True)
class LockedSnapshot:
    """An ephemeral backup valid only while its enclosing context remains open."""
    path: Path
    _source: Path
    _identity: tuple[int, int]
    _connection: sqlite3.Connection

    def verify(self) -> None:
        try:
            valid = self._connection.in_transaction and _identity(self._source) == self._identity
        except (sqlite3.Error, OSError) as exc:
            raise LockSnapshotError("writer reservation or source identity unavailable") from exc
        if not valid:
            raise LockSnapshotError("writer reservation or source identity changed")


@contextmanager
def locked_snapshot(source: Path, *, staging_dir: Path, timeout: float = 30.0) -> Iterator[LockedSnapshot]:
    """Reserve SQLite writes and yield a validated separate-connection backup.

    This captures committed local state even if a different snapshot lags.
    It cannot stop another replica, file replacement, external side effects, or
    writers after this process exits. Call ``verify`` while holding the context;
    independently fence those hazards before relying on it for any cutover.
    """
    if not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("timeout must be positive, finite, and at most 120 seconds")
    source = Path(source).absolute()
    identity = _identity(source)
    uri = source.as_uri() + "?mode=rw"
    guard = sqlite3.connect(uri, uri=True, timeout=timeout)
    try:
        guard.execute("BEGIN IMMEDIATE")
        if _identity(source) != identity:
            raise LockSnapshotError("source identity changed before writer reservation")
        with tempfile.TemporaryDirectory(prefix="tpa-locked-", dir=staging_dir) as directory:
            fd, name = tempfile.mkstemp(prefix="snapshot-", suffix=".sqlite3", dir=directory)
            os.close(fd)
            staged = Path(name)
            reader = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=timeout)
            try:
                backup = sqlite3.connect(str(staged), timeout=timeout)
                try:
                    deadline = time.monotonic() + timeout
                    def progress(_status: int, _remaining: int, _total: int) -> None:
                        if time.monotonic() > deadline:
                            raise LockSnapshotError("SQLite backup exceeded its deadline")
                    reader.backup(backup, pages=256, progress=progress, sleep=0.05)
                    backup.commit()
                finally:
                    backup.close()
            finally:
                reader.close()
            locked = LockedSnapshot(staged, source, identity, guard)
            locked.verify()
            validate_snapshot(staged)
            locked.verify()
            yield locked
    finally:
        if guard.in_transaction:
            guard.rollback()
        guard.close()
