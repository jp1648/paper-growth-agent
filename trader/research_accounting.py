"""Offline accounting close for an expired, incomplete durable research run."""

import hashlib
import json
import zlib
from datetime import timedelta
from zoneinfo import ZoneInfo

from .models import dec, dt, dumps, now
from .paper_dispatch import fingerprint
from .research_journal import Journal
from .research_manifest import load_manifest, run_directory
from .research_team import ROLES
from .store import Store


class AccountingEvidenceError(ValueError):
    """Saved identities do not prove the requested accounting close."""


def _nonnegative_int(value):
    return type(value) is int and value >= 0


def _decode_body(body, headers):
    if not isinstance(body, bytes) or len(body) > 20_000_000:
        return None
    try:
        encodings = [
            value.decode("ascii").lower() for name, value in headers if name.lower() == b"content-encoding"
        ]
        if len(encodings) > 1:
            return None
        encoding = encodings[0] if encodings else "identity"
        if encoding in {"gzip", "deflate"}:
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS)
            body = decoder.decompress(body, 20_000_001)
            if len(body) > 20_000_000 or not decoder.eof or decoder.unconsumed_tail:
                return None
            body += decoder.flush()
            if len(body) > 20_000_000:
                return None
        elif encoding != "identity":
            return None
        return json.loads(body)
    except (UnicodeError, ValueError, TypeError, zlib.error):
        return None


def _prior_cost(budget, cfg, record):
    if (
        not isinstance(budget, dict)
        or budget.get("unrestricted_research") is not False
        or budget.get("day") != record["day"]
        or dec(budget.get("limit")) != dec(record["durable_allowance"])
        or not _nonnegative_int(budget.get("models"))
        or not isinstance(budget.get("events"), list)
    ):
        raise AccountingEvidenceError("Saved pre-dispatch budget differs from original reservation")
    charged = dec(0)
    records = [event for kind, event in budget["events"] if kind == "model_cost"]
    all_known = len(records) == budget["models"]
    for event in records:
        if not isinstance(event, dict) or not isinstance(event.get("stage"), str):
            raise AccountingEvidenceError("Prior model cost record is malformed")
        route = cfg.research_model(event["stage"])
        if (
            event.get("model") != route.model
            or event.get("reasoning_effort") != route.reasoning_effort
            or dec(event.get("input_cost_per_million")) != route.input_cost_per_million
            or dec(event.get("output_cost_per_million")) != route.output_cost_per_million
        ):
            raise AccountingEvidenceError("Prior model route or price changed")
        usage = event.get("usage")
        if event.get("estimated_usd") is None:
            all_known = False
            continue
        if not isinstance(usage, dict) or not all(
            _nonnegative_int(usage.get(name)) for name in ("input_tokens", "output_tokens")
        ):
            raise AccountingEvidenceError("Prior model usage is malformed")
        expected = (
            dec(usage["input_tokens"]) * route.input_cost_per_million
            + dec(usage["output_tokens"]) * route.output_cost_per_million
        ) / dec(1_000_000)
        if dec(event["estimated_usd"]) != expected:
            raise AccountingEvidenceError("Prior model cost differs from its saved usage")
        charged += expected
    reserved = dec(budget.get("reserved"))
    if reserved < 0 or all_known and reserved != charged:
        raise AccountingEvidenceError("Saved model budget does not reconcile")
    return charged, all_known


