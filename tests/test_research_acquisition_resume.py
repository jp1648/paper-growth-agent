from datetime import timedelta

import httpx
import pytest
from conftest import AT
from test_research_stages import Desk

from trader.alpaca import AlpacaData
from trader.evidence import EvidenceProvider
from trader.paper_dispatch import fingerprint
from trader.research_stages import ResearchStages
from trader.store import Store
from trader.x_api import XDiscovery


def test_acquisition_restarts_after_each_saved_external_subresponse(env, tmp_path, monkeypatch):
    service, _, _ = env
    cfg = service.cfg
    cfg.sec_user_agent = "Offline Test research@example.com"
    cfg.x_enabled = True
    monkeypatch.setattr("trader.research_stages.funding_gate", lambda *a, **kw: None)
    monkeypatch.setattr("trader.paper_dispatch.funding_gate", lambda *a, **kw: None)
    clock = {"at": AT}
    monkeypatch.setattr("trader.research_stages.now", lambda: clock["at"])
    monkeypatch.setattr("trader.evidence.now", lambda: clock["at"])
    monkeypatch.setattr("trader.alpaca.now", lambda: clock["at"])
    monkeypatch.setattr("trader.x_api.now", lambda: clock["at"])
    cik = cfg.research_universe["ACME"].cik.zfill(10)
    calls = {"sec": [], "news": [], "x": []}

    def sec_request(self, url):
        calls["sec"].append(url)
        if "companyfacts" in url:
            return httpx.Response(
                200,
                json={
                    "cik": int(cik),
                    "entityName": "Offline Example",
                    "facts": {
                        "us-gaap": {
                            "Revenues": {
                                "units": {
                                    "USD": [
                                        {
                                            "val": 100,
                                            "filed": "2025-01-01",
                                            "end": "2024-12-31",
                                        }
                                    ]
                                }
                            }
                        }
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "cik": int(cik),
                "name": "Offline Example",
                "filings": {
                    "recent": {
                        "form": ["10-K"],
                        "accessionNumber": ["0000000001-25-000001"],
                        "acceptanceDateTime": ["2025-02-01T12:00:00+00:00"],
                    }
                },
            },
        )

    def news_get(self, path, params):
        calls["news"].append((path, params.copy()))
        return {
            "news": [
                {
                    "id": len(calls["news"]),
                    "created_at": (AT - timedelta(days=1)).isoformat(),
                    "headline": "Offline headline",
                }
            ],
            "next_page_token": "page-two" if "page_token" not in params else None,
        }

    def x_search(self, symbol, perspective):
        calls["x"].append(perspective)
        return {
            "id": "x-" + perspective,
            "source": "X API",
            "state": "received",
            "retrieved_at": clock["at"].isoformat(),
            "content": {"perspective": perspective},
        }

    monkeypatch.setattr(EvidenceProvider, "request", sec_request)
    monkeypatch.setattr(AlpacaData, "get", news_get)
    monkeypatch.setattr(XDiscovery, "_search", x_search)
    data = object.__new__(AlpacaData)
    data.settings = cfg
    context = {"run_key": "acquisition-resume", "symbol": "ACME", "portfolio": {}, "research_only": True}
    binding = {
        "run_key": context["run_key"],
        "experiment_id": service.store.scope,
        "config_sha256": fingerprint(cfg),
        "code_sha256": "a" * 64,
        "context_sha256": fingerprint(context),
    }
    counts = {}
    engine = ResearchStages(
        cfg,
        cfg.database,
        lambda store: EvidenceProvider(cfg, store, data),
        context,
        {},
        tmp_path / "private",
        binding,
        desk_factory=lambda *args, **kwargs: Desk(*args, **kwargs, counts=counts),
    )
    request = {"run_key": context["run_key"], "manifest_hash": engine.manifest_hash, "stage": "acquisition"}
    original = engine._acquire_call
    barriers = ["sec_filings", "news_page_1", "x_contrary"]

    def interrupted(journal, name, payload, handler, budget, stop, plan=None):
        if barriers and name == barriers[0]:
            barriers.pop(0)
            raise RuntimeError("crash before next dispatch")
        return original(journal, name, payload, handler, budget, stop, plan)

    monkeypatch.setattr(engine, "_acquire_call", interrupted)
    for expected in ("sec_filings", "news_page_1", "x_contrary"):
        with pytest.raises(RuntimeError, match="crash before next dispatch"):
            engine(request)
        clock["at"] += timedelta(minutes=10)
    assert barriers == []
    assert engine(request)["next_stage"] == "business"
    assert len(calls["sec"]) == 2
    assert len(calls["news"]) == 2
    assert calls["news"][0][1]["end"] == calls["news"][1][1]["end"] == AT.isoformat()
    assert calls["news"][1][1]["page_token"] == "page-two"
    assert calls["x"] == ["discussion", "contrary"]
    bundle = Store(cfg.database).get("ACME", "evidence_cache")
    assert bundle["fetched_at"] == AT.isoformat()
    assert bundle["sources"][0]["retrieved_at"] == AT.isoformat()
    news_sources = [source for source in bundle["sources"] if source["id"].startswith("news-")]
    assert news_sources[0]["retrieved_at"] == (AT + timedelta(minutes=10)).isoformat()
    assert len({source["id"] for source in news_sources}) == 2
