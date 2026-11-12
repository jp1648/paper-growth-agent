"""Local, fail-closed journal for a single resumable research run.

The caller holds ``lease()`` across the entire run, including external calls.
An operation left pending after a crash needs manual reconciliation; the
journal never assumes that repeating an external call is safe.
"""

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


class JournalError(RuntimeError):
    """The journal cannot safely continue."""


class JournalBusy(JournalError):
    """Another process holds the run lease."""


class BindingMismatch(JournalError):
    """The journal belongs to a different code, config, or experiment."""


class RequestHashMismatch(JournalError):
    """An operation key was reused for a different request."""


class AmbiguousOperation(JournalError):
    """An operation began but has no committed result."""


class ResolutionRejected(JournalError):
    """The supplied evidence did not prove definitive non-dispatch."""


def _same_state_except_elapsed(previous, current):
    """Permit only forward elapsed time between proof and its one retry."""
    import copy

    old, new = copy.deepcopy(previous), copy.deepcopy(current)
    for value in (old, new):
        budget = value.get("budget", value.get("acquisition_budget")) if isinstance(value, dict) else None
        if not isinstance(budget, dict) or "elapsed_seconds" not in budget:
            return previous == current
    old_budget = old.get("budget", old.get("acquisition_budget"))
    new_budget = new.get("budget", new.get("acquisition_budget"))
    old_elapsed, new_elapsed = old_budget.pop("elapsed_seconds"), new_budget.pop("elapsed_seconds")
    return (
        isinstance(old_elapsed, (int, float))
        and isinstance(new_elapsed, (int, float))
        and new_elapsed >= old_elapsed
        and old == new
    )


