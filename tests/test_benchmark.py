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
    assert Decimal(result["balances"][-1]["net_receivable"]) == 0


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


@pytest.fixture
def sourced_ledger(ledger_input, tmp_path, monkeypatch, request):
    import json
    from datetime import UTC, datetime

    import httpx
    from test_price_capture import bars, provider

    from trader.price_capture import capture_prices, validate_prices

    monkeypatch.setattr("trader.price_capture.now", lambda: datetime(2026, 11, 8, tzinfo=UTC))
    calendar = tmp_path / "calendar.json"
    calendar.write_text(
        json.dumps([{"date": d, "open": "09:30", "close": "16:00"} for d in ("2026-10-30", "2026-11-02")])
    )
    capture = tmp_path / "capture"
    symbol = getattr(request, "param", "SPY")
    capture_prices(
        provider(lambda req: httpx.Response(200, json={"bars": {symbol: bars()}, "next_page_token": None})),
        [symbol],
        calendar,
        capture,
    )
    prices = validate_prices(capture)
    d = ledger_input
    d.update(
        symbol=symbol,
        price_manifest_sha256=prices["manifest_sha256"],
        calendar_sha256=prices["calendar_sha256"],
        expected_sessions=["2026-10-30", "2026-11-02"],
    )
    d["sessions"] = d["sessions"][:2]
    for row, day, close, price in zip(
        d["sessions"], d["expected_sessions"], ["20:00", "21:00"], ["100", "101"], strict=True
    ):
        row.update(date=day, close=f"{day}T{close}:00Z", mark=price, price_sha256=prices["manifest_sha256"])
    return capture, d


def test_retained_capture_binding_and_cli(sourced_ledger, tmp_path, capsys):
    import json

    from trader.cli import main

    capture, d = sourced_ledger
    source = tmp_path / "ledger.json"
    output = tmp_path / "result.json"
    source.write_text(json.dumps(d))
    assert (
        main(
            [
                "build-benchmark-ledger",
                "--input",
                str(source),
                "--output",
                str(output),
                "--capture",
                str(capture),
            ]
        )
        == 0
    )
    result = json.loads(output.read_text())
    assert result["retained_price_binding"]["price_manifest_sha256"] == d["price_manifest_sha256"]
    assert result["balances"][1]["nav"] == "1008.55"
    assert result["retained_price_binding"]["request"]["adjustment"] == "raw"
    assert not result["retained_price_binding"]["source_authenticity_verified"]


@pytest.mark.parametrize(
    "fault", ["mark", "close", "calendar", "manifest", "row_hash", "symbol", "missing_session", "page_tamper"]
)
def test_retained_capture_rejects_mismatched_evidence(sourced_ledger, fault):
    from trader.benchmark import bind_prices

    capture, d = sourced_ledger
    if fault == "mark":
        d["sessions"][1]["mark"] = "999"
    elif fault == "close":
        d["sessions"][0]["close"] = "2026-10-30T21:00:00Z"
    elif fault == "calendar":
        d["calendar_sha256"] = "a" * 64
    elif fault == "manifest":
        d["price_manifest_sha256"] = "a" * 64
    elif fault == "row_hash":
        d["sessions"][1]["price_sha256"] = "a" * 64
    elif fault == "symbol":
        d["symbol"] = "QQQ"
    elif fault == "missing_session":
        d["expected_sessions"].pop()
    else:
        (capture / "page-001.json").write_text("{}")
    with pytest.raises(ValueError):
        bind_prices(capture, d)


