# Paired paper-return diagnostics

`paper-agent analyze-paper-returns --input returns.json --output analysis.json`

This offline command needs no configuration, API credentials, broker or model.
It never changes the ledger, starts the study, or authorizes orders. Output is
created exclusively: existing analyses are not overwritten. Its input byte hash
and the supplied source-manifest hash are retained.

The forward protocol is still `predeclared_not_started`. This calculation is
only the uncertainty component. The current daily price-only benchmark reports
are **not valid inputs**. Complete after-cost comparable total-return series,
broker-calendar verification and independent economic review remain prerequisites.

## Input contract

Supply a JSON object with these fields:

| Field | Required value/content |
|---|---|
| `version` | `1` |
| `currency` | `USD` |
| `benchmark` | `SPY`; secondary benchmarks cannot replace the primary |
| `return_basis` | `flow_adjusted_net_total_return` |
| `source_manifest_sha256` | Lowercase SHA-256 of the separately retained observation manifest |
| `benchmark_method` | Provenance and common dividend/withholding/execution-cost treatment |
| `cost_allocation_method` | Attribution of all model, data and infrastructure costs, including failed attempts |
| `flow_method` | Common external-flow timing/adjustment method |
| `expected_sessions` | Ordered unique canonical ISO dates from the verified broker calendar |
| `observations` | Exactly one object per expected session, in the same order |

Each observation has `date`, `portfolio_return`, and `benchmark_return`. Returns
are fractions, e.g. `"0.01"` means 1%, after the declared costs and flow adjustment.
Every value must be finite and greater than -1; zero-NAV return chains require
separate treatment and cannot silently resume. No missing values become zero.
There must be at least 60 observations and 84 calendar days between first/last
session dates; the bounded offline input supports at most 1000 sessions.

These checks verify structure, not the truth of the provenance declarations.
The command does not fetch the calendar or validate the underlying manifest,
cost invoices, accruals or independent reviews. A caller cannot use a successful
analysis as evidence that those gates passed. Synthetic tests deliberately use
synthetic dates and are not evidence of a valid trading calendar.

## Calculation frozen before trial activation

For n daily pairs, observed excess return is the difference between the two
separately compounded returns: `product(1 + portfolio_return) - product(1 + SPY_return)`.
It is not the arithmetic mean or compounded series of daily excess returns.

For each block length L=5 (primary) and L=10 (sensitivity), sample starts uniformly
from 0 through n-L inclusive. Copy the whole contiguous pair block, preserving
cross-series pairing. Concatenate independently drawn overlapping blocks until
n observations are available, and truncate only the last block. Blocks do not
wrap around the series boundary. Recompute compounded excess for each of 10,000
resamples using a fresh Python `Random(1648)` for each length. Report the 2.5th
and 97.5th percentiles with linear interpolation at `(N-1)*q`.

This specifies an overlapping moving-block calculation, consistent with the
block construction described in [Kreiss and Lahiri, Bootstrap Methods for Time
Series](https://bashtage.github.io/kevinsheppard.com/files/teaching/mfe/advanced-econometrics/Kreiss_and_lahiri.pdf).
It is a descriptive percentile interval, not a calibrated guarantee for this
trading process. Small samples, regime changes and nonstationarity can undermine
coverage. Do not choose whichever block length or baseline looks best afterward.
An interval above zero does not independently demonstrate investment edge or
satisfy the safety/research/sample gates. No automatic GO decision is produced.
