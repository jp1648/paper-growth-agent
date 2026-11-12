"""Bridge a single research role's LangGraph checkpoint to its operation journal."""

import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from .research_journal import AmbiguousOperation, JournalError
from .research_transport import ACTIVE_EXCHANGE


class PausedOperation(AmbiguousOperation):
    """A previous external call may have happened; reconciliation is required."""


class ResearchCancelled(BaseException):
    """Stop between operations without letting a tool turn cancellation into a result."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def model_identity(pre_projection_payload, *, occurrence=None):
    """Identify a model node from original messages, tools and output schema.

    The caller must supply the pre-projection, JSON-compatible request, rather
    than the shortened provider payload. ``occurrence`` may carry a stable
    LangGraph node/step identifier when identical original requests are
    intentionally made more than once. Replayed nodes keep the same key.
    """
    return hashlib.sha256(_canonical([pre_projection_payload, occurrence])).hexdigest()


class ResearchCheckpoint:
    """Persist graph progress separately from external operation responses.

    ``journal.lease()`` must cover ``attach_desk`` and the full ``invoke``.
    The caller compiles its graph using ``saver`` and calls ``close`` afterward.
    """

    model_identity = staticmethod(model_identity)

    def __init__(
        self,
        journal,
        checkpoint_path: str | Path,
        *,
        phase: str,
        role: str,
        thread_id: str,
        stop_event=None,
        before_dispatch=None,
    ):
        if not phase or not role or not thread_id:
            raise ValueError("Phase, role and thread ID must be nonempty")
        self.journal = journal
        self.phase = phase
        self.role = role
        self.stop_event = stop_event
        self.before_dispatch = before_dispatch
        self.thread_id = _canonical([phase, role, thread_id]).decode()
        self.path = Path(checkpoint_path)
        if self.path == journal.path:
            raise ValueError("Graph checkpoint must use a separate database")
        if self.path.parent != journal.path.parent:
            raise ValueError("Graph checkpoint must share the private journal directory")
        if stat.S_IMODE(self.path.parent.stat().st_mode) & 0o077:
            raise JournalError("Graph checkpoint directory must be private (0700)")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if stat.S_IMODE(os.fstat(fd).st_mode) & 0o077:
                raise JournalError("Graph checkpoint file must be private (0600)")
        finally:
            os.close(fd)
        self.connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.saver = SqliteSaver(self.connection, serde=journal._serde)
        self.desk = None
        self._active_operation = None

    def close(self):
        self.connection.close()

    def attach_desk(self, desk, *, restore=True):
        """Attach a desk; restore only once at run startup, before new work."""
        with self.journal.lock:
            self.desk = desk
            saved = self.journal.load_state() if restore else None
            if saved is not None:
                desk.restore(saved)

    def _config(self, config):
        result = {**(config or {})}
        options = dict(result.get("configurable") or {})
        supplied = options.get("thread_id")
        if supplied is not None and supplied != self.thread_id:
            raise ValueError("Graph thread ID differs from research role checkpoint")
        options["thread_id"] = self.thread_id
        result["configurable"] = options
        return result

    def invoke(self, agent, state, config=None, *, resume=True):
        """Resume a persisted node from its checkpoint, else start the role."""
        self.journal.load_state()  # Require the run lease before graph execution.
        resolved = self._config(config)
        snapshot = agent.get_state(resolved)
        existing = bool((snapshot.config or {}).get("configurable", {}).get("checkpoint_id"))
        return agent.invoke(None if existing and resume else state, resolved)

    def call(self, kind, role, identity, payload, handler, *, preflight=None):
        """Replay a completed call, or pass pure local preflight before journaling."""
        if self.desk is None:
            raise JournalError("Attach the research desk before dispatch")
        if not kind or not role or not identity:
            raise ValueError("Operation kind, role and identity must be nonempty")
        request_hash = hashlib.sha256(_canonical(payload)).hexdigest()
        key = _canonical([self.phase, kind, role, identity]).decode()
        with self.journal.lock:
            if self.stop_event is not None and self.stop_event.is_set():
                raise ResearchCancelled("Research activity cancelled before dispatch")
            if self.before_dispatch is not None:
                self.before_dispatch()
            replay_only = False
            try:
                cached = self.journal.begin(key, request_hash, self.desk.checkpoint(), preflight=preflight)
            except AmbiguousOperation as exc:
                if kind != "model" or not self.journal.replayable_exchange(
                    key, request_hash, self.desk.checkpoint()
                ):
                    raise PausedOperation(str(exc)) from exc
                cached, replay_only = None, True
            if cached is not None:
                # The latest snapshot was restored at startup. An older cached
                # operation must never roll it back.
                return cached["result"]
            self._active_operation = (key, request_hash)
            token = ACTIVE_EXCHANGE.set((self.journal, key, request_hash, replay_only))
            try:
                result = handler()
            finally:
                ACTIVE_EXCHANGE.reset(token)
                self._active_operation = None
            self.journal.complete(key, request_hash, result, self.desk.checkpoint())
            return result

    def record_dispatch(self, projected_request):
        if self._active_operation is None:
            raise JournalError("Provider dispatch is outside a journaled operation")
        return self.journal.record_dispatch(*self._active_operation, projected_request)
