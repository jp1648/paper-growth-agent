"""Explicit read-only source capture; pagination completion is not economic completeness."""

import hashlib
import json
import re
from datetime import date
from pathlib import Path

from .alpaca import safe_error
from .models import now

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
            page = json.loads(raw)
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
