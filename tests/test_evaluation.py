import copy
import json
from pathlib import Path

import pytest

from trader.evaluation import fingerprint, load_suite, model_inputs, replay, scenario_values, score

SUITE = Path("evaluations/baseline-v1.json")
RECORDING = Path("tests/fixtures/evaluation/recording.json")


def inputs():
    suite = load_suite(SUITE)
    recording = json.loads(RECORDING.read_text())
    return suite, recording


def test_frozen_suite_has_development_holdout_and_all_required_cases():
    suite, recording = inputs()
    assert len(suite["cases"]) == 16
    assert {c["split"] for c in suite["cases"]} == {"development", "holdout"}
    assert recording["suite_hash"] == fingerprint(suite)
    for case in suite["cases"]:
        exposed = model_inputs(case)
        assert "expected" not in exposed and "split" not in exposed
        result = score(case, recording["cases"][case["id"]])
        if case["expected"].get("valuation_required"):
            assert result["failures"] == ["unverified_financial_provenance"]
            assert result["acceptance"] == "failed"
        else:
            assert result["mechanical_pass"], result
            assert result["acceptance"] == "pending_human_review"


def test_independent_calculation_oracle():
    values = scenario_values(
        {
            "cash_flow_per_share": "5",
            "growth_rates": ["0", ".1", ".2"],
            "discount_rate": ".1",
            "terminal_multiples": ["10", "15", "20"],
            "years": 1,
        }
    )
    assert values == ["50.00", "80.00", "114.55"]


@pytest.mark.parametrize(
    "mutation, failure",
    [
        (lambda r: r["decision"].update(evidence_ids=["made-up"]), "unknown_citation"),
        (lambda r: r["reports"]["valuation"].update(base_value="999"), "incorrect_calculation"),
        (lambda r: r["decision"].update(symbol="OTHER"), "wrong_symbol"),
        (lambda r: r["decision"].update(next_review="2020-01-01T00:00:00Z"), "review_not_future"),
        (lambda r: r["reports"].pop("business"), "incomplete_research"),
        (lambda r: r.update(derived_evidence_ids=["invented"]), "unverified_derived_evidence"),
    ],
)
def test_bad_candidates_fail(mutation, failure):
    suite, recording = inputs()
    case = suite["cases"][0]
    result = copy.deepcopy(recording["cases"][case["id"]])
    mutation(result)
    assert failure in score(case, result)["failures"]


def test_gap_buy_and_false_human_review_fail():
    suite, recording = inputs()
    case = suite["cases"][4]
    candidate = recording["cases"][case["id"]]
    candidate["decision"].update(action="BUY", quantity="1", limit_price="40")
    assert "unsupported_buy" in score(case, candidate)["failures"]
    candidate["human_review"] = {
        "reviewer": "test",
        "reviewed_at": "2026-11-02T00:00:00Z",
        "claims_supported": False,
        "abstention_reason_correct": True,
    }
    assert "embedded_review_not_accepted" in score(case, candidate)["failures"]


def test_future_sources_and_mismatched_recordings_fail(tmp_path):
    suite, recording = inputs()
    suite["cases"][0]["inputs"]["sources"][0]["available_at"] = "2027-01-01T00:00:00Z"
    path = tmp_path / "future.json"
    path.write_text(json.dumps(suite))
    with pytest.raises(ValueError, match="Future source"):
        load_suite(path)
    recording["suite_hash"] = "wrong"
    path.write_text(json.dumps(recording))
    with pytest.raises(ValueError, match="frozen suite"):
        replay(SUITE, path, tmp_path / "out.json", "test")


def test_cli_replay_cannot_construct_broker_or_touch_operational_config(tmp_path, monkeypatch):
    from trader import cli

    def forbidden(*args, **kwargs):
        raise AssertionError("Operational path invoked")

    monkeypatch.setattr(cli, "AlpacaBroker", forbidden)
    monkeypatch.setattr(cli, "Service", forbidden)
    monkeypatch.setattr(cli.Settings, "load", forbidden)
    output = tmp_path / "evaluation.json"
    exit_code = cli.main(
        [
            "--config",
            "does-not-exist",
            "evaluate",
            "--suite",
            str(SUITE),
            "--responses",
            str(RECORDING),
            "--output",
            str(output),
            "--run-id",
            "test",
        ]
    )
    assert exit_code == 0
    result = json.loads(output.read_text())
    assert result["summary"]["mechanical_passes"] == 12
    assert result["summary"]["pending_semantic_reviews"] == 16
    assert not result["summary"]["investment_performance_measured"]
    with pytest.raises(FileExistsError):
        replay(SUITE, RECORDING, output, "test")


