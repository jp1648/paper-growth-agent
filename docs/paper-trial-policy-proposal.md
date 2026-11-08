# Paper trial measurement policy — proposal for review

Status: **DRAFT, NOT APPROVED, NOT STARTED**. This selects a concrete measurement
method for implementation and review. It does not authorize orders, resume trading,
replace the original experiment, approve research, or start the observation clock.
The existing `evaluations/forward-paper-v1.json` remains authoritative: at least
60 regular sessions **and** 12 calendar weeks, with all failed attempts retained.

## Opening boundary and comparators

Approve the manifest before a future broker-calendar regular-session close. That
close is the opening valuation; the next full regular session is session one.
Never select a historical opening after inspecting returns. Include the existing
paper AAPL holding and all other reconciled cash, positions, earned receivables
and liabilities at opening. Disclose their legacy origin. No liquidation or reset.

Give each hypothetical comparator the same opening economic capital as the
portfolio. Primary: SPY. Secondary: QQQ and cash. Each ETF is purchased only in
its separate hypothetical accounting record, never through the broker. Keep
cash-only and ETF ledgers distinct. No retrospective choice of the best comparator.

Use the same retained raw SIP daily-bar marking method for portfolio and ETF
holdings. It is an aggregate daily mark, not a certified closing-auction execution.
Reconcile actual closing quantities and cash from timestamped order/fill/activity
history; do not relabel a later broker snapshot as closing inventory. Retain the
broker-marked equity and the separate common-mark adjustment side by side.

## Hypothetical execution and reinvestment

Proposed primary calculation: whole ETF shares, no borrowing. This avoids requiring
fractional broker access. At the opening close, buy the maximum whole shares
funded by opening capital, after the explicitly modeled transaction cost. Leave
the remainder as cash. Disclose the resulting cash drag; neither comparator has
a matching artificial rounding loss imposed on the actual portfolio.

Model each hypothetical purchase at the retained raw daily mark plus **5 basis
points** adverse slippage. This is a predeclared modeling assumption, not a measured
broker spread or a claim that such an execution was available. Use the applicable
verified fee schedule as a separate cost; unavailable fees block finalization.
Also retain zero- and ten-basis-point sensitivity series. Do not promote a
candidate solely because adding assumed benchmark friction creates excess return:
require the same directional conclusion against the zero-slippage series.

Initial purchase costs remain in benchmark performance: opening comparison capital
is the pre-purchase capital, not a reduced denominator that hides those costs.
Retain the opening purchase, immediate marked value and residual cash explicitly.
The first session's benchmark return includes this initial cost exactly once.

Use decimal arithmetic. Retain the unrounded modeled price; round each hypothetical
order's total cash notional once to USD cents, half up, and account for fees
separately. Choose the largest whole-share quantity affordable after those rounded
costs. This is a declared simulation convention, not verified broker rounding.

Reinvest only confirmed **net** distribution cash, at the next regular-session
close strictly after its confirmed payment date, using the same purchase policy.
This intentionally avoids assumptions about intraday payment availability. Unspent
residual cash may be pooled with the next payment. No reinvestment occurs on the
ex-date or solely because the payable date arrived. A skipped purchase due to
insufficient cash buys zero shares and retains cash, without inventing a fill.

Every hypothetical fill records symbol, session, source-price hash, reference and
modeled prices, quantity, fee, slippage amount, cash before/after and reason
(opening or identified distribution reinvestment). It is visibly labeled
HYPOTHETICAL and never mixed with actual broker order/fill metrics.

## Distributions, splits and cash

Entitlement uses supported ownership at the event's economic boundary, not the
payment-date position. Earned net receivables contribute to economic NAV once;
payment transfers receivable to cash. Unknown withholding, fees, opening unpaid
entitlements or revised records block a final comparison. Track gross entitlement,
withholding, fees and net cash separately. A reported payable date is not proof of
payment. Missing corporate-action records are not cancellations.

Apply verified splits once to quantities with raw prices. Retain the split ratio,
effective timing and before/after quantities. Unsupported distributions, fractional
cash-in-lieu treatment or ambiguous same-date ordering stop the affected series for
review; do not silently drop the action or substitute adjusted prices.

