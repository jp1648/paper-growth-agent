from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
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

    def backup(self):
        """Publish a consistent SQLite backup once per UTC day, including WAL transactions."""
        directory = self.path.parent / "backups"
        directory.mkdir(mode=0o700, exist_ok=True)
        destination = directory / ("ledger-" + now().date().isoformat() + ".sqlite")
        if destination.exists():
            return destination
        fd, name = tempfile.mkstemp(prefix=".backup-", suffix=".sqlite", dir=directory)
        os.close(fd)
        temporary = Path(name)
        try:
            with sqlite3.connect(temporary) as target:
                self.db.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
            os.replace(temporary, destination)
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
        if old:
            if dec(order["filled_qty"]) < dec(old["filled_qty"]):
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