def test_failures_are_included_in_cost_denominator(tmp_path):
    _suite, recording = inputs()
    recording["cases"]["provider-timeout"]["metrics"].update(known_cost_usd="2", unknown_cost_calls=1)
    path = tmp_path / "recording.json"
    path.write_text(json.dumps(recording))
    result = replay(SUITE, path, tmp_path / "result.json", "costs")
    assert result["summary"]["known_cost_usd"] == "2"
    assert result["summary"]["unknown_cost_calls"] == 1
    assert result["summary"]["known_cost_per_completed_decision"] == str(__import__("decimal").Decimal(2) / 9)


def test_actual_research_desk_runs_frozen_tools_without_broker(tmp_path, monkeypatch):
    from test_research_team import TeamModel, financial_facts

    from trader import alpaca, observability
    from trader.config import Settings
    from trader.evaluation import run_suite
    from trader.store import Store

    def forbidden(*args, **kwargs):
        raise AssertionError("Broker constructed during evaluation")

    monkeypatch.setattr(alpaca, "AlpacaBroker", forbidden)
    sink = observability.Sink(tmp_path / "trace.sqlite", "unused", start=False)
    monkeypatch.setattr(observability, "_sink", sink)
    monkeypatch.setattr("trader.store.configure", lambda path: None)
    from trader import evaluation

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "evaluation.py").write_text("# fixture source")
    (bundle / "._evaluation.py").write_bytes(b"\xff\xfe macOS metadata")
    monkeypatch.setattr(evaluation, "__file__", str(bundle / "evaluation.py"))
    suite, recording = inputs()
    case = suite["cases"][0]
    case["inputs"]["sources"][0]["content"] = {
        "facts": financial_facts(),
        "filings": [{"accessionNumber": "0000000001-26-000001", "form": "10-K", "filingDate": "2026-01-01"}],
    }
    case["inputs"]["documents"] = [
        {
            "available_at": "2026-01-01T00:00:00Z",
            "accession": "0000000001-26-000001",
            "metadata": {
                "url": "https://www.sec.gov/Archives/fixture.htm",
                "name": "fixture.htm",
                "published_at": "2026-01-01",
                "form": "10-K",
            },
            "text": "Revenue and cash flow grew. Customer concentration creates risk.",
        }
    ]
    suite["cases"] = [case]
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(suite))
    decision = recording["cases"][case["id"]]["decision"]
    model = TeamModel(decision=decision)
    cfg = Settings(
        database=str(tmp_path / "operational.sqlite"), input_cost_per_million="1", output_cost_per_million="1"
    )
    result = run_suite(cfg, suite_path, tmp_path / "run", "real-graph-offline", "1", models=model)
    assert result["cases"][0]["complete"]
    assert result["cases"][0]["mechanical_pass"], result["cases"][0]
    assert result["cases"][0]["metrics"]["model_calls"] > 5
    assert result["cases"][0]["metrics"]["tool_calls"] >= 4
    assert result["cases"][0]["metrics"]["peak_context_bytes"] > 0
    assert result["cases"][0]["stages"]["valuation"]["model_seconds"] > 0
    with sink.db() as db:
        events = [json.loads(row[0]) for row in db.execute("SELECT body FROM outbox")]
    roots = [event for event in events if event["name"] == "evaluation.case"]
    assert {event["phase"] for event in roots} == {"start", "end"}
    calls = [event for event in events if event["name"] == "model.call"]
    assert calls and all(event["run_key"] == "real-graph-offline:" + case["id"] for event in calls)
    ledger = Store(cfg.database)
    assert ledger.get("active", scope="global") is None
    assert not ledger.items("intent")
    assert ledger.get("real-graph-offline", "evaluation_manifest", scope="global")["status"] == "complete"
    ledger.db.close()
    with pytest.raises(ValueError, match="already exists"):
        run_suite(cfg, suite_path, tmp_path / "other", "real-graph-offline", "1", models=model)


