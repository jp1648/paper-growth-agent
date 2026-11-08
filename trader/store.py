from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
from contextlib import closing, contextmanager
from pathlib import Path

from .models import TERMINAL, dec, dt, dumps, now
from .observability import configure, emit


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.path) + suffix)
            if sidecar.exists():
                sidecar.chmod(0o600)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
          scope TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, body TEXT NOT NULL,
          PRIMARY KEY(scope,kind,key));
        CREATE TABLE IF NOT EXISTS journal (
          sequence INTEGER PRIMARY KEY, scope TEXT NOT NULL, at TEXT NOT NULL,
          kind TEXT NOT NULL, body TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS journal_scope_kind ON journal(scope,kind,sequence);
        """)
        self.db.commit()
        configure(self.path)
        self.experiment = self.get("active", scope="global")
        self._mutex = threading.RLock()
        self._depth = 0

    @property
    def scope(self):
        return self.experiment["id"] if self.experiment else "uninitialized"

    def get(self, key, kind="state", scope=None):
        row = self.db.execute(
            "SELECT body FROM documents WHERE scope=? AND kind=? AND key=?", (scope or self.scope, kind, key)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value, kind="state", scope=None):
        with self.db:
            self.db.execute(
                "INSERT INTO documents VALUES (?,?,?,?) ON CONFLICT(scope,kind,key) DO UPDATE SET body=excluded.body",
                (scope or self.scope, kind, key, dumps(value)),
            )

        emit("document." + kind, {"key": key, "value": value}, experiment_id=scope or self.scope)

    def items(self, kind):
        return [
            json.loads(r[0])
            for r in self.db.execute(
                "SELECT body FROM documents WHERE scope=? AND kind=? ORDER BY key", (self.scope, kind)
            )
        ]

    def approve_adjustments(self, adjustments):
        """Preflighted approvals and their audit receipts commit as one unit."""
        stamp = now().isoformat()
        with self.db:
            for item in adjustments:
                body = dumps(item)
                self.db.execute(
                    "INSERT INTO documents VALUES (?,?,?,?)",
                    (self.scope, "adjustment", item["id"], body),
                )
                self.db.execute(
                    "INSERT INTO journal(scope,at,kind,body) VALUES (?,?,?,?)",
                    (self.scope, stamp, "explicit_adjustment", body),
                )
        for item in adjustments:
            emit("document.adjustment", {"key": item["id"], "value": item}, experiment_id=self.scope)
            emit("explicit_adjustment", item, experiment_id=self.scope)

    def save_daily_report(self, sample, report):
        """Publish the measurement, report and audit receipt together."""
        date = sample["date"]
        receipt = {"date": date, "equity": report["close_equity"], "benchmarks": report["benchmarks"]}
        with self.db:
            self.db.execute(
                "INSERT INTO documents VALUES (?,?,?,?) ON CONFLICT(scope,kind,key) DO UPDATE SET body=excluded.body",
                (self.scope, "daily", date, dumps(sample)),
            )
            self.db.execute(
                "INSERT INTO documents VALUES (?,?,?,?)", (self.scope, "daily_report", date, dumps(report))
            )
            self.db.execute(
                "INSERT INTO journal(scope,at,kind,body) VALUES (?,?,?,?)",
                (self.scope, now().isoformat(), "daily_report", dumps(receipt)),
            )
        for kind, body in (("daily", sample), ("daily_report", report)):
            emit("document." + kind, {"key": date, "value": body}, experiment_id=self.scope)
        emit("daily_report", receipt, experiment_id=self.scope)

    def log(self, kind, body):
        with self.db:
            self.db.execute(
                "INSERT INTO journal(scope,at,kind,body) VALUES (?,?,?,?)",
                (self.scope, now().isoformat(), kind, dumps(body)),
            )

        emit(
            kind,
            body,
            experiment_id=self.scope,
            run_key=body.get("run_key") if isinstance(body, dict) else None,
        )

    def activities(self):
        """Effective broker activities; original records and approved revision chains remain intact."""
        result = []
        revisions = self.items("broker_revision")
        for original in self.items("activity"):
            current = original
            # Each approval names its exact predecessor, so unrelated/replayed approvals cannot apply.
            remaining = [r for r in revisions if "sequence" not in r]
            while True:
                matches = [r for r in remaining if r["before"] == current]
                if not matches:
                    break
                if len(matches) != 1:
                    raise RuntimeError("Conflicting broker activity revisions")
                current = matches[0]["after"]
                remaining.remove(matches[0])
            ordered = sorted(
                (r for r in revisions if "sequence" in r and r["before"]["id"] == original["id"]),
                key=lambda r: r["sequence"],
            )
            for revision in ordered:
                if revision["before"] != current:
                    raise RuntimeError("Broker revision predecessor mismatch")
                current = revision["after"]
            result.append(current)
        return result

    def backup(self, intent_id=None):
        """Publish a verified daily backup or immutable prepared-intent checkpoint."""
        intent = self.get(intent_id, "intent") if intent_id else None
        if intent_id and (not intent or intent.get("status") != "prepared"):
            raise RuntimeError("Execution checkpoint requires a prepared intent")
        directory = self.path.parent / "backups"
        directory.mkdir(mode=0o700, exist_ok=True)
        name = (
            "intent-" + hashlib.sha256(f"{self.scope}:{intent_id}".encode()).hexdigest()
            if intent_id
            else "ledger-" + now().date().isoformat()
        )
        destination = directory / (name + ".sqlite")

        def verify(path):
            if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
                raise RuntimeError("Backup must be a private regular file")
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as saved:
                if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
                row = saved.execute(
                    "SELECT body FROM documents WHERE scope='global' AND kind='state' AND key='active'"
                ).fetchone()
                experiment = json.loads(row[0]) if row else None
                if experiment != self.experiment:
                    raise RuntimeError("Backup belongs to a different experiment; preserve and investigate")
                if intent_id:
                    row = saved.execute(
                        "SELECT body FROM documents WHERE scope=? AND kind='intent' AND key=?",
                        (self.scope, intent_id),
                    ).fetchone()
                    if not row or json.loads(row[0]) != intent:
                        raise RuntimeError("Execution checkpoint does not match prepared intent")

        def sync_directory():
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

        if destination.exists() or destination.is_symlink():
            verify(destination)
            sync_directory()
            return destination
        fd, name = tempfile.mkstemp(prefix=".backup-", suffix=".sqlite", dir=directory)
        os.close(fd)
        temporary = Path(name)
        try:
            with closing(sqlite3.connect(temporary)) as target:
                self.db.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
                # Publish one closed database, not a file depending on temporary WAL sidecars.
                target.execute("PRAGMA journal_mode=DELETE")
            verify(temporary)
            with temporary.open("rb") as source:
                os.fsync(source.fileno())
            os.replace(temporary, destination)
            sync_directory()
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def events(self, kind=None):
        sql, args = "SELECT at,kind,body FROM journal WHERE scope=?", [self.scope]
        if kind:
            kinds = (kind,) if isinstance(kind, str) else tuple(kind)
            sql += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
            args.extend(kinds)
        return [
            {"at": at, "kind": k, "body": json.loads(b)}
            for at, k, b in self.db.execute(sql + " ORDER BY sequence", args)
        ]

    def order(self, order):
        old = self.get(order["client_order_id"], "order")
        corrected = old and any(
            revision.get("order_before") == old and revision.get("order_after") == order
            for revision in self.items("broker_revision")
        )
        if old:
            if not corrected and dec(order["filled_qty"]) < dec(old["filled_qty"]):
                return
            if dt(order["updated_at"] or order["created_at"]) < dt(old["updated_at"] or old["created_at"]):
                return
            if old["status"] in TERMINAL and order["status"] not in TERMINAL:
                return
        if old != order:
            self.put(order["client_order_id"], order, "order")
            self.log("order_update", order)

    def active_orders(self):
        return [o for o in self.items("order") if o["status"] not in TERMINAL]

    def control_path(self, control_dir, account, suffix):
        root = Path(control_dir).expanduser().resolve()
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        root.chmod(0o700)
        key = hashlib.sha256(account.encode()).hexdigest()
        return root / (key + suffix)

    @contextmanager
    def serialized(self, control_dir, account):
        with self._mutex:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            path = self.control_path(control_dir, account, ".lock")
            with path.open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self._depth = 1
                try:
                    yield
                finally:
                    self._depth = 0
                    fcntl.flock(lock, fcntl.LOCK_UN)
