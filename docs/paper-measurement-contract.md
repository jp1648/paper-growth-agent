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
