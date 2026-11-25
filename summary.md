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
Held AAPL research required a refresh. The first brought-forward attempt failed
at the 240,000-byte context limit, was canceled and settled at an estimated
$4.878760. Its original history remains preserved and its duplicate timer is
disabled. The subsequent context/tool repair is deployed. New stable run
`held-aapl-refresh-20261125-2` completed all four reports, valuation correction,
fresh critique and final lead decision. It used 50 model calls and 96 tools,
settling at $11.2428000 estimated cost ($20.2236825 total November 25). These
estimates are not provider invoices. Offline checks passed: 1,636 tests, with
seven skipped and one deselected; CI succeeded. The temporary 320 KB/96-tool
allowances enabled this test, and ordinary limits were subsequently restored in
both containers. The proposed SELL of one paper AAPL share remains unexecuted
and unpromoted. Completed independent review withholds economic acceptance
because material valuation gaps remain; technical completion is established. One abnormal native worker shutdown remains a follow-up,
although later controlled shutdowns and both service restarts succeeded.
Trading remains paused.
Preserve original runs and history.

## Start here

- [README](README.md): setup and command behavior.
- [Live readiness](docs/live-readiness.md): detailed results and remaining gates.
- [Operations](docs/operations.md): runtime and recovery procedures.
- [Evaluation](docs/evaluation.md): isolated research benchmarks.
- [Observability](docs/observability.md) and [Google Cloud](docs/google-cloud.md): infrastructure.
- [Agent instructions](AGENTS.md): development and operational boundaries.