def test_comparison_rejects_different_evidence_and_reports_regression(tmp_path):
    from trader.evaluation import compare

    baseline = tmp_path / "baseline.json"
    replay(SUITE, RECORDING, baseline, "baseline")
    candidate = json.loads(baseline.read_text())
    candidate["run_id"] = "candidate"
    changed = next(row for row in candidate["cases"] if row["mechanical_pass"])
    changed["mechanical_pass"] = False
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate))
    result = compare(baseline, path, tmp_path / "comparison.json")
    assert result["regressions"] == [changed["case_id"]]
    assert not result["promotion_recommended"]
    candidate["cases"][0]["inputs_hash"] = "different"
    path.write_text(json.dumps(candidate))
    with pytest.raises(ValueError, match="identical cases"):
        compare(baseline, path, tmp_path / "invalid.json")


def test_provider_failure_retains_reservation_and_stops_next_case(tmp_path):
    import httpx
    from test_research_team import TeamModel

    from trader.config import Settings
    from trader.evaluation import run_suite
    from trader.spending import spending_status
    from trader.store import Store

    class TimeoutModel(TeamModel):
        def _generate(self, *args, **kwargs):
            raise httpx.ReadTimeout("fixture provider timeout")

    suite, recording = inputs()
    suite["cases"] = suite["cases"][:2]
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(suite))
    cfg = Settings(
        database=str(tmp_path / "operational.sqlite"), input_cost_per_million="1", output_cost_per_million="1"
    )
    model = TimeoutModel(decision=recording["cases"][suite["cases"][0]["id"]]["decision"])
    result = run_suite(cfg, path, tmp_path / "run", "timeout-case", "1", models=model)
    assert result["cases"][0]["metrics"]["unknown_cost_calls"] == 1
    assert result["cases"][1]["metrics"]["model_calls"] == 0
    store = Store(cfg.database)
    status = spending_status(store, cfg.research_daily_cost_usd)
    assert status["charged_or_reserved_usd"] == 1
    assert status["unresolved_runs"] == 1
    assert store.get("active", scope="global") is None
    store.db.close()


def test_paid_cli_requires_explicit_opt_in_before_config_load(monkeypatch):
    from trader.cli import main
    from trader.config import Settings

    def forbidden(*args):
        raise AssertionError("Should fail argparse before loading config")

    monkeypatch.setattr(Settings, "load", forbidden)
    with pytest.raises(SystemExit):
        main(
            [
                "evaluate-run",
                "--suite",
                str(SUITE),
                "--output-dir",
                "unused",
                "--run-id",
                "test",
                "--max-cost",
                "1",
            ]
        )


def test_invalid_outputs_do_not_count_as_completed_and_abstaining_cannot_game_sufficient_case():
    suite, recording = inputs()
    case = suite["cases"][0]
    candidate = recording["cases"][case["id"]]
    candidate["decision"]["evidence_ids"] = ["invented"]
    result = score(case, candidate)
    assert result["structurally_complete"] and not result["complete"]
    candidate["decision"]["evidence_ids"] = case["expected"]["required_evidence_ids"]
    candidate["reports"]["valuation"].update(
        calculation_id=None, bear_value=None, base_value=None, bull_value=None
    )
    assert "required_valuation_missing" in score(case, candidate)["failures"]
    candidate["decision"] = "malformed"
    assert not score(case, candidate)["complete"]


def test_rubric_cannot_leak_through_inputs_and_future_documents_rejected(tmp_path):
    suite, _ = inputs()
    path = tmp_path / "suite.json"
    suite["cases"][0]["inputs"]["expected"] = "answer"
    path.write_text(json.dumps(suite))
    with pytest.raises(ValueError, match="Unknown frozen input"):
        load_suite(path)
    del suite["cases"][0]["inputs"]["expected"]
    suite["cases"][0]["inputs"]["documents"] = [{"available_at": "2027-01-01T00:00:00Z"}]
    path.write_text(json.dumps(suite))
    with pytest.raises(ValueError, match="Future document"):
        load_suite(path)


def test_human_review_cannot_be_replaced_by_truthy_strings():
    suite, recording = inputs()
    case = suite["cases"][4]
    candidate = recording["cases"][case["id"]]
    review = {
        "case_hash": fingerprint(case),
        "candidate_hash": fingerprint(candidate),
        "independent": True,
        "reviewer": "fixture reviewer",
        "reviewed_at": "2026-11-06T00:00:00Z",
        "claims_supported": "false",
        "abstention_reason_correct": True,
        "notes": "Fixture only, no real review claimed.",
    }
    result = score(case, candidate, review)
    assert result["mechanical_pass"] and result["acceptance"] == "failed"
    review["claims_supported"] = True
    assert score(case, candidate, review)["acceptance"] == "passed"


