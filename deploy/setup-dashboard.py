"""Provision a local SELECT-only dashboard login without printing credentials."""

import os
import secrets
from pathlib import Path

import psycopg
from psycopg import sql

root = Path(__file__).resolve().parents[1]
env = root / "dashboard" / ".env.local"
if env.exists():
    raise SystemExit("Dashboard environment already exists; preserved without changes.")
admin = (root / "state/observability/postgres-password").read_text().strip()
password = secrets.token_hex(32)
with psycopg.connect(
    host="127.0.0.1", port=55432, user="trader", password=admin, dbname="observability"
) as db:
    exists = db.execute("SELECT 1 FROM pg_roles WHERE rolname='dashboard_reader'").fetchone()
    if exists:
        raise SystemExit("dashboard_reader already exists; configure its existing credentials manually.")
    db.execute(sql.SQL("CREATE ROLE dashboard_reader LOGIN PASSWORD {}").format(sql.Literal(password)))
    db.execute("GRANT CONNECT ON DATABASE observability TO dashboard_reader")
    db.execute("GRANT USAGE ON SCHEMA public TO dashboard_reader")
    db.execute("GRANT SELECT ON telemetry_events TO dashboard_reader")
    db.execute("ALTER ROLE dashboard_reader SET default_transaction_read_only = on")
with os.fdopen(os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
    output.write(
        "DATABASE_URL=postgresql://dashboard_reader:"
        + password
        + "@127.0.0.1:55432/observability\n"
        + "DASHBOARD_PASSWORD="
        + secrets.token_urlsafe(32)
        + "\nDATABASE_SSL=false\n"
    )
env.chmod(0o600)
print("Dashboard read-only login and private environment configured. No credentials displayed.")
