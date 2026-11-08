"""Explicit read-only source capture; pagination completion is not economic completeness."""

import hashlib
import json
import re
from datetime import date
from pathlib import Path

from .alpaca import safe_error
from .models import dec, dt, now

ENDPOINT = "https://data.alpaca.markets/v1/corporate-actions"
MAX_PAGE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 10 * MAX_PAGE_BYTES
MAX_PAGES = 100


def capture_actions(provider, symbol, start, end, output):
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol):
        raise ValueError("Require one canonical stock symbol")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first.isoformat() != start or last.isoformat() != end or not 0 <= (last - first).days <= 366:
        raise ValueError("Require an ordered canonical processing-date range of at most 366 days")
    root = Path(output)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    params = {
        "symbols": symbol,
        "start": start,
        "end": end,
        "data_quality": "all",
        "limit": 1000,
        "sort": "asc",
    }
    request = {"endpoint": ENDPOINT, "params": params.copy(), "started_at": now().isoformat()}
    (root / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    pages, identities, tokens = [], {}, set()
    total = 0
    status = "failed"
    error = None
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
                        raise ValueError("Corporate-action response byte bound exceeded")
                    raw.extend(chunk)
            name = f"page-{index + 1:03d}.json"
            (root / name).write_bytes(raw)
            pages.append({"file": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
            page = json.loads(raw, parse_float=dec)
            if not isinstance(page, dict) or not isinstance(page.get("corporate_actions"), dict):
                raise TypeError("Malformed corporate-action page")
            for kind, records in page["corporate_actions"].items():
                if not isinstance(records, list):
                    raise TypeError("Malformed corporate-action group")
                for record in records:
                    if (
                        not isinstance(record, dict)
                        or not isinstance(record.get("id"), str)
                        or not record["id"].strip()
                    ):
                        raise ValueError("Corporate action missing identity")
                    identifier = record["id"]
                    value = {"kind": kind, "record": record}
                    if identifier in identities and identities[identifier] != value:
                        raise ValueError("Conflicting corporate-action identity")
                    identities[identifier] = value
            if "next_page_token" not in page:
                raise ValueError("Corporate-action pagination state missing")
            token = page["next_page_token"]
            if token is None:
                status = "pagination_exhausted"
                break
            if not isinstance(token, str) or not token.strip() or token in tokens:
                raise ValueError("Invalid or repeated corporate-action pagination token")
            tokens.add(token)
            params["page_token"] = token
        else:
            raise ValueError("Corporate-action pagination bound exceeded")
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
            "unique_actions": len(identities),
            "history_completeness_verified": False,
            "economic_fields_validated": False,
        }
        with (root / "manifest.json").open("x") as stream:
            stream.write(json.dumps(manifest, indent=2) + "\n")
    return manifest


def validate_cash_dividends(directory):
    """Validate retained ordinary cash events, without asserting entitlements or coverage."""
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

    manifest_raw = read("manifest.json")
    manifest = json.loads(manifest_raw)
    request = json.loads(read("request.json"))
    if (
        manifest["status"] != "pagination_exhausted"
        or manifest["error"] is not None
        or manifest["endpoint"] != ENDPOINT
        or request != {k: manifest[k] for k in ("endpoint", "params", "started_at")}
        or dt(manifest["finished_at"]) < dt(manifest["started_at"])
    ):
        raise ValueError("Capture is incomplete or its request does not match")
    params = manifest["params"]
    symbol = params["symbols"]
    if params["data_quality"] != "all" or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol):
        raise ValueError("Invalid capture symbol or quality filter")
    start, end = date.fromisoformat(params["start"]), date.fromisoformat(params["end"])
    if start > end:
        raise ValueError("Invalid capture processing-date interval")
    pages = manifest["pages"]
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise ValueError("Invalid capture page count")
    records, tokens, total = {}, set(), 0
    for index, receipt in enumerate(pages, 1):
        name = f"page-{index:03d}.json"
        if receipt["file"] != name:
            raise ValueError("Capture pages must be consecutive local files")
        raw = read(name)
        total += len(raw)
        if (
            total > MAX_TOTAL_BYTES
            or len(raw) != receipt["bytes"]
            or hashlib.sha256(raw).hexdigest() != receipt["sha256"]
        ):
            raise ValueError("Capture page size or hash mismatch")
        page = json.loads(raw, parse_float=dec)
        token = page["next_page_token"]
        if index == len(pages):
            if token is not None:
                raise ValueError("Capture has an unconsumed continuation token")
        elif not isinstance(token, str) or not token.strip() or token in tokens:
            raise ValueError("Capture pagination is incomplete or cyclic")
        else:
            tokens.add(token)
        for kind, actions in page["corporate_actions"].items():
            if not isinstance(actions, list):
                raise TypeError("Invalid corporate-action group")
            if actions and kind != "cash_dividends":
                raise ValueError("Non-cash action requires separate accounting")
            for action in actions:
                identifier = action["id"]
                if not isinstance(identifier, str) or not identifier.strip() or action["symbol"] != symbol:
                    raise ValueError("Invalid cash-dividend identity or symbol")
                if identifier in records and records[identifier] != action:
                    raise ValueError("Conflicting cash-dividend identity")
                if action.get("foreign") is not False or action.get("special") is not False:
                    raise ValueError("Foreign or special distributions require separate review")
                dates = {
                    k: date.fromisoformat(action[k])
                    for k in ("ex_date", "record_date", "payable_date", "process_date")
                }
                if any(v.isoformat() != action[k] for k, v in dates.items()):
                    raise ValueError("Dividend dates must be canonical")
                if (
                    not dates["ex_date"] <= dates["record_date"] <= dates["payable_date"]
                    or not start <= dates["process_date"] <= end
                ):
                    raise ValueError("Unsupported dividend dates or processing interval")
                if isinstance(action["rate"], bool) or dec(action["rate"]) <= 0:
                    raise ValueError("Dividend rate must be finite and positive")
                records[identifier] = action
    if len(records) != manifest["unique_actions"]:
        raise ValueError("Capture action count mismatch")
    return {
        "status": "ordinary_cash_fields_validated",
        "symbol": symbol,
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "dividends": list(records.values()),
        "history_completeness_verified": False,
        "entitlements_verified": False,
        "net_withholding_verified": False,
    }
