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
conservatively November 29 at 00:00 UTC. The first November 25 retry failed claim-source validation. After a reviewed
correction was deployed, the second retry completed all four reports and a
zero-share WATCH; independent review accepted the missing-evidence abstention
case only. It does not establish complete investment research. Both retries cost
an estimated $4.1021225, with no unknown charges. Original failures remain.
Held AAPL research is still overdue. Following the user's authorization to raise
testing limits, its single $20 attempt was brought forward to November 25 with a
temporary $24.1021225 daily ceiling; the November 26 duplicate timer was disabled.
The same stable run ID was preserved. The valuation rebuild stopped at 262,710
input bytes against the 240,000-byte context limit, with 30/60 model calls and a
projected next-call cost of $6.25255/$20. This is a context failure, not a dollar
budget failure. Final research acceptance remains incomplete. The exact execution was canceled
and its original reservation settled from saved usage at an estimated $4.878760
($8.9808825 total November 25). The ordinary $20 daily ceiling was restored and
verified in both containers. These estimates are not provider invoices.
Trading remains paused.
Preserve original runs and history.

## Start here

- [README](README.md): setup and command behavior.
- [Live readiness](docs/live-readiness.md): detailed results and remaining gates.
- [Operations](docs/operations.md): runtime and recovery procedures.
- [Evaluation](docs/evaluation.md): isolated research benchmarks.
- [Observability](docs/observability.md) and [Google Cloud](docs/google-cloud.md): infrastructure.
- [Agent instructions](AGENTS.md): development and operational boundaries.
