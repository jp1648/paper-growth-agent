import copy
import json

import pytest

from trader.paper_observations import reconcile_marks


@pytest.fixture
def inventory(monkeypatch):
    monkeypatch.setattr(
        "trader.price_capture.validate_prices",
        lambda path: {
            "manifest_sha256": "a" * 64,
            "marks": [
                {"date": day, "symbol": symbol, "bar": {"c": price}}
                for day, symbol, price in [
                    ("2026-01-02", "AAPL", "100"),
                    ("2026-01-05", "AAPL", "50"),
                    ("2026-01-02", "SPY", "200"),
                    ("2026-01-05", "SPY", "201"),
                ]
            ],
        },
    )

    def side(symbol, qty, value, cash="100"):
        return {
            "basis": "regular_session_close",
            "complete": True,
            "evidence": "synthetic-closing-inventory",
            "cash": cash,
            "reported_equity": str(int(cash) + int(value)),
            "positions": [{"symbol": symbol, "quantity": qty, "reported_market_value": value}],
        }

    return {
        "version": 1,
        "currency": "USD",
        "price_manifest_sha256": "a" * 64,
        "observations": [
            {
                "date": "2026-01-02",
                "portfolio": side("AAPL", "1", "90"),
                "benchmark": side("SPY", "2", "400"),
            },
            {
                "date": "2026-01-05",
                "portfolio": side("AAPL", "2", "100"),
                "benchmark": side("SPY", "2", "400"),
            },
        ],
    }


def test_common_marks_keep_cash_and_explicit_quantity_changes(inventory):
    original = copy.deepcopy(inventory)
    result = reconcile_marks("capture", inventory)
    rows = result["observations"]
    assert rows[0]["portfolio"]["reported_equity"] == "190"
    assert rows[0]["portfolio"]["marked_equity"] == "200"
    assert rows[0]["portfolio"]["mark_adjustment"] == "10"
    # Raw price halves and supplied post-split inventory doubles: no second price adjustment.
    assert rows[1]["portfolio"]["marked_equity"] == "200"
    assert rows[1]["benchmark"]["marked_equity"] == "502"
    assert rows[1]["benchmark"]["mark_adjustment"] == "2"
    assert inventory == original and result["status"] == "diagnostic_only"


@pytest.mark.parametrize(
    "fault",
    [
        "cash",
        "residual",
        "missing_price",
        "duplicate",
        "short",
        "zero",
        "snapshot",
        "incomplete",
        "hash",
        "date",
        "evidence",
    ],
)
def test_unreconciled_inventory_fails(inventory, fault):
    row = inventory["observations"][0]["portfolio"]
    if fault == "cash":
        row["cash"] = None
    elif fault == "residual":
        row["reported_equity"] = "190.01"
    elif fault == "missing_price":
        row["positions"][0]["symbol"] = "MSFT"
    elif fault == "duplicate":
        row["positions"].append(row["positions"][0])
    elif fault == "short":
        row["positions"][0]["quantity"] = "-1"
    elif fault == "zero":
        row["positions"][0]["quantity"] = "0"
    elif fault == "snapshot":
        row["basis"] = "weekend_snapshot"
    elif fault == "incomplete":
        row["complete"] = False
    elif fault == "hash":
        inventory["price_manifest_sha256"] = "b" * 64
    elif fault == "date":
        inventory["observations"].pop()
    else:
        row["evidence"] = ""
    with pytest.raises((TypeError, ValueError)):
        reconcile_marks("capture", inventory)


def test_cash_only_and_cli_do_not_create_providers(inventory, monkeypatch, tmp_path, capsys):
    from trader import cli

    for row in inventory["observations"]:
        row["portfolio"].update(positions=[], reported_equity="100")
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory))
    monkeypatch.setattr(cli.Settings, "load", lambda p: pytest.fail("No configuration"))
    monkeypatch.setattr(cli, "AlpacaBroker", lambda p: pytest.fail("No broker"))
    assert cli.main(["reconcile-paper-marks", "capture", "--input", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert all(r["portfolio"]["marked_equity"] == "100" for r in result["observations"])
