# Apple development review — changes required

Jay's independent review rejects completion of `annual-dev-20261107-2` while
allowing temporary WATCH, zero shares. It uses November 1, 2025 and the hypothetical
$200 reference, not subsequent performance. No trade, trial activation or live
approval follows. Original suite, recording and result bytes are preserved.

The separate private review is in
`state/evaluations/annual-dev-20261107-2-jay-review/reviews.json`; the implementation
agent transcribed the user-message verdict and bound it to retained hashes. Jay
explicitly did not independently attest those hashes or mechanical checks from
the HTML. Rescoring rejects semantic acceptance (`human_review_failed`); mechanical
completion is not approved investment research. The missing-data case subsequently completed in run `missing-dev-20261108-3`;
its abstention reasoning was approved on November 8 by the delegated independent
AI reviewer. This is not a human attestation or approval of the rejected complete case.

## Required revised result and evidence

| Requirement | Concrete completion check | Current state |
|---|---|---|
| Arithmetic | Required terminal multiple includes all five annual cash flows; independently reproduce value, inverse value and separate sensitivity axes | Corrected arithmetic artifact and deterministic inspection tool tested; revised model report not yet collected |
| Business analysis | Cite available product/category revenue and growth, Services/Products margins, China, receivables concentration and App Store regulation; distinguish available-but-unread from unavailable evidence | Filing-based analyst follow-up completed in [apple-filing-followup.md](apple-filing-followup.md), including the omitted saved evidence; revised model report and independent approval remain pending |
| Normalization | Source-bound numerical bridge with explicit equity/enterprise basis, SBC/share treatment, taxes/escrow, reinvestment and financing; no unsupported zero adjustments or double deductions | Historical bridges documented; explicit numerical normalization is deployed paused and supported by the tools and frozen replay. Apple's recurring-tax, reinvestment and forward-share forecasts remain incomplete. No normalized valuation accepted |
| Scenarios | Connect business drivers to growth/margins/reinvestment; identify 10% as a chosen hurdle unless cost of equity is established; explain post-year-five economics and show separate growth/multiple sensitivities | Arithmetic sensitivity implemented; economic rationale still pending |
| Decision/review | Measurable triggers, review periods and valuation effects; reconcile holding/valuation horizons; distinguish confidence in abstention from value/performance confidence | Revised report and independent review pending |
| Failure recovery | Rerun the missing-capex/filing case after offline checks; complete WATCH with an explicit material gap and no fabricated value | Real run missing-dev-20261108-3 completed WATCH/zero shares with all four reports; independent AI reasoning review approved, with corrected packet hash binding |

## Arithmetic correction

For $6.582405 historical proxy cash flow, 7% growth, 10% discount and five years:
PV of interim cash flows is $30.3151618689. Discounted final-year cash flow per unit
of terminal multiple is $5.73244719059. Therefore:

`required multiple = (200 - 30.3151618689) / 5.73244719059 = 29.6007677855`

The earlier approximately 31x statement is wrong. Terminal fractions of total
scenario value are 75.477%, 81.944%, 86.146%. Independent axes, USD/share:

| Growth | 18x terminal | 24x terminal | 30x terminal |
|---|---:|---:|---:|
| 2% | 107.62 | 134.69 | 161.77 |
| 7% | 133.50 | 167.89 | 202.29 |
| 12% | 164.40 | 207.62 | 250.84 |

These remain historical-proxy sensitivities, not forecasts or supported fair
values. `inspect_valuation` computes these diagnostics from a saved validated
calculation and cannot override its financial inputs. Its outputs are traced via
the existing tool middleware. Existing calculation identities/output are unchanged.

## Normalization work must preserve economic distinctions

Reconcile $90.711bn repurchases plus $15.421bn dividends against $98.767bn CFO
minus capex: $7.365bn excess distributions before other financing uses. Explain
funding; this arithmetic alone is not distress evidence. Separate employee-issuance
offsets from net share reduction and distinguish historical weighted-average
shares from valuation-date shares. Use either a consistent SBC economic deduction
or an explicit dilution/offsetting-repurchase model; do not charge both.

Bridge cash taxes, expense and escrow rather than adding back a tax charge or the
entire cash-tax increase. Avoid re-deducting operating investment already in CFO.
The supported $33.763bn net cash/securities calculation is before other claims,
not automatically excess distributable cash. Do not subtract obligations already
captured in cash-flow projections again.

Changes are developed on Apple. Preserve Microsoft as the unseen annual holdout;
never tune on its candidate outputs. A new development run requires the existing
spending preflight, a fresh stable ID and preserved original artifacts. The original complete-evidence Apple valuation has not been rerun for this
remediation; the separate missing-evidence reruns are documented below.

## Accounting diagnostic implementation

`inspect_cash_flow` now exposes deterministic historical SBC-cost sensitivity,
repurchases plus common dividends above CFO-minus-capex, and net cash taxes paid.
It retains selected original facts and requires the same annual period, accession,
filing date and form as the proxy. Missing eligible facts return explicit gaps and
null diagnostics, never zero; conflicts or negative amounts require separate
review. The SEC collector includes the corresponding distribution and tax tags.
Older caches and frozen suites are not rewritten to invent these inputs.

This is a step toward the numerical bridge, not its completion. It does not change
the valuation cash-flow input, establish recurring tax adjustments, reconcile all
financing flows, select future shares or justify forecast growth. SBC deduction is
explicitly a sensitivity; cash taxes already included in CFO are not deducted
again. The valuation instructions request the diagnostic and preserve material-gap
reporting. A full normalized model and independent acceptance remain outstanding.