@pytest.mark.parametrize("available,shares", [("2026-11-14T15:00:00Z", "10"), ("2026-11-16T21:00:00Z", "9")])
def test_weekend_payment_uses_actual_date_and_first_eligible_close(ledger_input, available, shares):
    from trader.benchmark import build_ledger

    d = ledger_input
    days = (12, 13, 16, 17)
    for row, day in zip(d["sessions"], days, strict=True):
        row.update(date=f"2026-11-{day}", close=f"2026-11-{day}T21:00:00Z")
    d["expected_sessions"] = [r["date"] for r in d["sessions"]]
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
    payment = {
        "id": "p1",
        "kind": "payment",
        "evidence": "completed-payment",
        "dividend_id": "d1",
        "payment_date": "2026-11-14",
        "available_at": available,
        "net_cash": "9",
    }
    d["sessions"][2]["events"] = [payment]
    result = build_ledger(d)
    assert result["balances"][2]["shares"] == shares
    assert Decimal(result["balances"][2]["net_receivable"]) == 0
    assert result["entitlements"]["d1"]["payments"][0]["payment_date"] == "2026-11-14"
    assert result["balances"][-1]["shares"] == "10"
    for invalid_date in ("20261114", "2026-11-13", "2026-11-17"):
        payment["payment_date"] = invalid_date
        with pytest.raises(ValueError):
            build_ledger(d)
    payment["payment_date"] = "2026-11-14"
    payment["available_at"] = "2026-11-13T23:59:59-05:00"
    with pytest.raises(ValueError, match="predate payment"):
        build_ledger(d)
    payment["available_at"] = available
    d["sessions"][2]["events"] = []
    d["sessions"][3]["events"] = [payment]
    with pytest.raises(ValueError, match="first regular session"):
        build_ledger(d)


def test_partial_payments_preserve_receivable_and_lock_each_tranche(ledger_input):
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
    first = {
        "id": "p1",
        "kind": "payment",
        "evidence": "paid-1",
        "dividend_id": "d1",
        "payment_date": "2026-11-11",
        "available_at": "2026-11-11T22:00:00Z",
        "net_cash": "4",
    }
    d["sessions"][2]["events"] = [first]
    result = build_ledger(d)
    assert result["balances"][2]["cash"] == "103.55"
    assert Decimal(result["balances"][2]["net_receivable"]) == 5
    assert result["unpaid_entitlements"] == ["d1"]
    assert result["balances"][-1]["shares"] == "10"
    second = dict(first, id="p2", evidence="paid-2", net_cash="5", available_at="2026-11-12T21:00:00Z")
    d["sessions"][2]["events"].append(second)
    d["sessions"][3]["mark"] = "105"
    result = build_ledger(d)
    assert result["balances"][2]["cash"] == "108.55"
    assert Decimal(result["balances"][2]["net_receivable"]) == 0
    assert result["balances"][-1]["cash"] == "108.55"
    assert result["balances"][-1]["shares"] == "9"
    assert result["purchases"][-1]["payment_ids"] == ["p1"]
    assert not result["unpaid_entitlements"]
    assert len(result["entitlements"]["d1"]["payments"]) == 2
    second["net_cash"] = "6"
    with pytest.raises(ValueError, match="outstanding"):
        build_ledger(d)
    second["net_cash"] = "0"
    with pytest.raises(ValueError, match="positive"):
        build_ledger(d)


