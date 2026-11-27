import asyncio
import threading

import pytest

from personal_algorithm.fenced_http import FencedHttpGate, _DisconnectableReceive
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


def test_cancelled_request_waits_for_running_sync_worker_before_unlock():
    async def scenario():
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        calls, cleanups = [], []

        def worker():
            entered.set()
            assert release.wait(5)
            finished.set()

        async def app(scope, receive, send):
            calls.append(scope["path"])
            if scope["path"] == "/first":
                await asyncio.to_thread(worker)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def send(message): pass
        gate = FencedHttpGate(app, verify=lambda: None, cleanup=lambda: cleanups.append(finished.is_set()))
        first = asyncio.create_task(gate({"type": "http", "path": "/first"}, None, send))
        assert await asyncio.to_thread(entered.wait, 2)
        first.cancel()
        await asyncio.sleep(0.02)
        first.cancel()
        second = asyncio.create_task(gate({"type": "http", "path": "/second"}, None, send))
        try:
            await asyncio.sleep(0.05)
            assert calls == ["/first"]
            assert not cleanups
        finally:
            release.set()
            await asyncio.gather(first, second, return_exceptions=True)
        assert finished.is_set()
        assert cleanups == [True, True]
        assert first.cancelled()
        assert calls == ["/first", "/second"]
    asyncio.run(scenario())


def test_independently_cancelled_handler_fails_closed():
    async def scenario():
        calls, sent = [], []
        async def app(scope, receive, send):
            calls.append(scope["path"])
            raise asyncio.CancelledError
        async def send(message): sent.append(message)
        gate = FencedHttpGate(app, verify=lambda: None, cleanup=lambda: None)
        with pytest.raises(asyncio.CancelledError):
            await gate({"type": "http", "path": "/first"}, None, send)
        await gate({"type": "http", "path": "/second"}, None, send)
        assert calls == ["/first"]
        assert sent[0]["status"] == 503
    asyncio.run(scenario())


def test_idle_budget_resets_on_data_and_excludes_processing_time():
    async def scenario():
        count = 0
        async def receive():
            nonlocal count
            await asyncio.sleep(0.04)
            count += 1
            return {"type": "http.request", "body": b"x", "more_body": count < 3}
        reader = _DisconnectableReceive(receive, idle_timeout=0.1)
        for _ in range(3):
            message = await reader()
            assert message["body"] == b"x"
            await asyncio.sleep(0.12)  # Processing time isn't client idle time.
        assert not reader.timed_out
    asyncio.run(scenario())


def test_gate_rejects_invalid_body_max_duration():
    async def app(scope, receive, send):
        pass

    for bad in (0.0, -5.0, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="body max duration"):
            FencedHttpGate(app, verify=lambda: None, cleanup=lambda: None, body_max_duration=bad)


def test_gate_times_out_continuously_slow_body_when_max_duration_exceeded():
    async def scenario():
        sent = []
        cleanups = []

        async def slow_receive():
            await asyncio.sleep(0.03)
            return {"type": "http.request", "body": b"x", "more_body": True}

        async def app(scope, receive, send):
            while True:
                msg = await receive()
                if msg["type"] == "http.disconnect":
                    return

        async def send(message):
            sent.append(message)

        gate = FencedHttpGate(
            app,
            verify=lambda: None,
            cleanup=lambda: cleanups.append(True),
            body_idle_timeout=1.0,
            body_max_duration=0.05,
        )
        await gate({"type": "http", "path": "/upload", "method": "POST"}, slow_receive, send)
        assert sent[0]["status"] == 408
        assert sent[1]["body"] == b"Request body timed out"
        assert cleanups == [True]

    asyncio.run(scenario())
