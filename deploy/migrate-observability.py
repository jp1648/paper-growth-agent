"""Copy telemetry into an initialized remote Postgres; never change local config/history."""

import argparse
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from trader.observability import SCHEMA


def copy_events(source, target):
    copied = 0
    target.execute("CREATE TEMP TABLE migration_batch (LIKE public.telemetry_events)")
    try:
        with source.transaction():
            source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            with source.cursor(name="telemetry_migration") as cursor:
                # Sorting large JSON rows wastes source temp disk; snapshot isolation is sufficient.
                cursor.execute("SELECT * FROM public.telemetry_events")
                while rows := cursor.fetchmany(2000):
                    with target.transaction():
                        target.execute("TRUNCATE migration_batch")
                        with target.cursor().copy("COPY migration_batch FROM STDIN") as batch:
                            for row in rows:
                                batch.write_row((*row[:-1], Jsonb(row[-1])))
                        target.execute(
                            "INSERT INTO public.telemetry_events SELECT s.* FROM migration_batch s "
                            "WHERE NOT EXISTS (SELECT 1 FROM public.telemetry_events t "
                            "WHERE t.event_id=s.event_id) "
                            "ON CONFLICT (event_id) DO NOTHING"
                        )
                        mismatch = target.execute(
                            "SELECT EXISTS (SELECT 1 FROM migration_batch s "
                            "LEFT JOIN public.telemetry_events t USING(event_id) "
                            "WHERE s IS DISTINCT FROM t)"
                        ).fetchone()[0]
                        if mismatch:
                            raise ValueError("Migration verification failed; existing event differs")
                    copied += len(rows)
                    print(f"Verified {copied} source events", flush=True)
    finally:
        target.execute("DROP TABLE migration_batch")
    return copied


def validate_transport(options, root, cloud_sql_proxy=False):
    if cloud_sql_proxy:
        socket = Path(options.get("host", ""))
        if (
            not socket.is_absolute()
            or not socket.resolve().is_relative_to(root / "state/cloudsql")
            or options.get("hostaddr")
        ):
            raise ValueError("Cloud SQL proxy must use the private state/cloudsql Unix socket")
    elif options.get("sslmode") != "verify-full":
        raise ValueError("Remote migration requires sslmode=verify-full and a trusted root certificate")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-dsn-file",
        type=Path,
        required=True,
        help="Private file containing the target admin DSN",
    )
    parser.add_argument(
        "--cloud-sql-proxy", action="store_true", help="Use an authenticated local Unix-socket proxy"
    )
    args = parser.parse_args()
    target_dsn = args.target_dsn_file.read_text().strip()
    options = conninfo_to_dict(target_dsn)
    root = Path(__file__).resolve().parents[1]
    validate_transport(options, root, args.cloud_sql_proxy)
    password = (root / "state/observability/postgres-password").read_text().strip()
    with (
        psycopg.connect(
            host="127.0.0.1",
            port=55432,
            user="trader",
            password=password,
            dbname="observability",
            autocommit=True,
        ) as source,
        psycopg.connect(target_dsn, autocommit=True, connect_timeout=10) as target,
    ):
        with target.transaction():
            target.execute(SCHEMA)
            target.execute((root / "deploy/supabase-observability.sql").read_text())
        count = copy_events(source, target)
    print(f"Verified {count} telemetry events. Local database and connection settings unchanged.")


if __name__ == "__main__":
    try:
        main()
    except (psycopg.Error, OSError, ValueError) as error:
        # Connection exceptions may contain credentials or connection-string fragments.
        raise SystemExit(
            f"Migration stopped ({type(error).__name__}). Local settings remain unchanged."
        ) from None
