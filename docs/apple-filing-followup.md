# Apple filing follow-up — analyst work, research acceptance still withheld

This follow-up addresses available-but-omitted evidence in Jay's review. It is not
a replacement model run, independent approval, point-in-time backtest or purchase
recommendation. It uses the frozen FY2025 filing, November 1, 2025 exercise cutoff
and hypothetical $200 reference. Exact extracted passages with character offsets,
filing-text SHA-256 and checked arithmetic are retained privately in
`state/evaluations/apple-filing-followup-20261108/evidence.json`; its `extract.py`
reproduces the arithmetic and verifies each passage against the frozen text.
Original reports remain unchanged. Source: Apple FY2025 10-K, accession
0000320193-25-000079, operating discussion, cash-flow statement and Notes 4/10.

## What the business evidence establishes

| Category | FY2025 sales, $bn | Filing's rounded annual change |
|---|---:|---:|
| iPhone | 209.586 | +4% |
| Mac | 33.708 | +12% |
| iPad | 28.023 | +5% |
| Wearables, Home and Accessories | 35.686 | -4% |
| Services | 109.158 | +14% |
| Greater China (geographic subset, not additive to categories) | 64.377 | -4% |

Services represents 26.23% of total revenue and supplied $12.989bn of the
$25.126bn revenue increase (51.70%). Its gross profit rose $11.264bn, versus
$3.254bn for Products: 77.59% of the $14.518bn total increase came from Services.
These contributions use reported dollar gross profits, not multiplication by
rounded margin percentages. Services margin rose from 73.9% to 75.4%; Products
margin fell from 37.2% to 36.8%. The filing attributes product-margin pressure
partly to product mix and tariffs, offset by favorable costs.

**Interpretation:** higher-margin Services already offset some weaker hardware
categories and China in the historical results. That is a concrete thesis driver,
not proof the offset persists. iPhone remains the largest category; Services also
depends partly on the installed device ecosystem, so the two should not be modeled
as independent diversification. A forward case needs separate product demand,
Services growth/monetization, margin and investment assumptions before converting
these observations into 2%/7%/12% cash-flow growth.

Greater China revenue declined $2.575bn from $66.952bn. This deserves a specific
regional demand/competition/FX investigation; a decline does not identify its
cause or prove permanent deterioration. Retention, switching-cost magnitude and
competitive market share remain additional-source questions. The original saved
passage `passage-2821c133eaaca3113da5` already contains category revenue figures;
that omission was insufficient investigation, not missing source availability.

One customer accounts for 12% of trade receivables, while third-party cellular
carriers account for 34%, down from 38%. These are credit/receivables exposures,
not revenue concentration, churn or retention estimates. The filing also records
the April 23, 2025 €500m Commission fine and cease-and-desist order addressing
App Store steering restrictions, plus Apple's appeal. This is concrete regulatory
risk to monetization economics; neither the fine alone nor the generic risk text
quantifies recurring Services revenue damage.

## Funding reconciliation, USD millions

| Cash-flow component | Amount |
|---|---:|
| CFO less capex | 98,767 |
| Cash common-stock repurchases | -90,711 |
| Cash dividends/equivalents | -15,421 |
| Securities maturities + sales - purchases | 29,390 |
| Other investing | -1,480 |
| Equity-award tax settlement payments | -5,960 |
| Term-debt proceeds | 4,481 |
| Term-debt repayments | -10,932 |
| Net commercial-paper repayments | -2,032 |
| Other financing | -111 |
| **Net cash increase** | **5,991** |

This ties $29,943m opening cash/cash equivalents/restricted cash to $35,934m
closing cash on the cash-flow statement. It does not assign a particular dollar
of securities proceeds to a specific repurchase: funds are fungible. Distributions
exceeding the historical proxy by $7,365m is a funding question, not by itself a
financial-distress conclusion. Capital allocation is not sustainably explained
until recurring cash generation, required liquidity and debt maturity needs are
considered together.

## Shares and normalization: known bridge versus remaining judgment

The share roll-forward, in thousands, is exact:
`15,116,786 - 401,672 repurchased + 58,146 net issued = 14,773,260`.
Net issuance offsets 14.48% of the repurchased share count; net outstanding shares
fell 343.526m. The issuance line is net of employee tax withholding: it is not gross
employee issuance and does not establish management's purpose for each repurchase.
It cannot allocate repurchase cash between issuance offsets and net reduction
without a timing/price convention. Note 10's $89.3bn repurchase figure is not the
cash-flow statement's $90.711bn; do not silently substitute one for the other.

The cover page discloses 14,776,353,000 outstanding shares as of October 17, 2025.
That is a dated share-base observation available to this exercise, not proof of
November 1 shares or a diluted future share forecast. The historical proxy's
15,004,697,000 weighted-average diluted denominator remains appropriate for its
historical definition; it must not silently become a valuation-date denominator.

