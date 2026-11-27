"""Offline paired-return diagnostics; never a trial acceptance or live release gate."""

import hashlib
import json
import math
import random
from datetime import date
from pathlib import Path


def paired_uncertainty(document):
    if document.get("version") != 1 or document.get("return_basis") != "flow_adjusted_net_total_return":
        raise ValueError("Require version 1 flow-adjusted net total returns, not prices or gross returns")
    if document.get("benchmark") != "SPY" or document.get("currency") != "USD":
        raise ValueError("The declared primary comparison is SPY in USD")
    for key in ("benchmark_method", "cost_allocation_method", "flow_method", "source_manifest_sha256"):
        if not isinstance(document.get(key), str) or not document[key].strip():
            raise ValueError(f"Missing provenance: {key}")
    digest = document["source_manifest_sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid source manifest hash")
    sessions, observations = document.get("expected_sessions"), document.get("observations")
    if not isinstance(sessions, list) or not 60 <= len(sessions) <= 1000:
        raise ValueError("Require 60–1000 declared sessions")
    dates = [date.fromisoformat(s) for s in sessions]
    if [d.isoformat() for d in dates] != sessions or sessions != sorted(set(sessions)):
        raise ValueError("Sessions must be unique, increasing canonical ISO dates")
    if (dates[-1] - dates[0]).days < 84:
        raise ValueError("Require at least 12 calendar weeks")
    if (
        not isinstance(observations, list)
        or any(not isinstance(r, dict) for r in observations)
        or [r.get("date") for r in observations] != sessions
    ):
        raise ValueError("Observations must match every declared session exactly and in order")
    pairs = []
    for row in observations:
        values = []
        for key in ("portfolio_return", "benchmark_return"):
            value = row.get(key)
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise TypeError("Missing or invalid daily return")
            value = float(value)
            if not math.isfinite(value) or value <= -1:
                raise ValueError(
                    "Returns must be finite and greater than -100%; zero-NAV chains are undefined"
                )
            values.append(value)
        pairs.append(values)

    def compounded(indices):
        portfolio = math.prod(1 + pairs[i][0] for i in indices) - 1
        benchmark = math.prod(1 + pairs[i][1] for i in indices) - 1
        if not math.isfinite(portfolio) or not math.isfinite(benchmark):
            raise ValueError("Compounded return overflow")
        return portfolio, benchmark

    portfolio, benchmark = compounded(range(len(pairs)))
    drawdowns = []
    for side in (0, 1):
        wealth = peak = 1.0
        maximum = 0.0
        for pair in pairs:
            wealth *= 1 + pair[side]
            peak = max(peak, wealth)
            maximum = max(maximum, 1 - wealth / peak)
        drawdowns.append(maximum)
    intervals = []
    for length in (5, 10):
        rng = random.Random(1648)
        samples = []
        for _ in range(10000):
            indices = []
            while len(indices) < len(pairs):
                start = rng.randrange(len(pairs) - length + 1)
                indices.extend(range(start, start + length))
            p, b = compounded(indices[: len(pairs)])
            samples.append(p - b)
        samples.sort()

        def quantile(samples, q):
            index = (len(samples) - 1) * q
            lower = math.floor(index)
            return samples[lower] + (samples[math.ceil(index)] - samples[lower]) * (index - lower)

        intervals.append(
            {
                "block_sessions": length,
                "lower_95": quantile(samples, 0.025),
                "upper_95": quantile(samples, 0.975),
            }
        )
    return {
        "analysis_version": "paired-moving-block-v1",
        "status": "diagnostic_only",
        "sessions": len(pairs),
        "start": sessions[0],
        "end": sessions[-1],
        "portfolio_return": portfolio,
        "benchmark_return": benchmark,
        "excess_return": portfolio - benchmark,
        "portfolio_max_drawdown": drawdowns[0],
        "benchmark_max_drawdown": drawdowns[1],
        "drawdown_method": "Positive peak-to-trough fraction of compounded session returns, including opening wealth 1; not intraday drawdown.",
        "intervals": intervals,
        "resamples": 10000,
        "seed": 1648,
        "source_manifest_sha256": digest,
        "limitations": [
            "Input provenance, complete costs, broker calendar and independent review are not verified by this calculation.",
            "Overlapping noncircular paired blocks; final block truncated; linear percentile interpolation.",
            "Small samples, nonstationarity and regime changes can invalidate nominal interval coverage.",
            "No trial acceptance, demonstrated investment edge or live-trading authorization follows from these intervals.",
        ],
    }


def analyze_file(source, output):
    raw = Path(source).read_bytes()
    result = paired_uncertainty(json.loads(raw))
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    with Path(output).open("x") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result
