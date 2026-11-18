"""Offline entitlement arithmetic; never posts to the broker or accounting ledger."""

from datetime import date
from decimal import ROUND_HALF_UP

from .corporate_actions import validate_cash_dividends
from .models import ZERO, dec


def audit_entitlements(capture, document):
    source = validate_cash_dividends(capture)
    if (
        type(document.get("version")) is not int
        or document["version"] != 1
        or document.get("rounding") != "USD_cent_half_up"
    ):
        raise ValueError("Require version 1 and an explicit USD_cent_half_up rounding policy")
    if document.get("capture_manifest_sha256") != source["manifest_sha256"]:
        raise ValueError("Entitlements do not match the retained capture")
    cutoff = date.fromisoformat(document["as_of"])
    if cutoff.isoformat() != document["as_of"]:
        raise ValueError("Require canonical as-of date")
    claims = document["entitlements"]
    if not isinstance(claims, list):
        raise TypeError("Entitlements must be a list")
    by_id = {claim["action_id"]: claim for claim in claims}
    actions = {a["id"]: a for a in source["dividends"] if date.fromisoformat(a["ex_date"]) <= cutoff}
    if len(by_id) != len(claims) or by_id.keys() != actions.keys():
        raise ValueError("Require exactly one explicit entitlement for each earned-date action")
    payments_seen, results = set(), []

    def amount(value):
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise TypeError("Use decimal strings or integers for monetary amounts and quantities")
        value = dec(value)
        if value < 0:
            raise ValueError("Negative amounts require a separately reviewed correction")
        return value

    def evidence(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Missing evidence reference")

    for identifier, action in actions.items():
        claim = by_id[identifier]
        evidence(claim["entitlement_evidence"])
        quantity = amount(claim["entitled_quantity"])
        unrounded = quantity * dec(action["rate"])
        gross = unrounded.quantize(dec("0.01"), rounding=ROUND_HALF_UP)
        paid, withheld, fees = ZERO, ZERO, ZERO
        payments = claim["payments"]
        if not isinstance(payments, list):
            raise TypeError("Payments must be a list")
        for payment in payments:
            payment_id = payment["id"]
            evidence(payment_id)
            evidence(payment["evidence"])
            if payment_id in payments_seen:
                raise ValueError("Duplicate payment identity")
            payments_seen.add(payment_id)
            stamp = date.fromisoformat(payment["date"])
            if (
                stamp.isoformat() != payment["date"]
                or not date.fromisoformat(action["ex_date"]) <= stamp <= cutoff
            ):
                raise ValueError("Payment is outside the entitlement/as-of window")
            net, tax, fee = [amount(payment[key]) for key in ("net_cash", "withholding", "fee")]
            if any(v != v.quantize(dec("0.01")) for v in (net, tax, fee)):
                raise ValueError("Payment amounts must match the declared cent precision")
            paid += net + tax + fee
            withheld += tax
            fees += fee
        if paid > gross:
            raise ValueError("Payments exceed the source-derived rounded entitlement")
        results.append(
            {
                "action_id": identifier,
                "ex_date": action["ex_date"],
                "payable_date": action["payable_date"],
                "entitled_quantity": quantity,
                "gross_unrounded": unrounded,
                "gross_entitlement": gross,
                "gross_settled": paid,
                "net_cash_received": paid - withheld - fees,
                "withholding": withheld,
                "fees": fees,
                "gross_receivable": gross - paid,
                "payment_overdue": cutoff > date.fromisoformat(action["payable_date"]) and paid < gross,
                "entitlement_evidence": claim["entitlement_evidence"],
                "payments": payments,
            }
        )
    return {
        "status": "diagnostic_only",
        "as_of": document["as_of"],
        "symbol": source["symbol"],
        "capture_manifest_sha256": source["manifest_sha256"],
        "rounding": document["rounding"],
        "actions": results,
        "history_completeness_verified": False,
        "evidence_authenticity_verified": False,
        "limitations": [
            "Quantities must describe entitlement at ex-date, not holdings at payment; caller evidence is not independently verified.",
            "Unpaid receivables are gross; future withholding and fees are unknown, not assumed zero.",
            "Cent rounding is an explicit diagnostic policy, not verification of broker or issuer rounding.",
            "No returns, reinvestment, historical data availability, study acceptance or ledger adjustments are certified.",
        ],
    }
