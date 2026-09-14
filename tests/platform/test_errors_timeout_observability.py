from __future__ import annotations

import asyncio

import pytest

from cdt_solidworks.platform.errors import (
    ErrorCode,
    ProviderError,
    ProviderTimeoutError,
    normalize_exception,
)
from cdt_solidworks.platform.observability import SafeObserver
from cdt_solidworks.platform.timeout import DispatchState, run_bounded


def test_error_code_surface_matches_provider_standard_minimum() -> None:
    assert {code.value for code in ErrorCode} == {
        "authentication_error",
        "authorization_error",
        "validation_error",
        "not_found",
        "conflict",
        "rate_limited",
        "timeout",
        "provider_unavailable",
        "internal_error",
        "unsupported_capability",
    }


def test_internal_error_redacts_exception_and_stack_details() -> None:
    payload = normalize_exception(RuntimeError("Bearer super-secret should never escape"))

    assert payload.code == ErrorCode.INTERNAL_ERROR
    assert payload.message == "Internal provider error."
    assert "secret" not in payload.model_dump_json().lower()
    assert "runtimeerror" not in payload.model_dump_json().lower()


def test_known_provider_error_preserves_safe_typed_message() -> None:
    payload = normalize_exception(
        ProviderError(ErrorCode.VALIDATION_ERROR, "Request validation failed.")
    )

    assert payload.code == ErrorCode.VALIDATION_ERROR
    assert payload.message == "Request validation failed."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mark_dispatched", "expected_state"),
    [(False, "not_started"), (True, "uncertain")],
)
async def test_bounded_timeout_tracks_the_actual_dispatch_boundary(
    mark_dispatched: bool, expected_state: str
) -> None:
    dispatch_state = DispatchState()

    async def slow_call() -> None:
        if mark_dispatched:
            dispatch_state.mark_dispatched()
        await asyncio.sleep(0.05)

    with pytest.raises(ProviderTimeoutError) as exc_info:
        await run_bounded(
            slow_call,
            timeout_seconds=0.001,
            dispatch_state=dispatch_state,
        )

    assert exc_info.value.state == expected_state
    assert exc_info.value.code == ErrorCode.TIMEOUT


def test_observer_records_only_safe_dimensions() -> None:
    observer = SafeObserver()
    observer.record(
        tool_name="help",
        outcome="success",
        provider_latency_ms=1.25,
        dependency_latency_ms=None,
    )

    snapshot = observer.snapshot()
    assert snapshot.request_count == 1
    assert snapshot.success_count == 1
    assert snapshot.failure_count == 0
    assert snapshot.uncertain_count == 0
    assert snapshot.timeout_count == 0
    assert snapshot.reconciliation_count == 0
    assert snapshot.queue_wait_ms_total == 0.0
    assert snapshot.last_tool == "help"
    assert snapshot.last_outcome == "success"
    assert snapshot.last_operation_id is None
    assert snapshot.last_document_identity_hash is None
    assert snapshot.last_failure_class is None


def test_observer_can_count_uncertain_timeout_and_reconciliation_separately() -> None:
    observer = SafeObserver()
    observer.record(
        tool_name="feature_update",
        outcome="uncertain",
        operation_id="op-123",
        document_identity_hash="sha256:opaque-document",
        failure_class="timeout",
        queue_wait_ms=2.0,
        provider_latency_ms=8.0,
        dependency_latency_ms=5.0,
        timed_out=True,
    )
    observer.record(
        tool_name="reconcile",
        outcome="failure",
        operation_id="op-123",
        document_identity_hash="sha256:opaque-document",
        failure_class="provider_unavailable",
        queue_wait_ms=1.0,
        provider_latency_ms=4.0,
        dependency_latency_ms=3.0,
        reconciled=True,
    )

    snapshot = observer.snapshot()
    assert snapshot.request_count == 2
    assert snapshot.failure_count == 2
    assert snapshot.uncertain_count == 1
    assert snapshot.timeout_count == 1
    assert snapshot.reconciliation_count == 1
    assert snapshot.queue_wait_ms_total == 3.0
    assert snapshot.provider_latency_ms_total == 12.0
    assert snapshot.dependency_latency_ms_total == 8.0
    assert snapshot.last_operation_id == "op-123"
    assert snapshot.last_document_identity_hash == "sha256:opaque-document"
    assert snapshot.last_failure_class == "provider_unavailable"
