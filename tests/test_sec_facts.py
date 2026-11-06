import httpx
import pytest

from trader.evidence import EvidenceProvider


def provider(env, monkeypatch, facts_cik=1, filings_cik="0000000001"):
    service, _, data = env
    service.cfg.sec_user_agent = "test test@example.com"
    facts = {
        "cik": facts_cik,
        "entityName": "ACME",
        "facts": {
            "us-gaap": {"NetIncomeLoss": {"units": {"USD": [{"val": 10, "filed": "2026-01-01"}]}}},
            "custom": {"NetIncomeLoss": {"units": {"USD": [{"val": 99999, "filed": "2026-01-01"}]}}},
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {"shares": [{"val": 100, "filed": "2026-01-01"}]}
                }
            },
        },
    }
    filings = {"cik": filings_cik, "name": "ACME", "filings": {"recent": {"form": []}}}
    source = EvidenceProvider(service.cfg, service.store, data)
    monkeypatch.setattr(
        source, "request", lambda url: httpx.Response(200, json=facts if "companyfacts" in url else filings)
    )
    return source


def test_sec_taxonomy_collision_cannot_replace_standard_fact(env, monkeypatch):
    bundle = provider(env, monkeypatch).fetch("ACME")
    content = bundle["sources"][0]["content"]
    assert content["facts"]["NetIncomeLoss"]["USD"][0]["val"] == 10
    assert content["facts"]["EntityCommonStockSharesOutstanding"]["shares"][0]["val"] == 100
    assert content["fact_taxonomies"]["NetIncomeLoss"] == "us-gaap"
    assert content["fact_taxonomies"]["EntityCommonStockSharesOutstanding"] == "dei"
    assert content["cik"] == "0000000001"


@pytest.mark.parametrize("field", ["facts_cik", "filings_cik"])
@pytest.mark.parametrize("value", [2, None, "not-a-cik"])
def test_sec_payload_must_match_configured_company(env, monkeypatch, field, value):
    source = provider(env, monkeypatch, **{field: value})
    with pytest.raises(ValueError, match="SEC.*identity"):
        source.get("ACME", fresh=True)
    assert source.store.get("ACME", "evidence_cache") is None


def test_legacy_unverified_cache_is_refetched(env, monkeypatch):
    source = provider(env, monkeypatch)
    legacy = source.fetch("ACME")
    legacy.pop("sec_schema")
    source.store.put("ACME", legacy, "evidence_cache")
    bundle = source.get("ACME")
    assert bundle["sec_schema"] == 2
    assert bundle["sources"][0]["content"]["cik"] == "0000000001"


def test_provider_omits_future_and_out_of_window_news(env, monkeypatch):
    from datetime import timedelta

    from conftest import AT

    monkeypatch.setattr("trader.evidence.now", lambda: AT)
    source = provider(env, monkeypatch)
    source.data.news_items = [
        {"id": str(days), "created_at": (AT + timedelta(days=days)).isoformat(), "headline": "Fixture"}
        for days in (-8, -1, 1)
    ]
    bundle = source.fetch("ACME")
    news = [s for s in bundle["sources"] if s["id"].startswith("news-")]
    assert len(news) == 1
    assert news[0]["published_at"] == (AT - timedelta(days=1)).isoformat()
