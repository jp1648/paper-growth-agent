# Paper trial measurement policy — selected rules

Status: **SELECTED FOR IMPLEMENTATION, NOT ACTIVATED** (November 8, 2026).
The user delegated independent reviews and authorized selecting the measurement
rules from their outcomes. Independent AI review is retained in
`state/independent-review-20261108/review.md` with exact hashes. This is not a human
research attestation or approval to trade. The selected rules below incorporate
that review; the original experiment and trading pause remain unchanged.
The existing `evaluations/forward-paper-v1.json` remains authoritative: at least
60 regular sessions **and** 12 calendar weeks, with all failed attempts retained.
An exact future opening manifest still requires explicit operator activation.

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

Selected primary calculation: whole ETF shares, no borrowing. This avoids requiring
fractional broker access. At the opening close, buy the maximum whole shares
funded by opening capital, after the explicitly modeled transaction cost. Leave
the remainder as cash. Disclose the resulting cash drag; neither comparator has
a matching artificial rounding loss imposed on the actual portfolio. Also retain
a fractional, zero-slippage ideal ETF sensitivity to expose the whole-share
rounding disadvantage. It is secondary, never a replacement primary chosen after
results. The fractional sensitivity uses quantities truncated to six decimal
places, zero slippage, the same evidenced fee/tax/payment/interest conventions,
and the same reinvestment timing. Choose the largest quantity on that grid whose
order notional rounded half-up to cents plus separate fees is affordable; retain
residual cash. This is a declared hypothetical precision, not broker eligibility.
A favorable edge must retain its direction against both the whole-share
zero-slippage and fractional zero-slippage series.

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

Reinvest only confirmed **net** distribution cash, at the first regular-session
close whose session date is later than the confirmed payment date and whose
closing timestamp is later than confirmation available-at, using the same purchase
policy. A payment date must not be converted to midnight to allow same-day buying. Never insert a
retroactive hypothetical fill when confirmation arrives late.
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

Actual portfolio payments require broker activity and reconciliation. Hypothetical
ETF payments instead require issuer/fund-administrator evidence of completed
payment plus a frozen, source-backed synthetic investor/account tax and fee
convention. Retain gross/net rates, ex/record/payment dates, evidence available-at
time and revisions. A payment schedule alone leaves a supported net entitlement
as a receivable, with no reinvestment and a provisional series. Unknown net
entitlement blocks final NAV. A contractual-payment assumption may appear only as
a labeled sensitivity, never as primary confirmed payment. Newly purchased ETFs
have no inherited pre-purchase entitlement; inherited actual rights belong in
opening capital. Investor tax residence, account product and fee/interest terms
must be evidenced before activation, not assumed from unrelated balances.

Process verified split/entitlement boundaries using prior ownership, then fills,
cash events, supported accruals and close marks. Retain timestamps within each
category; ambiguous same-date events, due bills and special distributions require
an explicit reviewed treatment before the affected series can be finalized.

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
For the dedicated trader project, charge all recurring operating infrastructure
(including dashboard, logs, backups and billing-export storage/query costs) to the
agent's after-cost series. Also show the separately identified measurement subset
for transparency; do not subtract it from the primary cost burden. Keep historical
development/drill costs visible in a separate pretrial total. Any shared resources
require an evidenced allocation frozen before activation; unresolved allocation
blocks final after-cost acceptance. Retain gross consumption costs before provider
credits as the sustainable primary cost measure, and net-after-credit costs as a
secondary cash-expense view.

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
2. Implementation of these selected rules and approval of the exact future opening boundary.
3. Source-backed fee, interest, distribution, withholding and split treatment.
4. Implemented benchmark opening/reinvestment/entitlement ledger, tested against
   independent hand calculations and retained source fixtures.
5. Collector for actual closing inventory, common marks, actions, liabilities and
   every allocated cost category; billing export is enabled but populated cost evidence is still pending.
6. Immutable manifest binding protocol, policy, artifact, prompts/models, config,
   original experiment, capital/inventory, calendars and reviewer identity.
7. Explicit operator activation after all preceding evidence passes. Current pause
   stays in force; this policy itself grants no submission permission.

Existing helpers cover raw source retention, ordinary-dividend revision comparison,
entitlement arithmetic, common-mark reconciliation and economic NAV/return
calculation. They do not yet supply the complete ledger/collector above. A green
unit suite or selection of this policy does not start or complete the trial.


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

## Acceptance and implementation checks

