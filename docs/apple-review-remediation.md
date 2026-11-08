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
completion is not approved investment research. The missing-data case remains a
failure, not an accepted abstention.

## Required revised result and evidence

| Requirement | Concrete completion check | Current state |
|---|---|---|
| Arithmetic | Required terminal multiple includes all five annual cash flows; independently reproduce value, inverse value and separate sensitivity axes | Corrected arithmetic artifact and deterministic inspection tool tested; revised model report not yet collected |
| Business analysis | Cite available product/category revenue and growth, Services/Products margins, China, receivables concentration and App Store regulation; distinguish available-but-unread from unavailable evidence | Reviewer identified omissions, including numbers already in saved passage-2821c133eaaca3113da5; revised analysis pending |
| Normalization | Source-bound numerical bridge with explicit equity/enterprise basis, SBC/share treatment, taxes/escrow, reinvestment and financing; no unsupported zero adjustments or double deductions | Not implemented/accepted; narrative declarations remain insufficient |
| Scenarios | Connect business drivers to growth/margins/reinvestment; identify 10% as a chosen hurdle unless cost of equity is established; explain post-year-five economics and show separate growth/multiple sensitivities | Arithmetic sensitivity implemented; economic rationale still pending |
| Decision/review | Measurable triggers, review periods and valuation effects; reconcile holding/valuation horizons; distinguish confidence in abstention from value/performance confidence | Revised report and independent review pending |
| Failure recovery | Rerun the missing-capex/filing case after offline checks; complete WATCH with an explicit material gap and no fabricated value | Still unvalidated by a new model run |

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
spending preflight, a fresh stable ID and preserved original artifacts. No paid
rerun has been performed for this remediation yet.
