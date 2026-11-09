from decimal import Decimal

import pytest

from trader.benchmark import purchase, reinvestment_session


def test_policy_example_and_fee_no_fill():
    opening = purchase("1000", "100", "0")
    assert opening["quantity"] == "9"
    assert opening["cash_after"] == "99.55"
    assert opening["marked_nav"] == "999.55"
    assert opening["opening_return_denominator"] == "1000"
    reinvest = purchase("108.55", "99", "0")
    assert reinvest["quantity"] == "1"
    assert reinvest["cash_after"] == "9.50"
    assert Decimal(reinvest["marked_nav"]) + 9 * 99 == Decimal("999.50")
    none = purchase("99", "100", "1")
    assert none["quantity"] == "0" and none["fee"] == "0"
    assert none["cash_after"] == "99.00"


def test_affordability_uses_rounded_notional_not_floor_of_unrounded_ratio():
    assert purchase("100", "100.004", "0", slippage_bps=0)["quantity"] == "1"
    assert purchase("100", "100.005", "0", slippage_bps=0)["quantity"] == "0"
    assert purchase("100", "99", "1", slippage_bps=0)["quantity"] == "1"
    assert purchase("100", "99.01", "1", slippage_bps=0)["quantity"] == "0"


def test_fractional_grid_maximal_and_no_free_shares():
    result = purchase("100", "30", "0", slippage_bps=0, fractional=True)
    q = Decimal(result["quantity"])
    assert q == Decimal("3.333499")
    assert result["notional"] == "100.00"
    assert ((q + Decimal(".000001")) * 30).quantize(Decimal(".01"), rounding="ROUND_HALF_UP") > 100
    assert purchase("0", "1", "0", slippage_bps=0, fractional=True)["quantity"] == "0"


@pytest.mark.parametrize(
    "args",
    [
        ("NaN", "1", "0"),
        ("1", "0", "0"),
        ("1", "1", None),
        ("1.001", "1", "0"),
        ("1", "1", "-1"),
        (True, "1", "0"),
    ],
)
def test_invalid_purchase(args):
    with pytest.raises((ValueError, TypeError)):
        purchase(*args)


def test_sensitivities_and_timing():
    assert purchase("1000", "100", "0", slippage_bps=0)["quantity"] == "10"
    assert purchase("1000", "100", "0", slippage_bps=10)["cash_after"] == "99.10"
    with pytest.raises(ValueError):
        purchase("1000", "100", "0", fractional=True)
    sessions = [
        {"date": f"2026-11-{day:02d}", "close": f"2026-11-{day:02d}T21:00:00Z"} for day in (9, 10, 11)
    ]
    assert reinvestment_session("2026-11-09", "2026-11-09T12:00:00Z", sessions) == "2026-11-10"
    assert reinvestment_session("2026-11-09", "2026-11-10T21:00:00Z", sessions) == "2026-11-11"
    assert reinvestment_session("2026-11-09", "2026-11-12T12:00:00Z", sessions) is None
    with pytest.raises(ValueError):
        reinvestment_session("2026-11-09", "2026-11-09T12:00:00", sessions)
    with pytest.raises(ValueError):
        reinvestment_session("2026-11-09", "2026-11-09T12:00:00Z", sessions + sessions)


