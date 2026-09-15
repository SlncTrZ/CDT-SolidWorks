"""R0 dispatcher regressions for quarantine, trusted recovery and bounded retention."""

from concurrent.futures import ThreadPoolExecutor
import gc
import threading
import weakref

from cdt_solidworks.native.dispatcher import RecoveryPlan, SerializedNativeDispatcher
from cdt_solidworks.native.models import NativeCallState


def _uncertain_call(dispatcher, *, stage="mutation", recovery=None):
    started = threading.Event()
    release = threading.Event()

    def operation():
        started.set()
        assert release.wait(3)
        return "done"

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(
            dispatcher.run,
            operation,
            stage=stage,
            timeout=0.05,
            mutation=True,
            recovery=recovery,
        )
        assert started.wait(1)
        result = future.result(timeout=1)
    return result, release


def test_queued_mutation_does_not_start_after_prior_timeout():
    dispatcher = SerializedNativeDispatcher()
    started, release, queued = threading.Event(), threading.Event(), threading.Event()
    writes = []
    original_put = dispatcher._queue.put

    def observe_put(job, *args, **kwargs):
        original_put(job, *args, **kwargs)
        if getattr(job, "stage", None) == "second":
            queued.set()

    dispatcher._queue.put = observe_put

    def first():
        started.set()
        assert release.wait(3)

    try:
        with ThreadPoolExecutor(2) as pool:
            a = pool.submit(dispatcher.run, first, stage="first", timeout=0.3, mutation=True)
            assert started.wait(1)
            b = pool.submit(
                dispatcher.run,
                lambda: writes.append("second"),
                stage="second",
                timeout=2,
                mutation=True,
            )
            assert queued.wait(1)
            uncertain = a.result(timeout=1)
            assert uncertain.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
            release.set()
            blocked = b.result(timeout=2)
            assert writes == []
            assert blocked.state is NativeCallState.FAILURE
            assert not blocked.dispatched
            assert dispatcher.quarantined_call_id == uncertain.call_id
    finally:
        release.set()
        dispatcher.close()


def test_timeout_before_start_never_executes_cancelled_job():
    dispatcher = SerializedNativeDispatcher()
    first_started = threading.Event()
    first_release = threading.Event()
    writes = []

    def first():
        first_started.set()
        assert first_release.wait(3)

    try:
        with ThreadPoolExecutor(2) as pool:
            held = pool.submit(dispatcher.run, first, stage="held", timeout=2)
            assert first_started.wait(1)
            cancelled = pool.submit(
                dispatcher.run,
                lambda: writes.append("cancelled"),
                stage="cancelled",
                timeout=0.01,
                mutation=True,
            ).result(timeout=1)
            assert cancelled.state is NativeCallState.TIMEOUT_BEFORE_DISPATCH
            assert not cancelled.dispatched
            first_release.set()
            assert held.result(timeout=1).state is NativeCallState.SUCCESS
        assert dispatcher.run(lambda: None, stage="drain", timeout=1).state is NativeCallState.SUCCESS
        assert writes == []
    finally:
        first_release.set()
        dispatcher.close()


def test_unregistered_reconciliation_cannot_unlock_uncertain_mutation():
    dispatcher = SerializedNativeDispatcher()
    release = threading.Event()
    try:
        uncertain = dispatcher.run(
            lambda: release.wait(2),
            stage="assembly_mate_create",
            timeout=0.05,
            mutation=True,
        )
        assert uncertain.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
        release.set()
        dispatcher.run(lambda: None, stage="barrier", timeout=1)
        result = dispatcher.reconcile(
            uncertain.call_id,
            lambda: True,
            stage="document_reconcile",
            timeout=1,
        )
        assert result.state is NativeCallState.FAILURE
        assert result.call_id == uncertain.call_id
        assert result.failure.code == "recovery_unsupported"
        assert dispatcher.quarantined_call_id == uncertain.call_id
    finally:
        release.set()
        dispatcher.close()


def test_reconciliation_rejects_wrong_identity_and_stage_without_running_verifier():
    dispatcher = SerializedNativeDispatcher()
    release = threading.Event()
    verifier_calls = []
    plan = RecoveryPlan(("assembly-a", "mate-1"), "mate_reconcile", lambda: verifier_calls.append("ran"))
    try:
        uncertain = dispatcher.run(
            lambda: release.wait(2),
            stage="assembly_mate_create",
            timeout=0.05,
            mutation=True,
            recovery=plan,
        )
        assert uncertain.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
        release.set()
        dispatcher.run(lambda: None, stage="barrier", timeout=1)

        wrong_target = dispatcher.reconcile(
            uncertain.call_id,
            stage="mate_reconcile",
            timeout=1,
            identity=("assembly-b", "mate-1"),
        )
        wrong_stage = dispatcher.reconcile(
            uncertain.call_id,
            stage="document_reconcile",
            timeout=1,
            identity=("assembly-a", "mate-1"),
        )
        assert wrong_target.failure.code == "reconciliation_mismatch"
        assert wrong_stage.failure.code == "reconciliation_mismatch"
        assert verifier_calls == []
        assert dispatcher.quarantined_call_id == uncertain.call_id
    finally:
        release.set()
        dispatcher.close()


