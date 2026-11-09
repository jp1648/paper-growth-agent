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
