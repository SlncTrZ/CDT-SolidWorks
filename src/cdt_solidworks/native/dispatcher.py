"""Single-thread native dispatcher with bounded timeout and uncertainty tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from queue import Queue
import threading
import uuid
from typing import Callable, Generic, TypeVar

from .errors import failure_from_exception
from .models import NativeCallResult, NativeCallState, NativeFailure


T = TypeVar("T")
_SENTINEL = object()


@dataclass
class _Job(Generic[T]):
    call_id: str
    stage: str
    operation: Callable[[], T]
    mutation: bool
    lock: threading.Lock = field(default_factory=threading.Lock)
    completed: threading.Event = field(default_factory=threading.Event)
    started: bool = False
    cancelled: bool = False
    timed_out_after_dispatch: bool = False
    value: T | None = None
    exception: Exception | None = None


class SerializedNativeDispatcher:
    """Runs all native calls on one worker thread.

    The worker can host a COM STA via initializer/finalizer hooks. A timed-out call
    that has already started is never reported as cancelled: it becomes uncertain.
    Mutations are quarantined until a verifier reconciles that call.
    """

    def __init__(
        self,
        initializer: Callable[[], None] | None = None,
        finalizer: Callable[[], None] | None = None,
    ) -> None:
        self._initializer = initializer
        self._finalizer = finalizer
        self._queue: Queue[object] = Queue()
        self._jobs: dict[str, _Job[object]] = {}
        self._jobs_lock = threading.Lock()
        self._quarantine_call_id: str | None = None
        self._ready = threading.Event()
        self._startup_exception: Exception | None = None
        self._closed = False
        self._thread = threading.Thread(target=self._worker, name="cdt-solidworks-native", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

    @property
    def quarantined_call_id(self) -> str | None:
        return self._quarantine_call_id

    def run(
        self,
        operation: Callable[[], T],
        *,
        stage: str,
        timeout: float,
        mutation: bool = False,
    ) -> NativeCallResult[T]:
        return self._run(operation, stage=stage, timeout=timeout, mutation=mutation, bypass_quarantine=False)

    def _run(
        self,
        operation: Callable[[], T],
        *,
        stage: str,
        timeout: float,
        mutation: bool,
        bypass_quarantine: bool,
    ) -> NativeCallResult[T]:
        call_id = uuid.uuid4().hex
        if self._closed:
            return NativeCallResult.failed(
                NativeFailure("dispatcher_closed", stage, "Native dispatcher is closed."),
                call_id=call_id,
                dispatched=False,
            )
        if self._startup_exception is not None:
            return NativeCallResult.failed(
                failure_from_exception(self._startup_exception, stage),
                call_id=call_id,
                dispatched=False,
            )
        if mutation and self._quarantine_call_id and not bypass_quarantine:
            return NativeCallResult.failed(
                NativeFailure(
                    code="uncertain_state",
                    stage=stage,
                    message="A prior native mutation is uncertain and must be reconciled before another mutation.",
                    retryable=False,
                    details={"call_id": self._quarantine_call_id},
                ),
                call_id=call_id,
                dispatched=False,
            )

        job: _Job[T] = _Job(call_id=call_id, stage=stage, operation=operation, mutation=mutation)
        with self._jobs_lock:
            self._jobs[call_id] = job  # type: ignore[assignment]
        self._queue.put(job)

        if job.completed.wait(max(0.0, timeout)):
            return self._result_from_completed_job(job)

        with job.lock:
            if job.completed.is_set():
                return self._result_from_completed_job(job)
            if not job.started:
                job.cancelled = True
                return NativeCallResult(
                    state=NativeCallState.TIMEOUT_BEFORE_DISPATCH,
                    call_id=call_id,
                    failure=NativeFailure(
                        code="timeout_before_dispatch",
                        stage=stage,
                        message="Native call timed out before dispatch and was not executed.",
                        retryable=True,
                    ),
                    dispatched=False,
                )

            job.timed_out_after_dispatch = True
            if mutation:
                self._quarantine_call_id = call_id
            return NativeCallResult(
                state=NativeCallState.UNCERTAIN_AFTER_DISPATCH,
                call_id=call_id,
                failure=NativeFailure(
                    code="uncertain_state",
                    stage=stage,
                    message="Native call timed out after dispatch; actual application state is uncertain.",
                    retryable=False,
                ),
                dispatched=True,
            )

    def is_complete(self, call_id: str) -> bool:
        with self._jobs_lock:
            job = self._jobs.get(call_id)
        return bool(job and job.completed.is_set())

    def reconcile(
        self,
        call_id: str,
        verifier: Callable[[], T],
        *,
        stage: str,
        timeout: float,
    ) -> NativeCallResult[T]:
        with self._jobs_lock:
            job = self._jobs.get(call_id)
        if job is None:
            return NativeCallResult.failed(
                NativeFailure("unknown_call", stage, "Unknown native call identifier."),
                call_id=uuid.uuid4().hex,
                dispatched=False,
            )
        if not job.completed.is_set():
            return NativeCallResult(
                state=NativeCallState.UNCERTAIN_AFTER_DISPATCH,
                call_id=call_id,
                failure=NativeFailure(
                    "uncertain_state",
                    stage,
                    "Original native call is still running; reconciliation is pending.",
                ),
                dispatched=True,
            )

        result = self._run(
            verifier,
            stage=stage,
            timeout=timeout,
            mutation=False,
            bypass_quarantine=True,
        )
        if result.state is NativeCallState.SUCCESS and self._quarantine_call_id == call_id:
            self._quarantine_call_id = None
        return result

    def close(self, *, timeout: float = 2.0) -> bool:
        if self._closed:
            return not self._thread.is_alive()
        self._closed = True
        self._queue.put(_SENTINEL)
        self._thread.join(max(0.0, timeout))
        return not self._thread.is_alive()

    def _result_from_completed_job(self, job: _Job[T]) -> NativeCallResult[T]:
        if job.exception is not None:
            return NativeCallResult.failed(
                failure_from_exception(job.exception, job.stage),
                call_id=job.call_id,
                dispatched=job.started,
            )
        return NativeCallResult.success(job.value, call_id=job.call_id, dispatched=job.started)  # type: ignore[arg-type]

    def _worker(self) -> None:
        try:
            if self._initializer is not None:
                self._initializer()
        except Exception as exc:
            self._startup_exception = exc
            self._ready.set()
            return

        self._ready.set()
        try:
            while True:
                item = self._queue.get()
                if item is _SENTINEL:
                    return
                job = item
                assert isinstance(job, _Job)
                with job.lock:
                    if job.cancelled:
                        job.completed.set()
                        continue
                    job.started = True
                try:
                    job.value = job.operation()
                except Exception as exc:  # normalization occurs at result boundary
                    job.exception = exc
                finally:
                    job.completed.set()
        finally:
            if self._finalizer is not None:
                try:
                    self._finalizer()
                except Exception:
                    pass
