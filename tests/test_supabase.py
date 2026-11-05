import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    not os.environ.get("TEST_OBSERVABILITY_DATABASE_URL"), reason="Local Postgres integration"
)
def test_supabase_roles_and_idempotent_copy():
    spec = importlib.util.spec_from_file_location("migration", ROOT / "deploy/migrate-observability.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    dsn = os.environ["TEST_OBSERVABILITY_DATABASE_URL"]
    with psycopg.connect(dsn, autocommit=True) as source, psycopg.connect(dsn, autocommit=True) as target:
        count = source.execute("SELECT count(*) FROM telemetry_events").fetchone()[0]
        assert migration.copy_events(source, target) == count
        assert target.execute("SELECT count(*) FROM telemetry_events").fetchone()[0] == count
    with psycopg.connect(dsn) as db:
        try:
            db.execute((ROOT / "deploy/supabase-observability.sql").read_text())
            db.execute("SET LOCAL ROLE trader_writer")
            event_id = uuid4()
            db.execute(
                "INSERT INTO telemetry_events(event_id,at,trace_id,name,phase,payload) "
                "VALUES (%s,now(),'policy-test','policy-test','event','{}')",
                (event_id,),
            )
            assert (
                db.execute("SELECT count(*) FROM telemetry_events WHERE event_id=%s", (event_id,)).fetchone()[
                    0
                ]
                == 1
            )
            db.execute("RESET ROLE")
            db.execute("SET LOCAL ROLE trader_dashboard")
            assert (
                db.execute("SELECT count(*) FROM telemetry_events WHERE event_id=%s", (event_id,)).fetchone()[
                    0
                ]
                == 1
            )
            assert db.execute(
                "SELECT has_table_privilege(current_user,'telemetry_events','INSERT'), "
                "has_table_privilege(current_user,'telemetry_events','UPDATE'), "
                "has_table_privilege(current_user,'telemetry_events','DELETE')"
            ).fetchone() == (False, False, False)
        finally:
            db.rollback()  # No role, policy or fixture changes survive this test.


def test_migration_transport_rejects_network_plaintext_and_socket_escape(tmp_path):
    spec = importlib.util.spec_from_file_location("migration", ROOT / "deploy/migrate-observability.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.validate_transport({"sslmode": "verify-full"}, tmp_path)
    migration.validate_transport({"host": str(tmp_path / "state/cloudsql/instance")}, tmp_path, True)
    for options, proxy in [
        ({"host": "remote.example", "sslmode": "disable"}, False),
        ({"host": "127.0.0.1"}, True),
        ({"host": str(tmp_path / "state/cloudsql/../../escape")}, True),
        ({"host": str(tmp_path / "state/cloudsql/instance"), "hostaddr": "1.2.3.4"}, True),
    ]:
        with pytest.raises(ValueError):
            migration.validate_transport(options, tmp_path, proxy)
