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
            "us-gaap": {
                tag: {"units": {"USD": [{"val": 10, "filed": "2026-01-01"}]}}
                for tag in (
                    "NetIncomeLoss",
                    "PaymentsForRepurchaseOfCommonStock",
                    "PaymentsOfDividendsCommonStock",
                    "IncomeTaxesPaidNet",
                    "ShareBasedCompensation",
                )
            },
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
    assert content["facts"]["ShareBasedCompensation"]["USD"][0]["val"] == 10
    assert content["fact_taxonomies"]["ShareBasedCompensation"] == "us-gaap"
    for tag in ("PaymentsForRepurchaseOfCommonStock", "PaymentsOfDividendsCommonStock", "IncomeTaxesPaidNet"):
        assert content["facts"][tag]["USD"][0]["val"] == 10
        assert content["fact_taxonomies"][tag] == "us-gaap"


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
    legacy["sec_schema"] = 4
    source.store.put("ACME", legacy, "evidence_cache")
    bundle = source.get("ACME")
    assert bundle["sec_schema"] == 5
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


@pytest.mark.parametrize(
    "offset,form,expected", [(-1, "10-K", 0), (0, "10-K/A", 1), (1, "8-K", 1), (1, "4", 0)]
)
def test_filing_freshness_uses_acceptance_time(env, monkeypatch, offset, form, expected):
    from datetime import timedelta

    from conftest import AT

    source = provider(env, monkeypatch)
    monkeypatch.setattr("trader.evidence.now", lambda: AT)
    since = AT - timedelta(minutes=5)
    raw = {
        "cik": 1,
        "filings": {
            "recent": {
                "form": [form],
                "accessionNumber": ["0000000001-26-000001"],
                "acceptanceDateTime": [(since + timedelta(seconds=offset)).isoformat()],
            }
        },
    }
    monkeypatch.setattr(source, "request", lambda url: httpx.Response(200, json=raw))
    assert len(source.filings_since("ACME", since)) == expected


@pytest.mark.parametrize(
    "fault", ["identity", "missing_time", "naive", "future", "missing_accession", "unequal_arrays"]
)
def test_filing_freshness_rejects_unverifiable_response(env, monkeypatch, fault):
    from datetime import timedelta

    from conftest import AT

    source = provider(env, monkeypatch)
    monkeypatch.setattr("trader.evidence.now", lambda: AT)
    recent = {
        "form": ["10-Q/A"],
        "accessionNumber": ["0000000001-26-000001"],
        "acceptanceDateTime": [AT.isoformat()],
    }
    raw = {"cik": 2 if fault == "identity" else 1, "filings": {"recent": recent}}
    if fault == "missing_time":
        del recent["acceptanceDateTime"]
    elif fault == "naive":
        recent["acceptanceDateTime"] = ["2026-10-21T14:59:00"]
    elif fault == "future":
        recent["acceptanceDateTime"] = [(AT + timedelta(seconds=1)).isoformat()]
    elif fault == "missing_accession":
        recent["accessionNumber"] = [""]
    elif fault == "unequal_arrays":
        recent["accessionNumber"] = []
    monkeypatch.setattr(source, "request", lambda url: httpx.Response(200, json=raw))
    with pytest.raises((ValueError, KeyError, IndexError)):
        source.filings_since("ACME", AT - timedelta(minutes=5))


@pytest.mark.parametrize(
    "fault",
    [
        "empty",
        "string_forms",
        "blank_form",
        "extra_row",
        "nonlist_column",
        "old_bad_accession",
        "future_cutoff",
        "expired_cutoff",
    ],
)
def test_incomplete_filing_history_cannot_prove_absence(env, monkeypatch, fault):
    from datetime import timedelta

    from conftest import AT

    source = provider(env, monkeypatch)
    monkeypatch.setattr("trader.evidence.now", lambda: AT)
    since = AT - timedelta(minutes=5)
    recent = {
        "form": ["10-K"],
        "accessionNumber": ["0000000001-26-000001"],
        "acceptanceDateTime": [(AT - timedelta(days=1)).isoformat()],
    }
    if fault == "empty":
        recent = {key: [] for key in recent}
    elif fault == "string_forms":
        recent["form"] = "10-K"
    elif fault == "blank_form":
        recent["form"] = [""]
    elif fault == "extra_row":
        recent["acceptanceDateTime"].append(AT.isoformat())
    elif fault == "nonlist_column":
        recent["unexpected"] = "not-an-array"
    elif fault == "old_bad_accession":
        recent["accessionNumber"] = ["unverified"]
    elif fault == "future_cutoff":
        since = AT + timedelta(seconds=1)
    else:
        since = AT - timedelta(days=365)
    monkeypatch.setattr(
        source, "request", lambda url: httpx.Response(200, json={"cik": 1, "filings": {"recent": recent}})
    )
    with pytest.raises(ValueError):
        source.filings_since("ACME", since)


