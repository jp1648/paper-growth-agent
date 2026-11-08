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
    execute = sub.add_parser("execute")
    execute.add_argument("decision_file")
    execute.add_argument("--intent-key", required=True)
    kill = sub.add_parser("kill")
    kill.add_argument("--reason", required=True)
    adjust = sub.add_parser("acknowledge-adjustment")
    adjust.add_argument("file")
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
    comparison = sub.add_parser("evaluate-compare", help="Compare paired frozen research evaluations")
    comparison.add_argument("--baseline", required=True)
    comparison.add_argument("--candidate", required=True)
    comparison.add_argument("--output", required=True)
    paper_analysis = sub.add_parser(
        "analyze-paper-returns", help="Offline paired return diagnostics; no broker"
    )
    paper_analysis.add_argument("--input", required=True)
    paper_analysis.add_argument("--output", required=True)
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
    args = parser.parse_args(argv)
    if args.command == "validate-cash-dividends":
        from .corporate_actions import validate_cash_dividends

        print(dumps(validate_cash_dividends(args.directory)))
        return 0
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
            Settings.load(args.config), args.suite, args.output_dir, args.run_id, args.max_cost, args.split
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
                    service, DeepResearchTeam(cfg, store, EvidenceProvider(cfg, store, service.data))
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
