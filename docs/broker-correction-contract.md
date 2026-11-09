# Trading API correction contract — evidence still required

Checked November 8, 2026. This is a concrete provider-evidence request, not an
implemented bust adapter or authorization to alter any ledger.

The executor uses Trading API account activities/orders and its paper WebSocket.
The [Trading API WebSocket documentation](https://docs.alpaca.markets/us/docs/websocket-streaming)
describes order updates with an event and order, including fills and partial fills.
The [account activity documentation](https://docs.alpaca.markets/us/docs/account-activities)
describes FILL records and their order, quantity and pagination identifiers.
Neither inspected page establishes the complete historical mutation/deletion and
order-reopening contract needed here.

The separate [Broker API Trade Events endpoint](https://docs.alpaca.markets/us/reference/subscribetotradev2sse)
and [Broker API Activity SSE](https://docs.alpaca.markets/us/docs/activity-sse)
describe correction/bust events. Their endpoints and event schemas are not this
application's Trading API contract. Do not copy those semantics into the executor
without confirmation that they apply to the actual account/API product.

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