A transparent *illustrative compensation-cost sensitivity* is:
`$111.482bn CFO - $12.715bn capex - $12.863bn SBC = $85.904bn`.
This is not normalized owner earnings. It deliberately recognizes the SBC amount
added back in CFO as an economic cost; a parallel explicit dilution/offsetting-
repurchase model must not charge that same economic cost again. The filing's
award-tax cash flows, timing and tax effects must be reconciled before selecting
an approved forward treatment. Repurchases themselves are financing/capital
allocation, not an additional operating-expense deduction.

Cash taxes were $43.369bn versus $26.102bn; escrow notes identify $2.6bn restricted
cash and $13.2bn restricted securities at the prior year end for the State Aid
obligation. Those balances, tax expense and current cash payments are different
measures. This follow-up does not add back the $10.2bn charge or the entire cash-tax
increase. A recurring tax/escrow roll-forward and sustainable reinvestment/working-
capital bridge remain necessary. The exact funding reconciliation above does not
resolve them. The $33.763bn net-cash/securities balance before other claims also
must not be added to an inconsistent enterprise/equity valuation or reduced by
operating obligations already included in projected cash flows.

## Conditions for the next research revision

Use observed annual baselines as comparisons, not arbitrary claims of calibrated
thresholds. The revised candidate must predeclare metric, window, threshold and
valuation effect for each trigger: Services revenue/margin and contribution to
profit growth, product margins, China revenue, cash conversion after separated tax
items, diluted/outstanding shares and repurchase funding, and regulatory effects
on App Store economics. Explain why each threshold is economically material.
Review quarterly disclosures when available; a review trigger is not automatic
sale. No trigger thresholds have been independently approved here.

Confidence in temporary abstention can be high while forecast/valuation confidence
is low. The model's confidence number must identify its referent and remain an
uncalibrated judgment, not a probability of positive returns. A five-year valuation
does not establish weeks-to-months convergence; missing near-term catalysts do not
refute long-term ownership, but do leave the shorter-horizon thesis unsupported.

## Numerical operating-cash bridge and tax reconciliation

The frozen cash-flow statement supports the following exact bridge (USD billions).
These are accounting movements, not estimates of recurring cash generation:

| Component | FY2025 | FY2024 | Change |
|---|---:|---:|---:|
| Net income | 112.010 | 93.736 | +18.274 |
| Depreciation/amortization | 11.698 | 11.445 | +0.253 |
| SBC add-back in cash-flow statement | 12.863 | 11.688 | +1.175 |
| Other reconciliation adjustments | -0.089 | -2.266 | +2.177 |
| Subtotal before operating asset/liability changes | 136.482 | 114.603 | +21.879 |
| Operating asset/liability changes | -25.000 | +3.651 | -28.651 |
| Operating cash flow | 111.482 | 118.254 | -6.772 |
| Capex | -12.715 | -9.447 | -3.268 |
| Historical CFO-minus-capex proxy | 98.767 | 108.807 | -10.040 |

The largest line in the operating-change swing is other current/noncurrent
liabilities: -$11.076bn versus +$15.552bn, a -$26.628bn change. This is a reason
to inspect the liability/tax movements, not evidence that the entire swing is
nonrecurring or available for add-back. Receivables, inventory, payables and other
assets also contribute. Removing all operating changes would discard actual
reinvestment and timing effects without establishing a sustainable replacement.

Net-income growth also needs qualification: pretax income increased $9.244bn,
while tax expense fell $9.030bn, together explaining the $18.274bn increase.
Removing only the disclosed State Aid tax effects yields $111.524bn for FY2025
and $103.982bn for FY2024, or 7.25% growth. This narrow accounting comparison does
not normalize the rest of earnings or convert earnings to cash.

The annual tax note supports current tax expense of $22.058bn and a deferred tax
benefit of $1.339bn, summing to the $20.719bn total provision. Cash taxes were
$43.369bn. Current income-tax payable declined from $26.601bn to $13.016bn:

`22.058 + (26.601 - 13.016) + 7.726 = 43.369`

The $7.726bn is an **unreconciled residual of this deliberately incomplete bridge**,
not a new expense or an approved adjustment. Current tax-payable balances alone
do not cover every tax-related asset/liability, settlement, credit, FX or
classification movement. Deferred expense also is not automatically a cash
adjustment. A full recurring-tax forecast remains open.

Twenty source rows, exact character offsets, filing-text hash and assert-checked
identities are retained in `state/evaluations/apple-filing-followup-20261108/normalization.json`;
`normalization.py` reproduces them from the untouched frozen suite.

## Supplemental pre-cutoff Ireland settlement evidence

