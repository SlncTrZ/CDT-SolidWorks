"""W2: transport + workstation agent + remote adapter (COM stays on Windows)."""

from __future__ import annotations

import threading
import time

import pytest

from cdt_solidworks.native.models import ApplicationOwnership, NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession
from cdt_solidworks.runtime.local_adapter import LocalSessionAdapter
from cdt_solidworks.runtime.remote_adapter import RemoteSessionAdapter
from cdt_solidworks.runtime.transport import (
    LocalSolidWorksTransport,
    RemoteSolidWorksTransport,
    RuntimeAuthError,
    RuntimeGenerationMismatchError,
    RuntimeOpRefusedError,
    RuntimeUnavailableError,
    RuntimeUncertainError,
    RuntimeTransportError,
    SolidWorksRuntimeTransport,
    check_deadline,
    check_op,
)
from cdt_solidworks.runtime.workstation_agent import (
    WorkstationAgentConfig,
    WorkstationSolidWorksRuntimeAgent,
)
from tests.runtime.test_local_port import FakeComApi


def _live_session(**kwargs) -> SolidWorksSession:
    return SolidWorksSession(api=FakeComApi(**kwargs))


def _agent_for(session: SolidWorksSession, **config_kwargs):
    config = WorkstationAgentConfig(auth_token="secret-token", **config_kwargs)
    agent = WorkstationSolidWorksRuntimeAgent(LocalSessionAdapter(session), config)
    base_url = agent.start()
    return agent, base_url


# -- contract bounds: refused before dispatch, never a CAD effect --


def test_check_op_refuses_unknown_before_dispatch() -> None:
    with pytest.raises(RuntimeOpRefusedError):
        check_op("execute")
    with pytest.raises(RuntimeOpRefusedError):
        check_op("OpenDoc6")
    assert check_op("probe") == "probe"


def test_check_deadline_is_bounded() -> None:
    with pytest.raises(ValueError):
        check_deadline(50)
    with pytest.raises(ValueError):
        check_deadline(10_000_000)
    assert check_deadline(None) > 0


def test_local_transport_requires_port_and_honors_generation() -> None:
    session = _live_session()
    try:
        with pytest.raises(TypeError):
            LocalSolidWorksTransport(object())  # type: ignore[arg-type]
        transport = LocalSolidWorksTransport(LocalSessionAdapter(session), generation="g1")
        with pytest.raises(RuntimeGenerationMismatchError):
            transport.call("probe", expected_generation="stale")
        health = transport.health()
        assert health["generation"] == "g1"
        assert health["reachable"] is True
        transport.close()
        with pytest.raises(RuntimeUnavailableError):
            transport.call("probe")
    finally:
        session.close_dispatcher(timeout=0.5)


def test_remote_transport_constructor_bounds() -> None:
    with pytest.raises(ValueError):
        RemoteSolidWorksTransport("http://127.0.0.1:9", "")
    with pytest.raises(RuntimeOpRefusedError):
        RemoteSolidWorksTransport("http://example.com:9", "token")
    transport = RemoteSolidWorksTransport("http://127.0.0.1:9", "token")
    assert "token" not in repr(transport)


def test_remote_transport_refused_connection_is_clean_unavailable() -> None:
    transport = RemoteSolidWorksTransport("http://127.0.0.1:9", "token")
    with pytest.raises(RuntimeUnavailableError):
        transport.call("probe")


# -- agent: auth, generation, allowlist, serial dispatch --


def test_agent_requires_port_and_token() -> None:
    session = _live_session()
    try:
        with pytest.raises(TypeError):
            WorkstationSolidWorksRuntimeAgent(object(), WorkstationAgentConfig(auth_token="x"))  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            WorkstationAgentConfig(auth_token="  ")
        with pytest.raises(ValueError):
            WorkstationAgentConfig(auth_token="x", host="example.com")
    finally:
        session.close_dispatcher(timeout=0.5)


def test_agent_restart_mints_new_generation() -> None:
    first = _live_session()
    second = _live_session()
    agent_first, _ = _agent_for(first)
    agent_second, _ = _agent_for(second)
    try:
        assert agent_first.generation != agent_second.generation
        assert agent_first.heartbeat()["generation"] == agent_first.generation
    finally:
        agent_first.stop()
        agent_second.stop()
        first.close_dispatcher(timeout=0.5)
        second.close_dispatcher(timeout=0.5)


def test_agent_refuses_unknown_op_and_stale_generation_before_cad() -> None:
    existing = object()
    api = FakeComApi(attach_app=existing)
    session = SolidWorksSession(api=api)
    agent, _ = _agent_for(session)
    try:
        refused = agent.dispatch("execute", [], {})
        assert refused["ok"] is False
        assert refused["error_code"] == "unknown_op"
        stale = agent.dispatch(
            "probe", [], {}, expected_generation="stale-gen", deadline_ms=1000
        )
        assert stale["ok"] is False
        assert stale["error_code"] == "generation_mismatch"
    finally:
        agent.stop()
        session.close_dispatcher(timeout=0.5)


