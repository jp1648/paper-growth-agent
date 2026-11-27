import copy
import json

import pytest
from test_corporate_actions import ordinary_capture

from trader.corporate_actions import validate_cash_dividends
from trader.dividends import audit_entitlements
from trader.models import dec


def fixture(tmp_path):
    root, _ = ordinary_capture(tmp_path, rate="2")
    document = {
        "version": 1,
        "rounding": "USD_cent_half_up",
        "as_of": "2026-10-31",
        "capture_manifest_sha256": validate_cash_dividends(root)["manifest_sha256"],
        "entitlements": [
            {
                "action_id": "a",
                "entitled_quantity": "10",
                "entitlement_evidence": "fixture opening holdings before ex-date",
                "payments": [],
            }
        ],
    }
    return root, document


def payment(identifier="payment", net="16.50", tax="3", fee="0.50"):
    return {
        "id": identifier,
        "date": "2026-10-30",
        "net_cash": net,
        "withholding": tax,
        "fee": fee,
        "evidence": "fixture broker receipt",
    }


def test_dividend_moves_from_receivable_to_cash_without_double_income(tmp_path):
    root, document = fixture(tmp_path)
    before = copy.deepcopy(document)
    earned = audit_entitlements(root, document)["actions"][0]
    assert earned["gross_entitlement"] == earned["gross_receivable"] == dec(20)
    assert earned["net_cash_received"] == 0 and earned["payment_overdue"] is True
    # Ten shares fall from 100 to 98 ex-dividend. Gross economic NAV remains 1000.
    assert dec(980) + earned["gross_receivable"] == 1000
    assert document == before
    document["entitlements"][0]["payments"] = [payment()]
    result = audit_entitlements(root, document)
    settled = result["actions"][0]
    assert settled["gross_entitlement"] == 20 and settled["gross_receivable"] == 0
    assert settled["net_cash_received"] == dec("16.50")
    assert settled["withholding"] == 3 and settled["fees"] == dec(".50")
    assert dec(980) + settled["net_cash_received"] == dec("996.50")
    assert settled["payment_overdue"] is False
    assert result == audit_entitlements(root, document)
    assert result["evidence_authenticity_verified"] is False


def test_partial_settlement_and_zero_entitlement_are_explicit(tmp_path):
    root, document = fixture(tmp_path)
    document["entitlements"][0]["payments"] = [payment(net="8", tax="1", fee="1")]
    partial = audit_entitlements(root, document)["actions"][0]
    assert partial["gross_receivable"] == 10 and partial["net_cash_received"] == 8
    document["entitlements"][0]["payments"].append(payment("second", "9", "1", "0"))
    settled = audit_entitlements(root, document)["actions"][0]
    assert settled["gross_receivable"] == 0 and settled["net_cash_received"] == 17
    document["entitlements"][0].update(entitled_quantity="0", payments=[])
    assert audit_entitlements(root, document)["actions"][0]["gross_entitlement"] == 0


def test_entitlement_window_and_rounding_are_explicit(tmp_path):
    root, document = fixture(tmp_path)
    document["as_of"] = "2026-09-17"
    document["entitlements"] = []
    assert audit_entitlements(root, document)["actions"] == []
    document["as_of"] = "2026-09-18"
    with pytest.raises(ValueError, match="exactly one"):
        audit_entitlements(root, document)
    document["entitlements"] = [
        {
            "action_id": "a",
            "entitled_quantity": "0.0025",
            "entitlement_evidence": "fixture fractional entitlement",
            "payments": [],
        }
    ]
    row = audit_entitlements(root, document)["actions"][0]
    assert row["gross_unrounded"] == dec(".005") and row["gross_entitlement"] == dec(".01")
    assert row["payment_overdue"] is False


@pytest.mark.parametrize(
    "fault",
    [
        "capture",
        "rounding",
        "missing",
        "duplicate_claim",
        "unknown",
        "evidence",
        "negative",
        "nonfinite",
        "duplicate_payment",
        "overpaid",
        "future",
        "early",
        "precision",
        "unknown_tax",
    ],
)
def test_unverified_or_inconsistent_entitlement_inputs_fail(tmp_path, fault):
    root, document = fixture(tmp_path)
    claim = document["entitlements"][0]
    claim["payments"] = [payment()]
    if fault == "capture":
        document["capture_manifest_sha256"] = "0" * 64
    elif fault == "rounding":
        document["rounding"] = "unknown"
    elif fault == "missing":
        document["entitlements"] = []
    elif fault == "duplicate_claim":
        document["entitlements"].append(copy.deepcopy(claim))
    elif fault == "unknown":
        claim["action_id"] = "unknown"
    elif fault == "evidence":
        claim["entitlement_evidence"] = " "
    elif fault == "negative":
        claim["entitled_quantity"] = "-1"
    elif fault == "nonfinite":
        claim["entitled_quantity"] = "NaN"
    elif fault == "duplicate_payment":
        claim["payments"].append(payment())
    elif fault == "overpaid":
        claim["payments"][0]["net_cash"] = "100"
    elif fault == "future":
        claim["payments"][0]["date"] = "2026-11-01"
    elif fault == "early":
        claim["payments"][0]["date"] = "2026-09-17"
    elif fault == "precision":
        claim["payments"][0]["net_cash"] = "16.501"
    else:
        claim["payments"][0]["withholding"] = None
    with pytest.raises((ValueError, TypeError, ArithmeticError)):
        audit_entitlements(root, document)


def test_entitlement_cli_is_offline(tmp_path, monkeypatch):
    from trader import cli

    root, document = fixture(tmp_path)
    source = tmp_path / "entitlements.json"
    source.write_text(json.dumps(document))
    monkeypatch.setattr(cli.Settings, "load", lambda _: pytest.fail("No configuration expected"))
    monkeypatch.setattr(cli, "Store", lambda _: pytest.fail("No ledger expected"))
    assert cli.main(["audit-dividend-entitlements", str(root), "--input", str(source)]) == 0
