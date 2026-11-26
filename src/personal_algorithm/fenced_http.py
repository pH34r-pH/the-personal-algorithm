"""Prototype serialization and lease checks around complete HTTP requests.

This gate is not enabled in the hosted application. Background work and startup
must also be fenced before it can serve as a complete single-writer boundary.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from .leased_snapshot import SnapshotUnavailable


class FencedHttpGate:
    """Serialize HTTP requests, check the lease, and roll back unfinished work."""

    def __init__(self, app: Any, *, verify: Callable[[], None], cleanup: Callable[[], None]):
        self._app = app
        self._verify = verify
        self._cleanup = cleanup
        self._lock = asyncio.Lock()
        self._broken = False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        async with self._lock:
            started = False

            async def checked_send(message):
                nonlocal started
                if message["type"] in {"http.response.start", "http.response.body"}:
                    await asyncio.to_thread(self._verify)
                await send(message)
                if message["type"] == "http.response.start":
                    started = True

            try:
                if self._broken:
                    raise SnapshotUnavailable("request cleanup failed")
                await asyncio.to_thread(self._verify)
                await self._app(scope, receive, checked_send)
            except SnapshotUnavailable:
                if started:
                    # Headers are already on the wire; abort rather than emit a
                    # successful completion with potentially stale data.
                    raise
                await send({"type": "http.response.start", "status": 503, "headers": []})
                await send({"type": "http.response.body", "body": b"State unavailable"})
            finally:
                # A cancelled task must not allow another request to inherit its
                # unfinished SQLite transaction. Shield cleanup before unlock.
                cleanup = asyncio.create_task(asyncio.to_thread(self._cleanup))
                cancelled = False
                while True:
                    try:
                        await asyncio.shield(cleanup)
                        break
                    except asyncio.CancelledError:
                        if cleanup.cancelled():
                            self._broken = True
                            raise
                        cancelled = True
                    except Exception:
                        self._broken = True
                        raise
                if cancelled:
                    raise asyncio.CancelledError
