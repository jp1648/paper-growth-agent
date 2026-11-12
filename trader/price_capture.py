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


def request_parameters(symbols, calendar_raw, captured_at):
    if not 1 <= len(symbols) <= 100 or len(set(symbols)) != len(symbols):
        raise ValueError("Require 1–100 distinct symbols")
    if any(not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", s) for s in symbols):
        raise ValueError("Require canonical stock symbols")
    if len(calendar_raw) > MAX_PAGE_BYTES:
        raise ValueError("Calendar byte bound exceeded")
    days = json.loads(calendar_raw)
    if not isinstance(days, list) or not 1 <= len(days) <= 366:
        raise ValueError("Require 1–366 broker calendar sessions")
    zone = ZoneInfo("America/New_York")
    sessions = []
    for day in days:
        stamp = datetime.strptime(day["date"], "%Y-%m-%d").date()
        if stamp.isoformat() != day["date"] or stamp >= captured_at.astimezone(zone).date():
            raise ValueError("Require canonical completed historical session dates")
        if not time.fromisoformat(day["open"]) < time.fromisoformat(day["close"]):
            raise ValueError("Invalid regular-session boundaries")
        sessions.append(stamp)
    if sessions != sorted(set(sessions)) or (sessions[-1] - sessions[0]).days > 366:
        raise ValueError("Require unique increasing sessions within 366 days")
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
    return params, {(s, d.isoformat()) for s in symbols for d in sessions}


def merge_bars(page, expected, records):
    if not isinstance(page, dict) or not isinstance(page.get("bars"), dict):
        raise TypeError("Malformed price groups")
    symbols = {s for s, _ in expected}
    for symbol, bars in page["bars"].items():
        if symbol not in symbols or not isinstance(bars, list):
            raise ValueError("Unexpected symbol or malformed bars")
        for bar in bars:
            stamp = dt(bar["t"]).astimezone(ZoneInfo("America/New_York"))
            key = symbol, stamp.date().isoformat()
            if key not in expected or stamp.time() != time.min:
                raise ValueError("Daily bar is outside supplied sessions or not at NY midnight")
            if isinstance(bar["c"], bool) or dec(bar["c"]) <= 0:
                raise ValueError("Invalid close price")
            if key in records and records[key] != bar:
                raise ValueError("Conflicting daily bar")
            records[key] = bar


def capture_prices(provider, symbols, calendar, output):
    with Path(calendar).open("rb") as stream:
        calendar_raw = stream.read(MAX_PAGE_BYTES + 1)
    captured_at = now()
    params, expected = request_parameters(symbols, calendar_raw, captured_at)
    root = Path(output)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    (root / "calendar.json").write_bytes(calendar_raw)
    request = {
        "endpoint": ENDPOINT,
        "params": params.copy(),
        "started_at": captured_at.isoformat(),
        "calendar_sha256": hashlib.sha256(calendar_raw).hexdigest(),
    }
    (root / "request.json").write_text(json.dumps(request, indent=2) + "\n")
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
            merge_bars(page, expected, records)
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


def validate_prices(directory):
    root = Path(directory)

    def read(name):
        path = root / name
        if path.is_symlink():
            raise ValueError("Capture files must not be symlinks")
        with path.open("rb") as stream:
            raw = stream.read(MAX_PAGE_BYTES + 1)
        if len(raw) > MAX_PAGE_BYTES:
            raise ValueError("Capture file byte bound exceeded")
        return raw

    raw_manifest = read("manifest.json")
    manifest = json.loads(raw_manifest)
    request = json.loads(read("request.json"))
    calendar = read("calendar.json")
    if (
        manifest["status"] != "complete_requested_matrix"
        or manifest["error"] is not None
        or manifest["endpoint"] != ENDPOINT
        or request != {k: manifest[k] for k in ("endpoint", "params", "started_at", "calendar_sha256")}
        or hashlib.sha256(calendar).hexdigest() != manifest["calendar_sha256"]
        or dt(manifest["finished_at"]) < dt(manifest["started_at"])
    ):
        raise ValueError("Capture incomplete or request/calendar mismatch")
    params, expected = request_parameters(
        manifest["params"]["symbols"].split(","), calendar, dt(manifest["started_at"])
    )
    if params != manifest["params"]:
        raise ValueError("Unexpected price request policy")
    pages = manifest["pages"]
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise ValueError("Invalid price page count")
    records, tokens, total = {}, set(), 0
    for index, receipt in enumerate(pages, 1):
        name = f"page-{index:03d}.json"
        if receipt["file"] != name:
            raise ValueError("Pages must be consecutive local files")
        raw = read(name)
        total += len(raw)
        if (
            total > MAX_TOTAL_BYTES
            or len(raw) != receipt["bytes"]
            or hashlib.sha256(raw).hexdigest() != receipt["sha256"]
        ):
            raise ValueError("Price page size or hash mismatch")
        page = json.loads(raw, parse_float=dec)
        merge_bars(page, expected, records)
        token = page["next_page_token"]
        if index == len(pages):
            if token is not None:
                raise ValueError("Unconsumed price continuation token")
        elif not isinstance(token, str) or not token.strip() or token in tokens:
            raise ValueError("Invalid price pagination chain")
        else:
            tokens.add(token)
    if (
        set(records) != expected
        or len(records) != manifest["unique_marks"]
        or len(expected) != manifest["expected_marks"]
    ):
        raise ValueError("Price matrix/count mismatch")
    return {
        "status": "retained_matrix_validated",
        "manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
        "calendar_sha256": manifest["calendar_sha256"],
        "calendar": json.loads(calendar),
        "params": params,
        "finished_at": manifest["finished_at"],
        "marks": [{"symbol": s, "date": d, "bar": bar} for (s, d), bar in sorted(records.items())],
        "source_authenticity_verified": False,
        "trial_acceptance": False,
    }


def compare_prices(before, after):
    old, new = validate_prices(before), validate_prices(after)
    if old["params"] != new["params"] or old["calendar_sha256"] != new["calendar_sha256"]:
        raise ValueError("Revision comparison requires identical request policy and calendar")
    if dt(new["finished_at"]) < dt(old["finished_at"]):
        raise ValueError("Replacement capture predates original")
    changes = []
    for left, right in zip(old["marks"], new["marks"], strict=True):
        if left != right:
            changes.append(
                {
                    "symbol": left["symbol"],
                    "date": left["date"],
                    "before": left["bar"],
                    "after": right["bar"],
                    "close_changed": dec(left["bar"]["c"]) != dec(right["bar"]["c"]),
                }
            )
    return {
        "status": "revisions_require_review" if changes else "no_bar_changes_in_compared_captures",
        "before_manifest_sha256": old["manifest_sha256"],
        "after_manifest_sha256": new["manifest_sha256"],
        "changes": changes,
        "trial_acceptance": False,
    }
