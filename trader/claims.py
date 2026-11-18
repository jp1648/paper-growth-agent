"""Runtime claim-completion checks; not a semantic investment or frozen replay rubric."""

from datetime import date
from typing import Literal

from pydantic import Field, field_validator

from .models import Model, dt


class ClaimCoverage(Model):
    status: Literal[
        "supported_conditional_treatment",
        "supported_not_applicable",
        "unread",
        "not_disclosed_in_inspected_sources",
        "unsupported_treatment",
    ]
    evidence_ids: list[str]
    as_of_date: str | None = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    treatment_or_gap: str = Field(min_length=1, max_length=2000)

    @field_validator("as_of_date")
    @classmethod
    def canonical_date(cls, value):
        if value is not None:
            date.fromisoformat(value)
        return value

    @field_validator("treatment_or_gap")
    @classmethod
    def nonblank_treatment(cls, value):
        if not value.strip():
            raise ValueError("Claim treatment or exact gap is required")
        return value


class ClaimsCoverage(Model):
    common_shares: ClaimCoverage
    preferred_or_convertible: ClaimCoverage
    legacy_awards: ClaimCoverage
    noncontrolling_interests: ClaimCoverage


def claim_gaps(valuation):
    review = valuation.get("normalization_review") or {}
    coverage = review.get("claims_coverage") if isinstance(review, dict) else None
    invalid = False
    if coverage is not None:
        try:
            coverage = ClaimsCoverage.model_validate(coverage).model_dump(mode="json")
        except ValueError:
            coverage, invalid = None, True
    return [
        {
            "claim": name,
            **(coverage or {}).get(
                name,
                {
                    "status": "invalid_coverage" if invalid else "legacy_unreviewed",
                    "evidence_ids": [],
                    "as_of_date": None,
                    "treatment_or_gap": "Coverage is malformed"
                    if invalid
                    else "Legacy report has no structured claim review",
                },
            ),
        }
        for name in ClaimsCoverage.model_fields
        if not (coverage or {}).get(name, {}).get("status", "").startswith("supported_")
    ]


def validate_claim_coverage(valuation, sources, cutoff):
    cutoff = dt(cutoff)
    review = valuation.get("normalization_review") or {}
    if not isinstance(review, dict):
        raise TypeError("Invalid normalization review")
    coverage = review.get("claims_coverage")
    if coverage is None:
        return  # Legacy evidence is unreviewed, never automatically closed.
    coverage = ClaimsCoverage.model_validate(coverage).model_dump(mode="json")
    for name, claim in coverage.items():
        refs = claim["evidence_ids"]
        if not set(refs).issubset(valuation.get("evidence_ids", [])) or not set(refs).issubset(sources):
            raise ValueError("Claim coverage must cite registered valuation evidence")
        if any(
            sources[ref].get("source") not in {"SEC EDGAR", "SEC EDGAR filing passage"}
            or sources[ref].get("original_source") is False
            or sources[ref].get("available") is False
            or not sources[ref].get("content")
            or (isinstance(sources[ref].get("content"), str) and not sources[ref]["content"].strip())
            for ref in refs
        ):
            raise ValueError("Claim coverage requires original SEC disclosure evidence")
        for ref in refs:
            for field in ("available_at", "published_at"):
                published = sources[ref].get(field)
                if published:
                    # SEC date-only disclosure metadata means midnight UTC, not end of day.
                    if isinstance(published, str) and len(published) == 10:
                        date.fromisoformat(published)
                        published += "T00:00:00+00:00"
                    if dt(published) > cutoff:
                        raise ValueError("Claim source was published or available after research cutoff")
        closed = claim["status"].startswith("supported_")
        if closed and not refs:
            raise ValueError("Supported claim coverage requires original evidence")
        if closed and name == "common_shares" and claim["as_of_date"] is None:
            raise ValueError("Supported common shares require a point date")
        if claim["as_of_date"] and date.fromisoformat(claim["as_of_date"]) > cutoff.date():
            raise ValueError("Claim coverage date exceeds research cutoff")
