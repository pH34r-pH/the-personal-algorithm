"""Exclusive, lease-fenced access to an existing Azure Blob snapshot.

Callers must stop serving local state after any failure and handle ambiguous
publication results. Live production cutover remains an explicit deployment
boundary outside this module.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO


class SnapshotUnavailable(RuntimeError):
    """The snapshot lease or an operation under it could not be trusted."""


class LeasedSnapshot:
    """Hold a finite lease and require it on every snapshot read and write.

    ``blob`` is an Azure BlobClient (or a compatible test double). The blob
    must already exist: initial migration is a separate operator action, never
    an implicit creation of an empty database.
    """

    def __init__(
        self, blob: Any, *, duration: int = 60, renew_interval: float = 20.0,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        if not 15 <= duration <= 60 or not 0 < renew_interval < duration / 2:
            raise ValueError("invalid finite snapshot lease timing")
        self._blob = blob
        self._duration = duration
        self._renew_interval = renew_interval
        self._on_close = on_close
        self._lease: Any = None
        self._failure: BaseException | None = None
        self._state_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._closed = False

    def start(self) -> None:
        with self._lifecycle_lock:
            self._start()

    def _start(self) -> None:
        with self._state_lock:
            if self._started or self._closed:
                raise SnapshotUnavailable("snapshot session cannot be restarted")
            self._started = True
        try:
            lease = self._blob.acquire_lease(lease_duration=self._duration)
        except Exception as exc:
            self._poison(exc)
            raise SnapshotUnavailable("could not acquire snapshot lease") from exc
        with self._state_lock:
            self._lease = lease
            self._thread = threading.Thread(target=self._renew, daemon=True)
            self._thread.start()

    def _poison(self, cause: BaseException) -> None:
        with self._state_lock:
            if self._failure is None:
                self._failure = cause
        self._stop.set()

    def _require_lease(self) -> Any:
        with self._state_lock:
            if self._closed or self._failure is not None or self._lease is None:
                raise SnapshotUnavailable("snapshot lease is unavailable")
            return self._lease

    def _renew(self) -> None:
        while not self._stop.wait(self._renew_interval):
            try:
                self._require_lease().renew()
            except Exception as exc:  # noqa: BLE001 - fail closed on any renewal failure
                self._poison(exc)
                return

    def download(self, destination: BinaryIO) -> None:
        """Read the leased blob; the caller owns temporary-file cleanup."""
        try:
            self._blob.download_blob(lease=self._require_lease()).readinto(destination)
            self._require_lease()
        except Exception as exc:
            self._poison(exc)
            raise SnapshotUnavailable("snapshot download failed") from exc

    def verify(self) -> None:
        """Ask Blob Storage to validate the lease before using local state."""
        try:
            self._blob.get_blob_properties(lease=self._require_lease())
            self._require_lease()
        except Exception as exc:
            self._poison(exc)
            raise SnapshotUnavailable("snapshot lease verification failed") from exc

    def publish(self, source: Path) -> None:
        """Atomically publish a complete snapshot with server-side fencing.

        An exception can mean the write succeeded but the response was lost;
        callers must treat the result as ambiguous and stop using local state.
        """
        try:
            with source.open("rb") as data:
                self._blob.upload_blob(
                    data, overwrite=True, lease=self._require_lease(), max_concurrency=1
                )
            self._require_lease()
        except Exception as exc:
            self._poison(exc)
            raise SnapshotUnavailable("snapshot publication failed or is ambiguous") from exc

    def close(self, *, timeout: float = 10.0) -> None:
        """Stop renewal before release; a stuck renewal is left to expire."""
        with self._lifecycle_lock:
            self._close(timeout)

    def _close(self, timeout: float) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            thread, lease = self._thread, self._lease
        self._stop.set()
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise SnapshotUnavailable("snapshot lease renewal did not stop")
        try:
            if lease is not None:
                lease.release()
        except Exception as exc:
            raise SnapshotUnavailable("snapshot lease release failed") from exc
        finally:
            if self._on_close is not None:
                self._on_close()
