from types import SimpleNamespace

from trader import cli
from trader.research_client import ResearchPending


def test_cli_uses_temporal_client_and_reports_pending_without_failure(env, monkeypatch, capsys):
    service, broker, data = env
    service.cfg.temporal_address = "127.0.0.1:7233"
    monkeypatch.setattr(cli.Settings, "load", lambda _: service.cfg)
    monkeypatch.setattr(cli, "AlpacaBroker", lambda _: broker)
    monkeypatch.setattr(cli, "AlpacaData", lambda _: data)
    captured = {}

    def client(cfg, store, **kwargs):
        captured.update(kwargs)
        return "temporal-client"

    def graph(service, team, *, research_client):
        assert research_client == "temporal-client"

        def invoke(state):
            raise ResearchPending(
                {"run_key": state["run_key"], "manifest_hash": "a" * 64}, "running", "valuation"
            )

        return SimpleNamespace(invoke=invoke)

    monkeypatch.setattr("trader.research_client.TemporalResearchClient", client)
    monkeypatch.setattr(cli, "build_graph", graph)
    assert cli.main(["research", "ACME", "--run-key", "one", "--defer-execution"]) == 0
    assert captured["address"] == "127.0.0.1:7233"
    output = capsys.readouterr()
    assert '"status": "research pending"' in output.out and not output.err
    assert service.broker.submits == 0
