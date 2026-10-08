from __future__ import annotations

from types import SimpleNamespace

import pytest

from cdt_solidworks.native.api import WindowsComApi
from cdt_solidworks.native.errors import NativeRuntimeError
from cdt_solidworks.native.models import ApplicationOwnership, NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession


class FakeComClient:
    def __init__(self):
        self.starts = 0
        self.attaches = 0
        self.application = SimpleNamespace(Visible=False, RevisionNumber="32.0.1")

    def GetActiveObject(self, prog_id):
        self.attaches += 1
        raise RuntimeError("no running fake application")

    def DispatchEx(self, prog_id):
        self.starts += 1
        return self.application


class InitializedApi(WindowsComApi):
    def __init__(self, session_id):
        super().__init__()
        self._client = FakeComClient()
        self._pythoncom = object()
        self._process_session_id = lambda: session_id

    def initialize_thread(self):
        pass

    def uninitialize_thread(self):
        pass


@pytest.mark.parametrize("policy", [AttachPolicy.ATTACH_OR_START, AttachPolicy.START_NEW])
def test_session_zero_connect_refuses_before_com_activation(policy):
    api = InitializedApi(0)
    session = SolidWorksSession(api=api)
    try:
        result = session.connect(policy=policy, version=2024, timeout=0.5)
        assert result.state is NativeCallState.FAILURE
        assert result.failure.code == "solidworks_interactive_session_required"
        assert result.failure.details["windows_session_id"] == 0
        assert api._client.starts == 0
        assert not session.connected
        assert session.uncertain_call_id is None
    finally:
        session.close_dispatcher(timeout=1.0)


def test_direct_activation_in_session_zero_is_also_blocked():
    api = InitializedApi(0)
    with pytest.raises(NativeRuntimeError, match="interactive Windows"):
        api.start_application("SldWorks.Application.32")
    assert api._client.starts == 0


def test_unknown_windows_context_fails_closed_before_com_activation():
    api = InitializedApi(None)
    def unknown():
        raise NativeRuntimeError(
            "solidworks_session_context_unavailable", "connect",
            "Windows process session could not be verified.",
        )
    api._process_session_id = unknown
    session = SolidWorksSession(api=api)
    try:
        result = session.connect(policy=AttachPolicy.START_NEW, version=2024, timeout=0.5)
        assert result.state is NativeCallState.FAILURE
        assert result.failure.code == "solidworks_session_context_unavailable"
        assert api._client.starts == 0
        assert session.uncertain_call_id is None
    finally:
        session.close_dispatcher(timeout=1.0)


def test_interactive_session_activates_once_with_existing_version_identity():
    api = InitializedApi(1)
    session = SolidWorksSession(api=api)
    try:
        result = session.connect(policy=AttachPolicy.START_NEW, version=2024, timeout=0.5)
        assert result.state is NativeCallState.SUCCESS
        assert result.value.version_year == 2024
        assert api._client.starts == 1
        assert session.connected
    finally:
        session.close_dispatcher(timeout=1.0)


def test_session_zero_readonly_attach_policy_never_starts_application():
    api = InitializedApi(0)
    session = SolidWorksSession(api=api)
    try:
        result = session.connect(policy=AttachPolicy.ATTACH_ONLY, version=2024, timeout=0.5)
        assert result.state is NativeCallState.FAILURE
        assert result.failure.code == "solidworks_attach_failed"
        assert api._client.attaches == 1
        assert api._client.starts == 0
        assert session.uncertain_call_id is None
    finally:
        session.close_dispatcher(timeout=1.0)


class BackgroundApplication:
    """Model the documented CloseDoc lifetime of an automation-owned session."""
    def __init__(self):
        self.Visible = False
        self.UserControl = False
        self.UserControlBackground = False
        self.alive = True
        self.exits = 0

    def CloseDoc(self, title):
        if not self.UserControl and not self.UserControlBackground:
            self.alive = False

    def RevisionNumber(self):
        if not self.alive:
            raise RuntimeError("background application exited after its last document")
        return "32.0.1"

    def ExitApp(self):
        self.exits += 1
        self.alive = False


@pytest.mark.parametrize("visible", [True, False])
def test_owned_session_survives_last_document_close_until_explicit_disconnect(visible):
    api = InitializedApi(1)
    app = BackgroundApplication()
    api._client.application = app
    session = SolidWorksSession(api=api)
    try:
        connected = session.connect(policy=AttachPolicy.START_NEW, version=2024, visible=visible, timeout=1)
        assert connected.state is NativeCallState.SUCCESS
        assert app.Visible is visible
        closed = session.execute(lambda application: api.close_document(application, "trial.SLDPRT"),
                                 stage="close_last_trial_document", timeout=1, mutation=True)
        assert closed.state is NativeCallState.SUCCESS
        probe = session.probe(version=2024, timeout=1)
        assert probe.state is NativeCallState.SUCCESS
        assert probe.value.version_year == 2024
        assert app.exits == 0
        disconnected = session.disconnect(timeout=1)
        assert disconnected.state is NativeCallState.SUCCESS
        assert app.exits == 1
    finally:
        session.close_dispatcher(timeout=1)


def test_attached_session_keeps_user_control_and_is_not_exited():
    api = InitializedApi(1)
    app = BackgroundApplication()
    api._client.GetActiveObject = lambda prog_id: app
    session = SolidWorksSession(api=api)
    try:
        result = session.connect(policy=AttachPolicy.ATTACH_ONLY, version=2024, timeout=1)
        assert result.state is NativeCallState.SUCCESS
        assert result.value.ownership is ApplicationOwnership.USER_OWNED
        assert not app.UserControl and not app.UserControlBackground
        assert not app.Visible
        assert session.disconnect(timeout=1).state is NativeCallState.SUCCESS
        assert app.exits == 0
    finally:
        session.close_dispatcher(timeout=1)


def test_cyclic_native_reference_is_released_on_owner_before_com_uninitialize():
    import gc
    import threading

    events = []
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        def worker():
            api = WindowsComApi()
            owner = threading.get_ident()
            class ComState:
                active = True
                def CoUninitialize(self):
                    events.append(("uninitialized", threading.get_ident()))
                    self.active = False
            state = ComState()
            api._pythoncom = state
            api._initialized = True
            class NativeReference:
                def __init__(self):
                    self.cycle = self
                def __del__(self):
                    events.append(("released", threading.get_ident(), state.active))
            reference = NativeReference()
            del reference
            api.uninitialize_thread()
            events.append(("worker_done", owner))
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(2)
        assert not thread.is_alive()
        assert events[0][0] == "released", events
        assert events[0][2] is True
        assert events[0][1] == events[-1][1]
        assert events[1][0] == "uninitialized"
    finally:
        gc.collect()
        if was_enabled:
            gc.enable()