@pytest.mark.parametrize("fault", ["future", "missing_time", "naive_time", "bad_accession", "unequal_arrays"])
def test_research_ingestion_requires_verified_filing_acceptance(env, monkeypatch, fault):
    from datetime import timedelta

    from conftest import AT

    monkeypatch.setattr("trader.evidence.now", lambda: AT)
    source = provider(env, monkeypatch)
    original = source.request
    recent = {
        "form": ["10-K"],
        "filingDate": [AT.date().isoformat()],
        "accessionNumber": ["0000000001-26-000001"],
        "acceptanceDateTime": [(AT - timedelta(minutes=1)).isoformat()],
    }
    if fault == "future":
        recent["acceptanceDateTime"] = [(AT + timedelta(seconds=1)).isoformat()]
    elif fault == "missing_time":
        del recent["acceptanceDateTime"]
    elif fault == "naive_time":
        recent["acceptanceDateTime"] = [AT.replace(tzinfo=None).isoformat()]
    elif fault == "bad_accession":
        recent["accessionNumber"] = ["unverified"]
    else:
        recent["acceptanceDateTime"].append(AT.isoformat())
    monkeypatch.setattr(
        source,
        "request",
        lambda url: (
            original(url)
            if "companyfacts" in url
            else httpx.Response(200, json={"cik": 1, "name": "ACME", "filings": {"recent": recent}})
        ),
    )
    with pytest.raises(ValueError):
        source.get("ACME", fresh=True)
    assert source.store.get("ACME", "evidence_cache") is None


def test_research_fetch_pins_cutoff_before_requests(env, monkeypatch):
    from datetime import timedelta

    from conftest import AT

    current = [AT]
    monkeypatch.setattr("trader.evidence.now", lambda: current[0])
    source = provider(env, monkeypatch)
    original = source.request

    def request(url):
        current[0] = AT + timedelta(minutes=1)
        if "companyfacts" in url:
            return original(url)
        return httpx.Response(
            200,
            json={
                "cik": 1,
                "name": "ACME",
                "filings": {
                    "recent": {
                        "form": ["8-K"],
                        "filingDate": [AT.date().isoformat()],
                        "accessionNumber": ["0000000001-26-000001"],
                        "acceptanceDateTime": [(AT + timedelta(seconds=1)).isoformat()],
                    }
                },
            },
        )

    monkeypatch.setattr(source, "request", request)
    with pytest.raises(ValueError, match="future-dated"):
        source.get("ACME", fresh=True)
    assert source.store.get("ACME", "evidence_cache") is None


def test_fetch_keeps_verified_same_day_filing_but_excludes_date_only_facts(env, monkeypatch):
    from datetime import timedelta

    from conftest import AT

    current = [AT]
    monkeypatch.setattr("trader.evidence.now", lambda: current[0])
    source = provider(env, monkeypatch)
    original = source.request
    accepted = (AT - timedelta(seconds=1)).isoformat()

    def request(url):
        current[0] = AT + timedelta(minutes=1)
        if "companyfacts" in url:
            raw = original(url).json()
            raw["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"].append(
                {"val": 999, "filed": AT.date().isoformat()}
            )
        else:
            raw = {
                "cik": 1,
                "name": "ACME",
                "filings": {
                    "recent": {
                        "form": ["10-K"],
                        "filingDate": [AT.date().isoformat()],
                        "accessionNumber": ["0000000001-26-000001"],
                        "acceptanceDateTime": [accepted],
                    }
                },
            }
        return httpx.Response(200, json=raw)

    monkeypatch.setattr(source, "request", request)
    source.data.news_items = [
        {"id": str(offset), "created_at": (AT + timedelta(seconds=offset)).isoformat(), "headline": "Fixture"}
        for offset in (-1, 1)
    ]
    bundle = source.get("ACME", fresh=True)
    assert bundle["fetched_at"] == AT.isoformat()
    assert bundle["sources"][0]["content"]["facts"]["NetIncomeLoss"]["USD"] == [
        {"val": 10, "filed": "2026-01-01"}
    ]
    assert bundle["sources"][1]["content"]["filings"][0]["acceptanceDateTime"] == accepted
    assert [s["published_at"] for s in bundle["sources"] if s["id"].startswith("news-")] == [
        (AT - timedelta(seconds=1)).isoformat()
    ]
    assert source.store.get("ACME", "evidence_cache")["sec_schema"] == 5


@pytest.mark.parametrize("offset,expected", [(-1, 1), (1, 0)])
def test_news_revision_timestamp_controls_snapshot_eligibility(env, monkeypatch, offset, expected):
    from datetime import timedelta

    from conftest import AT

    monkeypatch.setattr("trader.evidence.now", lambda: AT)
    source = provider(env, monkeypatch)
    source.data.news_items = [
        {
            "id": "revised",
            "created_at": (AT - timedelta(days=8)).isoformat(),
            "updated_at": (AT + timedelta(seconds=offset)).isoformat(),
            "headline": "Revised claim",
        }
    ]
    bundle = source.fetch("ACME")
    news = [s for s in bundle["sources"] if s["id"].startswith("news-")]
    assert len(news) == expected
    if news:
        assert news[0]["updated_at"] == source.data.news_items[0]["updated_at"]


@pytest.mark.parametrize("updated", ["2020-01-01T00:00:00Z", "2026-01-01T00:00:00", "invalid"])
def test_news_invalid_revision_cannot_be_cached(env, monkeypatch, updated):
    from conftest import AT

    source = provider(env, monkeypatch)
    source.data.news_items = [{"id": "invalid", "created_at": AT.isoformat(), "updated_at": updated}]
    with pytest.raises(ValueError):
        source.get("ACME", fresh=True)
    assert source.store.get("ACME", "evidence_cache") is None
