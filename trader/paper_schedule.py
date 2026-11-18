"""Offline prospective schedule draft; no runner integration or activation."""

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import now

SYMBOLS = ("AAPL", "MSFT", "AMZN", "PG", "CAT")


def build_file(source, output):
    with Path(source).open("rb") as stream:
        raw = stream.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("Schedule input exceeds byte bound")
    supplied = json.loads(raw)
    identity = supplied["study_id"]
    if not isinstance(identity, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", identity):
        raise ValueError("Require a bounded lowercase study ID")
    start = date.fromisoformat(supplied["start_session"])
    zone = ZoneInfo("America/New_York")
    if start.isoformat() != supplied["start_session"] or start <= now().astimezone(zone).date():
        raise ValueError("Require a canonical future starting session")
    calendar = supplied["calendar"]
    if not isinstance(calendar, list) or not 60 <= len(calendar) <= 366:
        raise ValueError("Require 60–366 supplied calendar sessions")
    dates, sessions = [], []
    for row in calendar:
        day = date.fromisoformat(row["date"])
        opening, closing = time.fromisoformat(row["open"]), time.fromisoformat(row["close"])
        if (
            day.isoformat() != row["date"]
            or day.weekday() >= 5
            or opening.tzinfo is not None
            or closing.tzinfo is not None
            or not time(9, 30) == opening < closing <= time(16)
        ):
            raise ValueError("Require canonical regular NY session boundaries")
        dates.append(day)
        sessions.append(
            {
                "date": day.isoformat(),
                "open": datetime.combine(day, opening, zone).isoformat(),
                "close": datetime.combine(day, closing, zone).isoformat(),
            }
        )
    if dates != sorted(set(dates)) or (dates[-1] - dates[0]).days > 366 or start not in dates:
        raise ValueError("Require increasing unique bounded calendar containing start")
    first = dates.index(start)
    eligible = dates[first:]
    after_twelve_weeks = next((i for i, d in enumerate(eligible) if d >= start + timedelta(days=84)), None)
    if len(eligible) < 60 or after_twelve_weeks is None:
        raise ValueError("Calendar does not cover both window minima")
    end_index = first + max(59, after_twelve_weeks)
    opportunities = []
    for week in range(12):
        lower, upper = start + timedelta(days=7 * week), start + timedelta(days=7 * (week + 1))
        days = [row for row, d in zip(sessions, dates, strict=True) if lower <= d < upper]
        if len(days) < 3:
            raise ValueError("Each study week requires three sessions; review exceptional closures")
        for row in (days[0], days[len(days) // 2], days[-1]):
            number = len(opportunities) + 1
            claim_from = datetime.fromisoformat(row["open"]) + timedelta(minutes=30)
            claim_before = datetime.fromisoformat(row["close"]) - timedelta(minutes=30)
            if claim_from >= claim_before:
                raise ValueError("Session too short for the reviewed claiming window")
            opportunities.append(
                {
                    "id": f"{identity}:scheduled:{number:02d}",
                    "symbol": SYMBOLS[(number - 1) % len(SYMBOLS)],
                    "week": week + 1,
                    "claim_from": claim_from.isoformat(),
                    "claim_before": claim_before.isoformat(),
                    **row,
                }
            )
    result = {
        "version": 1,
        "status": "draft_not_activated",
        "study_id": identity,
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "research_universe": list(SYMBOLS),
        "execution_universe": list(SYMBOLS[:3]),
        "start_session": start.isoformat(),
        "end_session": dates[end_index].isoformat(),
        "expected_sessions": sessions[first : end_index + 1],
        "opportunities": opportunities,
        "activation_authorized": False,
        "policy": {
            "sampling": "First, middle (floor n/2), last session in each start-anchored seven-day block",
            "missed_slots": "Retain missed/failed/deferred slots; no automatic rescheduling or paid catchup",
            "safety": "Safety reviews have priority and are recorded separately under the same budget",
            "budget": "Fresh funding and remaining daily/run allowance must pass before every paid call",
            "claiming": "Open+30 inclusive to close-30 exclusive; claim once before any paid request",
            "completion": "Claimed run may finish within existing bounded run deadline; retain actual times",
            "scheduled_denominator": 36,
            "minimum_mechanical_completions": 33,
            "minimum_independent_economic_acceptances": 30,
            "sufficiency": "Freeze per-company sufficient-case templates before opening; later retrieval failures remain failures",
            "safety_stratum": "Report all safety attempts, costs and actionable reviews separately; never pad scheduled counts",
        },
        "limitations": [
            "Supplied calendar authenticity/completeness and all activation prerequisites require review.",
            "No source sufficiency classification or successful outcome is inferred from a scheduled slot.",
            "Runner integration, frozen release identity and explicit opening approval remain required.",
        ],
    }
    with Path(output).open("x") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result
