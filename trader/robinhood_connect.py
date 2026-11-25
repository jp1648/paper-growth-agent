"""Interactive OAuth setup; no research calls, account reads or order tools."""

import argparse
import asyncio
import fcntl
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .robinhood_mcp import RobinhoodClient, RobinhoodUnavailable


def parse_callback(path):
    url = urlsplit(path)
    query = parse_qs(url.query)
    if "error" in query:
        error = query["error"][0]
        known = {
            "access_denied",
            "invalid_scope",
            "invalid_request",
            "unauthorized_client",
            "server_error",
            "temporarily_unavailable",
            "unsupported_response_type",
        }
        raise RobinhoodUnavailable(error if error in known else "provider_error")
    if url.path != "/callback" or len(query.get("code", [])) != 1 or len(query.get("state", [])) != 1:
        raise RobinhoodUnavailable("missing_code_or_state")
    return {"code": query["code"][0], "state": query["state"][0]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", type=Path, default=Path("state/robinhood-oauth.json"))
    args = parser.parse_args()
    args.token_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    received, ready = {}, threading.Event()

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Authorization codes must never enter request logs.

        def do_GET(self):
            if urlsplit(self.path).path != "/callback":
                self.send_error(404)
                return
            try:
                payload = parse_callback(self.path)
                valid = True
            except RobinhoodUnavailable as exc:
                payload = {"error": str(exc)}  # Enumerated safe reason, never raw OAuth fields.
                valid = False
            if not ready.is_set():
                received.update(payload)
                ready.set()
            self.send_response(200 if valid else 400)
            self.end_headers()
            self.wfile.write(
                b"Authorization callback received. Return to the Trader terminal for token-exchange status."
                if valid
                else ("Authorization failed: " + payload["error"] + ". Return to Trader.").encode()
            )

    server = HTTPServer(("127.0.0.1", 18765), Callback)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def redirect(url):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "robinhood.com" or parsed.path != "/oauth":
            raise ValueError("Unexpected Robinhood authorization endpoint")
        print("Open this Robinhood sign-in link on this computer:", flush=True)
        print(url, flush=True)

    async def callback():
        if not await asyncio.to_thread(ready.wait, 300):
            raise TimeoutError("OAuth sign-in timed out")
        if "error" in received:
            raise RobinhoodUnavailable(received["error"])
        return received["code"], received["state"]

    fd = os.open(args.token_file.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            client = RobinhoodClient(args.token_file)
            catalog = asyncio.run(
                asyncio.wait_for(client.exchange(redirect=redirect, callback=callback), 360)
            )
            # No response descriptions, tokens, account identifiers or credentials are printed.
            print(json.dumps({"connected": True, "research_tools": sorted(catalog)}))
    except Exception as exc:  # noqa: BLE001 - sanitize SDK/OAuth exception groups at credential boundary

        def error_types(error):
            return [type(error).__name__] + [
                name for child in getattr(error, "exceptions", []) for name in error_types(child)
            ]

        print(
            json.dumps(
                {
                    "connected": False,
                    "callback_received": ready.is_set(),
                    "callback_error": received.get("error"),
                    "error_types": error_types(exc),
                }
            )
        )
        raise SystemExit("Robinhood connection incomplete. No orders or research calls were made.") from None
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
