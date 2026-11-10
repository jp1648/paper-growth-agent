import copy
import hashlib
import json
from pathlib import Path

import pytest
from test_benchmark import ledger_input, sourced_ledger  # noqa: F401
from test_paper_observations import claim

from trader.benchmark import build_ledger
from trader.cli import main
from trader.paper_bundle import build_file


def test_cash_matrix_from_retained_prices_inventory_and_event_hashes(bundle, tmp_path):
    import httpx
    from test_price_capture import bars, provider

    from trader.paper_cash import AMENDMENT_SHA256
    from trader.price_capture import capture_prices, validate_prices

    doc, save = bundle
    original = doc["comparisons"]["SPY:whole_5bps"]
    ledger = json.loads((tmp_path / original["ledger"]["path"]).read_text())
    nav = json.loads((tmp_path / original["observations"]["path"]).read_text())
    for point in [nav["opening"], *nav["observations"]]:
        point["portfolio"]["cash_interest_liability_not_in_equity"] = {
            "amount": "0",
            "status": "final",
            "evidence_sha256": "d" * 64,
        }
        point["portfolio"]["cash_interest_receivable_not_in_equity"] = {
            "amount": "0",
            "status": "final",
            "evidence_sha256": "e" * 64,
        }
    for key, entry in doc["comparisons"].items():
        existing = json.loads((tmp_path / entry["observations"]["path"]).read_text())
        for point in [existing["opening"], *existing["observations"]]:
            point["portfolio"]["cash_interest_liability_not_in_equity"] = {
                "amount": "0",
                "status": "final",
                "evidence_sha256": "d" * 64,
            }
            point["portfolio"]["cash_interest_receivable_not_in_equity"] = {
                "amount": "0",
                "status": "final",
                "evidence_sha256": "e" * 64,
            }
        entry["observations"] = save(key.replace(":", "-") + "-accrual-nav.json", existing)
    capture = tmp_path / "qqq-capture"
    capture_prices(
        provider(lambda req: httpx.Response(200, json={"bars": {"QQQ": bars()}, "next_page_token": None})),
        ["QQQ"],
        tmp_path / "capture/calendar.json",
        capture,
    )
    prices = validate_prices(capture)
    qqq = copy.deepcopy(ledger)
    qqq.update(symbol="QQQ", price_manifest_sha256=prices["manifest_sha256"])
    for row in qqq["sessions"]:
        row["price_sha256"] = prices["manifest_sha256"]
    qqq_nav = copy.deepcopy(nav)
    qqq_nav["benchmark"] = "QQQ"
    doc["comparisons"]["QQQ:whole_5bps"] = {
        "study_id": doc["study_id"],
        "observations": save("qqq-nav.json", qqq_nav),
        "ledger": save("qqq-ledger.json", qqq),
        "capture_manifest": {"path": "qqq-capture/manifest.json", "sha256": prices["manifest_sha256"]},
    }
    protocol_path = Path(__file__).parents[1] / "evaluations/forward-paper-v2-paper-cash.json"
    (tmp_path / "cash-protocol.json").write_bytes(protocol_path.read_bytes())
    doc["protocol"] = {"path": "cash-protocol.json", "sha256": AMENDMENT_SHA256}
    doc["study_manifest"]["protocol_sha256"] = AMENDMENT_SHA256
    cash = {
        "version": 1,
        "series": "agent",
        "currency": "USD",
        "coverage_complete": True,
        "annual_rate": "0",
        "amendment_sha256": AMENDMENT_SHA256,
        "calendar_sha256": ledger["calendar_sha256"],
        "expected_sessions": ledger["expected_sessions"],
        "sessions": [
            {
                "date": row["date"],
                "close": row["close"],
                "cash": "1000" if i == 0 else "1010",
                "locked_cash": "0",
                "events": [],
                "evidence_sha256": "a" * 64,
            }
            for i, row in enumerate(ledger["sessions"])
        ],
    }
    cash["sessions"][1]["events"] = [
        {
            "id": "booked-interest",
            "kind": "interest",
            "at": "2026-11-02T12:00:00Z",
            "delta_cash": "10",
            "cash_after": "1010",
            "locked_cash_after": "0",
            "evidence_sha256": "b" * 64,
        }
    ]
    inventory = {
        "version": 1,
        "currency": "USD",
        "price_manifest_sha256": ledger["price_manifest_sha256"],
        "observations": [
            {
                "date": row["date"],
                **{
                    side: {
                        "basis": "regular_session_close",
                        "complete": True,
                        "evidence": "synthetic-inventory",
                        "cash": row["cash"],
                        "reported_equity": row["cash"],
                        "positions": [],
                    }
                    for side in ("portfolio", "benchmark")
                },
            }
            for row in cash["sessions"]
        ],
    }
    doc["evidence"]["cash_comparison"] = save(
        "cash-spec.json",
        {"agent_cash": save("agent-cash.json", cash), "inventory": save("inventory.json", inventory)},
    )
    source = tmp_path / "cash-bundle.json"
    save(source.name, doc)
    result = build_file(source, tmp_path / "cash-result.json")
    matrix = result["cash_scenario_matrix"]
    assert set(matrix["scenarios"]) == {"0", "0.02", "0.05"}
    assert matrix["scenarios"]["0"]["agent_cash"]["balances"][-1]["modeled_cash"] == "1000.00"
    assert matrix["scenarios"]["0.05"]["agent_cash"]["balances"][-1]["modeled_cash"] == "1000.41"
    assert not result["activation_authorized"]
    (tmp_path / "agent-cash.json").write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        build_file(source, tmp_path / "tampered.json")