def test_preview_cli_and_missing_evidence(tmp_path, capsys):
    import json

    from trader.benchmark import preview
    from trader.cli import main

    doc = {
        "version": 1,
        "symbol": "SPY",
        "currency": "USD",
        "price_sha256": "a" * 64,
        "fee_evidence_sha256": "b" * 64,
        "session": "2026-11-10",
        "reason": "opening",
        "cash": "1000",
        "mark": "100",
        "fee": "0",
        "slippage_bps": 5,
        "fractional": False,
    }
    path = tmp_path / "purchase.json"
    path.write_text(json.dumps(doc))
    assert main(["preview-benchmark-purchase", "--input", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["quantity"] == "9"
    doc["price_sha256"] = ""
    with pytest.raises(ValueError):
        preview(doc)
    doc["price_sha256"] = "a" * 64
    doc.update(
        reason="distribution_reinvestment",
        distribution_id="d1",
        payment_evidence="issuer",
        payment_date="2026-11-09",
        available_at="2026-11-10T21:00:00Z",
        sessions=[
            {"date": "2026-11-10", "close": "2026-11-10T21:00:00Z"},
            {"date": "2026-11-11", "close": "2026-11-11T21:00:00Z"},
        ],
    )
    with pytest.raises(ValueError, match="first eligible"):
        preview(doc)
    doc["session"] = "2026-11-11"
    assert preview(doc)["kind"] == "HYPOTHETICAL"


@pytest.fixture
def ledger_input():
    return {
        "version": 1,
        "currency": "USD",
        "symbol": "SPY",
        "coverage_complete": True,
        "calendar_evidence": "calendar",
        "action_coverage_evidence": "actions",
        "investor_terms_evidence": "terms",
        "fee_evidence": "fees",
        "fee_evidence_sha256": "b" * 64,
        "opening_capital": "1000",
        "order_fee": "0",
        "slippage_bps": 5,
        "fractional": False,
        "expected_sessions": ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"],
        "sessions": [
            {
                "date": f"2026-11-{d:02d}",
                "close": f"2026-11-{d:02d}T21:00:00Z",
                "mark": "100" if d == 9 else "99",
                "price_sha256": "a" * 64,
                "interest_evidence": "verified-zero-fixture",
                "interest_cash": "0",
                "external_deposits": "0",
                "external_withdrawals": "0",
                "events": [],
            }
            for d in (9, 10, 11, 12)
        ],
    }


def test_full_worked_dividend_chain(ledger_input):
    from trader.benchmark import build_ledger

    d = ledger_input
    d["sessions"][1]["events"] = [
        {
            "id": "d1",
            "kind": "dividend",
            "evidence": "issuer",
            "gross_per_share": "1",
            "withholding_per_share": "0",
            "fee_per_share": "0",
        }
    ]
    d["sessions"][2]["events"] = [
        {
            "id": "p1",
            "kind": "payment",
            "evidence": "paid",
            "dividend_id": "d1",
            "payment_date": "2026-11-11",
            "available_at": "2026-11-11T22:00:00Z",
            "net_cash": "9",
        }
    ]
    result = build_ledger(d)
    assert [Decimal(r["nav"]) for r in result["balances"]] == [Decimal("999.55")] * 3 + [Decimal("999.50")]
    assert Decimal(result["balances"][1]["return"]) == Decimal("-.00045")
    assert result["balances"][2]["return"] == "0"
    assert result["balances"][-1]["shares"] == "10"
    assert result["balances"][-1]["cash"] == "9.50"
    assert result["purchases"][-1]["distribution_ids"] == ["d1"]
    assert not result["unpaid_entitlements"]


def test_opening_exdate_no_entitlement_and_split(ledger_input):
    from trader.benchmark import build_ledger

    d = ledger_input
    d["sessions"][0]["events"] = [
        {
            "id": "old",
            "kind": "dividend",
            "evidence": "issuer",
            "gross_per_share": "1",
            "withholding_per_share": "0",
            "fee_per_share": "0",
        }
    ]
    d["sessions"][1]["events"] = [
        {"id": "split", "kind": "split", "evidence": "issuer", "numerator": "2", "denominator": "1"}
    ]
    for row in d["sessions"][1:]:
        row["mark"] = "50"
    result = build_ledger(d)
    assert result["entitlements"]["old"]["quantity"] == "0"
    assert result["balances"][-1]["shares"] == "18"
    assert Decimal(result["balances"][-1]["nav"]) == Decimal("999.55")
    d["sessions"][1]["events"][0]["denominator"] = "4"
    with pytest.raises(ValueError, match="cash-in-lieu"):
        build_ledger(d)


@pytest.mark.parametrize(
    "fault", ["coverage", "missing_row", "flow", "unknown_action", "unknown_tax", "duplicate"]
)
def test_ledger_rejects_incomplete_or_ambiguous_inputs(ledger_input, fault):
    from trader.benchmark import build_ledger

    d = ledger_input
    event = {
        "id": "d1",
        "kind": "dividend",
        "evidence": "issuer",
        "gross_per_share": "1",
        "withholding_per_share": "0",
        "fee_per_share": "0",
    }
    d["sessions"][1]["events"] = [event]
    if fault == "coverage":
        d["coverage_complete"] = False
    elif fault == "missing_row":
        d["sessions"].pop()
    elif fault == "flow":
        d["sessions"][1]["external_deposits"] = "1"
    elif fault == "unknown_action":
        event["kind"] = "spin_off"
    elif fault == "unknown_tax":
        event["withholding_per_share"] = None
    else:
        d["sessions"][2]["events"] = [event.copy()]
    with pytest.raises((ValueError, TypeError)):
        build_ledger(d)


def test_late_confirmation_does_not_backdate_reinvestment(ledger_input):
    from trader.benchmark import build_ledger

    d = ledger_input
    d["sessions"][1]["events"] = [
        {
            "id": "d1",
            "kind": "dividend",
            "evidence": "issuer",
            "gross_per_share": "1",
            "withholding_per_share": "0",
            "fee_per_share": "0",
        }
    ]
    d["sessions"][2]["events"] = [
        {
            "id": "p1",
            "kind": "payment",
            "evidence": "paid",
            "dividend_id": "d1",
            "payment_date": "2026-11-11",
            "available_at": "2026-11-12T21:00:00Z",
            "net_cash": "9",
        }
    ]
    result = build_ledger(d)
    assert len(result["purchases"]) == 1
    assert result["balances"][-1]["cash"] == "108.55"
    assert result["balances"][-1]["net_receivable"] == "0"


def test_ledger_cli_preserves_previous_output(ledger_input, tmp_path, capsys):
    import json

    from trader.cli import main

    source = tmp_path / "input.json"
    target = tmp_path / "output.json"
    source.write_text(json.dumps(ledger_input))
    args = ["build-benchmark-ledger", "--input", str(source), "--output", str(target)]
    assert main(args) == 0
    result = json.loads(target.read_text())
    assert result["kind"] == "HYPOTHETICAL" and len(result["input_sha256"]) == 64
    prior = target.read_bytes()
    with pytest.raises(FileExistsError):
        main(args)
    assert target.read_bytes() == prior


def test_due_payment_cannot_spend_other_unconfirmed_payment(ledger_input):
    from trader.benchmark import build_ledger

    d = ledger_input
    for day in (13, 16):
        template = d["sessions"][-1].copy()
        template.update(date=f"2026-11-{day}", close=f"2026-11-{day}T21:00:00Z", events=[])
        d["sessions"].append(template)
        d["expected_sessions"].append(template["date"])
    for index, identifier, rate in ((1, "d1", "1"), (2, "d2", "100")):
        d["sessions"][index]["events"] = [
            {
                "id": identifier,
                "kind": "dividend",
                "evidence": "issuer",
                "gross_per_share": rate,
                "withholding_per_share": "0",
                "fee_per_share": "0",
            }
        ]
    d["sessions"][3]["events"] = [
        {
            "id": "p1",
            "kind": "payment",
            "evidence": "issuer-paid",
            "dividend_id": "d1",
            "payment_date": "2026-11-12",
            "available_at": "2026-11-12T22:00:00Z",
            "net_cash": "9",
        },
        {
            "id": "p2",
            "kind": "payment",
            "evidence": "issuer-paid",
            "dividend_id": "d2",
            "payment_date": "2026-11-12",
            "available_at": "2026-11-16T22:00:00Z",
            "net_cash": "900",
        },
    ]
    result = build_ledger(d)
    assert result["purchases"][-1]["quantity"] == "1"
    assert result["balances"][-1]["shares"] == "10"
    assert result["balances"][-1]["cash"] == "909.50"


def test_nonpurchase_mark_requires_exact_evidence_hash(ledger_input):
    from trader.benchmark import build_ledger

    ledger_input["sessions"][1]["price_sha256"] = "not-a-hash"
    with pytest.raises(ValueError, match="exact evidence hashes"):
        build_ledger(ledger_input)


def test_ledger_direct_and_file_use_same_money_precision(ledger_input, tmp_path):
    import json

    from trader.benchmark import build_file, build_ledger

    d = ledger_input
    d["opening_capital"] = "1000000000000"
    d["slippage_bps"] = 0
    d["sessions"][0]["mark"] = "0.000000000001"
    for row in d["sessions"][1:]:
        row["mark"] = "123456789012.123456789012"
    result = build_ledger(d)
    assert result["balances"][1]["nav"] == "123456789012124074072956937160494932.936543210988"
    source = tmp_path / "source.json"
    source.write_text(json.dumps(d))
    assert build_file(source, tmp_path / "out.json")["balances"] == result["balances"]
