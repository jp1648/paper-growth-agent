"""Lossless provider-context field sharing; never alters saved research artifacts."""

import copy
import json
from collections import Counter

from .models import dumps

_RESERVED = {"shared_field_refs", "exact_field_catalog", "field_projection_note"}


def share_context_fields(context):
    """Share exact repeated key/value pairs while retaining descriptive field names.

    This complements subtree sharing when calculation objects differ in only some
    fields. Catalog values refer only to strictly nested fields, so reconstruction
    is acyclic. Existing reserved keys disable the projection instead of being
    interpreted as aliases. No numeric tolerance or economic equivalence is used.
    """
    if not isinstance(context, dict) or not isinstance(context.get("calculations"), dict):
        return context
    counts = Counter()
    collision = False

    def collect(value):
        nonlocal collision
        if isinstance(value, dict):
            if _RESERVED.intersection(value):
                collision = True
            for key, item in value.items():
                pair = dumps([key, item])
                if len(pair.encode()) > 24:
                    counts[pair] += 1
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(context)
    if collision:
        return context
    repeated = sorted(
        pair
        for pair, count in counts.items()
        if count >= 3 and (count - 1) * len(pair.encode()) > count * 12 + 30
    )
    if not repeated:
        return context
    aliases = {pair: "p" + str(index) for index, pair in enumerate(repeated)}

    def encode(value):
        if isinstance(value, dict):
            result, refs = {}, []
            for key, item in value.items():
                pair = dumps([key, item])
                if pair in aliases:
                    refs.append(aliases[pair])
                else:
                    result[key] = encode(item)
            if refs:
                result["shared_field_refs"] = refs
            return result
        if isinstance(value, list):
            return [encode(item) for item in value]
        return copy.deepcopy(value)

    projected = encode(context)
    projected["exact_field_catalog"] = {
        aliases[pair]: [json.loads(pair)[0], encode(json.loads(pair)[1])] for pair in repeated
    }
    projected["field_projection_note"] = (
        "Lossless exact dictionary field sharing: each shared_field_refs item resolves a "
        "[literal field name, exact value] pair in exact_field_catalog and inserts that field "
        "into the same object. Resolve recursively before interpretation and before resolving "
        "calculation_context_ref aliases. Every original descriptive key, value, ID, rationale "
        "and provenance is retained; no economic equivalence is inferred. These are context "
        "aliases, not evidence IDs or executable tool arguments. Original artifacts are unchanged."
    )
    return projected if len(dumps(projected).encode()) < len(dumps(context).encode()) else context


def project_revision_history(context):
    """Omit only superseded audit history from a reviewed revision's model view.

    This selection is deliberately not lossless: omitted values remain solely in
    the immutable audit. Current reports, calculations and all evidence stay in
    the request. The caller must construct the evidence index before selection.
    """
    import hashlib

    synthesis = context.get("reviewed_synthesis", {})
    if not isinstance(synthesis, dict) or "revision" not in synthesis:
        return context
    if "historical_context_projection" in context:
        raise ValueError("Historical context projection marker already present")
    projected = copy.deepcopy(context)
    omitted = []
    for path in (
        ("analytical_hypothesis",),
        ("portfolio", "prior_thesis"),
        ("supplement_navigation",),
        ("review_focus",),
    ):
        parent = projected
        for key in path[:-1]:
            parent = parent.get(key, {})
        if path[-1] in parent:
            value = parent.pop(path[-1])
            omitted.append(
                {"path": ".".join(path), "value_sha256": hashlib.sha256(dumps(value).encode()).hexdigest()}
            )
    projected["historical_context_projection"] = {
        "omitted_fields": omitted,
        "preprojection_model_context_sha256": hashlib.sha256(dumps(context).encode()).hexdigest(),
        "note": (
            "Model-view selection, not lossless compression: the superseded unverified acquisition "
            "hypothesis, older portfolio thesis and obsolete tool-navigation/review checklist are "
            "omitted and remain available only in the immutable audit. Their absence is not evidence "
            "of acceptance or resolution. Current holdings/cash/reference, every full calculation, "
            "all four reports, complete analytical addendum, previous rejected decision and review, "
            "arithmetic and the complete pre-selection evidence index remain unchanged."
        ),
    }
    return projected
