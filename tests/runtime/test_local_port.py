"""W1/W3: LocalSessionAdapter reuses the existing session 1:1 (no COM moved)."""

from __future__ import annotations

import pytest

from cdt_solidworks.integration.runtime import IntegratedProviderRuntime
from cdt_solidworks.native.models import ApplicationOwnership, NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession
from cdt_solidworks.runtime.local_adapter import LocalSessionAdapter
from cdt_solidworks.runtime.port import SolidWorksRuntimePort


class FakeComApi:
    def __init__(self, *, attach_app=None, start_app=None, registered=True) -> None:
        self.attach_app = attach_app
        self.start_app = start_app if start_app is not None else object()
        self.registered = registered
        self.exited: list[object] = []
        self.visible: list[tuple[object, bool]] = []

    def initialize_thread(self) -> None:
        pass

    def uninitialize_thread(self) -> None:
        pass

    def prog_id_registered(self, prog_id: str) -> bool:
        return self.registered

    def attach_application(self, prog_id: str):
        if self.attach_app is None:
            raise RuntimeError("no running instance")
        return self.attach_app

    def start_application(self, prog_id: str):
        return self.start_app

    def set_visible(self, app, visible: bool) -> None:
        self.visible.append((app, visible))

    def revision_number(self, app) -> str:
        return "34.1.1"

    @staticmethod
    def _member(obj, name, *args):
        member = getattr(obj, name)
        if args:
            return member(*args)
        return member() if callable(member) else member

    def __getattr__(self, name: str):
        # Wiring-only fallback: service constructors bind api callables at
        # composition time; behavior is never exercised through this fake.
        if name.startswith("__"):
            raise AttributeError(name)

        def _stub(*args, **kwargs):
            return None

        return _stub

    def exit_application(self, app) -> None:
        self.exited.append(app)


def _session(**kwargs) -> SolidWorksSession:
    return SolidWorksSession(api=FakeComApi(**kwargs))


def test_adapter_requires_real_session() -> None:
    with pytest.raises(TypeError):
        LocalSessionAdapter(object())  # type: ignore[arg-type]


def test_adapter_satisfies_port_protocol() -> None:
    session = _session(attach_app=object())
    adapter = LocalSessionAdapter(session)
    try:
        assert isinstance(adapter, SolidWorksRuntimePort)
        assert adapter.session is session
        assert adapter.api is session.api
        assert adapter.session_id == session.session_id
    finally:
        session.close_dispatcher(timeout=0.5)


def test_adapter_preserves_session_identity_for_document_binding() -> None:
    session = _session(attach_app=object())
    adapter = LocalSessionAdapter(session)
    try:
        assert adapter.session_id == session.session_id
        assert adapter.session_id  # stable non-empty scope for topology/document guards
    finally:
        session.close_dispatcher(timeout=0.5)


def test_probe_delegates_without_mutation() -> None:
    existing = object()
    api = FakeComApi(attach_app=existing, registered=True)
    session = SolidWorksSession(api=api)
    adapter = LocalSessionAdapter(session)
    try:
        result = adapter.probe(timeout=0.5)
        assert result.state is NativeCallState.SUCCESS
        assert result.value.registered is True
        assert result.value.running is True
        assert adapter.connected is False
        assert api.visible == []
    finally:
        session.close_dispatcher(timeout=0.5)


def test_connect_ownership_and_disconnect_delegate() -> None:
    existing = object()
    api = FakeComApi(attach_app=existing)
    session = SolidWorksSession(api=api)
    adapter = LocalSessionAdapter(session)
    try:
        connected = adapter.connect(policy=AttachPolicy.ATTACH_OR_START, timeout=0.5)
        assert connected.state is NativeCallState.SUCCESS
        assert connected.value.ownership is ApplicationOwnership.USER_OWNED
        assert adapter.connected is True
        assert adapter.ownership is ApplicationOwnership.USER_OWNED
        assert adapter.disconnect(timeout=0.5).state is NativeCallState.SUCCESS
        assert api.exited == []
    finally:
        session.close_dispatcher(timeout=0.5)


def test_connect_accepts_wire_policy_string() -> None:
    existing = object()
    session = _session(attach_app=existing)
    adapter = LocalSessionAdapter(session)
    try:
        result = adapter.connect(policy="attach_only", timeout=0.5)
        assert result.state is NativeCallState.SUCCESS
        assert result.value.ownership is ApplicationOwnership.USER_OWNED
    finally:
        session.disconnect(timeout=0.5)
        session.close_dispatcher(timeout=0.5)


def test_runtime_status_and_health_are_read_only() -> None:
    api = FakeComApi()
    session = SolidWorksSession(api=api)
    adapter = LocalSessionAdapter(session)
    try:
        status = adapter.runtime_status()
        assert status["transport"] == "local"
        assert status["session_id"] == session.session_id
        assert status["connected"] is False
        health = adapter.health()
        assert health["reachable"] is True
        assert adapter.connected is False
        assert api.visible == []
    finally:
        session.close_dispatcher(timeout=0.5)


def test_runtime_composes_services_over_port_adapter() -> None:
    existing = object()
    session = _session(attach_app=existing)
    adapter = LocalSessionAdapter(session)
    try:
        runtime = IntegratedProviderRuntime(allowed_roots=("/tmp",), session=adapter)
        # Services bind through the port escape hatch to the same COM api.
        assert runtime.cad_service is not None
        assert runtime.document_service.session is adapter
        assert runtime.cad_service.session is adapter
        context = runtime.runtime_context()
        assert context.dependencies[0].available is True
        assert context.dependencies[0].version == "2026"
    finally:
        session.close_dispatcher(timeout=0.5)


def test_execute_through_port_preserves_dispatcher_semantics() -> None:
    existing = object()
    session = _session(attach_app=existing)
    adapter = LocalSessionAdapter(session)
    try:
        assert adapter.connect(policy=AttachPolicy.ATTACH_OR_START, timeout=0.5).state is (
            NativeCallState.SUCCESS
        )
        direct = session.execute(lambda app: {"marker": 1}, stage="probe-stage", timeout=0.5)
        via_port = adapter.execute(lambda app: {"marker": 1}, stage="probe-stage", timeout=0.5)
        assert direct.state is NativeCallState.SUCCESS
        assert via_port.state is NativeCallState.SUCCESS
        assert via_port.value == {"marker": 1}
        assert adapter.uncertain_call_id is None
    finally:
        session.disconnect(timeout=0.5)
        session.close_dispatcher(timeout=0.5)
