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
