import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path

from .alpaca import AlpacaBroker, AlpacaData, safe_error
from .config import Settings
from .evidence import EvidenceProvider
from .models import Decision, dumps, now
from .observability import configure, emit, traced
from .reports import daily_report, portfolio_report
from .research import build_graph
from .research_team import DeepResearchTeam
from .runner import Runner
from .service import Service
from .store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description="Active paper investing; no live trading support")
    parser.add_argument("--config", default="config.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--new-experiment", action="store_true")
    for name in ("reconcile", "maintain", "report", "daily", "resume", "listen", "backup"):
        sub.add_parser(name)
    tick = sub.add_parser("tick")
    tick.add_argument("--event", action="store_true")
    research = sub.add_parser("research")
    research.add_argument("symbol")
    research.add_argument(
        "--defer-execution",
        action="store_true",
        help="Research now; reevaluate execution during a regular session",
    )
    research.add_argument("--run-key", required=True, help="Stable logical decision ID, reused on retry")
    research.add_argument("--finalize-from", help="Explicit failed run with four fresh completed reports")
    research.add_argument(
        "--testing",
        action="store_true",
        help="Research only: use the explicit --max-cost allowance instead of normal daily funding; retain other limits",
    )
    research.add_argument("--max-cost", help="Explicit bounded testing allowance in USD")
    execute = sub.add_parser("execute")
    execute.add_argument("decision_file")
    execute.add_argument("--intent-key", required=True)
    kill = sub.add_parser("kill")
    kill.add_argument("--reason", required=True)
    adjust = sub.add_parser("acknowledge-adjustment")
    adjust.add_argument("file")
    extension = sub.add_parser("approve-experiment-extension")
    extension.add_argument("file")
    revision = sub.add_parser("acknowledge-revision")
    revision.add_argument("file")
    notification = sub.add_parser(
        "resolve-notification", help="Record delivery evidence without sending a message"
    )
    notification.add_argument("file")
    costs = sub.add_parser("record-data-cost")
    costs.add_argument("usd")
    costs.add_argument("--reason", required=True)
    evaluation = sub.add_parser("evaluate", help="Score frozen research records; never connects to a broker")
    evaluation.add_argument("--suite", required=True)
    evaluation.add_argument("--responses", required=True)
    evaluation.add_argument("--output", required=True)
    evaluation.add_argument("--run-id", required=True)
    evaluation.add_argument("--reviews", help="Separate reviewer-supplied, hash-bound semantic reviews")
    live_eval = sub.add_parser(
        "evaluate-run", help="Explicitly budgeted model research on frozen evidence; no broker"
    )
    live_eval.add_argument("--suite", required=True)
    live_eval.add_argument("--output-dir", required=True)
    live_eval.add_argument("--run-id", required=True)
    live_eval.add_argument("--max-cost", required=True)
    live_eval.add_argument("--split", choices=["development", "holdout"], default="development")
    live_eval.add_argument("--allow-paid", action="store_true", required=True)
    live_eval.add_argument(
        "--testing",
        action="store_true",
        help="Use the explicit --max-cost testing allowance instead of normal run/day budgets; no broker",
    )
    comparison = sub.add_parser("evaluate-compare", help="Compare paired frozen research evaluations")
    comparison.add_argument("--baseline", required=True)
    comparison.add_argument("--candidate", required=True)
    comparison.add_argument("--output", required=True)
    paper_analysis = sub.add_parser(
        "analyze-paper-returns", help="Offline paired return diagnostics; no broker"
    )
    paper_analysis.add_argument("--input", required=True)
    paper_analysis.add_argument("--output", required=True)
    paper_observations = sub.add_parser(
        "build-paper-observations", help="Offline economic NAV to paired returns; no broker"
    )
    paper_observations.add_argument("--input", required=True)
    paper_observations.add_argument("--output", required=True)
    paper_observations.add_argument("--benchmark-input")
    paper_observations.add_argument("--benchmark-capture")
    paper_comparison = sub.add_parser(
        "compare-paper-inputs", help="Offline consistency check and recomputed SPY/QQQ comparisons"
    )
    paper_comparison.add_argument("--primary", required=True)
    paper_comparison.add_argument("--secondary", required=True)
    paper_comparison.add_argument("--output", required=True)
    paper_bundle = sub.add_parser("build-paper-bundle", help="Offline draft evidence bundle; no activation")
    paper_bundle.add_argument("--input", required=True)
    paper_bundle.add_argument("--output", required=True)
    paper_schedule = sub.add_parser(
        "draft-paper-schedule", help="Offline calendar/rotation draft; no activation"
    )
    paper_schedule.add_argument("--input", required=True)
    paper_schedule.add_argument("--output", required=True)
    benchmark = sub.add_parser(
        "preview-benchmark-purchase", help="Offline hypothetical ETF purchase arithmetic"
    )
    benchmark.add_argument("--input", required=True)
    cash_replay = sub.add_parser("model-paper-cash", help="Offline hypothetical cash replay")
    cash_replay.add_argument("--input", required=True)
    benchmark_ledger = sub.add_parser("build-benchmark-ledger", help="Offline hypothetical ETF ledger")
    benchmark_ledger.add_argument("--input", required=True)
    benchmark_ledger.add_argument("--output", required=True)
    benchmark_ledger.add_argument("--capture", help="Validate all marks against a retained raw SIP capture")
    inventory = sub.add_parser("reconcile-paper-marks", help="Offline inventory to common raw marks")
    inventory.add_argument("capture")
    inventory.add_argument("--input", required=True)
    validate_prices = sub.add_parser("validate-daily-prices", help="Offline retained price integrity check")
    validate_prices.add_argument("directory")
    compare_prices = sub.add_parser("compare-daily-prices", help="Offline retained price revision comparison")
    compare_prices.add_argument("before")
    compare_prices.add_argument("after")
    prices = sub.add_parser("capture-daily-prices", help="Retain raw historical SIP marks; no broker")
    prices.add_argument("symbols", nargs="+")
    prices.add_argument("--calendar", required=True, help="Retained broker calendar JSON")
    prices.add_argument("--output-dir", required=True, help="New evidence directory")
    actions = sub.add_parser(
        "capture-corporate-actions", help="Read-only market-data evidence capture; no broker"
    )
    actions.add_argument("symbol")
    actions.add_argument("--start", required=True, help="Inclusive processing date")
    actions.add_argument("--end", required=True, help="Inclusive processing date")
    actions.add_argument("--output-dir", required=True, help="New evidence directory; never overwritten")
    validate_actions = sub.add_parser(
        "validate-cash-dividends", help="Offline capture integrity and cash-field checks; no broker"
    )
    validate_actions.add_argument("directory")
    compare_actions = sub.add_parser(
        "compare-cash-dividends", help="Offline retained dividend revision comparison"
    )
    compare_actions.add_argument("before")
    compare_actions.add_argument("after")
    entitlement = sub.add_parser(
        "audit-dividend-entitlements", help="Offline dividend receivable/payment arithmetic"
    )
    entitlement.add_argument("directory")
    entitlement.add_argument("--input", required=True)
    args = parser.parse_args(argv)
    if args.command == "research":
        if args.testing and (not args.defer_execution or args.max_cost is None):
            parser.error("Research --testing requires --defer-execution and --max-cost")
        if args.max_cost is not None and not args.testing:
            parser.error("Research --max-cost requires --testing")
    if args.command == "model-paper-cash":
        from .paper_cash import replay

        path = Path(args.input)
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Cash replay exceeds input limit")
        print(dumps(replay(json.loads(path.read_text()))))
        return 0
    if args.command == "build-benchmark-ledger":
        from .benchmark import build_file

        print(dumps(build_file(args.input, args.output, capture=args.capture)))
        return 0
    if args.command == "preview-benchmark-purchase":
        from .benchmark import preview

        print(dumps(preview(json.loads(Path(args.input).read_text()))))
        return 0
    if args.command == "reconcile-paper-marks":
        from .paper_observations import reconcile_marks

        print(dumps(reconcile_marks(args.capture, json.loads(Path(args.input).read_text()))))
        return 0
    if args.command in {"validate-daily-prices", "compare-daily-prices"}:
        from .price_capture import compare_prices, validate_prices

        result = (
            validate_prices(args.directory)
            if args.command == "validate-daily-prices"
            else compare_prices(args.before, args.after)
        )
        print(dumps(result))
        return 0
    if args.command == "build-paper-bundle":
        from .paper_bundle import build_file

        print(dumps(build_file(args.input, args.output)))
        return 0
    if args.command == "draft-paper-schedule":
        from .paper_schedule import build_file

        print(dumps(build_file(args.input, args.output)))
        return 0
    if args.command == "compare-paper-inputs":
        from .paper_observations import compare_inputs

        print(dumps(compare_inputs(args.primary, args.secondary, args.output)))
        return 0
    if args.command == "build-paper-observations":
        from .paper_observations import build_file

        print(
            json.dumps(
                build_file(
                    args.input,
                    args.output,
                    benchmark_input=args.benchmark_input,
                    benchmark_capture=args.benchmark_capture,
                ),
                indent=2,
            )
        )
        return 0
    if args.command == "audit-dividend-entitlements":
        from .dividends import audit_entitlements

        print(dumps(audit_entitlements(args.directory, json.loads(Path(args.input).read_text()))))
        return 0
    if args.command == "compare-cash-dividends":
        from .corporate_actions import compare_cash_dividends

        print(dumps(compare_cash_dividends(args.before, args.after)))
        return 0
    if args.command == "validate-cash-dividends":
        from .corporate_actions import validate_cash_dividends

        print(dumps(validate_cash_dividends(args.directory)))
        return 0
    if args.command == "capture-daily-prices":
        from .price_capture import capture_prices

        provider = AlpacaData(Settings.load(args.config))
        try:
            print(
                json.dumps(capture_prices(provider, args.symbols, args.calendar, args.output_dir), indent=2)
            )
            return 0
        finally:
            provider.client.close()
    if args.command == "capture-corporate-actions":
        from .corporate_actions import capture_actions

        provider = AlpacaData(Settings.load(args.config))
        try:
            result = capture_actions(provider, args.symbol, args.start, args.end, args.output_dir)
            print(json.dumps(result, indent=2))
            return 0
        finally:
            provider.client.close()
    if args.command == "analyze-paper-returns":
        from .paper_analysis import analyze_file

        print(json.dumps(analyze_file(args.input, args.output), indent=2))
        return 0
    if args.command == "evaluate-compare":
        from .evaluation import compare

        result = compare(args.baseline, args.candidate, args.output)
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "evaluate-run":
        from .evaluation import run_suite

        result = run_suite(
            Settings.load(args.config),
            args.suite,
            args.output_dir,
            args.run_id,
            args.max_cost,
            args.split,
            testing=args.testing,
        )
        print(json.dumps(result["summary"], indent=2))
        return 0
    if args.command == "evaluate":
        from .evaluation import replay

        result = replay(args.suite, args.responses, args.output, args.run_id, reviews_path=args.reviews)
        print(json.dumps(result["summary"], indent=2))
        return 0
    try:
        configure(Settings.load(args.config).database)
    except Exception:  # noqa: BLE001, S110 - dispatch reports the configuration error
        pass  # Existing command boundary reports configuration failures.
    return dispatch(args)


