# Broker fill and correction boundary

Checked November 8, 2026. The application uses the paper Trading API's
`/v2/account/activities` history, not a Broker API partner activity stream.

The [Trading API activity schema](https://docs.alpaca.markets/us/docs/account-activities)
documents `activity_type: FILL` with `type` equal to `fill` or `partial_fill`.
It does not establish a REST full-bust representation, reversal cash sign,
replacement execution identity, or reopened order semantics. The
[Trading API endpoint reference](https://docs.alpaca.markets/us/reference/getaccountactivitiesbyactivitytype-1)
also distinguishes activity creation-time filters from settlement dates.

The separate [partner Activity SSE guide](https://docs.alpaca.markets/us/docs/activity-sse)
documents `TRD`, `details.execution_type`, `ref_id`, and a `previous_id` correction
link. That is a different envelope and endpoint family; it does not establish
that our paper REST history returns the same representation or that our account
can use the partner service. Do not import those semantics by renaming fields.

## Enforced local behavior

Ordinary accounting requires the documented fill subtype. Unknown, missing or
empty subtypes are rejected both in standalone replay and at the snapshot
boundary, before order/activity writes. The latter latches the persistent pause
and marks reconciliation unsuccessful. Positive quantity and price alone cannot
make an unrecognized correction an ordinary execution.

This is deliberately stricter for old/imported records lacking `type`. Such a
record requires broker-backed review; do not supply a default, rewrite history,
or clear reservations to pass validation. The retained real production fill
examined in the November 8 inventory extract has `type: fill`.

Existing audited positive-quantity revisions still require exact original and
replacement activities, exact order evidence, preserved approved identity,
accounting replay and explicit approval. Zero-quantity busts and reopened orders
remain unsupported. No operator adjustment should bypass the fill check.

## Evidence needed to implement full busts

Obtain broker-supported examples for this account/API family: original execution,
complete post-correction activity history, related order before/after, and resulting
cash/positions. Establish whether an original record is revised, removed or
reversed by a separate event; how IDs chain; whether amounts replace or offset;
and how cumulative order fills/status change. Include partial busts, fees and
later correction of a correction. A missing record or a single matching cash
balance is insufficient.

Only then design append-only effective-history handling and independently verify
cash, shares, basis, realized P/L, reservations, notifications and repeated replay
across crashes. Do not manufacture a live or paper trade to induce an undocumented
bust. The current rule is stop, retain evidence and seek broker-supported review.

## Provider questions / draft request (not sent)

Please provide the supported Trading API behavior and redacted JSON examples for
a fully canceled execution and a partial quantity/price correction, including:

1. Original and corrected `/v2/account/activities` responses: whether the original
   ID is mutated, removed, replaced, or offset by a new entry; exact activity type,
   quantity/price signs, effective timestamp, and reference to the prior execution.
2. Original and corrected order GET/lookup responses: status, filled quantity and
   average price, remaining quantity, replacement identity and update timestamps.
   Can a filled order reopen and execute again under the same client order ID?
3. Corresponding Trading API WebSocket payloads, execution/correction identifiers,
   replay behavior and guarantees when streaming events are missed or duplicated.
4. Relative visibility of account cash, positions, order status and historical
   activities during correction. Which supported queries establish convergence?
5. Pagination/date-filter behavior for late corrections to old activities, including
   how clients discover a deletion or correction outside the recent history window.
6. Supported paper/sandbox reproduction procedure, and differences from live
   account behavior. A paper example alone will not establish live equivalence.

Do not include API credentials. Retain the provider response, API product/version,
retrieval date, exact redacted before/after evidence and hashes with the acceptance
packet before implementing economic replay.

## Current verified failure response

The isolated zero-quantity fixture is deliberately labeled unverified provider
shape. Across filled/new/partially_filled order statuses, changed history triggers
a persistent pause before audited order or intent state is replaced. Resume is
rejected while divergence persists. Manual approval cannot force invalid zero-fill
replay, and original activity, audit and submission count remain unchanged.

This proves safe rejection for those fixture inputs, not real broker convergence,
full-bust recovery or reopening/reservation correctness. No broker messages were
sent and no actual account/order was changed for this review.