Apple's Q2 FY2025 10-Q reports that the first six fiscal months included an escrow
release of €14.2bn/$15.4bn to Ireland that fully settled the State Aid obligation.
This provides cash-settlement evidence distinct from the earlier $15.8bn payable
and $10.2bn accounting charge. The [SEC filing index](https://www.sec.gov/Archives/edgar/data/320193/000032019325000057/0000320193-25-000057-index.html)
shows acceptance on May 2, 2025, before the exercise cutoff. See the filing's
[State Aid Decision Tax Payable disclosure](https://www.sec.gov/Archives/edgar/data/320193/000032019325000057/aapl-20250329.htm).

Conditional gross-settlement sensitivity: if the rounded $15.4bn release is wholly
included in reported annual CFO/cash taxes, cash taxes excluding that payment are
$27.969bn; CFO plus settlement is $126.882bn; after capex and an SBC-cost deduction,
the resulting historical sensitivity is $101.304bn. It does not reconcile other
cash-tax effects, recurring taxes or future reinvestment. Do not also deduct
repurchases/employee dilution for the same SBC cost, or add the escrow securities
back as an asset after treating them as spent.

| Historical basis, not approved normalized cash | $bn | Per historical diluted share |
|---|---:|---:|
| CFO minus capex | 98.767 | $6.582405 |
| Same, less SBC-cost sensitivity | 85.904 | $5.725141 |
| Same SBC sensitivity, plus conditional gross settlement adjustment | 101.304 | $6.751486 |

The per-share figures use historical weighted-average diluted shares only; they
are not forward valuation inputs. The settlement amount is rounded, so the
six-decimal arithmetic does not imply economic precision. The supplemental
source is retained separately in `tax-supplement.json` with its URL and filing
metadata; raw original HTML bytes/hash have not been captured. No original
frozen case or model output was modified. A future expanded development case
must explicitly version any added source. This follow-up remains implementation-
agent analysis requiring independent review, not a replacement accepted valuation.

## SBC, issuance and the cost of a flat share count

The frozen filing supports two different roll-forwards. Do not merge them into
one employee-issuance figure (all counts below are millions):

| Common shares | FY2025 |
|---|---:|
| Opening outstanding | 15,116.786 |
| Repurchased | -401.672 |
| Issued, **net** of employee-tax withholding | +58.146 |
| Closing outstanding | 14,773.260 |
| Net reduction | 343.526 |

| RSU awards | FY2025 |
|---|---:|
| Opening unvested awards | 163.326 |
| Granted | +73.466 |
| Vested | -76.845 |
| Forfeited | -8.373 |
| Closing unvested awards | 151.574 |

The 18.699m difference between vested RSUs and net shares issued is not a verified
withholding count. Net issuance is broader than the RSU roll-forward, and the
filing excerpt does not supply a complete gross issuance/withholding reconciliation.
Likewise, 58.146/401.672 = 14.48% describes the **net issuance share-count offset**,
not a proven fraction of actual repurchase cash spent offsetting compensation.

As a deliberately conditional illustration at the exercise's $200 price,
offsetting 58.146m net shares would cost $11.6292bn. Buying the additional
343.526m shares to reproduce the historical net reduction would cost $68.7052bn.
The $80.3344bn total is not Apple's actual repurchase cash or a forecast: future
issuance, settlement dates and purchase prices must be modeled independently.
A stable-share forecast requires funding the offset; simply assuming a declining
denominator creates an unsupported valuation benefit.

Tax and compensation measurements also must remain distinct:

- SBC expense was $12.863bn; its related accounting tax benefit was $3.602bn.
  Their $9.261bn difference is an after-tax expense measure, not necessarily
  current cash tax savings or a new amount to add to CFO.
- The RSUs' vesting-date fair value was $17.1bn. Grant-date expense, vesting-date
  value and repurchase cost measure different things and periods.
- Note 11 reports approximately $6.1bn employee-tax payments; the cash-flow
  financing line is $5.960bn. Do not silently equate them or claim an exact
  withholding reconciliation from the rounded note.
- Unrecognized RSU compensation was $21.8bn, expected to be recognized over a
  weighted-average 2.5 years. Dividing that stock of expense by 2.5 does not
  forecast future annual compensation: it omits future grants and vesting patterns.

Two internally consistent valuation approaches remain possible, but must not be
combined indiscriminately. A cash-equivalent compensation approach retains an
explicit economic SBC cost and separately addresses existing award claims. An
actual dilution approach models gross vesting/issuance, tax withholding cash,
forfeitures and funded repurchases over time. Neither permits adding back SBC,
assuming a flat share count and omitting the offset cost. Subtracting full SBC
expense and then charging the same compensation again through dilution/offset
repurchases would also be inconsistent.

The October 17 cover-page basic share count is 14,776.353m, versus the historical
annual diluted weighted average of 15,004.697m. Applying the same unnormalized
$98.767bn proxy to the former produces $6.684126, versus $6.582405 using the
latter. This is a denominator sensitivity, not a forward valuation. The 151.574m
unvested RSUs are about 1.026% of September-end common shares; that is neither
immediate dilution nor the treasury-stock-method diluted share count. Future
awards, vesting, withholding, forfeitures and buybacks remain explicit forecast
inputs rather than hidden inside historical weighted averages.

Seven exact source passages, both checked roll-forwards and the conditional
arithmetic are retained in `share-bridge.json`, reproduced by `shares.py` in the
private filing-follow-up directory. This resolves the historical count identities
while preserving the gross issuance and forward-policy gaps for independent review.
