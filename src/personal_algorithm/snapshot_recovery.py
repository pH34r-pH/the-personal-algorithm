"""Stage and validate one explicitly selected Azure Blob version for recovery.

This helper never changes the live blob or application. The caller must supply
a trusted version-bound BlobClient, choose the correct account/container/blob,
and independently fence writers and control any later restoration. Temporary
files contain private data and can survive a process or machine crash.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from azure.core import MatchConditions

from .snapshot_validation import validate_snapshot


class RecoveryError(RuntimeError):
    """The requested version could not be staged and validated safely."""


@dataclass(frozen=True, slots=True)
class RecoveryReceipt:
    version_id: str
    etag: str
    size: int
    sha256: str = field(repr=False)


def _valid_version(value: object) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 128 and value.isascii()
            and all(33 <= ord(char) <= 126 and char not in "?&#/\\%" for char in value))


def _version_metadata(blob: Any, version_id: str, max_bytes: int) -> tuple[str, int]:
    properties = blob.get_blob_properties()
    etag, size = properties.etag, properties.size
    if (properties.version_id != version_id or not isinstance(etag, str) or not etag
            or type(size) is not int or not 0 < size <= max_bytes):
        raise RecoveryError("version metadata is missing or outside the size limit")
    return etag, size


def _download_version(blob: Any, staged: Path, etag: str, size: int, max_bytes: int) -> str:
    download = blob.download_blob(etag=etag, match_condition=MatchConditions.IfNotModified)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    digest = hashlib.sha256()
    written = 0
    with os.fdopen(os.open(staged, flags, 0o600), "wb") as output:
        for chunk in download.chunks():
            if not isinstance(chunk, bytes):
                raise RecoveryError("snapshot stream returned an invalid chunk")
            written += len(chunk)
            if written > size or written > max_bytes:
                raise RecoveryError("snapshot exceeds its verified size")
            digest.update(chunk)
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    if written != size:
        raise RecoveryError("snapshot size does not match version metadata")
    return digest.hexdigest()


@contextmanager
def staged_versioned_snapshot(
    blob: Any, *, version_id: str, staging_dir: Path, max_bytes: int,
) -> Iterator[tuple[Path, RecoveryReceipt]]:
    """Yield a private, validated copy of exactly one immutable blob version.

    ``blob`` must be a trusted SDK client already bound to ``version_id``.
    Metadata must report that same version and its ETag is required on the
    download. The size cap is checked before and during transfer. This is a
    read-only building block, not an operator command or a safe cutover.
    """
    if (not _valid_version(version_id) or getattr(blob, "version_id", None) != version_id
            or type(max_bytes) is not int or max_bytes <= 0):
        raise RecoveryError("an explicit version-bound client and positive size limit are required")
    with tempfile.TemporaryDirectory(prefix="tpa-recovery-", dir=staging_dir) as directory:
        staged = Path(directory) / "snapshot.sqlite3"
        try:
            etag, size = _version_metadata(blob, version_id, max_bytes)
            digest = _download_version(blob, staged, etag, size, max_bytes)
            validate_snapshot(staged)
            receipt = RecoveryReceipt(version_id=version_id, etag=etag, size=size, sha256=digest)
        except Exception as exc:
            raise RecoveryError("selected version could not be staged or validated") from exc
        yield staged, receipt