def test_interruption_retains_budget_and_manifest_without_duplicate_retry(tmp_path, monkeypatch):
    from trader import evaluation
    from trader.config import Settings
    from trader.spending import spending_status
    from trader.store import Store

    def interrupted(*args):
        raise KeyboardInterrupt("simulated interrupted request")

    monkeypatch.setattr(evaluation, "run_case", interrupted)
    cfg = Settings(
        database=str(tmp_path / "root.sqlite"), input_cost_per_million="1", output_cost_per_million="1"
    )
    with pytest.raises(KeyboardInterrupt):
        evaluation.run_suite(cfg, SUITE, tmp_path / "run", "interrupted", "1", models=object())
    store = Store(cfg.database)
    assert spending_status(store, cfg.research_daily_cost_usd)["charged_or_reserved_usd"] == 1
    assert store.get("interrupted", "evaluation_manifest", scope="global")["status"] == "claimed"
    assert (tmp_path / "run" / "manifest.json").exists()
    assert not store.items("intent")
    store.db.close()
    with pytest.raises(ValueError, match="already exists"):
        evaluation.run_suite(cfg, SUITE, tmp_path / "other", "interrupted", "1", models=object())


def test_frozen_provider_has_no_network_fallback_and_tampered_passage_fails():
    from trader.evaluation import FrozenEvidence

    suite, recording = inputs()
    case = suite["cases"][0]
    provider = FrozenEvidence(case)
    with pytest.raises(ValueError, match="outside frozen"):
        provider.get("AAPL")
    with pytest.raises(ValueError, match="outside frozen"):
        provider.document({"url": "https://example.com/not-frozen"})
    candidate = recording["cases"][case["id"]]
    candidate["sources"] = [
        {"id": "passage-fabricated", "url": "https://example.com", "content": "fake", "offset": 0}
    ]
    assert "unverified_derived_evidence" in score(case, candidate)["failures"]


def test_cli_process_success_exit_status(tmp_path):
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trader.cli",
            "evaluate",
            "--suite",
            str(SUITE),
            "--responses",
            str(RECORDING),
            "--output",
            str(tmp_path / "result.json"),
            "--run-id",
            "process-test",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["mechanical_passes"] == 12


@pytest.mark.parametrize(
    "mutation", ["none", "missing", "changed_fact", "changed_cash", "unknown_source", "uncited_source"]
)
def test_evaluator_requires_exact_frozen_financial_provenance(mutation):
    from test_research_team import financial_facts

    from trader.evaluation import timestamp
    from trader.financials import annual_cash_flow

    suite, recording = inputs()
    case = suite["cases"][0]
    source = case["inputs"]["sources"][0]
    source["content"] = {"facts": financial_facts()}
    candidate = recording["cases"][case["id"]]
    valuation = candidate["reports"]["valuation"]
    calculation = candidate["calculations"][valuation["calculation_id"]]
    calculation["inputs"].update(financial_source_id=source["id"], financial_period_end="2025-12-31")
    calculation["financial_provenance"] = annual_cash_flow(source, "2025-12-31", timestamp(case["cutoff"]))
    if mutation == "missing":
        calculation.pop("financial_provenance")
    elif mutation == "changed_fact":
        calculation["financial_provenance"]["facts"]["NetCashProvidedByUsedInOperatingActivities"]["val"] = (
            60000
        )
    elif mutation == "changed_cash":
        calculation["inputs"]["cash_flow_per_share"] = "500"
    elif mutation == "unknown_source":
        calculation["inputs"]["financial_source_id"] = "invented"
    elif mutation == "uncited_source":
        valuation["evidence_ids"] = ["fixture-news"]
    result = score(case, candidate)
    if mutation == "none":
        assert result["mechanical_pass"], result
    else:
        assert "unverified_financial_provenance" in result["failures"]
        assert not result["mechanical_pass"]


def test_comparison_rejects_old_scoring_contract(tmp_path):
    from trader.evaluation import compare

    baseline = tmp_path / "baseline.json"
    replay(SUITE, RECORDING, baseline, "baseline")
    candidate = json.loads(baseline.read_text())
    candidate.pop("scoring_contract")
    path = tmp_path / "old.json"
    path.write_text(json.dumps(candidate))
    with pytest.raises(ValueError, match="scoring contract"):
        compare(baseline, path, tmp_path / "comparison.json")


