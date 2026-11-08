"""Retained raw SIP daily marks against a supplied broker calendar; no trading."""

import hashlib
import json
import re
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .alpaca import safe_error
from .corporate_actions import MAX_PAGE_BYTES, MAX_PAGES, MAX_TOTAL_BYTES
from .models import dec, dt, now

ENDPOINT = "https://data.alpaca.markets/v2/stocks/bars"


def capture_prices(provider, symbols, calendar, output):
    if not 1 <= len(symbols) <= 100 or len(set(symbols)) != len(symbols):
        raise ValueError("Require 1–100 distinct symbols")
    if any(not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", s) for s in symbols):
        raise ValueError("Require canonical stock symbols")
    with Path(calendar).open("rb") as stream:
        calendar_raw = stream.read(MAX_PAGE_BYTES + 1)
    if len(calendar_raw) > MAX_PAGE_BYTES:
        raise ValueError("Calendar byte bound exceeded")
    days = json.loads(calendar_raw)
    if not isinstance(days, list) or not 1 <= len(days) <= 366:
        raise ValueError("Require 1–366 broker calendar sessions")
    zone = ZoneInfo("America/New_York")
    sessions = []
    for day in days:
        stamp = datetime.strptime(day["date"], "%Y-%m-%d").date()
        if stamp.isoformat() != day["date"] or stamp >= now().astimezone(zone).date():
            raise ValueError("Require canonical completed historical session dates")
        if not time.fromisoformat(day["open"]) < time.fromisoformat(day["close"]):
            raise ValueError("Invalid regular-session boundaries")
        sessions.append(stamp)
    if sessions != sorted(set(sessions)) or (sessions[-1] - sessions[0]).days > 366:
        raise ValueError("Require unique increasing sessions within 366 days")
    root = Path(output)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    (root / "calendar.json").write_bytes(calendar_raw)
    params = {
        "symbols": ",".join(symbols),
        "start": datetime.combine(sessions[0], time.min, zone).isoformat(),
        "end": datetime.combine(sessions[-1], time.max, zone).isoformat(),
        "timeframe": "1Day",
        "adjustment": "raw",
        "feed": "sip",
        "currency": "USD",
        "asof": sessions[-1].isoformat(),
        "limit": 10000,
        "sort": "asc",
    }
    request = {
        "endpoint": ENDPOINT,
        "params": params.copy(),
        "started_at": now().isoformat(),
        "calendar_sha256": hashlib.sha256(calendar_raw).hexdigest(),
    }
    (root / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    expected = {(s, d.isoformat()) for s in symbols for d in sessions}
    records, pages, tokens = {}, [], set()
    total, status, error = 0, "failed", None
    try:
        for index in range(MAX_PAGES):
            raw = bytearray()
            with provider.client.stream(
                "GET", ENDPOINT, params=params, headers=provider.headers, follow_redirects=False
            ) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if len(raw) + len(chunk) > MAX_PAGE_BYTES or total > MAX_TOTAL_BYTES:
                        raise ValueError("Price response byte bound exceeded")
                    raw.extend(chunk)
            name = f"page-{index + 1:03d}.json"
            (root / name).write_bytes(raw)
            pages.append({"file": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            page = json.loads(raw, parse_float=dec)
            if not isinstance(page, dict) or not isinstance(page.get("bars"), dict):
                raise TypeError("Malformed price groups")
            for symbol, bars in page["bars"].items():
                if symbol not in symbols or not isinstance(bars, list):
                    raise ValueError("Unexpected symbol or malformed bars")
                for bar in bars:
                    stamp = dt(bar["t"]).astimezone(zone)
                    key = symbol, stamp.date().isoformat()
                    if key not in expected or stamp.time() != time.min:
                        raise ValueError("Daily bar is outside supplied sessions or not at NY midnight")
                    if isinstance(bar["c"], bool) or dec(bar["c"]) <= 0:
                        raise ValueError("Invalid close price")
                    if key in records and records[key] != bar:
                        raise ValueError("Conflicting daily bar")
                    records[key] = bar
            if "next_page_token" not in page:
                raise ValueError("Price pagination state missing")
            token = page["next_page_token"]
            if token is None:
                if set(records) != expected:
                    raise ValueError("Missing symbol/session marks; no forward fill allowed")
                status = "complete_requested_matrix"
                break
            if not isinstance(token, str) or not token.strip() or token in tokens:
                raise ValueError("Invalid or repeated price pagination token")
            tokens.add(token)
            params["page_token"] = token
        else:
            raise ValueError("Price pagination bound exceeded")
    except Exception as exc:
        error = safe_error(exc)
        raise
    finally:
        manifest = {
            **request,
            "finished_at": now().isoformat(),
            "status": status,
            "error": error,
            "pages": pages,
            "unique_marks": len(records),
            "expected_marks": len(expected),
            "calendar_authenticity_verified": False,
            "official_exchange_close_verified": False,
            "trial_acceptance": False,
        }
        with (root / "manifest.json").open("x") as stream:
            stream.write(json.dumps(manifest, indent=2) + "\n")
    return manifest
