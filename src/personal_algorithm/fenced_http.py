"""Prototype serialization and lease checks around complete HTTP requests.

This gate is enabled only when a leased snapshot is explicitly injected.
Background work and startup still require production qualification.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from typing import Any

from starlette.requests import ClientDisconnect

from .leased_snapshot import SnapshotUnavailable


class _DisconnectableReceive:
    def __init__(self, receive, idle_timeout: float, max_duration: float | None = None):
        self._receive = receive
        self.disconnected = asyncio.Event()
        self.timed_out = False
        self._idle_timeout = idle_timeout
        self._remaining = idle_timeout
        self._max_duration = max_duration
        self._deadline: float | None = None
        self._body_complete = False

    def _next_timeout(self, now: float) -> float | None:
        if self._body_complete:
            return None
        timeout = max(0.0, self._remaining)
        if self._max_duration is not None:
            if self._deadline is None:
                self._deadline = now + self._max_duration
            timeout = min(timeout, max(0.0, self._deadline - now))
        return timeout

    async def __call__(self):
        if self.disconnected.is_set():
            return {"type": "http.disconnect"}
        inbound = asyncio.create_task(self._receive())
        stop = asyncio.create_task(self.disconnected.wait())
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            done, _ = await asyncio.wait(
                (inbound, stop), timeout=self._next_timeout(started),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop in done:
                return {"type": "http.disconnect"}
            if not done:
                self.timed_out = True
                self.disconnected.set()
                return {"type": "http.disconnect"}
            message = inbound.result()
            if message["type"] == "http.disconnect":
                self.disconnected.set()
            elif message["type"] == "http.request":
                self._body_complete = not message.get("more_body", False)
                if message.get("body"):
                    self._remaining = self._idle_timeout
                else:
                    self._remaining -= loop.time() - started
            return message
        finally:
            for task in (inbound, stop):
                if not task.done():
                    task.cancel()
            await asyncio.gather(inbound, stop, return_exceptions=True)


class _RequestBodyTimeout(Exception):
    pass


class FencedHttpGate:
    """Serialize HTTP requests, check the lease, and roll back unfinished work."""

    def __init__(self, app: Any, verify: Callable[[], None], cleanup: Callable[[], None],
                 check_complete: Callable[[], None] | None = None,
                 body_idle_timeout: float = 120.0,
                 body_max_duration: float | None = None):
        if not math.isfinite(body_idle_timeout) or body_idle_timeout <= 0:
            raise ValueError("body idle timeout must be positive")
        if body_max_duration is not None and (
            not math.isfinite(body_max_duration) or body_max_duration <= 0
        ):
            raise ValueError("body max duration must be positive")
        self._app = app
        self._verify = verify
        self._cleanup = cleanup
        self._check_complete = check_complete
        self._body_idle_timeout = body_idle_timeout
        self._body_max_duration = body_max_duration
        self._lock = asyncio.Lock()
        self._broken = False

    async def _wait_for_handler(self, scope, receive, send):
        # Cancelling an await on a sync worker does not stop its thread.
        # Keep the handler alive and the gate held until it finishes.
        handler = asyncio.create_task(self._app(scope, receive, send))
        cancelled = False
        while True:
            try:
                await asyncio.shield(handler)
                break
            except asyncio.CancelledError:
                if handler.cancelled():
                    # The child may have left a worker thread running.
                    self._broken = True
                    raise
                cancelled = True
                receive.disconnected.set()
            except ClientDisconnect:
                if receive.timed_out:
                    raise _RequestBodyTimeout from None
                if receive.disconnected.is_set():
                    raise asyncio.CancelledError from None
                raise
        if cancelled:
            raise asyncio.CancelledError
        if receive.timed_out:
            raise _RequestBodyTimeout

    async def _cleanup_safely(self):
        # Cleanup must finish before another request can enter.
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

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if scope.get("path") == "/health" and scope.get("method") == "GET":
            await self._health(send)
            return
        async with self._lock:
            started = False
            wrapped_receive = _DisconnectableReceive(
                receive, self._body_idle_timeout, self._body_max_duration
            )

            async def checked_send(message):
                nonlocal started
                if wrapped_receive.disconnected.is_set():
                    return
                if message["type"] == "http.response.start" and self._check_complete is not None:
                    await asyncio.to_thread(self._check_complete)
                if message["type"] in {"http.response.start", "http.response.body"}:
                    await asyncio.to_thread(self._verify)
                await send(message)
                if message["type"] == "http.response.start":
                    started = True

            try:
                if self._broken:
                    raise SnapshotUnavailable("request cleanup failed")
                await asyncio.to_thread(self._verify)
                await self._wait_for_handler(scope, wrapped_receive, checked_send)
            except _RequestBodyTimeout:
                if started:
                    raise
                await send({"type": "http.response.start", "status": 408, "headers": []})
                await send({"type": "http.response.body", "body": b"Request body timed out"})
            except SnapshotUnavailable:
                if started:
                    # Headers are already on the wire; abort rather than emit a
                    # successful completion with potentially stale data.
                    raise
                await send({"type": "http.response.start", "status": 503, "headers": []})
                await send({"type": "http.response.body", "body": b"State unavailable"})
            finally:
                await self._cleanup_safely()

    async def _health(self, send):
        try:
            if self._broken:
                raise SnapshotUnavailable("request cleanup failed")
            await asyncio.to_thread(self._verify)
            if self._broken:
                raise SnapshotUnavailable("request cleanup failed")
            status, body = 200, b'{"status":"ok"}'
        except SnapshotUnavailable:
            status, body = 503, b'{"status":"unavailable"}'
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
