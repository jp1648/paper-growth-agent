"""Provision telemetry schema/roles through the local Cloud SQL authenticated proxy."""

from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from trader.observability import SCHEMA

root = Path(__file__).resolve().parents[1]
private = root / "state/gcloud"
socket = root / "state/cloudsql/db"


def dsn(user, filename):
    return make_conninfo(
        host=str(socket),
        dbname="observability",
        user=user,
        password=(private / filename).read_text().strip(),
        sslmode="disable",
    )


try:
    with psycopg.connect(dsn("postgres", "postgres-password"), connect_timeout=10) as db:
        db.execute(SCHEMA)
        db.execute((root / "deploy/supabase-observability.sql").read_text())
        for role, filename in [("trader_writer", "writer-password"), ("trader_dashboard", "reader-password")]:
            db.execute(
                sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal((private / filename).read_text().strip())
                )
            )
            db.execute(sql.SQL("GRANT CONNECT ON DATABASE observability TO {}").format(sql.Identifier(role)))
        db.execute("ALTER ROLE trader_dashboard SET default_transaction_read_only=on")
    for role, filename, output in [
        ("postgres", "postgres-password", "admin-dsn"),
        ("trader_writer", "writer-password", "writer-dsn"),
        ("trader_dashboard", "reader-password", "reader-dsn"),
    ]:
        f = private / output
        f.touch(mode=0o600)
        f.chmod(0o600)
        f.write_text(dsn(role, filename))
    print("Cloud SQL schema and private role connections configured.")
except (psycopg.Error, OSError) as error:
    raise SystemExit("Cloud SQL setup failed: " + type(error).__name__) from None
