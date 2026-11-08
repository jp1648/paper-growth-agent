"""Child process for offline abrupt-death tests. Only loopback fixture RPC is used."""

import json
import os
import signal
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import (
    AT,
    FixtureData,
)

from trader.config import Settings
from trader.models import Decision, Snapshot
from trader.service import Service
from trader.store import Store

config_path, endpoint, stage = sys.argv[1:]
if not endpoint.startswith("http://127.0.0.1:"):
    raise ValueError("Only isolated loopback broker allowed")


def die():
    os.kill(os.getpid(), signal.SIGKILL)


class Broker:
    def __getattr__(self, name):
        def invoke(*args, before_send=None):
            if before_send is not None:
                before_send()
            try:
                response = httpx.post(endpoint + "/" + name, json=list(args), timeout=5)
            except httpx.TransportError as error:
                if stage.endswith("lost_ack") and name == "cancel":
                    Path(config_path).with_name("transport-failure.json").write_text(
                        json.dumps({"operation": name, "error": type(error).__name__})
                    )
                    die()
                raise
            response.raise_for_status()
            value = response.json()
            if (stage == "accepted" and name == "submit") or (
                (stage == "cancel" or stage.startswith("cancel_race")) and name == "cancel"
            ):
                die()
            return Snapshot.model_validate(value) if name == "snapshot" else value

        return invoke


with pytest.MonkeyPatch.context() as patch:
    for module in ("trader.models", "trader.service", "trader.store"):
        patch.setattr(module + ".now", lambda: AT)
    store = Store(Settings.model_validate_json(Path(config_path).read_text()).database)
    service = Service(
        Settings.model_validate_json(Path(config_path).read_text()), store, Broker(), FixtureData()
    )
    original = store.put

    def put(key, value, kind="state", **kwargs):
        original(key, value, kind, **kwargs)
        if (
            (stage == "prepared" and kind == "intent")
            or (stage == "order" and kind == "order")
            or (stage == "activity" and kind == "activity")
        ):
            die()

    store.put = put
    if stage.startswith("revision"):
        if stage == "revision_mid":

            def revision_checkpoint(sql):
                if sql.startswith("INSERT INTO journal") and "broker_revision_approved" in sql:
                    die()

            store.db.set_trace_callback(revision_checkpoint)
        else:
            approve_revision = store.approve_revision

            def revision_committed(identifier, revision):
                approve_revision(identifier, revision)
                die()

            store.approve_revision = revision_committed
        service.acknowledge_revision(
            json.loads(Path(config_path).with_name("fixture-revision.json").read_text())
        )
    elif stage.startswith("adjustment"):
        if stage == "adjustment_mid":

            def checkpoint(sql):
                if sql.startswith("INSERT INTO journal") and "explicit_adjustment" in sql:
                    die()

            store.db.set_trace_callback(checkpoint)
        else:
            approve = store.approve_adjustments

            def committed(items):
                approve(items)
                die()

            store.approve_adjustments = committed
        service.acknowledge_adjustment(
            json.loads(Path(config_path).with_name("fixture-adjustments.json").read_text())
        )
    elif stage == "cancel" or stage.startswith("cancel_race"):
        service.kill("Process crash drill")
    elif stage == "activity":
        service.reconcile()
    else:
        proposal = Decision.model_validate(
            json.loads((Path(__file__).parents[1] / "fixtures/decision.json").read_text())
        )
        service.execute(proposal, "process-drill")
    raise RuntimeError("Expected crash checkpoint was not reached")