@traced("command", inputs=lambda args: vars(args))
def dispatch(args):
    store = None
    try:
        cfg = Settings.load(args.config)
        testing_research = args.command == "research" and args.testing
        if testing_research:
            cfg = Settings.model_validate({**cfg.model_dump(), "research_max_cost_usd": args.max_cost})
        store = Store(cfg.database)
        if args.command == "resolve-notification":
            from .notifications import resolve_delivery

            result = resolve_delivery(store, json.loads(Path(args.file).read_text()))
            print(dumps(result))
            return 0
        service = Service(cfg, store, AlpacaBroker(cfg), AlpacaData(cfg))
        if args.command == "init":
            result = service.initialize(args.new_experiment)
        else:
            service.require_experiment()
            if args.command == "reconcile":
                result = service.reconcile()
            elif args.command == "maintain":
                result = Runner(service, None).maintain()
            elif args.command == "backup":
                result = {"path": str(store.backup())}
            elif args.command == "kill":
                service.kill(args.reason)
                result = {
                    "paused": True,
                    "message": "Cancellation requested; continue reconciliation for possible fills. No liquidation.",
                }
            elif args.command == "resume":
                service.resume()
                result = {"paused": False}
            elif args.command == "report":
                result = portfolio_report(service)
            elif args.command == "daily":
                result = daily_report(service)
            elif args.command == "execute":
                proposal = Decision.model_validate_json(Path(args.decision_file).read_text())
                result = service.execute(
                    proposal, args.intent_key, {"source": "explicit operator decision file"}
                )
            elif args.command == "acknowledge-adjustment":
                result = service.acknowledge_adjustment(json.loads(Path(args.file).read_text()))
            elif args.command == "approve-experiment-extension":
                from .lifecycle import approve_extension

                result = approve_extension(service, json.loads(Path(args.file).read_text()))
            elif args.command == "acknowledge-revision":
                result = service.acknowledge_revision(json.loads(Path(args.file).read_text()))
            elif args.command == "record-data-cost":
                from .models import dec

                amount = dec(args.usd)
                if amount < 0:
                    raise ValueError("Data cost must be nonnegative")
                store.log("data_cost", {"usd": amount, "reason": args.reason, "at": now()})
                result = {"recorded": True}
            elif args.command == "listen":
                import fcntl

                path = store.control_path(cfg.control_dir, service.account_id, ".listener.lock")
                with path.open("a") as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise RuntimeError("Another account listener is already running") from None
                    service.reconcile()
                    asyncio.run(
                        __import__("trader.streams", fromlist=["listen"]).listen(cfg, store, args.config)
                    )
                return
            else:
                graph = build_graph(
                    service,
                    DeepResearchTeam(
                        cfg, store, EvidenceProvider(cfg, store, service.data), testing=testing_research
                    ),
                )
                if args.command == "research":
                    result = graph.invoke(
                        {
                            "symbol": args.symbol,
                            "run_key": args.run_key,
                            "fresh": True,
                            "research_only": args.defer_execution,
                            "finalize_from": args.finalize_from,
                        }
                    )
                else:
                    result = Runner(service, graph).tick(args.event)
        print(dumps(result))
    except Exception as error:  # noqa: BLE001 - persist failure at the execution boundary
        emit(
            "command.failure",
            {"error_type": type(error).__name__, "error": str(error), "traceback": traceback.format_exc()},
        )
        message = safe_error(error)
        if store:
            store.log("error", {"command": args.command, "error": message})
        print(message, file=sys.stderr)
        return 1
    finally:
        if store:
            store.db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
