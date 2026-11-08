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
