import copy
import hashlib
import json

import pytest
from test_benchmark import ledger_input, sourced_ledger  # noqa: F401
from test_paper_observations import claim

from trader.benchmark import build_ledger
from trader.cli import main
from trader.paper_bundle import build_file


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
