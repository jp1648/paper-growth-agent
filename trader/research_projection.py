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
