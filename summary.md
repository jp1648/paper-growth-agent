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
Offline follow-up now supplies a reproducible operating-driver valuation, cash
and financing reconciliation, normalization sensitivities and a symmetric
sell/retain rule. It defers valuation action rather than endorsing the prior
SELL; absent award/financing disclosures remain explicit. Independent review accepted the conditional offline analytical repair and
its no-action process outcome, not full opening research or a trade.
The shutdown investigation reproduced a native Temporal 1.36 finalization
failure on macOS and Linux; the upstream 1.37 fix passes both and is pinned in
the candidate. All 1,638 offline tests passed, with seven skipped and one
deselected. The SDK 1.37 image is now deployed on both production containers. The controlled
worker shutdown exited 0, natural client exit passed, and services restarted with
pause/config/protected state preserved. The exact historical exit-139 stack
remains unproved. See docs/worker-shutdown.md.
A later 36-file primary-source supplement closes attainable filing-retrieval
work. Independent review accepts a scoped DEFER/no-action research conclusion;
undisclosed award inventory and future funding/rates remain explicit uncertainty,
not reasons to demand inaccessible internal data before abstaining. This does
not promote the original SELL or satisfy the exact durable-result opening gate.
See the latest primary-evidence section in docs/live-readiness.md.
Robinhood MCP research is now deployed and enabled in both production containers.
Authenticated AAPL acquisition, caching, injected-outage fallback, order-tool
denial and Postgres traces passed; see docs/robinhood-mcp-assessment.md.
This does not close investment acceptance: the durable AAPL result remains
unapproved, and a new result requires independent review. The later authorized $18 attempt stopped during valuation correction on an
unlisted filing accession; no final decision was committed. Recorded model
usage is $8.9845275. Independent review subsequently verified all 37 saved
model exchanges and the exact local pre-fetch failure; the ledger now settles
that estimate through an additive audit, preserving the original unknown
receipt and pending operation. The workflow is canceled, not retried. The
reviewed navigation-error fix is deployed and source-verified in both containers;
worker shutdown exited 0 and services restarted with pause and history intact. See the latest
November 25 attempt in docs/live-readiness.md. No orders were submitted.
The follow-up SEC-discovery fix now exposes financing/proxy forms and the full
validated filing index; 1,695 offline tests passed and production checks passed.
New run `held-aapl-navigation-20261125-2` completed all specialists, correction,
critique and final durable decision for $11.2011800 estimated model cost. Its
decision is HOLD the existing paper share with zero added shares. Valuation and
critique still report material gaps; independent investment review is separate
from technical completion. Ordinary limits were restored; no unsettled research
charges remain. Independent review supports temporary HOLD/no action but withholds
full investment acceptance. An offline supplement closes financing/lease/proxy
arithmetic omissions. The identified operating-to-cash tool gap is now fixed,
independently reviewed and deployed as `12c2390` (image `72dac496…`); 1,730
offline tests passed. A real result using this explicit forecast reconciliation
and independent economic acceptance remain outstanding. Trading remains paused.
Preserve original runs and history.

The annual-path follow-up now discounts explicit yearly cash flows and a separately
derived forward terminal year, with source-bound historical reconciliation and
independent arithmetic replay. Offline Apple analysis removes exceptional tax-flow
perpetuation and reconciles intangible replacement/growth spending. The complete
source-cited tool payload exceeds 8,000 tokens, so configuration permits an explicit
12,000-token supervised allowance while keeping the default unchanged. Offline
checks passed and the annual-path image was deployed. The subsequent durable run
`held-aapl-annual-20261125-3` failed before final decision: the lead repeatedly
submitted an overlong correction, while prompt compaction discarded the tool
rejection. All 39 model calls were reconciled to $16.600585 estimated usage;
confirmed cancellation and audited accounting closure preserved its history.
Ordinary limits were restored in both containers. A bounded correction-feedback
repair and source-verified evaluation of the supplied forecast are in progress;
full investment acceptance remains unproved. Trading remains paused.

## Start here

- [README](README.md): setup and command behavior.
- [Live readiness](docs/live-readiness.md): detailed results and remaining gates.
- [Operations](docs/operations.md): runtime and recovery procedures.
- [Evaluation](docs/evaluation.md): isolated research benchmarks.
- [Observability](docs/observability.md) and [Google Cloud](docs/google-cloud.md): infrastructure.
- [Agent instructions](AGENTS.md): development and operational boundaries.

The correction-feedback fix is now deployed (`35008be`, image `ebfb6fe9…`),
with 1,788 offline tests and CI passing. A subsequent fresh run produced the
correct annual valuation and verified source mappings, but its valuation request
exceeded the 320 KB context cap by 752 bytes. It remains incomplete and canceled;
$4.600570 estimated usage awaits audited closure after its original expiry.
Lossless policy pooling and a justified larger supervised context/tool allowance
are under validation. Trading remains paused; full research acceptance is pending.


Latest November 25 follow-up: deployed lossless forecast-policy projection passed
1,791 offline tests. Fresh annual run5 then hit a provider read timeout during
valuation after 23 completed calls ($4.480640 estimated). Its dispatched 24th
request has no saved response; the full $20 reservation remains protected and
research acceptance is incomplete. The independently reviewed 180-second
valuation timeout fix passes 1,794 offline tests but is not yet deployed. Provider
exports still omit known successful calls, so no charge release is justified.
Trading remains paused; see the latest live-readiness entry.
