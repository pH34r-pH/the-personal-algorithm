import asyncio
import threading

import pytest

from personal_algorithm.fenced_http import FencedHttpGate
from personal_algorithm.leased_snapshot import SnapshotUnavailable


def test_gate_serializes_requests_until_cleanup_finishes():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        cleanup_entered = threading.Event()
        cleanup_release = threading.Event()
        order = []

        async def app(scope, receive, send):
            order.append(scope["path"])
            if scope["path"] == "/first":
                entered.set()
                await release.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        def cleanup():
            if not cleanup_entered.is_set():
                cleanup_entered.set()
                assert cleanup_release.wait(3)

        gate = FencedHttpGate(app, verify=lambda: None, cleanup=cleanup)
        async def receive(): return {"type": "http.request"}
        async def send(message): pass
        first = asyncio.create_task(gate({"type": "http", "path": "/first"}, receive, send))
        await entered.wait()
        second = asyncio.create_task(gate({"type": "http", "path": "/second"}, receive, send))
        await asyncio.sleep(0.02)
        assert order == ["/first"]
        release.set()
        assert await asyncio.to_thread(cleanup_entered.wait, 2)
        await asyncio.sleep(0.02)
        assert order == ["/first"]
        cleanup_release.set()
        await asyncio.gather(first, second)
        assert order == ["/first", "/second"]

    asyncio.run(scenario())


def test_gate_returns_503_if_lease_lost_before_response():
    async def scenario():
        calls = 0
        cleanups = []
        sent = []
        def verify():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SnapshotUnavailable("expired")
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
        async def send(message): sent.append(message)
        gate = FencedHttpGate(app, verify=verify, cleanup=lambda: cleanups.append(True))
        await gate({"type": "http"}, None, send)
        assert sent[0]["status"] == 503
        assert sent[1]["body"] == b"State unavailable"
        assert cleanups == [True]
    asyncio.run(scenario())


def test_gate_aborts_started_response_after_lease_loss():
    async def scenario():
        calls = 0
        sent = []
        cleanups = []
        def verify():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise SnapshotUnavailable("expired")
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"stale"})
        async def send(message): sent.append(message)
        gate = FencedHttpGate(app, verify=verify, cleanup=lambda: cleanups.append(True))
        with pytest.raises(SnapshotUnavailable):
            await gate({"type": "http"}, None, send)
        assert [message["type"] for message in sent] == ["http.response.start"]
        assert cleanups == [True]
    asyncio.run(scenario())


def test_cancellation_does_not_unlock_before_cleanup():
    async def scenario():
        cleanup_entered = threading.Event()
        cleanup_release = threading.Event()
        calls = []

        async def app(scope, receive, send):
            calls.append(scope["path"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        def cleanup():
            if not cleanup_entered.is_set():
                cleanup_entered.set()
                assert cleanup_release.wait(3)

        async def send(message): pass
        gate = FencedHttpGate(app, verify=lambda: None, cleanup=cleanup)
        first = asyncio.create_task(gate({"type": "http", "path": "/first"}, None, send))
        assert await asyncio.to_thread(cleanup_entered.wait, 2)
        first.cancel()
        await asyncio.sleep(0.02)
        first.cancel()
        second = asyncio.create_task(gate({"type": "http", "path": "/second"}, None, send))
        await asyncio.sleep(0.02)
        assert calls == ["/first"]
        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
        assert calls == ["/first", "/second"]
    asyncio.run(scenario())


def test_cleanup_failure_fails_closed_for_later_requests():
    async def scenario():
        calls, sent = [], []
        cleanups = 0
        async def app(scope, receive, send):
            calls.append(scope["path"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        def cleanup():
            nonlocal cleanups
            cleanups += 1
            if cleanups == 1:
                raise RuntimeError("rollback failed")
        async def send(message): sent.append(message)
        gate = FencedHttpGate(app, verify=lambda: None, cleanup=cleanup)
        with pytest.raises(RuntimeError, match="rollback failed"):
            await gate({"type": "http", "path": "/first"}, None, send)
        sent.clear()
        await gate({"type": "http", "path": "/second"}, None, send)
        assert calls == ["/first"]
        assert sent[0]["status"] == 503
    asyncio.run(scenario())
