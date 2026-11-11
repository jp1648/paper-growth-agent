import pytest
from conftest import AT

from trader.research_manifest import prepare_manifest
from trader.research_runtime import stage_handler
from trader.research_workflow import StageConfigurationError


def test_completed_stage_replays_without_key_broker_or_live_funding(env, tmp_path, monkeypatch):
    from trader import research_runtime

    service, _, _ = env
    root = tmp_path / "runs"
    context = {
        "run_key": "cached-stage",
        "symbol": "ACME",
        "research_only": True,
        "current_timestamp": AT.isoformat(),
        "portfolio": {},
    }
    reference = prepare_manifest(root, service.cfg, service.store.scope, context)
    receipt = {
        "status": "next",
        "receipt_ref": "research_stage_receipt:cached-stage:business",
        "next_stage": "catalysts",
    }
    service.store.put(
        receipt["receipt_ref"],
        {**reference, "stage": "business", "receipt": receipt},
        "research_stage_receipt",
    )
    monkeypatch.setattr(research_runtime.Settings, "load", lambda _: service.cfg)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(research_runtime, "reconcile_if_stale", lambda *a: pytest.fail("broker accessed"))
    monkeypatch.setattr("trader.research_team.DeepResearchTeam.models", lambda *a: pytest.fail("model setup"))
    monkeypatch.setattr("trader.alpaca.AlpacaData", lambda *a: pytest.fail("data client"))
    request = {**reference, "stage": "business"}
    assert stage_handler("config.toml", root)(request) == receipt
    service.store.put(
        receipt["receipt_ref"],
        {**reference, "stage": "business", "receipt": {**receipt, "next_stage": "lead"}},
        "research_stage_receipt",
    )
    with pytest.raises(StageConfigurationError):
        stage_handler("config.toml", root)(request)