class Journal:
    """SQLite operation log with a single process lease and typed payloads."""

    def __init__(self, path: str | Path, binding: dict, *, serializer=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if stat.S_IMODE(self.path.parent.stat().st_mode) & 0o077:
            raise JournalError("Research journal directory must be private (0700)")
        self.binding = json.dumps(binding, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self._lock = threading.RLock()
        self.lock = self._lock
        self._lease_file = None
        self._db = None
        self._serde = serializer or JsonPlusSerializer(
            pickle_fallback=False,
            allowed_msgpack_modules=[
                ("langchain.agents.middleware.types", "ModelResponse"),
                ("trader.research_team", "Research"),
                ("trader.research_team", "Catalysts"),
                ("trader.research_team", "Valuation"),
                ("trader.research_team", "Critique"),
                ("trader.models", "Decision"),
            ],
        )

    @contextmanager
    def lease(self):
        """Hold exclusive ownership until the caller has stopped all work."""
        lock_path = str(self.path) + ".lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(lock_fd, "a+b") as lock:
            if stat.S_IMODE(os.fstat(lock.fileno()).st_mode) & 0o077:
                raise JournalError("Research journal lease file must be private (0600)")
            with self._lock:
                if self._lease_file is not None:
                    raise JournalBusy("Journal lease is already held by this instance")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise JournalBusy("Research run is already active") from exc
                self._lease_file = lock
                try:
                    self._open()
                except BaseException:
                    self._lease_file = None
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    raise
            try:
                yield self
            finally:
                with self._lock:
                    if self._db is not None:
                        self._db.close()
                        self._db = None
                    self._lease_file = None
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def _open(self):
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if stat.S_IMODE(os.fstat(fd).st_mode) & 0o077:
                raise JournalError("Research journal file must be private (0600)")
        finally:
            os.close(fd)
        db = sqlite3.connect(self.path, isolation_level=None, timeout=0, check_same_thread=False)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS identity (id INTEGER PRIMARY KEY CHECK(id=1), binding TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY CHECK(id=1), type TEXT NOT NULL, payload BLOB NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','complete')), result_type TEXT, result BLOB, state_type TEXT NOT NULL, state BLOB NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS non_dispatch_resolutions ("
                "key TEXT NOT NULL, attempt INTEGER NOT NULL, request_hash TEXT NOT NULL, "
                "evidence TEXT NOT NULL, evidence_hash TEXT NOT NULL, "
                "prior_state_type TEXT NOT NULL, prior_state BLOB NOT NULL, "
                "resolved_at TEXT NOT NULL, used_at TEXT, PRIMARY KEY(key,attempt))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS provider_dispatches ("
                "key TEXT NOT NULL, attempt INTEGER NOT NULL, request_hash TEXT NOT NULL, "
                "client_request_id TEXT NOT NULL UNIQUE, payload_type TEXT NOT NULL, "
                "payload BLOB NOT NULL, payload_hash TEXT NOT NULL, PRIMARY KEY(key,attempt))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS provider_exchanges (client_request_id TEXT PRIMARY KEY, "
                "request_type TEXT NOT NULL, request BLOB NOT NULL, response_type TEXT, response BLOB)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS accounting_close ("
                "id INTEGER PRIMARY KEY CHECK(id=1), receipt TEXT NOT NULL)"
            )
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT binding FROM identity WHERE id=1").fetchone()
            if row is None:
                db.execute("INSERT INTO identity(id,binding) VALUES (1,?)", (self.binding,))
            elif row[0] != self.binding:
                raise BindingMismatch("Research journal binding differs")
            db.execute("COMMIT")
        except BaseException:
            db.rollback()
            db.close()
            raise
        self._db = db

    def _connection(self):
        if self._db is None or self._lease_file is None:
            raise JournalError("Hold the journal lease before accessing it")
        return self._db

    @contextmanager
    def _transaction(self, db):
        db.execute("BEGIN IMMEDIATE")
        try:
            yield
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def _encode(self, value):
        kind, payload = self._serde.dumps_typed(value)
        return kind, payload

    def _decode(self, kind, payload):
        return self._serde.loads_typed((kind, payload))

    def _save_snapshot(self, db, kind, payload):
        db.execute(
            "INSERT INTO snapshot(id,type,payload) VALUES (1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET type=excluded.type,payload=excluded.payload",
            (kind, payload),
        )

    def load_state(self):
        with self._lock:
            row = self._connection().execute("SELECT type,payload FROM snapshot WHERE id=1").fetchone()
            return None if row is None else self._decode(*row)

    def record_dispatch(self, key, request_hash, projected_request):
        """Persist correlation before sending; this ID is not an idempotency guarantee."""
        with self._lock:
            db = self._connection()
            kind, payload = self._encode(projected_request)
            payload_hash = hashlib.sha256(kind.encode() + b"\0" + payload).hexdigest()
            with self._transaction(db):
                row = db.execute("SELECT request_hash,status FROM operations WHERE key=?", (key,)).fetchone()
                if row != (request_hash, "pending"):
                    raise JournalError("Provider dispatch requires its matching pending operation")
                attempt = db.execute(
                    "SELECT count(*) FROM non_dispatch_resolutions WHERE key=? AND used_at IS NOT NULL",
                    (key,),
                ).fetchone()[0]
                old = db.execute(
                    "SELECT client_request_id,payload_hash FROM provider_dispatches WHERE key=? AND attempt=?",
                    (key, attempt),
                ).fetchone()
                if old:
                    if old[1] != payload_hash:
                        raise RequestHashMismatch("Projected provider request changed")
                    return old[0]
                identifier = str(uuid4())
                db.execute(
                    "INSERT INTO provider_dispatches VALUES (?,?,?,?,?,?,?)",
                    (key, attempt, request_hash, identifier, kind, payload, payload_hash),
                )
                return identifier

    def replayable_exchange(self, key, request_hash, state):
        with self._lock:
            db = self._connection()
            row = db.execute(
                "SELECT request_hash,status,state_type,state FROM operations WHERE key=?", (key,)
            ).fetchone()
            if not row or row[:2] != (request_hash, "pending"):
                return False
            response = db.execute(
                "SELECT e.response FROM provider_dispatches d LEFT JOIN provider_exchanges e "
                "ON e.client_request_id=d.client_request_id WHERE d.key=? ORDER BY d.attempt DESC LIMIT 1",
                (key,),
            ).fetchone()
            return bool(
                response
                and response[0] is not None
                and _same_state_except_elapsed(self._decode(*row[2:]), state)
            )

    def exchange(self, key, request_hash, identifier, request, send, *, replay_only=False):
        """Save wire bytes before parsing; an unacknowledged send remains ambiguous."""
        with self._lock:
            db = self._connection()
            dispatch = db.execute(
                "SELECT client_request_id,request_hash FROM provider_dispatches WHERE key=? ORDER BY attempt DESC LIMIT 1",
                (key,),
            ).fetchone()
            if dispatch != (identifier, request_hash):
                raise RequestHashMismatch("HTTP request differs from the active provider attempt")
            kind, payload = self._encode(request)
            row = db.execute(
                "SELECT request_type,request,response_type,response FROM provider_exchanges WHERE client_request_id=?",
                (identifier,),
            ).fetchone()
            if row:
                if row[:2] != (kind, payload):
                    raise RequestHashMismatch("Exact provider request changed")
                if row[3] is None:
                    raise AmbiguousOperation("Provider HTTP response was not saved")
                return self._decode(*row[2:])
            if replay_only:
                raise AmbiguousOperation("Recovery cannot send a new provider request")
            with self._transaction(db):
                db.execute(
                    "INSERT INTO provider_exchanges VALUES (?,?,?,NULL,NULL)", (identifier, kind, payload)
                )
            response = send()
            response_type, response_payload = self._encode(response)
            with self._transaction(db):
                db.execute(
                    "UPDATE provider_exchanges SET response_type=?,response=? WHERE client_request_id=?",
                    (response_type, response_payload, identifier),
                )
            return response

    def inspect_operations(self):
        """Expose reconciliation identities without private prompts, sources or responses."""
        with self._lock:
            db = self._connection()
            pending = db.execute(
                "SELECT key,request_hash,state_type,state FROM operations WHERE status='pending' ORDER BY key"
            ).fetchall()
            return {
                "complete_operations": db.execute(
                    "SELECT count(*) FROM operations WHERE status='complete'"
                ).fetchone()[0],
                "pending_operations": [
                    {
                        "key": key,
                        "request_hash": request_hash,
                        "saved_pre_dispatch_state_sha256": hashlib.sha256(
                            kind.encode() + b"\0" + payload
                        ).hexdigest(),
                        "dispatches": [
                            {
                                "attempt": attempt,
                                "client_request_id": identifier,
                                "projected_request_sha256": digest,
                            }
                            for attempt, identifier, digest in db.execute(
                                "SELECT attempt,client_request_id,payload_hash FROM provider_dispatches WHERE key=? ORDER BY attempt",
                                (key,),
                            )
                        ],
                    }
                    for key, request_hash, kind, payload in pending
                ],
            }

    def accounting_evidence(self):
        """Return private pending model exchange evidence under the run lease."""
        with self._lock:
            db = self._connection()
            pending = db.execute(
                "SELECT key,request_hash,state_type,state FROM operations WHERE status='pending'"
            ).fetchall()
            if not pending:
                no_activity = all(
                    db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
                    for table in (
                        "operations",
                        "provider_dispatches",
                        "provider_exchanges",
                        "non_dispatch_resolutions",
                    )
                )
                return {
                    "key": None,
                    "request_hash": None,
                    "prestate": self.load_state(),
                    "exchanges": [],
                    "prior_attempts_proven_non_dispatch": True,
                    "no_activity": no_activity,
                }
            if len(pending) != 1:
                raise JournalError("Accounting close requires at most one pending operation")
            key, request_hash, state_type, state_payload = pending[0]
            dispatches = db.execute(
                "SELECT attempt,request_hash,client_request_id,payload_type,payload,payload_hash "
                "FROM provider_dispatches WHERE key=? ORDER BY attempt",
                (key,),
            ).fetchall()
            exchanges = []
            for attempt, dispatch_hash, identifier, payload_type, payload, digest in dispatches:
                if hashlib.sha256(payload_type.encode() + b"\0" + payload).hexdigest() != digest:
                    raise JournalError("Projected provider request hash changed")
                row = db.execute(
                    "SELECT request_type,request,response_type,response FROM provider_exchanges "
                    "WHERE client_request_id=?",
                    (identifier,),
                ).fetchone()
                exchanges.append(
                    {
                        "attempt": attempt,
                        "request_hash": dispatch_hash,
                        "client_request_id": identifier,
                        "projected_request": self._decode(payload_type, payload),
                        "projected_request_sha256": digest,
                        "wire_request": self._decode(*row[:2]) if row else None,
                        "wire_response": self._decode(*row[2:]) if row and row[3] is not None else None,
                    }
                )
            return {
                "key": key,
                "request_hash": request_hash,
                "prestate": self._decode(state_type, state_payload),
                "exchanges": exchanges,
                "prior_attempts_proven_non_dispatch": all(
                    db.execute(
                        "SELECT 1 FROM non_dispatch_resolutions WHERE key=? AND attempt=? "
                        "AND request_hash=? AND used_at IS NOT NULL",
                        (key, attempt + 1, request_hash),
                    ).fetchone()
                    for attempt in range(max(0, len(dispatches) - 1))
                ),
            }

    def accounting_receipt(self):
        with self._lock:
            row = self._connection().execute("SELECT receipt FROM accounting_close WHERE id=1").fetchone()
            return json.loads(row[0]) if row else None

    def record_accounting_receipt(self, receipt):
        """Append one immutable offline accounting decision; never alter operations."""
        from .models import dumps

        canonical = dumps(receipt)
        with self._lock:
            db = self._connection()
            with self._transaction(db):
                row = db.execute("SELECT receipt FROM accounting_close WHERE id=1").fetchone()
                if row:
                    if row[0] != canonical:
                        raise JournalError("Accounting receipt conflicts with prior close")
                    return json.loads(row[0])
                db.execute("INSERT INTO accounting_close(id,receipt) VALUES (1,?)", (canonical,))
                return json.loads(canonical)

    def save_state(self, state):
        with self._lock:
            db = self._connection()
            kind, payload = self._encode(state)
            with self._transaction(db):
                if db.execute("SELECT 1 FROM operations WHERE status='pending' LIMIT 1").fetchone():
                    raise AmbiguousOperation("Resolve the pending operation before saving state")
                self._save_snapshot(db, kind, payload)

    @staticmethod
    def _has_provider_response(db, key):
        return (
            db.execute(
                "SELECT 1 FROM provider_dispatches d JOIN provider_exchanges e "
                "ON e.client_request_id=d.client_request_id WHERE d.key=? AND e.response IS NOT NULL LIMIT 1",
                (key,),
            ).fetchone()
            is not None
        )

    def begin(self, key: str, request_hash: str, state, *, preflight=None):
        """Return a committed result and state or record a pending call.

        ``None`` means this is a new operation and the caller may invoke it.
        A pending operation is never retried automatically, even on reopen.
        """
        if not key or not request_hash:
            raise ValueError("Operation key and request hash must be nonempty")
        with self._lock:
            db = self._connection()
            kind, payload = self._encode(state)
            with self._transaction(db):
                row = db.execute(
                    "SELECT request_hash,status,result_type,result,state_type,state FROM operations WHERE key=?",
                    (key,),
                ).fetchone()
                if row is not None:
                    if row[0] != request_hash:
                        raise RequestHashMismatch("Operation key has a different request hash")
                    if row[1] == "pending":
                        if self._has_provider_response(db, key):
                            raise AmbiguousOperation(
                                "Saved provider response must be replayed, never retried"
                            )
                        approval = db.execute(
                            "SELECT attempt FROM non_dispatch_resolutions "
                            "WHERE key=? AND request_hash=? AND used_at IS NULL ORDER BY attempt DESC LIMIT 1",
                            (key, request_hash),
                        ).fetchone()
                        if approval is None:
                            raise AmbiguousOperation("Operation has no committed response")
                        old_state = self._decode(row[4], row[5])
                        if not _same_state_except_elapsed(old_state, state):
                            raise ResolutionRejected("Retry state differs from proven non-dispatch state")
                        if preflight is not None:
                            preflight()
                        db.execute(
                            "UPDATE non_dispatch_resolutions SET used_at=? WHERE key=? AND attempt=? AND used_at IS NULL",
                            (datetime.now(UTC).isoformat(), key, approval[0]),
                        )
                        db.execute(
                            "UPDATE operations SET state_type=?,state=? WHERE key=? AND status='pending'",
                            (kind, payload, key),
                        )
                        self._save_snapshot(db, kind, payload)
                        return None
                    return {
                        "result": self._decode(row[2], row[3]),
                        "state": self._decode(row[4], row[5]),
                    }
                if db.execute("SELECT 1 FROM operations WHERE status='pending' LIMIT 1").fetchone():
                    raise AmbiguousOperation("Another operation has no committed response")
                if preflight is not None:
                    preflight()
                db.execute(
                    "INSERT INTO operations(key,request_hash,status,state_type,state) VALUES (?,?, 'pending',?,?)",
                    (key, request_hash, kind, payload),
                )
                self._save_snapshot(db, kind, payload)
            return None

    def resolve_non_dispatch(self, key, request_hash, evidence, *, verifier):
        """Authorize one retry after independently verified definitive non-dispatch.

        ``verifier`` must inspect external evidence for this exact operation;
        production callers must not use an unconditional approval callback.
        A completed external call cannot be recovered through this method.
        """
        if not isinstance(evidence, dict) or set(evidence) != {"operator", "reference", "basis"}:
            raise ResolutionRejected("Require operator, proof reference and non-dispatch basis")
        if any(not isinstance(v, str) or not v.strip() for v in evidence.values()):
            raise ResolutionRejected("Non-dispatch evidence fields must be nonempty text")
        evidence_text = json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(evidence_text.encode()) > 2048:
            raise ResolutionRejected("Non-dispatch evidence is too large")
        with self._lock:
            db = self._connection()
            row = db.execute(
                "SELECT request_hash,status,state_type,state FROM operations WHERE key=?", (key,)
            ).fetchone()
            if row is None or row[1] != "pending":
                raise ResolutionRejected("Only a pending operation can be resolved")
            if row[0] != request_hash:
                raise RequestHashMismatch("Operation key has a different request hash")
            if self._has_provider_response(db, key):
                raise ResolutionRejected("Saved provider response disproves non-dispatch")
            if verifier(key, request_hash, evidence) is not True:
                raise ResolutionRejected("Independent proof did not establish non-dispatch")
            with self._transaction(db):
                if self._has_provider_response(db, key):
                    raise ResolutionRejected("Provider response appeared during verification")
                if db.execute(
                    "SELECT 1 FROM non_dispatch_resolutions WHERE key=? AND used_at IS NULL LIMIT 1",
                    (key,),
                ).fetchone():
                    raise ResolutionRejected("A retry is already authorized")
                current = db.execute(
                    "SELECT request_hash,status,state_type,state FROM operations WHERE key=?", (key,)
                ).fetchone()
                snapshot = db.execute("SELECT type,payload FROM snapshot WHERE id=1").fetchone()
                if current != row or snapshot != (row[2], row[3]):
                    raise ResolutionRejected("Pending operation changed during verification")
                attempt = db.execute(
                    "SELECT coalesce(max(attempt),0)+1 FROM non_dispatch_resolutions WHERE key=?", (key,)
                ).fetchone()[0]
                proof_hash = hashlib.sha256(evidence_text.encode()).hexdigest()
                db.execute(
                    "INSERT INTO non_dispatch_resolutions "
                    "(key,attempt,request_hash,evidence,evidence_hash,prior_state_type,prior_state,resolved_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        key,
                        attempt,
                        request_hash,
                        evidence_text,
                        proof_hash,
                        row[2],
                        row[3],
                        datetime.now(UTC).isoformat(),
                    ),
                )
            return {"key": key, "attempt": attempt, "evidence_hash": proof_hash}

    def complete(self, key: str, request_hash: str, result, state):
        """Commit the external response and new Desk/Budget state together."""
        with self._lock:
            db = self._connection()
            result_type, result_payload = self._encode(result)
            state_type, state_payload = self._encode(state)
            with self._transaction(db):
                row = db.execute("SELECT request_hash,status FROM operations WHERE key=?", (key,)).fetchone()
                if row is None:
                    raise JournalError("Operation was not begun")
                if row[0] != request_hash:
                    raise RequestHashMismatch("Operation key has a different request hash")
                if row[1] != "pending":
                    raise JournalError("Operation is already complete")
                db.execute(
                    "UPDATE operations SET status='complete',result_type=?,result=?,state_type=?,state=? WHERE key=?",
                    (result_type, result_payload, state_type, state_payload, key),
                )
                self._save_snapshot(db, state_type, state_payload)
