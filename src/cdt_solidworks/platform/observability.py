"""Safe bounded observability dimensions for provider operations."""

from __future__ import annotations

from threading import Lock

from cdt_solidworks.platform.models import ObservabilitySnapshot


class SafeObserver:
    """Aggregate only bounded metadata; no request payload or credential fields exist."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._request_count = 0
        self._success_count = 0
        self._failure_count = 0
        self._uncertain_count = 0
        self._timeout_count = 0
        self._reconciliation_count = 0
        self._queue_wait_ms_total = 0.0
        self._provider_latency_ms_total = 0.0
        self._dependency_latency_ms_total = 0.0
        self._last_tool: str | None = None
        self._last_outcome: str | None = None
        self._last_operation_id: str | None = None
        self._last_document_identity_hash: str | None = None
        self._last_failure_class: str | None = None

    def record(
        self,
        *,
        tool_name: str,
        outcome: str,
        provider_latency_ms: float,
        dependency_latency_ms: float | None,
        queue_wait_ms: float = 0.0,
        operation_id: str | None = None,
        document_identity_hash: str | None = None,
        failure_class: str | None = None,
        timed_out: bool = False,
        reconciled: bool = False,
    ) -> None:
        if outcome not in {"success", "failure", "uncertain", "timeout"}:
            raise ValueError("unsupported outcome")
        if provider_latency_ms < 0:
            raise ValueError("provider_latency_ms must be non-negative")
        if dependency_latency_ms is not None and dependency_latency_ms < 0:
            raise ValueError("dependency_latency_ms must be non-negative")
        if queue_wait_ms < 0:
            raise ValueError("queue_wait_ms must be non-negative")

        with self._lock:
            self._request_count += 1
            self._queue_wait_ms_total += queue_wait_ms
            self._provider_latency_ms_total += provider_latency_ms
            if dependency_latency_ms is not None:
                self._dependency_latency_ms_total += dependency_latency_ms
            if outcome == "success":
                self._success_count += 1
            else:
                self._failure_count += 1
            if outcome == "uncertain":
                self._uncertain_count += 1
            if outcome == "timeout" or timed_out:
                self._timeout_count += 1
            if reconciled:
                self._reconciliation_count += 1
            self._last_tool = tool_name
            self._last_outcome = outcome
            self._last_operation_id = operation_id
            self._last_document_identity_hash = document_identity_hash
            self._last_failure_class = failure_class

    def snapshot(self) -> ObservabilitySnapshot:
        with self._lock:
            return ObservabilitySnapshot(
                request_count=self._request_count,
                success_count=self._success_count,
                failure_count=self._failure_count,
                uncertain_count=self._uncertain_count,
                timeout_count=self._timeout_count,
                reconciliation_count=self._reconciliation_count,
                queue_wait_ms_total=self._queue_wait_ms_total,
                provider_latency_ms_total=self._provider_latency_ms_total,
                dependency_latency_ms_total=self._dependency_latency_ms_total,
                last_tool=self._last_tool,
                last_outcome=self._last_outcome,
                last_operation_id=self._last_operation_id,
                last_document_identity_hash=self._last_document_identity_hash,
                last_failure_class=self._last_failure_class,
            )