def test_payment_reversal_restores_receivable_without_erasing_receipt(ledger_input):
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
    reversal = {
        "id": "r1",
        "kind": "payment_reversal",
        "evidence": "debit-with-claim-preserved",
        "dividend_id": "d1",
        "payment_id": "p1",
        "reversal_date": "2026-11-12",
        "available_at": "2026-11-12T15:00:00Z",
        "net_cash": "9",
        "entitlement_remains_due": True,
    }
    d["sessions"][3]["events"] = [reversal]
    result = build_ledger(d)
    claim = result["entitlements"]["d1"]
    assert Decimal(claim["received"]) == 0 and not claim["paid"]
    assert claim["payments"][0]["net_cash"] == "9"
    assert claim["payments"][0]["reversals"][0]["id"] == "r1"
    assert len(result["purchases"]) == 1
    assert result["balances"][-1]["cash"] == "99.55"
    assert Decimal(result["balances"][-1]["net_receivable"]) == 9
    assert Decimal(result["balances"][-1]["nav"]) == Decimal("999.55")
    reversal["net_cash"] = "4"
    result = build_ledger(d)
    assert result["balances"][-1]["shares"] == "10"
    assert Decimal(result["balances"][-1]["net_receivable"]) == 4
    reversal["net_cash"] = "10"
    with pytest.raises(ValueError, match="remaining receipt"):
        build_ledger(d)
    reversal["net_cash"] = "9"
    reversal["entitlement_remains_due"] = False
    with pytest.raises(ValueError, match="entitlement"):
        build_ledger(d)

    reversal["entitlement_remains_due"] = True
    d["sessions"][3]["events"] = []
    last = dict(d["sessions"][3], date="2026-11-13", close="2026-11-13T21:00:00Z", events=[reversal])
    reversal.update(reversal_date="2026-11-13", available_at="2026-11-13T15:00:00Z")
    d["sessions"].append(last)
    d["expected_sessions"].append(last["date"])
    result = build_ledger(d)
    assert result["balances"][-1]["shares"] == "10"
    assert result["balances"][-1]["cash"] == "0.50"
    assert Decimal(result["balances"][-1]["net_receivable"]) == 9
    assert len(result["purchases"]) == 2
    d["sessions"][3]["mark"] = "100"
    with pytest.raises(ValueError, match="unavailable cash"):
        build_ledger(d)


@pytest.mark.parametrize("reverse_input", [False, True])
def test_weekend_reversal_cannot_borrow_later_payment(ledger_input, reverse_input):
    from trader.benchmark import build_ledger

    d = ledger_input
    d["order_fee"] = "1"
    dividend = {
        "id": "d1",
        "kind": "dividend",
        "evidence": "issuer",
        "gross_per_share": "1",
        "withholding_per_share": "0",
        "fee_per_share": "0",
    }
    payment = {
        "id": "p1",
        "kind": "payment",
        "evidence": "paid",
        "dividend_id": "d1",
        "payment_date": "2026-11-11",
        "available_at": "2026-11-11T22:00:00Z",
        "net_cash": "9",
    }
    d["sessions"][1]["events"] = [dividend]
    d["sessions"][2]["events"] = [payment]
    d["sessions"][3]["events"] = [dict(dividend, id="d2")]
    later = dict(
        payment, id="p2", dividend_id="d2", payment_date="2026-11-15", available_at="2026-11-15T15:00:00Z"
    )
    reversal = {
        "id": "r1",
        "kind": "payment_reversal",
        "evidence": "debit",
        "dividend_id": "d1",
        "payment_id": "p1",
        "reversal_date": "2026-11-14",
        "available_at": "2026-11-14T15:00:00Z",
        "net_cash": "9",
        "entitlement_remains_due": True,
    }
    events = [later, reversal] if reverse_input else [reversal, later]
    d["sessions"].append(
        dict(d["sessions"][3], date="2026-11-16", close="2026-11-16T21:00:00Z", events=events)
    )
    d["expected_sessions"].append("2026-11-16")
    with pytest.raises(ValueError, match="unavailable cash"):
        build_ledger(d)
    reversal["reversal_date"] = "2026-11-15"
    reversal["available_at"] = "2026-11-15T16:00:00Z"
    with pytest.raises(ValueError, match="Same-date"):
        build_ledger(d)
    d["sessions"][2]["events"].append(dict(dividend, id="d2"))
    later.update(payment_date="2026-11-12", available_at="2026-11-16T15:00:00Z")
    d["sessions"][3]["events"] = [later]
    reversal.update(reversal_date="2026-11-14", available_at="2026-11-14T15:00:00Z")
    d["sessions"][-1]["events"] = [reversal]
    with pytest.raises(ValueError, match="another payment lock"):
        build_ledger(d)


