"""Non-LLM event listener. Stream hints wake finite runs; REST reconciliation owns accounting."""

import asyncio
import json
import os
import sys
import time

from websockets.asyncio.client import connect

from .config import credentials
from .models import dec, dumps, now
from .observability import CONTEXT, traced


@traced("scheduler.command")
async def run_command(store, config_path, command, timeout, *args):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "trader.cli",
        "--config",
        config_path,
        command,
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        env={**os.environ, "OBSERVABILITY_TRACE_CONTEXT": json.dumps(CONTEXT.get() or {})},
    )
    try:
        code = await asyncio.wait_for(process.wait(), timeout)
        if code:
            store.log("error", {"command": command, "exit_code": code})
        print(dumps({"command": command, "exit_code": code, "at": now()}), flush=True)
        return {"exit_code": code}
    except TimeoutError:
        store.log("error", {"command": command, "reason": "Finite process exceeded time bound"})
        return {"status": "timeout"}
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 10)
            except TimeoutError:
                process.kill()
                await process.wait()


@traced("streams.listen", inputs=lambda settings, store, config_path: {"config_path": config_path})
async def listen(settings, store, config_path):
    key, secret = credentials()
    wake = asyncio.Event()
    maintenance_wake = asyncio.Event()
    quote_written = {}

    async def stream(url, auth, subscribe, kind):
        delay = 1
        while True:
            try:
                async with connect(
                    url, open_timeout=20, ping_interval=20, ping_timeout=20, max_size=2**22
                ) as ws:
                    await ws.send(json.dumps(auth))
                    authorized = False
                    for _ in range(5):
                        packet = json.loads(await asyncio.wait_for(ws.recv(), 20), parse_float=dec)
                        messages = packet if isinstance(packet, list) else [packet]
                        if any(
                            m.get("T") == "error" or m.get("data", {}).get("status") == "unauthorized"
                            for m in messages
                        ):
                            raise RuntimeError("Stream authentication rejected")
                        if any(
                            m.get("msg") == "authenticated" or m.get("data", {}).get("status") == "authorized"
                            for m in messages
                        ):
                            authorized = True
                            break
                    if not authorized:
                        raise RuntimeError("Stream authentication unconfirmed")
                    await ws.send(json.dumps(subscribe))
                    store.log("stream_connected", {"kind": kind, "at": now()})
                    wake.set()  # Always reconcile after reconnecting.
                    maintenance_wake.set()
                    delay = 1
                    async for packet in ws:
                        messages = json.loads(packet, parse_float=dec)
                        for message in messages if isinstance(messages, list) else [messages]:
                            if message.get("T") == "error":
                                raise RuntimeError("Stream subscription rejected")
                            if message.get("stream") == "trade_updates":
                                store.log("order_stream_hint", message["data"])
                                maintenance_wake.set()
                            elif message.get("T") == "q":
                                # Coalesce quotes; raw ticks alone never invoke an LLM.
                                stamp = time.monotonic()
                                if stamp - quote_written.get(message["S"], float("-inf")) < 1:
                                    continue
                                quote_written[message["S"]] = stamp
                                store.put(
                                    message["S"],
                                    {"message": message, "received_at": now(), "feed": settings.feed},
                                    "stream_quote",
                                )
                            elif message.get("T") == "n":
                                store.log(
                                    "news_stream_hint",
                                    {"id": message.get("id"), "symbols": message.get("symbols")},
                                )
                                wake.set()
                    # A normal socket close also needs backoff; otherwise this loop reconnects continuously.
                    raise ConnectionError("Stream closed; reconcile before reconnecting")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - failed cancellation/reconnect must remain observable
                store.log(
                    "interruption",
                    {
                        "stream": kind,
                        "error": type(e).__name__,
                        "retry_seconds": delay,
                        "fallback": "bounded REST polling",
                    },
                )
                wake.set()
                maintenance_wake.set()
                await asyncio.sleep(delay)
                delay = min(60, delay * 2)

    async def finite_runs():
        while True:
            try:
                await asyncio.wait_for(wake.wait(), timeout=settings.poll_seconds)
            except TimeoutError:
                pass
            event = wake.is_set()
            wake.clear()
            await run_command(store, config_path, "tick", 1800, *(["--event"] if event else []))
            await asyncio.sleep(settings.poll_seconds)

    async def maintenance():
        # Broker accounting/cancellation must not wait for a long research investigation.
        while True:
            maintenance_wake.clear()
            await run_command(store, config_path, "maintain", 180)
            try:
                await asyncio.wait_for(maintenance_wake.wait(), settings.poll_seconds)
            except TimeoutError:
                pass
            await asyncio.sleep(1)

    await asyncio.gather(
        stream(
            "wss://paper-api.alpaca.markets/stream",
            {"action": "auth", "key": key, "secret": secret},
            {"action": "listen", "data": {"streams": ["trade_updates"]}},
            "orders",
        ),
        stream(
            f"wss://stream.data.alpaca.markets/v2/{settings.feed}",
            {"action": "auth", "key": key, "secret": secret},
            {"action": "subscribe", "quotes": list(settings.universe)},
            "quotes",
        ),
        stream(
            "wss://stream.data.alpaca.markets/v1beta1/news",
            {"action": "auth", "key": key, "secret": secret},
            {"action": "subscribe", "news": list(settings.research_universe)},
            "news",
        ),
        finite_runs(),
        maintenance(),
    )
