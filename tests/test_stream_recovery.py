import asyncio
import json

import pytest

from trader.streams import listen


@pytest.mark.parametrize("failure", ["clean_close", "auth_rejected", "subscription_rejected", "malformed"])
def test_stream_failure_backs_off_and_maintenance_remains_available(env, monkeypatch, failure):
    service, _, _ = env

    async def scenario():
        connections = []
        commands = []

        class Socket:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def send(self, _):
                pass

            async def recv(self):
                return json.dumps(
                    [{"T": "error", "msg": "PRIVATE-provider-message"}]
                    if failure == "auth_rejected"
                    else [{"msg": "authenticated"}]
                )

            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.sleep(0)
                if failure == "clean_close":
                    raise StopAsyncIteration
                if failure == "subscription_rejected":
                    return '[{"T":"error","msg":"PRIVATE-provider-message"}]'
                return "not json PRIVATE-provider-message"

        def connect(url, **kwargs):
            connections.append(url)
            return Socket()

        async def command(store, config, name, timeout, *args):
            commands.append(name)
            return {"exit_code": 0}

        monkeypatch.setattr("trader.streams.credentials", lambda: ("PRIVATE-key", "PRIVATE-secret"))
        monkeypatch.setattr("trader.streams.connect", connect)
        monkeypatch.setattr("trader.streams.run_command", command)
        task = asyncio.create_task(listen(service.cfg, service.store, "fixture.toml"))
        try:
            await asyncio.sleep(0.05)
            assert len(connections) == 3  # One connection per stream, no hot-loop reconnect.
            assert "maintain" in commands
            assert "tick" in commands
            interruptions = service.store.events("interruption")
            assert len(interruptions) == 3
            assert all(e["body"]["retry_seconds"] == 1 for e in interruptions)
            assert "PRIVATE" not in str(service.store.events())
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_duplicate_order_hints_do_not_write_fills_or_submit_orders(env, monkeypatch):
    service, broker, _ = env

    async def scenario():
        class Socket:
            def __init__(self):
                self.remaining = 2

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def send(self, _):
                pass

            async def recv(self):
                return '[{"msg":"authenticated"}]'

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.remaining:
                    self.remaining -= 1
                    return '{"stream":"trade_updates","data":{"event":"fill","order":{"id":"same"}}}'
                await asyncio.Event().wait()

        commands = []

        async def command(store, config, name, timeout, *args):
            commands.append(name)
            return {"exit_code": 0}

        monkeypatch.setattr("trader.streams.credentials", lambda: ("fixture", "fixture"))
        monkeypatch.setattr("trader.streams.connect", lambda *args, **kwargs: Socket())
        monkeypatch.setattr("trader.streams.run_command", command)
        task = asyncio.create_task(listen(service.cfg, service.store, "fixture.toml"))
        try:
            await asyncio.sleep(0.05)
            assert "maintain" in commands
            assert len(service.store.events("order_stream_hint")) == 6
            assert service.store.items("activity") == []
            assert broker.submits == 0
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