Publish a completeness state for every session and comparator. Do not drop missing
sessions from paired comparisons. Preserve gross external deposits and withdrawals
separately, even if their net is zero. Freeze policy, code, sources, allocation and
reviewer identities before the observation window; later material changes retain
the predecessor and require a new preregistered candidate under the protocol.

Independent expected-value tests must cover entitlement before/after ex-date, sale
after entitlement, delayed/reversed payment, unknown net tax, late confirmation,
splits/cash in lieu, cent-boundary affordability, initial costs, no affordable whole
share, accrued-then-settled fees and revisions propagating through the return chain.
Selecting the policy does not certify these implementations or research quality.

## Implemented building blocks — November 8, 2026

`trader.benchmark.purchase` implements cent-rounded maximal affordability, separate
fixed order fees, whole-share zero/5/10bp sensitivities, and six-decimal fractional
zero-slip sensitivity. Zero affordable quantity charges no fee; rounded-free
purchases produce no shares. `reinvestment_session` requires a later payment-day
session and a close strictly after evidence availability. Calendar completeness
and price/payment authenticity remain upstream evidence responsibilities.

`paper-agent preview-benchmark-purchase --input purchase.json` runs before any
operational configuration/broker construction. Input requires version 1, USD,
SPY/QQQ, cash/mark/fee as decimal strings, slippage_bps (0/5/10), fractional boolean,
canonical session, reason (opening/distribution_reinvestment), price_sha256 and
fee_evidence_sha256. Reinvestment also requires payment_date, available_at, retained
sessions (`date`, timezone-aware `close`), distribution_id and payment_evidence.
Output is explicitly hypothetical/diagnostic. Its marked NAV describes only the
provided cash and newly purchased lot; it must be combined with existing inventory
by the future ledger. Its opening denominator applies only to an opening purchase,
never to whole-portfolio returns on a reinvestment purchase. Variable fee schedules
need a supported fee calculation before use; an unexplained zero is not valid.

The independent review ran 11 focused tests plus 2,000 affordability and cash-
conservation checks. Full entitlement/split/payment ledger, revised daily return
chain, collector integration and activation remain incomplete.

## Offline session ledger — November 8, 2026

`paper-agent build-benchmark-ledger --input ledger.json --output ledger-result.json`
replays a supplied complete session list, preserving the original capital denominator
for the first observation. It carries prior-share ordinary-dividend entitlements,
separate rounded gross/withholding/fee amounts, net receivables, full payments,
verified-ratio splits that fit the quantity grid, explicit cash interest, and
availability-aware reinvestment. Cash from later-confirmed payments stays locked
when another dividend becomes reinvestable. Initial costs, receipt and payment
are not counted twice. Output files are exclusive-create with an input-byte hash;
corrections require a new complete input/output pair, never an overwritten record.

Input version 1 declares USD SPY/QQQ, opening_capital, order_fee, fractional,
slippage_bps, fee_evidence_sha256, calendar_evidence, action_coverage_evidence,
investor_terms_evidence, fee_evidence and coverage_complete. expected_sessions must
match every sessions row in order, starting with the opening date. Each row supplies
date, timezone-aware close, mark, price_sha256, interest_cash, interest_evidence,
external_deposits, external_withdrawals and events. Every price hash is validated,
including days without purchases. All money/rates use decimal strings.

Events have a unique id, kind and evidence. Ordinary dividend events are on the
ex-date and supply gross_per_share, withholding_per_share and fee_per_share; rates
apply to pre-purchase shares. Payment events supply dividend_id, payment_date,
available_at and positive net_cash no greater than the remaining net entitlement.
Each receipt is retained in the entitlement payments list; received cash reduces
the remaining receivable by exactly the same amount. Each receipt has independent
evidence availability and reinvestment eligibility; a late-confirmed tranche
cannot be spent by an earlier eligible tranche. Purchase records identify both
the distribution and the individual payments making cash eligible. Payments belong on
the first supplied regular-session row on or after their actual payment date;
weekend/holiday cash conversion is reflected at that next close. The actual date
is retained and drives reinvestment eligibility, so a confirmed weekend payment
can reinvest at Monday close, while evidence arriving at or after that close must
wait. This does not create synthetic weekend price observations. Split events provide
numerator and denominator. The result contains purchases, daily balances/returns,
entitlement balances, split audit and unresolved unpaid entitlements. Output is a
restated diagnostic ledger, not an assertion that late evidence was available at
the economic date. Only purchase eligibility uses confirmation availability.

