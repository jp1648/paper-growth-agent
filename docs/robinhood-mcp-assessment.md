# Robinhood MCP assessment — November 25, 2026

Robinhood provides an official hosted Trading MCP. None of its tools is connected
in this task, so this assessment verifies documentation, not authenticated
responses, source completeness or unattended OAuth behavior.

## Fit for Trader

The [official external-agent tool table](https://robinhood.com/us/en/support/articles/trading-with-your-agent/)
lists these useful read capabilities:

| Need | Documented external-agent capability |
| --- | --- |
| Original filings and discovery | `get_sec_filing_index`, `get_sec_filing` |
| Extracted filing facts | `get_sec_filing_facts`, `get_sec_filing_facts_catalog` |
| Financial series | `get_financials` |
| Quotes and history | `get_equity_quotes`, `get_equity_historicals` |
| Earnings dates and results | `get_earnings_calendar`, `get_earnings_results` |
| Market comparisons | `get_equity_fundamentals`, `get_equity_analyst_ratings` |

The table currently lists news and earnings-call transcripts for built-in agents,
not external MCP agents. Robinhood documents both read tools and order tools;
`review_equity_order` is a preview, not a substitute for an end-to-end paper broker.

[Onboarding](https://robinhood.com/us/en/support/articles/onboarding-an-external-agent/)
uses `https://agent.robinhood.com/mcp/trading` with authentication and a dedicated
MCP account. Account reads can cover other Robinhood accounts. Its
[account agreement](https://cdn.robinhood.com/assets/robinhood/legal/account_scope_agentic_brokerage_agreement.pdf)
identifies the account as holding real money and assets.

## Recommendation and implementation boundary

Use it as a possible supplemental research adapter, while retaining Alpaca paper
execution. It could reduce our filing-discovery and financial-series plumbing.
It cannot resolve a company's undisclosed award inventory, use of borrowing
proceeds, accounting attribution, or justify an investment assumption merely by
returning a ratio. That is an inference about the remaining evidence work, not a
claim that its live responses have been tested.

Before integration, inspect authenticated schemas and sample only allowlisted
research reads. Verify filing accession, original URL, reporting period, units,
retrieval timestamp and completeness against original SEC documents. Store raw
responses and exact cited excerpts with stable IDs/hashes. Treat extracted facts
as leads until matched to the original disclosure. Apply our existing caching,
traces, timeouts and unknown-cost handling; never infer that a documented data
call is free or universally entitled.

The official trading page says trade approvals default off for external MCP
accounts. Do not assume a platform confirmation will prevent an unintended order.
Keep the MCP client in the evidence layer and fail closed on tool-list changes.
Do not expose order, transfer, watchlist mutation, alert mutation or account tools
to the research model. Test denied write calls, incomplete or stale facts,
conflicting accession/units, expired authentication, rate limits and replayed
responses before runtime use. A local allowlist is not a claim that Robinhood
supplies a server-enforced read-only scope; that still needs verification.

No Robinhood connection, account opening, authentication, integration or order was
performed. Direct SEC retrieval remains the immediate path for the current AAPL
acceptance work.

## Implemented research adapter

Trader now includes an opt-in MCP client and two research tools:
`research_data_catalog` retrieves authenticated schemas for the fixed read
allowlist, and `research_data` requests one of those capabilities. Order,
account, portfolio, transfer and other tools are blocked again at the HTTP
transport boundary, even if they appear in the remote catalog. This integration
does not replace Alpaca quotes or any execution/risk input.

Authenticate on the same computer where this command runs:

```sh
uv run python -m trader.robinhood_connect --token-file state/robinhood-oauth.json
```

Open the printed Robinhood URL and complete sign-in. The callback listens only
on `127.0.0.1:18765`; the SDK verifies OAuth state and PKCE. The command only
initializes MCP and lists tools. It does not read accounts or submit trades.
OAuth tokens and client registration are stored with mode 0600 outside the
ledger and telemetry. Runtime refresh uses the same private file; interactive
sign-in is never attempted by an unattended research worker. Configure
`robinhood_enabled = true` only after connection and read verification. The
credential file lives next to the configured ledger; do not commit it, paste it
into chat or place it in dashboard/browser configuration. Remote deployment
requires transferring that file privately to the worker's state directory.

The default allowance is 12 data-call attempts per New York day, with a four-hour
exact-request cache (`robinhood_daily_calls`, `robinhood_cache_hours`). This is
not a dollar budget: provider billing/entitlements remain separate, and recorded
cost is unknown rather than reported as zero. Completed original responses,
request arguments, schema hash, response hash and retrieval timestamps are
retained under durable operation IDs. Ambiguous calls leave a persistent block
across restarts. Inspect provider usage and the operation before resolving that
block; do not delete unknown records to retry. Schema discovery does not request
financial data and is separate from the data-call allowance.

When a supported filing, facts, financials, fundamentals or earnings request is
unavailable, the tool returns existing SEC facts/filing navigation and Alpaca
news, explicitly labeled as partial fallback coverage. It does not pretend that
news is a consensus series or that an index is a full filing. Original passages
remain available through `filing_documents`/`read_document`. No equivalent
analyst-rating fallback is claimed. Vendor extracts must be checked against
original issuer/accession/date/unit evidence before valuation. Historical
evaluations never call live Robinhood tools. Tool traces and research checkpoints
use the existing observability/replay path; SDK errors are sanitized at the
credential boundary.

### Local connection verification — November 25, 2026

OAuth completed successfully in Chrome on this Mac. Authenticated discovery
returned all nine allowlisted research tools. One bounded AAPL fundamentals
request succeeded through the audited adapter, with original response and schema
hash retained in the private, isolated `state/robinhood-verification/ledger.sqlite`.
Operation: `robinhood-e9c9aafb96f5429ba963d40453be426b`. The returned issuer was
AAPL; this verifies acquisition, not accuracy or investment acceptance. Provider
cost remains unknown, not zero. No account or order tools were called.

Offline validation: 1,670 passed, seven skipped, one deselected; Ruff lint and
format checks passed. Production deployment and credential provisioning remain
outstanding. Production configuration and trading pause were not changed.

### Production deployment and verification — November 25, 2026

Revision `9dcc529` was built from a tracked-only context and deployed to both
executor and research worker as image digest
`sha256:501f9d2a99588eca87f9845d8c2a5a7e52328172dcfe6cd52dd9c56beab213ad`.
OAuth credentials were privately provisioned for the runtime user. The sole
configuration change is `robinhood_enabled = true`; the trading pause remains.

Authenticated production discovery returned all nine allowlisted tools. One
AAPL fundamentals request succeeded, with source
`robinhood-060779576887444bae68ccf92842a033`; a repeat used the identical cached
source. An injected provider outage returned the 14 retained original sources
as explicitly partial fallback coverage. Analyst ratings correctly had no
fabricated fallback. An attempted order-tool name was denied before network
access. These are acquisition and failure-path checks, not investment acceptance.
PostgreSQL contains matching start/end traces and durable operation/cache events
under `robinhood-production-verification-20261125`.

The guarded release preserved the original experiment, protected ledger records,
journal prefix and pause. Final broker GET verification showed cash $99,655.93,
one AAPL share and zero open orders. No model calls or orders were made. Provider
dollar usage remains unknown, not reported as free. Private release receipts are
under `state/robinhood-release-20261125/`.

Investment acceptance remains closed: the original durable AAPL result still
has material valuation/critique gap flags and no complete-research approval.
The separately accepted DEFER supplement does not rewrite that result. A fresh
bounded result incorporating the corrected analysis needs its own independent
review. The $40 research allocation has $19.7763175 remaining; November 25 spend
is $20.2236825 against the ordinary $20/day limit. A proposed single $18 attempt
would need an explicit temporary daily allowance. No paper study was installed
and no automatic unpause was authorized by these checks.