def test_remote_auth_failure_is_typed() -> None:
    session = _live_session(attach_app=object())
    agent, base_url = _agent_for(session)
    try:
        bad = RemoteSolidWorksTransport(base_url, "wrong-token")
        with pytest.raises(RuntimeAuthError):
            bad.health()
        with pytest.raises(RuntimeAuthError):
            bad.call("probe")
    finally:
        agent.stop()
        session.close_dispatcher(timeout=0.5)


def test_remote_probe_round_trip_is_typed_and_equal() -> None:
    existing = object()
    session = SolidWorksSession(api=FakeComApi(attach_app=existing, registered=True))
    local = LocalSessionAdapter(session)
    agent, base_url = _agent_for(session)
    try:
        expected = local.probe(timeout=1.0)
        transport = RemoteSolidWorksTransport(base_url, "secret-token")
        remote = RemoteSessionAdapter(
            transport, workstation_session_id=session.session_id
        )
        actual = remote.probe(timeout=1.0)
        assert actual.state is expected.state
        assert actual.value == expected.value
        assert remote.session_id == session.session_id
    finally:
        agent.stop()
        session.close_dispatcher(timeout=0.5)


def test_remote_connect_disconnect_round_trip_with_snapshots() -> None:
    existing = object()
    session = SolidWorksSession(api=FakeComApi(attach_app=existing))
    agent, base_url = _agent_for(session)
    try:
        transport = RemoteSolidWorksTransport(base_url, "secret-token")
        remote = RemoteSessionAdapter(
            transport, workstation_session_id=session.session_id
        )
        connected = remote.connect(policy=AttachPolicy.ATTACH_OR_START, timeout=2.0)
        assert connected.state is NativeCallState.SUCCESS
        assert connected.value.ownership is ApplicationOwnership.USER_OWNED
        assert remote.connected is True
        assert remote.ownership is ApplicationOwnership.USER_OWNED
        assert remote.disconnect(timeout=2.0).state is NativeCallState.SUCCESS
        assert remote.connected is False
    finally:
        agent.stop()
        session.close_dispatcher(timeout=0.5)


def test_remote_generation_pin_discards_stale_results() -> None:
    session = _live_session(attach_app=object())
    agent, base_url = _agent_for(session)
    try:
        transport = RemoteSolidWorksTransport(base_url, "secret-token")
        remote = RemoteSessionAdapter(
            transport,
            workstation_session_id=session.session_id,
            expected_generation="pinned-elsewhere",
        )
        with pytest.raises(RuntimeGenerationMismatchError):
            remote.probe(timeout=1.0)
        remote.pin_generation(agent.generation)
        assert remote.probe(timeout=1.0).state is NativeCallState.SUCCESS
    finally:
        agent.stop()
        session.close_dispatcher(timeout=0.5)


def test_remote_adapter_has_no_com_or_callable_surface() -> None:
    session = _live_session()
    agent, base_url = _agent_for(session)
    try:
        transport = RemoteSolidWorksTransport(base_url, "secret-token")
        remote = RemoteSessionAdapter(
            transport, workstation_session_id=session.session_id
        )
        with pytest.raises(RuntimeUnavailableError):
            _ = remote.api
        with pytest.raises(RuntimeOpRefusedError):
            remote.execute(lambda app: None, stage="x", timeout=1.0)
        with pytest.raises(RuntimeOpRefusedError):
            remote.close_dispatcher()
        with pytest.raises(RuntimeOpRefusedError):
            remote.reconcile(
                "call", lambda app: None, stage="x", timeout=1.0, identity=(session.session_id,)
            )
        with pytest.raises(ValueError):
            RemoteSessionAdapter(transport, workstation_session_id="  ")
    finally:
        agent.stop()
        session.close_dispatcher(timeout=0.5)


def test_remote_health_reports_agent_and_lane() -> None:
    session = _live_session(attach_app=object())
    agent, base_url = _agent_for(session)
    try:
        transport = RemoteSolidWorksTransport(base_url, "secret-token")
        remote = RemoteSessionAdapter(
            transport, workstation_session_id=session.session_id
        )
        health = remote.health()
        assert health["transport"] == "remote"
        assert health["reachable"] is True
        assert health["agent"]["generation"] == agent.generation
        assert health["writer_lane"]["quarantined"] is False
    finally:
        agent.stop()
        session.close_dispatcher(timeout=0.5)


