from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from cdt_solidworks.cli import _shutdown_runtime
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession


class DelayedComApi:
    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()
        self.exits = 0
        self.finalized = 0
        self.application = object()

    def initialize_thread(self):
        pass

    def uninitialize_thread(self):
        self.finalized += 1

    def start_application(self, prog_id):
        self.started.set()
        if not self.release.wait(5.0):
            raise RuntimeError("fake COM gate was not released")
        return self.application

    def set_visible(self, application, visible):
        pass

    def revision_number(self, application):
        return "32.1.0"

    def exit_application(self, application):
        self.exits += 1


@pytest.mark.parametrize("complete_first", [False, True], ids=["running", "late-complete"])
def test_disconnect_preserves_uncertain_connect(complete_first):
    api = DelayedComApi()
    session = SolidWorksSession(api=api)
    try:
        result = session.connect(policy=AttachPolicy.START_NEW, version=2024, timeout=0.05)
        assert api.started.is_set()
        assert result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
        if complete_first:
            api.release.set()
            # A serialized read supplies a deterministic completion barrier.
            barrier = session._dispatcher.run(lambda: None, stage="test_barrier", timeout=1.0)
            assert barrier.state is NativeCallState.SUCCESS
        disconnected = session.disconnect(timeout=0.05)
        assert disconnected.state is NativeCallState.FAILURE
        assert disconnected.failure.code == "uncertain_state"
        assert disconnected.failure.details["call_id"] == result.call_id
        assert disconnected.dispatched is False
        assert session.uncertain_call_id == result.call_id
        assert api.exits == 0
    finally:
        api.release.set()
        assert session.close_dispatcher(timeout=1.0)


@pytest.mark.parametrize("suppress_errors", [False, True])
def test_shutdown_does_not_claim_clean_exit_after_uncertain_connect(monkeypatch, suppress_errors):
    api = DelayedComApi()
    session = SolidWorksSession(api=api)
    close = session.close_dispatcher
    try:
        result = session.connect(policy=AttachPolicy.START_NEW, version=2024, timeout=0.05)
        assert result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH

        def release_on_close(*, timeout):
            api.release.set()
            return close(timeout=timeout)

        monkeypatch.setattr(session, "close_dispatcher", release_on_close)
        runtime = SimpleNamespace(session=session)
        if suppress_errors:
            _shutdown_runtime(runtime, suppress_errors=True)
        else:
            with pytest.raises(RuntimeError, match="native shutdown failed: uncertain_state"):
                _shutdown_runtime(runtime, suppress_errors=False)
        assert api.finalized == 1
        assert api.exits == 0
        assert session.connected
        assert session.uncertain_call_id == result.call_id
    finally:
        api.release.set()
        assert close(timeout=1.0)


@pytest.mark.parametrize("outcome", ["uncertain", "failure", "exception"])
def test_live_fixture_setup_cleans_up_and_fences_following_cases(tmp_path, outcome):
    """Exercise the actual six fixture cases with a fake vendor boundary."""
    root = Path(__file__).resolve().parents[2]
    conftest = root / "tests" / "conftest.py"
    harness = conftest.read_text(encoding="utf-8") if conftest.exists() else ""
    fake = '''
import json
from pathlib import Path
from types import SimpleNamespace
import cdt_solidworks.native.session as native_session
from cdt_solidworks.native.models import NativeCallResult, NativeCallState, NativeFailure

TRACE = Path(__file__).with_name("trace.jsonl")
def record(event):
    with TRACE.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event) + "\\n")

class FakeSession:
    def __init__(self):
        self.uncertain_call_id = None
        record("new")
    def connect(self, **kwargs):
        record("connect")
        if OUTCOME == "exception":
            raise RuntimeError("simulated connect exception")
        if OUTCOME == "uncertain":
            self.uncertain_call_id = "pending-native-call"
            return NativeCallResult(
                NativeCallState.UNCERTAIN_AFTER_DISPATCH, "pending-native-call",
                failure=NativeFailure("uncertain_state", "connect", "simulated uncertainty"),
                dispatched=True,
            )
        return NativeCallResult.failed(
            NativeFailure("solidworks_attach_failed", "connect", "simulated refusal"),
            call_id="refused", dispatched=False,
        )
    def disconnect(self, **kwargs):
        record("disconnect")
        if self.uncertain_call_id:
            return NativeCallResult.failed(
                NativeFailure("uncertain_state", "disconnect", "still fenced"),
                call_id="disconnect", dispatched=False,
            )
        return NativeCallResult.success(False, call_id="disconnect", dispatched=False)
    def close_dispatcher(self, **kwargs):
        record("close")
        return True

SolidWorksSession = FakeSession
native_session.SolidWorksSession = FakeSession
'''
    (tmp_path / "conftest.py").write_text(
        harness + "\nOUTCOME = " + repr(outcome) + "\n" + fake, encoding="utf-8"
    )
    for family in ("body", "surface", "sheetmetal"):
        source = (root / "tests" / family / "test_native_windows_fixture.py").read_text(encoding="utf-8")
        # All native session constructors are replaced above, so no COM is touched.
        source = "\n".join(
            line for line in source.splitlines() if not line.startswith("pytestmark = ")
        )
        (tmp_path / f"test_{family}.py").write_text(source, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--import-mode=importlib", "--junitxml=result.xml"],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    events = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    expected = 6 if outcome == "failure" else 1
    assert events.count("new") == expected, result.stdout
    assert events.count("connect") == expected, result.stdout
    assert events.count("disconnect") == expected, result.stdout
    assert events.count("close") == expected, result.stdout
    import xml.etree.ElementTree as ET
    cases = list(ET.parse(tmp_path / "result.xml").getroot().iter("testcase"))
    assert len(cases) == 6
    assert sum(case.find("skipped") is not None for case in cases) == 6 - expected
    assert sum(case.find("error") is not None or case.find("failure") is not None for case in cases) == expected


@pytest.mark.parametrize("initialization_failed", [False, True])
def test_disconnected_idle_shutdown_remains_a_clean_noop(initialization_failed):
    api = DelayedComApi()
    if initialization_failed:
        def fail_initialize():
            raise RuntimeError("fake native dependency unavailable")
        api.initialize_thread = fail_initialize
    session = SolidWorksSession(api=api)
    try:
        disconnected = session.disconnect(timeout=0.05)
        assert disconnected.state is NativeCallState.SUCCESS
        assert disconnected.value is False
        assert disconnected.dispatched is False
        assert not session.connected
        assert api.exits == 0
    finally:
        assert session.close_dispatcher(timeout=1.0)


def test_disconnect_observes_an_already_admitted_connect_on_the_worker():
    api = DelayedComApi()
    session = SolidWorksSession(api=api)
    executor = ThreadPoolExecutor(max_workers=1)
    pending = executor.submit(
        session.connect, policy=AttachPolicy.START_NEW, version=2024, timeout=2.0
    )
    try:
        assert api.started.wait(1.0)
        # Connect owns the worker; disconnect cannot report an empty binding.
        disconnected = session.disconnect(timeout=0.05)
        assert disconnected.state is NativeCallState.TIMEOUT_BEFORE_DISPATCH
        assert disconnected.dispatched is False
        assert api.exits == 0
        api.release.set()
        assert pending.result(timeout=1.0).state is NativeCallState.SUCCESS
        assert session.disconnect(timeout=1.0).value is True
        assert api.exits == 1
    finally:
        api.release.set()
        executor.shutdown(wait=True)
        assert session.close_dispatcher(timeout=1.0)
