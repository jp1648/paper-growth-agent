# Paper measurement implementation contract — not activated

The current daily report is an operational view. Its split-adjusted price
benchmarks, approximate end-of-day external-flow treatment and incomplete
operating expenses cannot feed the prospective trial's acceptance analysis.
Keep saved reports immutable; build a separately versioned observation dataset.

## Verified source and its limits

A read-only request using the existing market-data credentials returned HTTP 200
for SPY corporate actions over processing dates September 1–October 31, 2026.
The retained response has one cash distribution: rate 1.888834 per share,
ex-date September 18, payment and processing date October 30. No continuation
page was indicated. Private raw evidence is in
`state/benchmark-source-audit/spy-response.json`. This verifies access and one
response, not full dividend history, data completeness, withholding, or returns.

[Alpaca's corporate-action API reference](https://docs.alpaca.markets/us/reference/corporateactions-1)
specifies processing-date filters and warns of publication delays. Its default
quality filter may omit incomplete records; processed records can still lack
fields. Therefore use explicit quality selection, exhaust bounded pagination,
retain retrieval times and raw revisions, and validate economic dates and fields.
An empty response cannot independently certify no distributions.

## Required implementation sequence

1. Retain raw close prices and corporate actions with immutable source hashes,
   retrieval timestamps, currency, feed and adjustment policy. Use raw prices
   when explicitly applying splits; do not apply split factors twice. Reject
   conflicting IDs, missing rates/dates, unsupported distributions and incomplete
   pagination. Re-fetch an overlapping processing-date range and reconcile event
   revisions rather than silently replacing historical observations.
2. Establish opening benchmark holdings and any unpaid entitlements. Entitlement
   depends on holdings around the ex-date, not holdings when cash is paid. A
   position opened after the ex-date cannot receive the earlier distribution.
   Include a supported dividend receivable from entitlement to payment; settlement
   transfers receivable to cash, not a second income event. An opening entitlement
   requires independently supported opening quantities and dates.
3. Apply equivalent accounting to portfolio and benchmark. If broker equity omits
   receivables, retain the broker reconciliation and separately disclose the
   measurement adjustment; do not overwrite the broker ledger. Verify net
   withholding and fees. Unknown tax or entitlement treatment makes the affected
   comparison incomplete, not zero. Record payment corrections and reversals.
4. Freeze reinvestment timing, execution price, fractional-share policy and
   baseline transaction costs before activation. Record hypothetical benchmark
   fills separately from broker fills. No benchmark order is submitted. Decide
   whether and when net distributions are reinvested in the frozen manifest;
   a price-adjusted index alone does not supply these investable assumptions.
5. Give both sides the same external-flow timestamps and amounts. Use valuations
   immediately around material intraday flows for linked subperiod returns, or
   prohibit such flows in the study and stop interpretation if one occurs. The
   existing end-of-day approximation cannot silently become exact time weighting.
6. Allocate every model/data/infrastructure attempt to the declared observation
   window, including failures and evaluations. Record provider evidence, estimates,
   reservations, attribution method and revisions separately. Do not add overlapping
   run and event cost totals, subtract broker fees twice, or treat absent invoices
   as zero. Infrastructure source access is still pending operator response.
7. Generate paired daily observations only after matching the declared broker
   calendar, opening balances, distributions, external flows and cost allocation.
   Keep gross and after-cost portfolio series, baseline series, source manifests
   and completeness failures. Unknown required inputs block acceptance. Then run
   the existing offline paired uncertainty calculation; it does not validate the
   truth of the input declarations.

## Required independent balance checks

- A price drop exactly equal to an earned dividend leaves pre-cost economic NAV
  unchanged when the receivable is recognized; later payment also leaves NAV
  unchanged. Withholding reduces net NAV once.
- Buying after the ex-date does not earn that dividend; selling after entitlement
  does not delete an already-earned receivable. Opening unpaid entitlements do
  not become free income at the study boundary.
- A split preserves NAV before costs; its price and quantity adjustments occur
  exactly once. Unsupported stock distributions cannot masquerade as cash.
- A mid-session deposit cannot be counted as profit or receive a full prior
  session's investment return. Apply the same flow schedule to each comparator.
- A late correction produces a new observation version and explicit impact;
  preserve the earlier version. Missing source coverage blocks finalization.
- A model timeout with unknown charge, missing infrastructure allocation or
  duplicate cost attribution cannot yield a complete after-cost result.

These are implementation and review requirements, not results of completed tests.
The source probe made no orders, paid model calls, billing changes or trial
activation. The protocol remains `predeclared_not_started`.

## Implemented source capture

```sh
paper-agent --config config.toml capture-corporate-actions SPY \
  --start 2026-09-01 --end 2026-10-31 \
  --output-dir state/corporate-actions/spy-20260901-20261031-capture1
```

This explicit command uses the existing market-data credentials and only GETs the
fixed corporate-action endpoint. It does not open the ledger, construct a broker,
run models, schedule collection or enable trading. The output directory must be
new; a repeated invocation cannot overwrite evidence or silently repeat a capture.

It retains the request, byte-exact completed response pages with SHA-256 hashes,
and a terminal manifest. Bounds are 100 pages, 1 MiB decoded bytes per page,
10 MiB total, and 366 days per processing-date interval. Redirects, missing or
repeated pagination tokens, malformed groups/identities and conflicting versions
of an ID fail the capture. Identical repeated records are retained in raw pages
and counted once. HTTP failures are not automatically retried. On an ordinary
failure the manifest records `failed`; an interrupted process may leave no
manifest and must be treated as incomplete. Failed or oversized partial pages
are not a complete source record.

`pagination_exhausted` means only that the requested traversal ended with an
explicit null continuation token. Every manifest keeps
`history_completeness_verified: false` and `economic_fields_validated: false`.
Incomplete economic records and unknown action groups are retained for review,
not interpreted or silently discarded. This implements source capture only;
entitlement accounting, return construction and trial activation remain pending.

## Offline validation of ordinary cash records

`paper-agent validate-cash-dividends CAPTURE_DIRECTORY` needs no configuration,
credentials, broker or network. It verifies the request/manifest agreement,
completed pagination, bounded local page paths and sizes, SHA-256 values and
unique-action count. It rejects symlinked files and preserves all source files.

For ordinary, non-foreign, non-special cash dividends it checks source identity,
requested symbol, finite positive rate, canonical ex/record/payment/processing
dates, date ordering and the requested processing-date interval. Ex-dates may
legitimately precede the processing window. Nonempty unsupported action groups
block this conversion; no split or special distribution is silently discarded.
The output retains the original records and manifest hash for downstream use.

This is structural integrity and ordinary-field validation, not signed source
authentication. A person able to replace both manifest and pages can replace the
claimed evidence. Successful validation still sets history completeness,
entitlement and net withholding verification to false. It does not establish
currency treatment, quantities owned at ex-date, actual settlement, reinvestment,
returns, source sufficiency or trial acceptance. Missing/canceled/revised economic
facts require separate review; no amounts or dates are invented.

## Offline entitlement and payment arithmetic

`paper-agent audit-dividend-entitlements CAPTURE_DIRECTORY --input claims.json`
first revalidates the source capture. It never opens the trading ledger or contacts
a provider. The input has:

- `version: 1`, `as_of` (canonical date), `capture_manifest_sha256`, and the
  explicitly selected diagnostic policy `rounding: "USD_cent_half_up"`.
- `entitlements`: exactly one entry per captured action whose ex-date is on or
  before `as_of`. Each entry names `action_id`, `entitled_quantity`, a nonempty
  `entitlement_evidence` reference, and a `payments` list. Supply explicit zero
  entitlement when supported; missing quantities do not become zero. Quantities
  represent ex-date entitlement, not current holdings or payment-date purchases.
- Each payment contains a unique `id`, canonical `date`, `net_cash`, `withholding`,
  `fee`, and nonempty `evidence` reference. Monetary quantities use decimal strings
  or integers. Unknown deductions are not zero; payment amounts use cents. Empty
  payments means no confirmed payment, including after the announced payable date.

The diagnostic derives gross entitlement from quantity times the original rate,
retains the unrounded amount, and rounds once under the selected cent policy.
Gross settled is net cash plus withholding plus fees. Gross receivable is rounded
entitlement less gross settled. Payment does not add another dividend entitlement.
Partial payments retain a receivable; unpaid balances after the payable date are
flagged overdue. Duplicate IDs, excess payments, negative correction amounts and
payments before ex-date or after `as_of` fail for review.

This is a calculation over supplied claims, not authentication of entitlement or
settlement evidence. It does not verify USD treatment or issuer/broker rounding;
those policies must be independently approved before study use. Unpaid balances
are **gross**: future withholding and fees remain unknown. It does not reinvest
cash, change broker equity, generate an approved net-return series, or establish
point-in-time source availability. Corrections require a new retained input/output
version and independent review; original captures remain untouched.

## Drawdown on the paired return series

`analyze-paper-returns` reports `portfolio_max_drawdown` and
`benchmark_max_drawdown` as nonnegative peak-to-trough fractions. Both use the
same ordered session-return observations used for compounding and uncertainty.
Opening indexed wealth is 1, so a loss on the first session is included. A later
recovery does not erase the earlier drawdown. The running peak updates when a new
high is reached. These are session-close drawdowns, not intraday extremes.

The calculation does not upgrade input declarations into verified after-cost
returns. Complete external-flow treatment, economic NAV, cost allocation and
calendar/source evidence still have to be established before study acceptance.

## Economic NAV to paired observations

`paper-agent build-paper-observations --input nav.json --output returns.json`
is an offline, configuration-free calculation. It creates a new output only and
hashes the exact input bytes. The output can feed `analyze-paper-returns` once its
60-session/12-week requirements are met. Shorter datasets can be built for
diagnostics but cannot pass that analysis window check.

Input version 1 declares `currency: "USD"`, `benchmark: "SPY"`,
`flow_policy: "no_external_flows"`, `source_manifest_sha256`, `benchmark_method`,
`cost_allocation_method`, and an ordered `expected_sessions` list. `opening` has a
canonical `date` before the first session and separate `portfolio` and `benchmark`
NAV claims. `observations` has one row per expected session, with the same two
claims plus `date`, `flow_evidence`, `external_deposits` and `external_withdrawals`.
Both gross flow directions must be explicitly zero. An offsetting deposit and
withdrawal stops this calculation; they cannot be hidden as zero net flow.
This policy requires approval in the frozen study manifest before activation.

Each NAV claim contains:

- `reported_equity`: cash and marked holdings, already reflecting broker fees
  and any settled dividend cash. Supply benchmark holdings/cash under the frozen
  investment policy separately; never submit benchmark orders.
- `net_receivables_not_in_equity`: supported unpaid entitlements after known
  withholding/fees, excluding receivables already included in reported equity.
  Unknown future deductions cannot be represented by the gross entitlement.
- `evidence`: a nonempty retained-source reference, and `complete: true` only
  when the input preparer has reconciled the required components.
- `cumulative_external_costs`: exactly `model`, `data`, `infrastructure`, and
  `other`. Each has `amount`, `status: "final"`, and `evidence`. Amounts are
  cumulative observation-window allocated expenses **not already in reported
  equity**, including failed attempts. Explicit supported zeros are required.
  Estimates, reservations and unknown charges stop finalization. Invoice/run
  overlap, allocation windows and duplicate attribution require source review.

Monetary inputs are decimal strings or integers. Opening cumulative costs are
zero; subsequent costs cannot decrease within a version. A refund/correction
requires a new consistently restated input/output pair, preserving the original.
Each category's final cost evidence may reference a shared invoice, but the
allocation must not charge the same expense twice. This calculator cannot infer
that from an opaque reference.

For each side, gross economic NAV is reported equity plus the net receivable;
after-cost NAV deducts cumulative external expenses. This is an economic expense
overlay on the actual account, not hypothetical sales or a simulation of the
returns the account would have earned if each external expense had been paid
from broker cash. Gross returns are before these external expenses but **after**
broker fees and withholding. Both daily return chains divide current NAV by
previous NAV and subtract one; nonpositive NAV blocks percentage chaining.
Payment moves a receivable into cash without creating a second income event.

Tests cover net dividend recognition, settlement, opening entitlements, separate
benchmark costs, cumulative expense treatment, unknown/missing inputs, offsetting
flows, immutable output and connection to the existing uncertainty calculation.
They do not authenticate source claims or validate benchmark reinvestment, marks,
splits or calendar coverage. The command does not collect balances, reconcile
invoices, modify the ledger, activate a trial or confer investment acceptance.

## Historical close-source probe — November 8

Five bounded read-only requests succeeded: the paper broker calendar for November
2–6 and raw daily/close-window minute bars for AAPL, SPY and QQQ on November 6,
once each using IEX and SIP. Every bar response explicitly ended pagination;
all three requested symbols appeared. Existing credentials therefore support
these historical SIP requests. This does not establish real-time SIP entitlement,
all-session completeness, source finality or an exchange official close.

| Symbol | IEX daily close | SIP daily close | Last SIP minute before 16:00 NY |
|---|---:|---:|---:|
| AAPL | 342.41 | 342.37 | 342.36 |
| SPY | 735.40 | 735.29 | 735.34 |
| QQQ | 656.31 | 656.10 | 656.19 |

The final pre-close minute starts at 20:59 UTC, following the November DST change.
The calendar's regular close is 16:00 NY, distinct from its extended-session
close. A bar starting at 21:00 UTC includes the following minute and cannot be
blindly treated as the closing auction. Neither daily nor minute aggregates in
this probe certify an official exchange auction price.

[Alpaca's methodology](https://docs.alpaca.markets/us/docs/market-data-faq)
describes different trade-condition rules for minute and daily bars, including
extended-hours and corrected-close conditions. Its
[historical-bar reference](https://docs.alpaca.markets/us/reference/stockbars)
documents raw adjustments, symbol mapping and pagination. These differences
explain why equality is not a valid invariant; the probe does not identify which
individual trades caused the observed differences.

Next implementation must retain the calendar and raw price pages, explicitly
freeze feed, currency, symbol mapping, retrieval lag and correction policy, and
validate all expected sessions/symbols. Use one declared method on both sides;
do not combine broker-marked portfolio equity with differently timed benchmark
prices without a separate, reconciled holdings-mark adjustment. Prefer the
verified historical SIP path for evaluating a consolidated marking policy;
do not silently change the trading feed or certify it as official-close data.
This is a measurement design decision still requiring a frozen manifest review.

Private byte-exact responses, request parameters, hashes and an assert-based
comparison are retained in `state/benchmark-close-probe-20261108/`; manifest SHA-256
`1529a85d31775faac610b24b6d9c3440b90e69fe06fe17a17dd565c2a48b317b`.
The probe used documented default USD/current symbol mapping; production capture
must make both explicit. No subscription change, paid model call, broker order,
ledger mutation or trial activation occurred. Dividend/split entitlement,
reinvestment, expense allocation and independent source review remain required.
