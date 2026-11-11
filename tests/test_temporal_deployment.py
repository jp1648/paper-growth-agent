import os
import subprocess
from pathlib import Path

import pytest
import yaml


@pytest.mark.skipif(
    os.environ.get("TEMPORAL_DEPLOY_TEST") != "1", reason="explicit local Docker verification"
)
def test_pinned_server_renders_private_bounded_config_and_requires_credentials():
    template = Path(__file__).parents[1] / "deploy/temporal-server.yaml"
    command = [
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=bind,src={template},dst=/etc/temporal/server.yaml,readonly",
    ]
    image = "temporalio/server@sha256:7bf69188a6232fea14869b9cef3d94aa82a1601d99517f7e86c71f22da4e0670"
    tail = [
        "--entrypoint",
        "temporal-server",
        image,
        "--config",
        "/etc/temporal",
        "--env",
        "server",
        "render-config",
    ]
    missing = subprocess.run(command + tail, check=False, capture_output=True, text=True, timeout=30)
    assert missing.returncode != 0 and "required" in missing.stderr
    environment = [
        "--env",
        "TEMPORAL_POSTGRES_ADDRESS=127.0.0.1:5432",
        "--env",
        "TEMPORAL_POSTGRES_USER=fixture",
        "--env",
        "TEMPORAL_POSTGRES_PASSWORD=fixture-only",
    ]
    result = subprocess.run(
        command + environment + tail, capture_output=True, text=True, timeout=30, check=True
    )
    config = yaml.safe_load(result.stdout)
    assert config["persistence"]["numHistoryShards"] == 4
    for store in config["persistence"]["datastores"].values():
        assert store["sql"]["maxConns"] == 2 and store["sql"]["maxIdleConns"] == 1
        assert store["sql"]["databaseName"].startswith("trader_temporal")
    assert all(service["rpc"]["bindOnIP"] == "127.0.0.1" for service in config["services"].values())
    assert config["publicClient"]["hostPort"] == "127.0.0.1:7233"
