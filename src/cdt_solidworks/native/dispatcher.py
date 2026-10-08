"""Serialized native calls with atomic quarantine and bounded recovery retention."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from queue import Queue
import threading
import time
import uuid
from typing import Callable, Generic, TypeVar

from .errors import failure_from_exception
from .models import NativeCallResult, NativeCallState, NativeFailure

T = TypeVar("T")
_SENTINEL = object()


@dataclass(frozen=True)
class RecoveryPlan:
    """Provider-owned verifier captured before dispatch, never supplied by MCP callers."""

    identity: tuple[object, ...]
    stage: str
    verifier: Callable[[], object]


@dataclass
class _Job(Generic[T]):
    call_id: str
    stage: str
    operation: Callable[[], T] | None
    mutation: bool
    recovery: RecoveryPlan | None = None
    completed: threading.Event = field(default_factory=threading.Event)
    started: bool = False
    cancelled: bool = False
    timed_out_after_dispatch: bool = False
    result: NativeCallResult[T] | None = None
    admitted_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None


class SerializedNativeDispatcher:
    """One STA worker; all lifecycle transitions share one lock, never held across COM.

    Capacity counts running and queued calls (default 64). Completed history retains
    only IDs (default 128); one unresolved mutation is pinned separately. Native
    operations/verifiers and exception frames are released on the worker.
    """

    def __init__(self, initializer=None, finalizer=None, *, max_outstanding=64, history_limit=128):
        if isinstance(max_outstanding, bool) or not isinstance(max_outstanding, int) or max_outstanding < 1:
            raise ValueError("max_outstanding must be a positive integer")
        if isinstance(history_limit, bool) or not isinstance(history_limit, int) or history_limit < 0:
            raise ValueError("history_limit must be a non-negative integer")
        self._initializer = initializer
        self._finalizer = finalizer
        self._queue: Queue[object] = Queue()
        self._jobs: dict[str, _Job[object]] = {}
        self._history: deque[str] = deque(maxlen=history_limit)
        self._jobs_lock = threading.Lock()
        self._max_outstanding = max_outstanding
        self._outstanding = 0
        self._quarantine_call_id: str | None = None
        self._ready = threading.Event()
        self._startup_failure: NativeFailure | None = None
        self._closed = False
        self._completed_count = 0
        self._queue_wait_seconds = 0.0
        self._operation_seconds = 0.0
        self._thread = threading.Thread(target=self._worker, name="cdt-solidworks-native", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

    @property
    def quarantined_call_id(self):
        with self._jobs_lock:
            return self._quarantine_call_id

    @property
    def metrics(self):
        with self._jobs_lock:
            return {"outstanding": self._outstanding, "max_outstanding": self._max_outstanding,
                    "history_count": len(self._history), "history_limit": self._history.maxlen,
                    "completed_count": self._completed_count,
                    "queue_wait_seconds": self._queue_wait_seconds,
                    "operation_seconds": self._operation_seconds,
                    "uncertain_call_id": self._quarantine_call_id}

    @staticmethod
    def _failure(call_id, stage, code, message, *, details=None):
        return NativeCallResult.failed(
            NativeFailure(code, stage, message, details=details or {}),
            call_id=call_id, dispatched=False)

    def _blocked(self, call_id, stage):
        return self._failure(call_id, stage, "uncertain_state",
                             "A prior mutation must be reconciled before another mutation.",
                             details={"call_id": self._quarantine_call_id})

    def run(self, operation, *, stage, timeout, mutation=False, recovery=None):
        call_id = uuid.uuid4().hex
        with self._jobs_lock:
            if self._closed:
                return self._failure(call_id, stage, "dispatcher_closed", "Native dispatcher is closed.")
            if self._startup_failure is not None:
                return NativeCallResult.failed(replace(self._startup_failure, stage=stage),
                                               call_id=call_id, dispatched=False)
            if mutation and self._quarantine_call_id:
                return self._blocked(call_id, stage)
            if self._outstanding >= self._max_outstanding:
                return self._failure(call_id, stage, "native_overloaded", "Native outstanding call limit reached.")
            job = _Job(call_id, stage, operation, mutation, recovery)
            self._jobs[call_id] = job
            self._outstanding += 1
            # Unbounded physical queue reserves shutdown signaling. Admission is bounded here.
            self._queue.put(job)
        if job.completed.wait(max(0.0, timeout)):
            return job.result
        with self._jobs_lock:
            if job.completed.is_set():
                return job.result
            if not job.started:
                job.cancelled = True
                return NativeCallResult(
                    NativeCallState.TIMEOUT_BEFORE_DISPATCH, call_id,
                    failure=NativeFailure("timeout_before_dispatch", stage,
                                          "Native call was not dispatched.", retryable=True),
                    dispatched=False)
            job.timed_out_after_dispatch = True
            if mutation:
                self._quarantine_call_id = call_id
            return NativeCallResult(
                NativeCallState.UNCERTAIN_AFTER_DISPATCH, call_id,
                failure=NativeFailure("uncertain_state", stage,
                                      "Native call timed out after dispatch; do not retry the mutation."),
                dispatched=True)

    def is_complete(self, call_id):
        with self._jobs_lock:
            job = self._jobs.get(call_id)
            return bool((job and job.completed.is_set()) or call_id in self._history)

    def reconcile(self, call_id, verifier=None, *, stage, timeout, identity=None):
        # Legacy caller callbacks are deliberately never executed. Recovery belongs
        # to the original mutation's provider-owned plan.
        with self._jobs_lock:
            job = self._jobs.get(call_id)
            if job is None or self._quarantine_call_id != call_id or not job.timed_out_after_dispatch:
                return self._failure(call_id, stage, "unknown_call", "No pending uncertain mutation for this call.")
            if not job.completed.is_set():
                return NativeCallResult(NativeCallState.UNCERTAIN_AFTER_DISPATCH, call_id,
                    failure=NativeFailure("uncertain_state", stage, "Original native call is still running."),
                    dispatched=True)
            plan = job.recovery
            if plan is None:
                return self._failure(call_id, stage, "recovery_unsupported",
                                     "This operation has no registered native recovery verifier.")
            if identity != plan.identity or stage != plan.stage:
                return self._failure(call_id, stage, "reconciliation_mismatch",
                                     "Recovery operation or target does not match the original mutation.")

        def verify_original():
            with self._jobs_lock:
                if self._quarantine_call_id != call_id:
                    raise RuntimeError("Recovery is no longer pending")
            return plan.verifier()

        result = self.run(verify_original, stage=stage, timeout=timeout)
        if result.state is NativeCallState.SUCCESS:
            with self._jobs_lock:
                if self._quarantine_call_id != call_id:
                    return self._failure(call_id, stage, "unknown_call", "Recovery is no longer pending.")
                self._quarantine_call_id = None
                self._jobs.pop(call_id, None)
                job.recovery = None
        return replace(result, call_id=call_id)

    def close(self, *, timeout=2.0):
        with self._jobs_lock:
            if not self._closed:
                self._closed = True
                self._queue.put(_SENTINEL)
        self._thread.join(max(0.0, timeout))
        return not self._thread.is_alive()

    def _finish(self, job):
        self._outstanding -= 1
        self._completed_count += 1
        self._history.append(job.call_id)
        if self._quarantine_call_id != job.call_id:
            self._jobs.pop(job.call_id, None)
            job.recovery = None
        if job.timed_out_after_dispatch:
            job.result = None
        job.operation = None
        job.completed.set()

    def _worker(self):
        try:
            if self._initializer is not None:
                self._initializer()
        except Exception as exc:
            with self._jobs_lock:
                self._startup_failure = failure_from_exception(exc, "initialize")
            self._ready.set()
            return
        self._ready.set()
        try:
            while True:
                job = self._queue.get()
                if job is _SENTINEL:
                    return
                with self._jobs_lock:
                    if job.cancelled:
                        job.result = NativeCallResult(NativeCallState.TIMEOUT_BEFORE_DISPATCH,
                                                       job.call_id, dispatched=False)
                        self._finish(job)
                        continue
                    if job.mutation and self._quarantine_call_id:
                        job.result = self._blocked(job.call_id, job.stage)
                        self._finish(job)
                        continue
                    job.started = True
                    job.started_at = time.monotonic()
                    self._queue_wait_seconds += job.started_at - job.admitted_at
                try:
                    value = job.operation()
                    result = NativeCallResult.success(value, call_id=job.call_id)
                except Exception as exc:
                    result = NativeCallResult.failed(failure_from_exception(exc, job.stage),
                                                     call_id=job.call_id, dispatched=True)
                with self._jobs_lock:
                    self._operation_seconds += time.monotonic() - job.started_at
                    job.result = result
                    self._finish(job)
                # Release worker-local references before blocking on the next queue item.
                value = result = job = None
        finally:
            if self._finalizer is not None:
                try:
                    self._finalizer()
                except Exception:
                    pass