## November 8 missing-evidence reruns

The isolated `d06e441` image matched all 29 Python source hashes; CI
42450816535 passed. Cloud Build ce87eeb4-bd50-44f4-b2e9-4f29a2d30cd5 succeeded
under the scoped builder. Image digest:
`sha256:4c74ab126b0c6ecad23703e5ed7a6223f0ba522a45d93a309eb241b22b486291`.
The production executor was not upgraded or unpaused.

Two development-only runs used the original Apple missing-capex/filing case,
without modifying its evidence or inspecting Microsoft candidate outputs:

- `missing-dev-20261108-1`: $1.25 cap, $0.6549325 settled estimated cost,
  16 model calls, 19 tools. Business/catalysts completed; lead next-call reserve
  would have brought the total to $2.0640575, so the guard stopped it. The cap
  chosen for this test was too small; this was not a successful abstention.
- After inspecting that exact limit, `missing-dev-20261108-2`: $4.50 cap,
  $1.2467825 settled estimated cost, 22 model calls, 22 tools. The valuation
  stage reached missing annual capex and raised a ValueError that terminated the
  case before critique/decision. Evaluator category `invalid_schema` is generic;
  the retained PostgreSQL exception identifies the missing financial fact.

Total $1.901715, both settled with zero unknown calls. The second cap plus first
spend remained below the configured $6/day ceiling. Each preflight checked prior
settlement, remaining daily allowance, conservative grant credit with $10 buffer,
original experiment, config hash and pause, then persisted a unique authorization.
Private recordings, reports, receipts and trace summaries are under
`state/evaluations/missing-dev-20261108-{1,2}` and corresponding preflight folders.
No third paid retry was made before the missing-fact correction below.

The expected missing-annual-fact condition now has a distinct ValueError subclass.
`value_scenarios` and `inspect_cash_flow` convert only that condition into an
explicit unavailable result; no calculation ID/value is created. Conflicting
filings, company mismatch and invalid monetary inputs still raise. Focused
regressions cover both tools. At that point the correction was not model-validated
or production-deployed; the subsequent deployment and third run are recorded
below. Neither failed run satisfies independent acceptance.

The missing-fact correction was subsequently deployed as paused release 156277c
(see the readiness release receipt). A full offline graph now proves tool-gap →
unsupported valuation → critique → WATCH/zero shares completion using a scripted
model. The subsequent real-model rerun is recorded below; independent review
remains pending. Neither test resolves the supported-valuation/normalization
requirements.

## Completed missing-evidence result — independent AI abstention review approved

`missing-dev-20261108-3` ran the deployed 156277c image with a fresh stable ID
and a $3.75 cap after settlement/daily/credit/pause checks. It completed all four
reports and returned WATCH, quantity/price zero, holding period zero. No
calculation or scenario values were created; valuation and critique explicitly
retain material gaps. The model distinguishes missing supplied evidence from
nonexistence of the public filing, historical reference price from executable
price, and confidence in abstention from confidence in value/returns. It does
not replace the rejected complete-evidence Apple research.

Estimated cost $2.3795900, 36 model calls, 35 tools, 167.56 seconds, 15 repeated
calls across roles, peak context 58,792 bytes. PostgreSQL shows matching 36
requests/responses and 35 tool starts/ends, four completed specialist spans,
completed final evaluation, and no errors. Cost settled with zero unknown calls.
November 8 total for three attempts: $4.281305, below $6/day. No more paid run
was made in this turn.

Mechanical checks pass. Independent AI review approved the abstention reasoning
on November 8; the corrected `review-manifest-v2.json` preserves the original
packet while fixing its case-hash binding. This is not human approval or approval
of complete-evidence valuation. A private HTML
packet with unmodified reports, full frozen facts and hash identifiers is at
`state/evaluations/missing-dev-20261108-3/review-for-jay.html`. Implementation-agent
checks of citations and selected financial inputs are labeled as such; they are
not Jay's approval. The packet explicitly separates the fictional $100,000 cash
portfolio from the actual paused paper account holding one AAPL share. Original
recording/suite bytes are retained. Jay was asked to review this abstention only.


## Next sufficient-evidence development run — prepared, not executed

The original complete Apple case is retained unchanged in
`state/evaluations/planned-apple-normalized-20261109/suite.json`; its plan records
parent/subset/case hashes, scoring v7, current prompt and relevant source hashes.
Stable planned run ID: `annual-normalized-dev-20261109-1`. Microsoft is excluded.
The original reconstructed November 1, 2025 cutoff and hypothetical $200 price
remain; this does not become a current-price investigation or point-in-time backtest.

Earliest execution is November 9 New York, after fresh ledger, settled-charge,
remaining daily allowance, confirmed credit/buffer, pause, deployed-source and
run-ID checks. Maximum is $5 for the run within the existing $6 daily cap and
available credit less $10 buffer; these are ceilings, not preflight proof of funds.
Do not execute if budget is insufficient, and do not retry automatically.
The plan itself creates no reservation or provider call and grants no extra spend.

Use the existing `evaluate-run` path only, with this single-case suite, stable ID,
new output directory and the preflight-bounded cap. Retain the full four-role
output and traces, then obtain independent semantic review against every requirement
in this document. Complete API execution and WATCH alone cannot approve normalized
valuation. Preserve rejected outputs and all costs regardless of outcome.