@pytest.mark.parametrize("sourced_ledger", ["SPY", "QQQ"], indirect=True)
def test_paired_returns_bind_recomputed_benchmark_and_full_costs(sourced_ledger, tmp_path, capsys):
    import copy
    import json

    from trader.cli import main
    from trader.paper_observations import bind_benchmark

    capture, ledger = sourced_ledger

    def claim(equity, cost="0"):
        return {
            "reported_equity": equity,
            "net_receivables_not_in_equity": "0",
            "complete": True,
            "evidence": "retained-fixture",
            "cumulative_external_costs": {
                kind: {"amount": cost, "status": "final", "evidence": "verified-fixture"}
                for kind in ("model", "data", "infrastructure", "other")
            },
        }

    doc = {
        "version": 1,
        "currency": "USD",
        "benchmark": ledger["symbol"],
        "flow_policy": "no_external_flows",
        "source_manifest_sha256": "a" * 64,
        "benchmark_method": "whole shares 5bps",
        "cost_allocation_method": "all costs before credits",
        "expected_sessions": ["2026-11-02"],
        "opening": {"date": "2026-10-30", "portfolio": claim("1000"), "benchmark": claim("1000")},
        "observations": [
            {
                "date": "2026-11-02",
                "portfolio": claim("1010", "1"),
                "benchmark": claim("1008.55"),
                "external_deposits": "0",
                "external_withdrawals": "0",
                "flow_evidence": "verified-zero",
            }
        ],
    }
    source, benchmark, output = (
        tmp_path / name for name in ("paired.json", "benchmark.json", "returns.json")
    )
    source.write_text(json.dumps(doc))
    benchmark.write_text(json.dumps(ledger))
    args = [
        "--config",
        "absent.toml",
        "build-paper-observations",
        "--input",
        str(source),
        "--output",
        str(output),
        "--benchmark-input",
        str(benchmark),
        "--benchmark-capture",
        str(capture),
    ]
    assert main(args) == 0
    result = json.loads(output.read_text())
    assert Decimal(result["observations"][0]["portfolio_return"]) == Decimal(".006")
    assert Decimal(result["observations"][0]["benchmark_return"]) == Decimal(".00855")
    assert result["benchmark_binding"]["symbol"] == ledger["symbol"]
    assert result["benchmark_binding"]["balances_verified"] == 2
    changed = copy.deepcopy(doc)
    changed["benchmark"] = "QQQ" if ledger["symbol"] == "SPY" else "SPY"
    with pytest.raises(ValueError, match="must match"):
        bind_benchmark(changed, ledger)
    assert not result["benchmark_binding"]["source_authenticity_verified"]
    with pytest.raises(FileExistsError):
        main(args)
    for fault in ("capital", "window", "nav", "receivable", "duplicate_cost"):
        changed = copy.deepcopy(doc)
        if fault == "capital":
            changed["opening"]["portfolio"]["reported_equity"] = "999"
        elif fault == "window":
            changed["observations"][0]["date"] = "2026-11-03"
        elif fault == "nav":
            changed["observations"][0]["benchmark"]["reported_equity"] = "1009"
        elif fault == "receivable":
            changed["observations"][0]["benchmark"]["net_receivables_not_in_equity"] = "1"
        else:
            changed["observations"][0]["benchmark"]["cumulative_external_costs"]["model"]["amount"] = "1"
        with pytest.raises(ValueError):
            bind_benchmark(changed, ledger)
    with pytest.raises(ValueError, match="together"):
        main(args[:-2])
    from trader.benchmark import build_ledger
    from trader.paper_observations import build_observations

    ledger.update(opening_capital="1000000000000", slippage_bps=0)
    ledger["sessions"][0]["mark"] = "0.000000000001"
    ledger["sessions"][1]["mark"] = "123456789012.123456789012"
    exact = build_ledger(ledger)["balances"][1]["nav"]
    doc["opening"]["portfolio"] = claim("1000000000000")
    doc["opening"]["benchmark"] = claim("1000000000000")
    doc["observations"][0]["benchmark"] = claim(exact)
    assert bind_benchmark(doc, ledger)["balances_verified"] == 2
    assert build_observations(doc)["balances"][0]["benchmark"]["net_nav"] == exact
    doc["observations"][0]["benchmark"] = claim(str(+Decimal(exact)))
    with pytest.raises(ValueError, match="differs"):
        bind_benchmark(doc, ledger)
