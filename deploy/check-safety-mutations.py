"""Prove selected safety tests detect disabled guards, using isolated source copies."""

import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = {
    "cash": ("trader/risk.py", "if notional + settings.fee_buffer > available:", "if False:"),
    "ownership": ("trader/risk.py", "if decision.quantity > held - pending_sell:", "if False:"),
    "pause": ("trader/risk.py", "if killed:", "if False:"),
    "research_age": (
        "trader/service.py",
        'research_reasons.append("Research is stale or future-dated")',
        "pass",
    ),
    "financial_basis": (
        "trader/research_team.py",
        "if cash_flow_per_share is not None and dec(cash_flow_per_share) != dec(required_cash):",
        "if False:",
    ),
    "reservation": ("trader/service.py", "        return amount\n", "        return ZERO\n"),
    "loss_latch": ("trader/service.py", 'if self.store.get("loss_limit")["latched"]:', "if False:"),
    "daily_baseline": (
        "trader/service.py",
        'if not self.store.get("loss_limit")["daily"]["valid"]:',
        "if False:",
    ),
    "loss_cancel": ("trader/service.py", 'or (loss_blocked and o["side"] == "buy")', "or False"),
    "transport_preflight": ("trader/alpaca.py", "if before_send is not None:", "if False:"),
    "missing_history": ("trader/service.py", "if missing:", "if False:"),
    "conflicting_history": (
        "trader/service.py",
        "if identifier in incoming and incoming[identifier] != activity:",
        "if False:",
    ),
    "history_pause": (
        "trader/service.py",
        "if isinstance(error, BrokerHistoryError) and not self.kill_path.exists():",
        "if False:",
    ),
    "cached_normalization": (
        "trader/service.py",
        'research_reasons.append("Valuation normalization review is missing or unresolved")',
        "pass",
    ),
    "lead_normalization": (
        "trader/research_team.py",
        'or not normalization_supported(self.reports["valuation"])',
        "or False",
    ),
    "activity_pagination_cycle": ("trader/alpaca.py", "if token in cursors:", "if False:"),
}


def main():
    with tempfile.TemporaryDirectory(prefix="trader-mutations-") as directory:
        target = Path(directory)
        for folder in ("trader", "tests"):
            shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
        (target / "evaluations").mkdir()
        for name in (
            "baseline-v1.json",
            "annual-filings-v2.json",
            "annual-filings-v3.json",
            "annual-filings-v3-manifest.json",
        ):
            shutil.copy2(ROOT / "evaluations" / name, target / "evaluations" / name)
        for name, mutation in [("baseline", None), *MUTATIONS.items()]:
            if mutation:
                file, before, after = mutation
                path = target / file
                original = path.read_text()
                if original.count(before) != 1:
                    raise RuntimeError(f"Mutation {name} no longer matches exactly one guard")
                path.write_text(original.replace(before, after))
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "-q",
                        "tests/test_risk.py",
                        "tests/test_execution.py",
                        "tests/test_live_readiness.py",
                        "tests/test_financials.py",
                        "tests/test_normalization.py",
                        "tests/test_loss_limits.py",
                        "tests/test_executor_owner.py",
                        "tests/test_adapters.py",
                        "tests/test_research_team.py::test_final_lead_cannot_override_normalization_gap",
                        "--junitxml=mutation.xml",
                    ],
                    cwd=target,
                    env={
                        **os.environ,
                        "OBSERVABILITY_DATABASE_URL": "",
                        "TEST_OBSERVABILITY_DATABASE_URL": "",
                        "PAPER_SMOKE": "0",
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                report = ET.parse(target / "mutation.xml").getroot()
                failures = len(report.findall(".//failure"))
                errors = len(report.findall(".//error"))
                if errors or (
                    result.returncode != 0 if mutation is None else result.returncode != 1 or not failures
                ):
                    print(f"{name}: INVALID or SURVIVED; failures={failures}, errors={errors}")
                    for case in report.findall(".//testcase"):
                        if case.find("failure") is not None or case.find("error") is not None:
                            print(f"  {case.get('classname')}::{case.get('name')}")
                    return 1
                print(f"{name}: " + ("passed" if mutation is None else f"caught ({failures} failing tests)"))
            finally:
                if mutation:
                    path.write_text(original)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
