import threading
import time
import unittest

from cdt_solidworks.native.dispatcher import RecoveryPlan, SerializedNativeDispatcher
from cdt_solidworks.native.models import NativeCallState


class SerializedNativeDispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatcher = SerializedNativeDispatcher()

    def tearDown(self) -> None:
        self.dispatcher.close(timeout=1.0)

    def test_distinguishes_timeout_before_dispatch_from_after_dispatch(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def blocking_call() -> str:
            started.set()
            release.wait(1.0)
            return "done"

        first = self.dispatcher.run(blocking_call, stage="blocking", timeout=0.02)
        self.assertTrue(started.wait(0.2))
        self.assertEqual(NativeCallState.UNCERTAIN_AFTER_DISPATCH, first.state)

        second = self.dispatcher.run(lambda: "never-ran", stage="queued", timeout=0.02)
        self.assertEqual(NativeCallState.TIMEOUT_BEFORE_DISPATCH, second.state)
        self.assertFalse(second.dispatched)

        release.set()
        time.sleep(0.03)

    def test_uncertain_mutation_quarantines_until_reconciled(self) -> None:
        started = threading.Event()
        release = threading.Event()
        mutation_count = 0

        def uncertain_mutation() -> str:
            nonlocal mutation_count
            mutation_count += 1
            started.set()
            release.wait(1.0)
            return "mutated"

        first = self.dispatcher.run(
            uncertain_mutation,
            stage="save",
            recovery=RecoveryPlan(("original-save",), "reconcile_save", lambda: "verified"),
            timeout=0.02,
            mutation=True,
        )
        self.assertTrue(started.wait(0.2))
        self.assertEqual(NativeCallState.UNCERTAIN_AFTER_DISPATCH, first.state)
        self.assertEqual(1, mutation_count)

        blocked = self.dispatcher.run(
            lambda: "must-not-run",
            stage="save_retry",
            timeout=0.1,
            mutation=True,
        )
        self.assertEqual(NativeCallState.FAILURE, blocked.state)
        self.assertEqual("uncertain_state", blocked.failure.code)
        self.assertEqual(1, mutation_count)

        release.set()
        deadline = time.monotonic() + 0.5
        while not self.dispatcher.is_complete(first.call_id) and time.monotonic() < deadline:
            time.sleep(0.005)

        reconciled = self.dispatcher.reconcile(
            first.call_id,
            identity=("original-save",),
            stage="reconcile_save",
            timeout=0.2,
        )
        self.assertEqual(NativeCallState.SUCCESS, reconciled.state)
        self.assertEqual("verified", reconciled.value)

        allowed = self.dispatcher.run(
            lambda: "next-mutation",
            stage="next",
            timeout=0.2,
            mutation=True,
        )
        self.assertEqual(NativeCallState.SUCCESS, allowed.state)

    def test_native_exception_is_normalized_without_traceback(self) -> None:
        def explode() -> None:
            raise ValueError("sensitive internal details")

        result = self.dispatcher.run(explode, stage="query", timeout=0.2)
        self.assertEqual(NativeCallState.FAILURE, result.state)
        self.assertEqual("native_call_failed", result.failure.code)
        self.assertNotIn("sensitive internal details", result.failure.message)
        self.assertNotIn("Traceback", result.failure.message)


if __name__ == "__main__":
    unittest.main()