def _current_usage(evidence, cfg, role):
    exchanges = evidence["exchanges"]
    if not exchanges:
        return None, None
    if not evidence["prior_attempts_proven_non_dispatch"]:
        raise AccountingEvidenceError("Earlier provider attempt lacks definitive non-dispatch proof")
    active = exchanges[-1]
    if active["request_hash"] != evidence["request_hash"] or active["attempt"] != len(exchanges) - 1:
        raise AccountingEvidenceError("Provider dispatch does not match pending model request")
    projected = active["projected_request"]
    route = cfg.research_model(role)
    if (
        not isinstance(projected, dict)
        or projected.get("provider") != "openai"
        or projected.get("model") != route.model
        or projected.get("reasoning_effort") != route.reasoning_effort
        or projected.get("max_output_tokens") != cfg.research_max_output_tokens
        or projected.get("service_tier") != "default"
    ):
        raise AccountingEvidenceError("Projected provider route differs from frozen configuration")
    request, response = active["wire_request"], active["wire_response"]
    if request is None or response is None:
        return None, active
    identifier = active["client_request_id"]
    headers = request.get("headers_sha256") if isinstance(request, dict) else None
    if (
        not isinstance(request, dict)
        or request.get("method") != "POST"
        or request.get("url") != "https://api.openai.com/v1/responses"
        or not isinstance(request.get("body"), bytes)
        or not isinstance(headers, list)
        or (b"x-client-request-id", hashlib.sha256(identifier.encode()).hexdigest())
        not in [tuple(item) for item in headers]
    ):
        raise AccountingEvidenceError("Saved HTTP request differs from provider dispatch")
    try:
        body = json.loads(request["body"])
    except (ValueError, TypeError) as exc:
        raise AccountingEvidenceError("Saved provider request body is invalid") from exc
    if (
        body.get("model") != route.model
        or body.get("max_output_tokens") != cfg.research_max_output_tokens
        or body.get("service_tier") != "default"
        or body.get("reasoning", {}).get("effort") != route.reasoning_effort
        or body.get("store") is not False
    ):
        raise AccountingEvidenceError("Saved HTTP model request differs from configured route")
    if not isinstance(response, dict) or not isinstance(response.get("headers"), list):
        return None, active
    raw = _decode_body(response.get("body"), response["headers"])
    if response.get("status") != 200 or not isinstance(raw, dict):
        return None, active
    if raw.get("object") != "response" or raw.get("model") != route.model:
        raise AccountingEvidenceError("Saved provider response has a different model identity")
    usage = raw.get("usage")
    if not isinstance(usage, dict) or not all(
        _nonnegative_int(usage.get(name)) for name in ("input_tokens", "output_tokens")
    ):
        return None, active
    if "total_tokens" in usage and (
        not _nonnegative_int(usage["total_tokens"])
        or usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]
    ):
        return None, active
    cost = (
        dec(usage["input_tokens"]) * route.input_cost_per_million
        + dec(usage["output_tokens"]) * route.output_cost_per_million
    ) / dec(1_000_000)
    return {
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "cost": cost,
    }, active


def _apply_receipt(store, cfg, reference, binding, receipt):
    key = reference["run_key"]
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        rows = store.db.execute(
            "SELECT key,body FROM documents WHERE scope='global' AND kind='research_spend' "
            "AND json_extract(body,'$.run_key')=? AND json_extract(body,'$.experiment_id')=?",
            (key, store.scope),
        ).fetchall()
        if len(rows) != 1:
            raise AccountingEvidenceError("Original durable reservation is not unique")
        spend_key, saved_body = rows[0]
        record = json.loads(saved_body)
        if (
            not record.get("durable")
            or spend_key != receipt["spend_key"]
            or record.get("run_key") != key
            or record.get("experiment_id") != store.scope
            or record.get("config_sha256") != binding["config_sha256"]
            or record.get("day") != receipt["day"]
            or dec(record.get("durable_allowance")) != dec(receipt["allowance_usd"])
        ):
            raise AccountingEvidenceError("Original spending authority differs from accounting receipt")
        expected = {
            **record,
            "usd": receipt["settlement_usd"],
            "unknown_total": receipt["unknown_total"],
            "status": receipt["status"],
            "outcome": "aborted_accounted",
            "accounting_receipt_sha256": fingerprint(receipt),
            "finished_at": receipt["closed_at"],
        }
        if record.get("status") == "reserved":
            if record.get("unknown_total") is not False or dec(record["usd"]) != dec(
                record["durable_allowance"]
            ):
                raise AccountingEvidenceError("Original reservation changed before close")
            changed = store.db.execute(
                "UPDATE documents SET body=? WHERE scope='global' AND kind='research_spend' "
                "AND key=? AND body=?",
                (dumps(expected), spend_key, saved_body),
            ).rowcount
            if changed != 1:
                raise AccountingEvidenceError("Original reservation changed during close")
        elif record != expected:
            raise AccountingEvidenceError("Existing accounting settlement differs from immutable receipt")
    return receipt