Remaining before actual trial use: connect authentic captures and account terms,
handle entitlement cancellations, unfunded/same-day reversals, reviewed cash-in-lieu
and special actions, integrate portfolio/all-cost paired return reporting and
freeze the opening manifest. Unsupported events currently stop calculation;
supplied coverage/evidence claims are not independent certification. These explicit
limits are incomplete trial requirements, not a reduction of the readiness goal.

### Retained mark/calendar binding

Add `--capture PATH` to `build-benchmark-ledger` to validate the entire raw SIP
capture through `validate_prices` before ledger construction. The input must bind
price_manifest_sha256 and calendar_sha256, cover its exact complete session list,
and use each retained close price and broker-calendar closing timestamp (with New
York DST). Each row's price_sha256 identifies that same complete capture manifest,
which transitively binds its pages. Mark mismatches, calendar mismatches, unknown
symbols and tampered source pages fail before output is created. Additional
symbols in the capture are permitted, but the selected ETF must be complete.

The result records retained_price_binding, request parameters and capture time.
Without `--capture`, that binding is null and the result remains unbound diagnostic
arithmetic. Even a valid binding is not independent source authenticity or action
coverage certification, and cannot by itself activate a trial. Tests exercise the
real capture validator with retained mock HTTP pages, including the DST boundary.


### Receipt reversals

A `payment_reversal` identifies the original dividend and payment, positive
`net_cash` debit, canonical `reversal_date`, `available_at` confirmation, evidence,
and explicit `entitlement_remains_due: true`. It restores the receivable only
under that evidenced continuing-entitlement claim. A cancellation or revised tax
obligation cannot use this event. Retain the original payment amount, cumulative
reversed amount and every reversal record; replacement receipts use new IDs.

Place a later-date reversal on the first regular session on or after its economic
date. Same-day payment/debit ordering requires separate review. Net receipt cash
remaining determines eligibility and locks; a fully reversed receipt cannot trigger
a purchase. Already completed purchases remain in history. If the debit requires
borrowing, selling shares or consuming another receipt's locked cash, calculation
stops pending a reviewed funding rule. This remains a restated diagnostic ledger,
not a claim that late correction evidence was known at the economic date.
Events mapped from multiple non-session dates are processed in economic-date
order, regardless of input order. A debit cannot use a later receipt or cash
whose reinvestment release is on/after the debit date. Payment and reversal events
on the same economic date require explicit ordering review rather than assuming
that one funded the other.

### Paired return binding

`build-paper-observations --input paired.json --output returns.json
--benchmark-input benchmark.json --benchmark-capture CAPTURE` reconstructs the
SPY or QQQ ledger and verifies its retained raw price/calendar capture before comparing
every supplied benchmark equity/receivable claim. The two optional flags must be
supplied together. Without them the original claims-only diagnostic remains.

The opening row uses pre-purchase capital shared with the portfolio; the first
observation includes initial purchase friction exactly once. Observation dates
must cover exactly the remaining benchmark rows. Model, data, infrastructure and
other external costs still require final evidence and cumulative nondecreasing
amounts through the existing builder. Benchmark trading fees are already included
in reconstructed cash, so extra benchmark external-cost/liability adjustments
stop for review. The output records exact benchmark-input hash and retained-price
binding. This does not authenticate external costs, portfolio marks or payment
claims, and does not activate the trial.
Ledger binding and paired NAV arithmetic share the ledger's 60-digit decimal
context; rounded substitutes are rejected, not accepted as exact balances.


### Secondary QQQ observations and descriptive performance

The same `build-paper-observations` input accepts `benchmark: "QQQ"` for the
predeclared secondary comparison. It requires the same complete costs, no-flow
window, NAV and receivable treatment; optional retained ledger binding must match
the named symbol exactly. Its output is labelled `secondary_descriptive_only`.
The primary uncertainty command still rejects QQQ; a better secondary outcome
cannot replace SPY in the acceptance rule.

Both ETF outputs now include gross and net cumulative return, gross and net
maximum session-close drawdown (including opening NAV), and net excess return as
the difference of cumulative returns. Gross includes booked trading costs but
excludes separately allocated operating costs. These are descriptive arithmetic,
not statistical significance, intraday risk, source authentication or trial
acceptance. Comparisons must use the same portfolio series, window and cost
allocation in the eventual frozen report. Cash comparison remains unsupported
until its actual account interest/fee terms and economic ledger are supplied;
an unverified zero yield is not a substitute.
