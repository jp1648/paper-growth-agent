# Trader — quick summary

Trader is a cash-only, paper-investing agent. Deep Agents coordinates company
research; LangGraph runs the decision/risk/execution workflow; Temporal supports
durable research. Alpaca's paper account is authoritative for cash, positions,
orders and fills. SQLite holds the operational ledger; PostgreSQL stores
observability data for a password-protected, read-only Next.js dashboard.

## Project map

- `trader/`: research, evidence, valuation, budgets, broker integration and safety gates.
- `dashboard/`: outcomes, deductions, traces, costs and portfolio views.
- `deploy/`: container, cloud, maintenance and supervised acceptance tooling.
- `tests/` and `evaluations/`: automated checks and research-quality evaluations.
- `docs/`: operating procedures, architecture and acceptance evidence.
- `state/`: private runtime records and evaluation artifacts; never commit secrets or ledgers.

## Last recorded status

As checked November 25, 2026: **paused; NO-GO for real
money**. A one-share AAPL paper fill was confirmed, but that does not establish
investment quality or unattended-trading readiness.

The unseen COST full-evidence case supported temporary WATCH, not completed
investment research. Its missing-evidence case failed on a provider connection
error. Provider exports have now reconciled that run to $0.1700242, with no
unresolved research charges remaining. Available grant balances totaled $156.20
at the billing check. An independently reviewed $40 research allocation was
installed November 25, with $20/day, $20/run and a $15 buffer unchanged. It expires
conservatively November 29 at 00:00 UTC. The November 25 missing-evidence retry failed claim-source validation after two
reports, costing an estimated $1.1849725 with no unknown calls. Exact rejected
output is retained in Postgres traces; a bounded correction is under offline
review. The held AAPL refresh remains outstanding; each paid attempt still
requires fresh admission.
Preserve original runs and history.

## Start here

- [README](README.md): setup and command behavior.
- [Live readiness](docs/live-readiness.md): detailed results and remaining gates.
- [Operations](docs/operations.md): runtime and recovery procedures.
- [Evaluation](docs/evaluation.md): isolated research benchmarks.
- [Observability](docs/observability.md) and [Google Cloud](docs/google-cloud.md): infrastructure.
- [Agent instructions](AGENTS.md): development and operational boundaries.