def close_expired_run(cfg, store_path, root, reference):
    """Close one expired durable reservation from saved bytes, without any network calls."""
    store = Store(store_path)
    try:
        manifest = load_manifest(root, reference, cfg, store.scope)
        if (
            store.get(f"research_stage_receipt:{reference['run_key']}:commit", "research_stage_receipt")
            or store.get(reference["run_key"], "research_result")
            or store.get(reference["run_key"], "research_audit")
        ):
            raise AccountingEvidenceError("Completed research cannot use aborted-run accounting")
        path = run_directory(root, reference["run_key"]) / "operations.sqlite"
        if not path.is_file() or path.is_symlink():
            raise AccountingEvidenceError("Existing private operation journal required")
        journal = Journal(path, manifest["binding"])
        with journal.lease():
            receipt = journal.accounting_receipt()
            if receipt is not None:
                return _apply_receipt(store, cfg, reference, manifest["binding"], receipt)
            evidence = journal.accounting_evidence()
            try:
                phase, kind, role, identity = json.loads(evidence["key"])
            except (ValueError, TypeError) as exc:
                raise AccountingEvidenceError("Pending operation is not a model call") from exc
            if (
                kind != "model"
                or phase not in {"initial", "correction"}
                or role not in {*ROLES, "research_lead"}
                or not isinstance(identity, str)
                or not identity
            ):
                raise AccountingEvidenceError("Pending operation is not a model call")
            rows = store.db.execute(
                "SELECT key,body FROM documents WHERE scope='global' AND kind='research_spend' "
                "AND json_extract(body,'$.run_key')=? AND json_extract(body,'$.experiment_id')=?",
                (reference["run_key"], store.scope),
            ).fetchall()
            if len(rows) != 1:
                raise AccountingEvidenceError("Original durable reservation is not unique")
            spend_key, spend_body = rows[0]
            record = json.loads(spend_body)
            if (
                record.get("status") != "reserved"
                or record.get("unknown_total") is not False
                or not record.get("durable")
                or record.get("config_sha256") != manifest["binding"]["config_sha256"]
                or dt(record["started_at"]).astimezone(ZoneInfo("America/New_York")).date().isoformat()
                != record.get("day")
                or dt(record["started_at"]) + timedelta(seconds=cfg.research_max_seconds) > now()
            ):
                raise AccountingEvidenceError("Durable reservation is not expired and unchanged")
            prestate = evidence["prestate"]
            if not isinstance(prestate, dict) or not isinstance(prestate.get("budget"), dict):
                raise AccountingEvidenceError("Pending operation lacks immutable Budget prestate")
            prior_cost, prior_known = _prior_cost(prestate["budget"], cfg, record)
            usage, active = _current_usage(evidence, cfg, role)
            known = prior_known and usage is not None
            known_cost = prior_cost + (usage["cost"] if usage else dec(0))
            allowance = dec(record["durable_allowance"])
            receipt = {
                "run_key": reference["run_key"],
                "spend_key": spend_key,
                "manifest_hash": reference["manifest_hash"],
                "operation_key": evidence["key"],
                "request_hash": evidence["request_hash"],
                "client_request_id": active["client_request_id"] if active else None,
                "projected_request_sha256": active["projected_request_sha256"] if active else None,
                "wire_request_sha256": hashlib.sha256(active["wire_request"]["body"]).hexdigest()
                if active and active["wire_request"] and isinstance(active["wire_request"].get("body"), bytes)
                else None,
                "wire_response_sha256": hashlib.sha256(active["wire_response"]["body"]).hexdigest()
                if active
                and active["wire_response"]
                and isinstance(active["wire_response"].get("body"), bytes)
                else None,
                "day": record["day"],
                "allowance_usd": allowance,
                "known_usd": known_cost,
                "settlement_usd": known_cost if known else max(allowance, known_cost),
                "usage": {k: usage[k] for k in ("input_tokens", "output_tokens")} if usage else None,
                "status": "settled" if known else "unknown",
                "unknown_total": not known,
                "outcome": "aborted_accounted",
                "closed_at": now(),
            }
            receipt = journal.record_accounting_receipt(receipt)
            return _apply_receipt(store, cfg, reference, manifest["binding"], receipt)
    finally:
        store.db.close()