Use only evidenced cash interest under the selected account/product terms for cash
balances, including comparator residual cash. Neither an assumed risk-free yield
nor an unexplained zero is acceptable. Establish applicability to hypothetical
balances explicitly; actual interest on a different balance is not their rate.

## Flows, costs, late information and revisions

Prohibit external deposits and withdrawals during the observation window. This is
a study constraint, not an account restriction. If either occurs, retain the data
and mark this no-flow analysis invalid; do not disguise offsetting gross flows as
zero, restart selectively, or count contributions as returns.

Portfolio gross NAV includes earned net receivables and incurred unbooked
liabilities. Broker-booked fees remain in cash/equity. Deduct model, data,
infrastructure and other allocated operating costs once in the after-cost series,
including failed calls and unsuccessful investigations. The comparator's execution
costs are already in its cash. Do not charge it the agent's research overhead.
Shared measurement infrastructure allocation must be separately documented.

Preserve estimates, unknown charges, reservations, invoice evidence and allocation
versions separately. Unknown amounts prevent final after-cost acceptance. A
provider credit is disclosed separately from gross service consumption so a
promotional grant cannot masquerade as a costless sustainable strategy.

Collect provisional marks and activities after each close; recapture overlapping
price/action windows after at least two regular sessions and again at final review.
This lag is an operational policy, not a guarantee of provider finality. Retain all
versions and explicit changed-value impacts. Use effective business dates for
supported accrued fees, distinct from posting/settlement timestamps; later cash
settlement releases the accrual. Never rewrite original broker reports.

A material later correction reopens the affected result and yields a new version
of the full dependent return chain. No-change across captures does not certify
source completeness. No final result while required economic facts remain unknown.

## What must exist before activation

1. Independent approval of sufficient-evidence research and preserved holdout review.
2. Approval of this measurement policy and exact future opening boundary.
3. Source-backed fee, interest, distribution, withholding and split treatment.
4. Implemented benchmark opening/reinvestment/entitlement ledger, tested against
   independent hand calculations and retained source fixtures.
5. Collector for actual closing inventory, common marks, actions, liabilities and
   every allocated cost category; infrastructure billing source still pending.
6. Immutable manifest binding protocol, policy, artifact, prompts/models, config,
   original experiment, capital/inventory, calendars and reviewer identity.
7. Explicit operator activation after all preceding evidence passes. Current pause
   stays in force; this proposal itself grants no submission permission.

Existing helpers cover raw source retention, ordinary-dividend revision comparison,
entitlement arithmetic, common-mark reconciliation and economic NAV/return
calculation. They do not yet supply the complete ledger/collector above. A green
unit suite or approval of this proposal does not start or complete the trial.


## Worked accounting example for implementation review

This is a synthetic example with an explicitly assumed zero fee and zero cash
interest, not evidence that either is zero for the real account. Opening capital
is $1,000; raw ETF mark is $100; modeled price is $100.05. Buy nine shares for
$900.45, retaining $99.55 cash. Immediate marked NAV is $999.55; the $0.45 modeled
cost must not disappear by replacing the $1,000 return denominator.

| Boundary | Raw mark | Shares | Cash | Net dividend receivable | Economic NAV |
|---|---:|---:|---:|---:|---:|
| Opening after hypothetical purchase | 100 | 9 | 99.55 | 0 | 999.55 |
| Next session ex-date, $1/share net entitlement | 99 | 9 | 99.55 | 9 | 999.55 |
| Confirmed payment date, unchanged price | 99 | 9 | 108.55 | 0 | 999.55 |
| Next regular close, reinvest one share | 99 | 10 | 9.50 | 0 | 999.50 |

The reinvestment price is $99.0495; its cash notional rounds to $99.05 under the
stated convention. The additional $0.05 reduces NAV once. Neither the ex-date
price drop nor payment creates another loss/gain. If this purchase instead occurs
after the ex-date without prior ownership, it earns no earlier entitlement.
These balances should become independent expected test values for the benchmark
ledger, alongside splits, withheld payments, fee deductions and source revisions.