def test_stale_recovery_cannot_clear_newer_pending_call():
    dispatcher = SerializedNativeDispatcher()
    first_release = threading.Event()
    second_release = threading.Event()
    try:
        first = dispatcher.run(
            lambda: first_release.wait(2),
            stage="first",
            timeout=0.05,
            mutation=True,
            recovery=RecoveryPlan(("first",), "first_reconcile", lambda: "verified-first"),
        )
        first_release.set()
        dispatcher.run(lambda: None, stage="barrier-1", timeout=1)
        assert dispatcher.reconcile(
            first.call_id,
            stage="first_reconcile",
            timeout=1,
            identity=("first",),
        ).state is NativeCallState.SUCCESS

        second = dispatcher.run(
            lambda: second_release.wait(2),
            stage="second",
            timeout=0.05,
            mutation=True,
            recovery=RecoveryPlan(("second",), "second_reconcile", lambda: "verified-second"),
        )
        assert second.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
        second_release.set()
        dispatcher.run(lambda: None, stage="barrier-2", timeout=1)

        stale = dispatcher.reconcile(
            first.call_id,
            stage="first_reconcile",
            timeout=1,
            identity=("first",),
        )
        assert stale.state is NativeCallState.FAILURE
        assert dispatcher.quarantined_call_id == second.call_id
    finally:
        first_release.set()
        second_release.set()
        dispatcher.close()


def test_completed_history_remains_within_limit():
    dispatcher = SerializedNativeDispatcher(history_limit=17)
    try:
        for _ in range(1000):
            assert dispatcher.run(lambda: None, stage="read", timeout=1).state is NativeCallState.SUCCESS
        metrics = dispatcher.metrics
        assert metrics["history_count"] <= 17
        assert metrics["outstanding"] == 0
        assert len(dispatcher._jobs) == 0
    finally:
        dispatcher.close()


def test_completed_operations_release_captured_objects():
    dispatcher = SerializedNativeDispatcher(history_limit=3)

    class Payload:
        pass

    payload = Payload()
    retained = weakref.ref(payload)
    try:
        result = dispatcher.run(
            lambda captured=payload: captured,
            stage="read",
            timeout=1,
        )
        assert result.state is NativeCallState.SUCCESS
        del result
        del payload
        gc.collect()
        assert retained() is None
        assert dispatcher.metrics["history_count"] <= 3
        assert len(dispatcher._jobs) == 0
    finally:
        dispatcher.close()


def test_pending_uncertainty_survives_history_eviction():
    dispatcher = SerializedNativeDispatcher(history_limit=3)
    release = threading.Event()
    try:
        uncertain = dispatcher.run(
            lambda: release.wait(2),
            stage="save",
            timeout=0.05,
            mutation=True,
            recovery=RecoveryPlan(("doc",), "save_reconcile", lambda: "verified"),
        )
        assert uncertain.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
        release.set()
        dispatcher.run(lambda: None, stage="barrier", timeout=1)
        for _ in range(20):
            assert dispatcher.run(lambda: None, stage="read", timeout=1).state is NativeCallState.SUCCESS
        assert dispatcher.quarantined_call_id == uncertain.call_id
        recovered = dispatcher.reconcile(
            uncertain.call_id,
            stage="save_reconcile",
            timeout=1,
            identity=("doc",),
        )
        assert recovered.state is NativeCallState.SUCCESS
        assert recovered.call_id == uncertain.call_id
        assert dispatcher.quarantined_call_id is None
    finally:
        release.set()
        dispatcher.close()


def test_queue_overload_is_not_dispatched():
    dispatcher = SerializedNativeDispatcher(max_outstanding=2)
    release = threading.Event()
    started = threading.Event()
    second_writes = []
    rejected_writes = []

    def held():
        started.set()
        assert release.wait(3)

    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(dispatcher.run, held, stage="held", timeout=2)
            assert started.wait(1)
            second = pool.submit(
                dispatcher.run,
                lambda: second_writes.append("second"),
                stage="queued",
                timeout=2,
            )
            while dispatcher.metrics["outstanding"] < 2:
                threading.Event().wait(0.001)
            rejected = dispatcher.run(
                lambda: rejected_writes.append("rejected"),
                stage="overload",
                timeout=1,
                mutation=True,
            )
            assert rejected.state is NativeCallState.FAILURE
            assert rejected.failure.code == "native_overloaded"
            assert not rejected.dispatched
            assert rejected_writes == []
            release.set()
            assert first.result(timeout=1).state is NativeCallState.SUCCESS
            assert second.result(timeout=1).state is NativeCallState.SUCCESS
            assert second_writes == ["second"]
    finally:
        release.set()
        dispatcher.close()


def test_close_with_full_admission_terminates_after_owned_work_releases():
    dispatcher = SerializedNativeDispatcher(max_outstanding=1)
    release = threading.Event()
    started = threading.Event()

    def held():
        started.set()
        assert release.wait(3)

    try:
        with ThreadPoolExecutor(1) as pool:
            call = pool.submit(dispatcher.run, held, stage="held", timeout=2)
            assert started.wait(1)
            assert dispatcher.metrics["outstanding"] == 1
            assert dispatcher.run(lambda: None, stage="overload", timeout=1).failure.code == "native_overloaded"
            release.set()
            assert call.result(timeout=1).state is NativeCallState.SUCCESS
        assert dispatcher.close(timeout=1)
    finally:
        release.set()
        dispatcher.close()