def test_remote_timeout_after_dispatch_is_uncertain_and_quarantines() -> None:
    started = threading.Event()

    class SlowApi(FakeComApi):
        def attach_application(self, prog_id: str):
            started.set()
            time.sleep(2.0)
            return object()

    session = SolidWorksSession(api=SlowApi())
    agent, base_url = _agent_for(session)
    try:
        transport = RemoteSolidWorksTransport(base_url, "secret-token")
        remote = RemoteSessionAdapter(
            transport,
            workstation_session_id=session.session_id,
            default_deadline_ms=300,
        )
        with pytest.raises(RuntimeUncertainError):
            remote.connect(policy=AttachPolicy.ATTACH_OR_START, timeout=2.0)
        assert started.is_set()
        # Provider-side lane quarantines the next mutation; reads stay available
        # once the agent worker is free again.
        with pytest.raises(RuntimeUncertainError):
            remote.connect(policy=AttachPolicy.ATTACH_OR_START, timeout=2.0)
        time.sleep(2.5)
        assert remote.probe(timeout=2.0).state is NativeCallState.SUCCESS
    finally:
        agent.stop()
        session.close_dispatcher(timeout=0.5)

class RecoveryTransport(SolidWorksRuntimeTransport):
    """Script a lost lifecycle reply and authenticated native recovery evidence."""

    def __init__(self, *, snapshot=None, receipt=None, known_call=False):
        self.snapshot = {
            "session_id": "workstation-session",
            "uncertain_call_id": "native-call",
        } if snapshot is None else snapshot
        self.receipt = {
            "state": "success", "call_id": "native-call", "value": True, "dispatched": True,
        } if receipt is None else receipt
        self.known_call = known_call
        self.calls = []

    def call(self, op, args=(), kwargs=None, **options):
        self.calls.append(op)
        if op == "connect":
            if self.known_call:
                return {
                    "state": "uncertain_after_dispatch", "call_id": "native-call",
                    "dispatched": True,
                }
            raise RuntimeUncertainError("lifecycle reply lost")
        if op == "runtime_status":
            return self.snapshot
        if op == "reconcile":
            return self.receipt
        if op == "disconnect":
            return {
                "state": "success", "call_id": "disconnect-call",
                "value": True, "dispatched": True,
            }
        return {}

    def health(self):
        return {}

    def close(self):
        pass


def recovery_remote(transport):
    remote = RemoteSessionAdapter(
        transport, workstation_session_id="workstation-session", expected_generation="generation-1",
    )
    if transport.known_call:
        assert remote.connect().state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
    else:
        with pytest.raises(RuntimeUncertainError):
            remote.connect()
    return remote


def test_lost_reply_recovery_binds_observed_native_call_and_releases_writer():
    transport = RecoveryTransport()
    remote = recovery_remote(transport)
    result = remote.reconcile(
        "native-call", stage="recover", timeout=1.0, identity=("target",),
    )
    assert result.state is NativeCallState.SUCCESS
    assert remote.writer_lane.status()["quarantined"] is False
    assert remote.disconnect().state is NativeCallState.SUCCESS
    assert transport.calls.count("connect") == 1
    assert transport.calls.count("reconcile") == 1


@pytest.mark.parametrize("snapshot", [
    {},
    {"session_id": "another-session", "uncertain_call_id": "native-call"},
    {"session_id": "workstation-session", "uncertain_call_id": "unrelated-call"},
    {"session_id": "workstation-session", "uncertain_call_id": None},
])
def test_lost_reply_recovery_refuses_unbound_native_identity(snapshot):
    transport = RecoveryTransport(snapshot=snapshot)
    remote = recovery_remote(transport)
    with pytest.raises(RuntimeOpRefusedError):
        remote.reconcile("native-call", stage="recover", timeout=1.0, identity=("target",))
    assert "reconcile" not in transport.calls
    assert remote.writer_lane.status()["quarantined"] is True
    with pytest.raises(RuntimeUncertainError):
        remote.disconnect()


@pytest.mark.parametrize("known_call", [False, True])
def test_wrong_recovery_receipt_cannot_clear_writer(known_call):
    transport = RecoveryTransport(
        known_call=known_call,
        receipt={"state": "success", "call_id": "unrelated-call", "value": True},
    )
    remote = recovery_remote(transport)
    with pytest.raises(RuntimeTransportError):
        remote.reconcile("native-call", stage="recover", timeout=1.0, identity=("target",))
    assert remote.writer_lane.status()["quarantined"] is True


@pytest.mark.parametrize("state", ["failure", "uncertain_after_dispatch"])
def test_unsuccessful_recovery_keeps_writer_quarantined(state):
    transport = RecoveryTransport(receipt={"state": state, "call_id": "native-call"})
    remote = recovery_remote(transport)
    remote.reconcile("native-call", stage="recover", timeout=1.0, identity=("target",))
    assert remote.writer_lane.status()["quarantined"] is True


def test_known_native_quarantine_refuses_recovery_of_another_call():
    transport = RecoveryTransport(known_call=True)
    remote = recovery_remote(transport)
    with pytest.raises(RuntimeOpRefusedError):
        remote.reconcile("unrelated-call", stage="recover", timeout=1.0, identity=("target",))
    assert "reconcile" not in transport.calls
    assert remote.writer_lane.status()["quarantined"] is True