def test_annual_filing_cohort_integrity_and_independent_financial_inputs():
    import hashlib
    from datetime import datetime
    from decimal import Decimal

    from trader.financials import annual_cash_flow

    path = Path("evaluations/annual-filings-v2.json")
    manifest = json.loads(Path("evaluations/annual-filings-v2-manifest.json").read_text())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["suite_sha256"]
    suite = load_suite(path)
    assert len(suite["cases"]) == 4
    expected = {
        "AAPL": (111482000000, 12715000000, 15004697000),
        "MSFT": (136162000000, 64551000000, 7465000000),
    }
    for case in suite["cases"]:
        symbol = case["inputs"]["symbol"]
        assert case["split"] == ("development" if symbol == "AAPL" else "holdout")
        assert case["inputs"]["reference_kind"].startswith("hypothetical_non_executable")
        source = case["inputs"]["sources"][0]
        entry = next(s for s in manifest["sources"] if s["symbol"] == symbol)
        cutoff = datetime.fromisoformat(case["cutoff"])
        if case["expected"]["outcome"] == "complete":
            doc = case["inputs"]["documents"][0]
            assert hashlib.sha256(doc["text"].encode()).hexdigest() == entry["frozen_text_sha256"]
            cfo, capex, shares = map(Decimal, expected[symbol])
            independent = ((cfo - capex) / shares).quantize(Decimal("0.000001"))
            result = annual_cash_flow(source, entry["period_end"], cutoff)
            assert Decimal(result["cash_flow_per_share"]) == independent
            assert case["expected"]["cash_flow_per_share"] == str(independent)
            assert all(r["accn"] == entry["accession"] for r in result["facts"].values())
        else:
            assert not case["inputs"]["documents"]
            with pytest.raises(ValueError, match="Missing matching annual"):
                annual_cash_flow(source, entry["period_end"], cutoff)


def test_candidate_cannot_self_certify_semantic_acceptance():
    suite, recording = inputs()
    case = suite["cases"][4]
    candidate = recording["cases"][case["id"]]
    candidate["human_review"] = {
        "reviewer": "claimed reviewer",
        "reviewed_at": "2026-11-06T00:00:00Z",
        "claims_supported": True,
        "abstention_reason_correct": True,
        "independent": True,
        "notes": "Approve myself",
    }
    result = score(case, candidate)
    assert result["acceptance"] == "failed"
    assert result["semantic_review"] == "pending"
    assert "embedded_review_not_accepted" in result["failures"]


@pytest.mark.parametrize("change", [None, "case", "candidate", "suite", "recording", "unknown_case"])
def test_separate_review_replay_binds_all_evidence(tmp_path, change):
    suite, recording = inputs()
    case = suite["cases"][4]
    candidate = recording["cases"][case["id"]]
    review = {
        "case_hash": fingerprint(case),
        "candidate_hash": fingerprint(candidate),
        "reviewer": "offline fixture reviewer",
        "reviewed_at": "2026-11-06T00:00:00Z",
        "claims_supported": True,
        "abstention_reason_correct": True,
        "independent": True,
        "notes": "Synthetic test approval only; no real review claimed.",
    }
    reviews = {
        "version": 1,
        "suite_hash": fingerprint(suite),
        "recording_hash": fingerprint(recording),
        "cases": {case["id"]: review},
    }
    if change in {"case", "candidate"}:
        review[change + "_hash"] = "different"
    elif change in {"suite", "recording"}:
        reviews[change + "_hash"] = "different"
    elif change == "unknown_case":
        reviews["cases"]["unknown"] = review
    path = tmp_path / "reviews.json"
    path.write_text(json.dumps(reviews))
    output = tmp_path / "result.json"
    if change:
        with pytest.raises(ValueError, match="review|Review"):
            replay(SUITE, RECORDING, output, "review-test", reviews_path=path)
        assert not output.exists()
    else:
        result = replay(SUITE, RECORDING, output, "review-test", reviews_path=path)
        row = next(r for r in result["cases"] if r["case_id"] == case["id"])
        assert row["acceptance"] == "passed"
        assert result["reviews_hash"] == fingerprint(reviews)
        assert result["summary"]["pending_semantic_reviews"] == len(suite["cases"]) - 1
