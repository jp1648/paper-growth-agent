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
    execute = sub.add_parser("execute")
    execute.add_argument("decision_file")
    execute.add_argument("--intent-key", required=True)
    kill = sub.add_parser("kill")
    kill.add_argument("--reason", required=True)
    adjust = sub.add_parser("acknowledge-adjustment")
    adjust.add_argument("file")
    costs = sub.add_parser("record-data-cost")
    costs.add_argument("usd")
    costs.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