@pytest.fixture
def bundle(sourced_ledger, tmp_path):  # noqa: F811
    capture, ledger = sourced_ledger

    def save(name, document):
        path = tmp_path / name
        path.write_text(json.dumps(document))
        return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    doc = {
        "version": 1,
        "study_id": "synthetic-only",
        "protocol": save("protocol.json", {"manifest_required": ["protocol_sha256", "reviewer_identity"]}),
        "study_manifest": {},
        "comparisons": {},
        "evidence": {},
    }
    doc["study_manifest"]["protocol_sha256"] = doc["protocol"]["sha256"]
    for method, bps in (("whole_5bps", 5), ("whole_0bps", 0)):
        data = copy.deepcopy(ledger)
        data["slippage_bps"] = bps
        rows = build_ledger(data)["balances"]
        nav = {
            "version": 1,
            "currency": "USD",
            "benchmark": "SPY",
            "flow_policy": "no_external_flows",
            "source_manifest_sha256": "a" * 64,
            "benchmark_method": method,
            "cost_allocation_method": "synthetic-only",
            "opening": {"date": rows[0]["date"], "portfolio": claim("1000"), "benchmark": claim("1000")},
            "expected_sessions": [rows[1]["date"]],
            "observations": [
                {
                    "date": rows[1]["date"],
                    "portfolio": claim("1010"),
                    "benchmark": claim(rows[1]["nav"]),
                    "external_deposits": "0",
                    "external_withdrawals": "0",
                    "flow_evidence": "synthetic",
                }
            ],
        }
        doc["comparisons"][f"SPY:{method}"] = {
            "study_id": doc["study_id"],
            "observations": save(f"{method}-nav.json", nav),
            "ledger": save(f"{method}-ledger.json", data),
            "capture_manifest": {
                "path": "capture/manifest.json",
                "sha256": hashlib.sha256((capture / "manifest.json").read_bytes()).hexdigest(),
            },
        }
    return doc, save


def test_real_capture_and_ledger_chain_cli_draft_only(bundle, tmp_path, capsys):
    doc, save = bundle
    save("bundle.json", doc)
    output = tmp_path / "report.json"
    assert (
        main(
            [
                "--config",
                "absent.toml",
                "build-paper-bundle",
                "--input",
                str(tmp_path / "bundle.json"),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    result = json.loads(output.read_text())
    assert result["activation_authorized"] is False
    assert result["comparisons"]["SPY:whole_5bps"]["benchmark_binding"]["balances_verified"] == 2
    assert "comparisons.QQQ:whole_5bps" in result["missing_evidence"]
    assert "evidence.cost_finality" in result["missing_evidence"]
    with pytest.raises(FileExistsError):
        build_file(tmp_path / "bundle.json", output)


@pytest.mark.parametrize("fault", ["study", "hash", "method", "portfolio", "escape", "capture", "fee"])
def test_bundle_rejects_substitution(bundle, tmp_path, fault):
    doc, save = bundle
    entry = doc["comparisons"]["SPY:whole_0bps"]
    if fault == "study":
        entry["study_id"] = "other"
    elif fault == "hash":
        entry["ledger"]["sha256"] = "f" * 64
    elif fault == "escape":
        entry["ledger"]["path"] = "../outside.json"
    elif fault == "capture":
        (tmp_path / "capture/calendar.json").write_text("[]")
    else:
        reference = entry["ledger" if fault in {"method", "fee"} else "observations"]
        data = json.loads((tmp_path / reference["path"]).read_text())
        if fault == "method":
            data["slippage_bps"] = 10
        elif fault == "fee":
            data["order_fee"] = "50"
            nav_ref = entry["observations"]
            nav = json.loads((tmp_path / nav_ref["path"]).read_text())
            nav["observations"][0]["benchmark"] = claim(build_ledger(data)["balances"][1]["nav"])
            entry["observations"] = save(nav_ref["path"], nav)
        else:
            data["observations"][0]["portfolio"]["reported_equity"] = "1020"
        entry["ledger" if fault in {"method", "fee"} else "observations"] = save(reference["path"], data)
    save("bundle.json", doc)
    with pytest.raises(ValueError):
        build_file(tmp_path / "bundle.json", tmp_path / "report.json")
    assert not (tmp_path / "report.json").exists()


@pytest.mark.parametrize(
    "field,value", [("start_session", "2026-11-03"), ("cost_allocation_method", "other")]
)
def test_declared_study_must_match_actual_comparisons(bundle, tmp_path, field, value):
    doc, save = bundle
    doc["study_manifest"][field] = value
    save("bundle.json", doc)
    with pytest.raises(ValueError, match="Study manifest differs"):
        build_file(tmp_path / "bundle.json", tmp_path / "report.json")


def test_symlink_evidence_rejected(bundle, tmp_path):
    doc, save = bundle
    entry = doc["comparisons"]["SPY:whole_0bps"]
    original = entry["ledger"]["path"]
    (tmp_path / "link.json").symlink_to(tmp_path / original)
    entry["ledger"]["path"] = "link.json"
    save("bundle.json", doc)
    with pytest.raises(ValueError, match="symlinks"):
        build_file(tmp_path / "bundle.json", tmp_path / "report.json")
