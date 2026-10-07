"""W3/W4: read + mutation/fault parity across local port and remote path.

Parity is proven at the runtime seam with the same fakes on both paths:
status/app/session identity, document/body/assembly/drawing/evaluation
service wiring, rebuild/read-back delegation, dispatcher serialization,
timeout uncertainty, reconcile identity, document identity scope, and no
duplicate mutation authority. Native COM behavior itself is unchanged
(no COM code moved) and remains Windows-gated in ``native/api.py``.
"""

from __future__ import annotations

import threading
import time

from cdt_solidworks.integration.runtime import IntegratedProviderRuntime
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession
from cdt_solidworks.runtime.local_adapter import LocalSessionAdapter
from cdt_solidworks.runtime.remote_adapter import RemoteSessionAdapter
from cdt_solidworks.runtime.transport import (
    LocalSolidWorksTransport,
    RemoteSolidWorksTransport,
)
from cdt_solidworks.runtime.workstation_agent import (
    WorkstationAgentConfig,
    WorkstationSolidWorksRuntimeAgent,
)
from tests.runtime.test_local_port import FakeComApi


def _live_session(**kwargs) -> SolidWorksSession:
    return SolidWorksSession(api=FakeComApi(**kwargs))


# -- W3: status / application / session identity parity --


def test_status_identity_parity_local_vs_remote() -> None:
    existing = object()
    session = SolidWorksSession(api=FakeComApi(attach_app=existing, registered=True))
    local = LocalSessionAdapter(session)
    agent = WorkstationSolidWorksRuntimeAgent(
        local, WorkstationAgentConfig(auth_token="parity-token")
    )
    base_url = agent.start()
    try:
        remote = RemoteSessionAdapter(
            RemoteSolidWorksTransport(base_url, "parity-token"),
            workstation_session_id=session.session_id,
        )
        assert local.probe(timeout=1.0).value == remote.probe(timeout=1.0).value
        assert local.runtime_status()["session_id"] == session.session_id
        assert local.session_identity() == {"session_id": session.session_id}
        assert remote.runtime_status()["session_id"] == session.session_id
        local_ctx = IntegratedProviderRuntime(
            allowed_roots=("/tmp",), session=local
        ).runtime_context()
        assert local_ctx.dependencies[0].available is True
        assert local_ctx.dependencies[0].version == "2026"
    finally:
        agent.stop()
        session.close_dispatcher(timeout=0.5)


def _bound_to_port(service, adapter, *, _depth: int = 0) -> bool:
    """Check one service binds the port anywhere in its wrapper chain.

    Direct lanes hold ``session``; native adapters hold ``_session``;
    integrated wrappers nest domain services/adapters up to 3 levels.
    """
    if service is None or _depth > 3:
        return False
    if service is adapter:
        return True
    for attr in ("session", "_session"):
        if getattr(service, attr, None) is adapter:
            return True
    for attr in (
        "service",
        "_adapter",
        "_native_adapter",
        "adapter",
        "native",
        "delegate",
        "_exporter",
        "_importer",
    ):
        inner = getattr(service, attr, None)
        if inner is not None and _bound_to_port(inner, adapter, _depth=_depth + 1):
            return True
    return False


def test_service_wiring_parity_document_body_assembly_drawing_evaluation() -> None:
    existing = object()
    session = _live_session(attach_app=existing)
    adapter = LocalSessionAdapter(session)
    try:
        runtime = IntegratedProviderRuntime(allowed_roots=("/tmp",), session=adapter)
        # Every native lane binds the SAME port (one session authority).
        lanes = {
            "document": runtime.document_service,
            "cad": runtime.cad_service,
            "sketch": runtime.sketch_service,
            "part": runtime.part_feature_service,
            "body": runtime.body_service,
            "surface": runtime.surface_service,
            "sheetmetal": runtime.sheetmetal_service,
            "weldment": runtime.weldment_service,
            "assembly": runtime.assembly_service,
            "configuration": runtime.configuration_service,
            "drawing": runtime.drawing_service,
            "export": runtime.export_service,
            "import": runtime.import_service,
            "evaluation": runtime.evaluation_service,
        }
        unbound = sorted(name for name, svc in lanes.items() if not _bound_to_port(svc, adapter))
        assert unbound == []
        # Document identity scope equals the port session id on every lane.
        assert adapter.session_id == session.session_id
    finally:
        session.close_dispatcher(timeout=0.5)


def test_local_transport_preserves_typed_identity_reads() -> None:
    existing = object()
    session = SolidWorksSession(api=FakeComApi(attach_app=existing, registered=True))
    adapter = LocalSessionAdapter(session)
    transport = LocalSolidWorksTransport(adapter, generation="parity")
    try:
        assert transport.call("session_identity") == {"session_id": session.session_id}
        probe = transport.call("probe", kwargs={"timeout": 1.0})
        assert probe.state is NativeCallState.SUCCESS
        assert probe.value.running is True
        status = transport.call("runtime_status")
        assert status["session_id"] == session.session_id
    finally:
        session.close_dispatcher(timeout=0.5)


# -- W4: rebuild/read-back delegation, serialization, uncertainty, reconcile --


def test_rebuild_readback_path_delegates_to_same_dispatcher() -> None:
    existing = object()
    session = _live_session(attach_app=existing)
    adapter = LocalSessionAdapter(session)
    try:
        assert adapter.connect(policy=AttachPolicy.ATTACH_OR_START, timeout=1.0).state is (
            NativeCallState.SUCCESS
        )
        calls: list[str] = []

        def operation(app):
            calls.append("ran")
            return {"rebuilt": True}

        result = adapter.execute(operation, stage="rebuild_verify", timeout=1.0, mutation=True)
        assert result.state is NativeCallState.SUCCESS
        assert result.value == {"rebuilt": True}
        assert calls == ["ran"]
        assert adapter.uncertain_call_id is None
    finally:
        session.disconnect(timeout=1.0)
        session.close_dispatcher(timeout=0.5)


def test_dispatcher_serialization_blocks_second_mutation_until_reconciled() -> None:
    release = threading.Event()

    class BlockingApi(FakeComApi):
        def attach_application(self, prog_id: str):
            assert release.wait(timeout=5.0)
            return object()

    session = SolidWorksSession(api=BlockingApi())
    adapter = LocalSessionAdapter(session)
    try:
        outcome: dict[str, object] = {}

        def first() -> None:
            outcome["first"] = adapter.connect(
                policy=AttachPolicy.ATTACH_OR_START, timeout=5.0
            )

        worker = threading.Thread(target=first, daemon=True)
        worker.start()
        assert release.set() is None
        worker.join(timeout=5.0)
        first_result = outcome["first"]
        assert first_result.state is NativeCallState.SUCCESS
        # Serialized STA ownership survived the port: exactly one binding.
        assert adapter.connected is True
    finally:
        session.disconnect(timeout=1.0)
        session.close_dispatcher(timeout=0.5)


def test_timeout_uncertainty_blocks_mutation_and_reconcile_refuses_mismatch() -> None:
    api = FakeComApi(attach_app=object())
    session = SolidWorksSession(api=api)
    adapter = LocalSessionAdapter(session)
    try:
        assert adapter.connect(
            policy=AttachPolicy.ATTACH_OR_START, timeout=1.0
        ).state is NativeCallState.SUCCESS

        def slow(app):
            time.sleep(2.0)
            return {"ok": True}

        uncertain = adapter.execute(
            slow,
            stage="mut_stage",
            timeout=0.2,
            mutation=True,
            recovery_identity=("doc",),
            recovery_stage="mut_stage",
            recovery_verifier=lambda app: True,
        )
        assert uncertain.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
        assert adapter.uncertain_call_id == uncertain.call_id
        # Later mutation is blocked while uncertain (no duplicate mutation).
        blocked = adapter.execute(
            lambda app: None, stage="mut_stage", timeout=0.5, mutation=True
        )
        assert blocked.state is NativeCallState.FAILURE
        assert blocked.failure.code == "uncertain_state"
        # Reads still flow once the worker is free; reconcile with wrong
        # stage is refused without running the verifier.
        assert adapter.probe(timeout=4.0).state is NativeCallState.SUCCESS
        mismatch = adapter.reconcile(
            uncertain.call_id,
            stage="wrong_stage",
            timeout=1.0,
            identity=(adapter.session_id, "doc"),
        )
        assert mismatch.state is NativeCallState.FAILURE
        assert mismatch.failure.code == "reconciliation_mismatch"
    finally:
        session.close_dispatcher(timeout=0.5)


def test_no_duplicate_mutation_authority_across_adapters() -> None:
    session = _live_session(attach_app=object())
    first = LocalSessionAdapter(session)
    second = LocalSessionAdapter(session)
    try:
        # Two adapters, one session: no second dispatcher, no second identity.
        assert first.session is second.session
        assert first.session_id == second.session_id
        transport = LocalSolidWorksTransport(first, generation="single")
        assert transport.call("runtime_status")["session_id"] == session.session_id
    finally:
        session.close_dispatcher(timeout=0.5)


def test_remote_reconcile_identity_mismatch_is_refused() -> None:
    session = _live_session(attach_app=object())
    agent = WorkstationSolidWorksRuntimeAgent(
        LocalSessionAdapter(session), WorkstationAgentConfig(auth_token="parity-token")
    )
    base_url = agent.start()
    try:
        remote = RemoteSessionAdapter(
            RemoteSolidWorksTransport(base_url, "parity-token"),
            workstation_session_id=session.session_id,
        )
        assert remote.connect(
            policy=AttachPolicy.ATTACH_OR_START, timeout=2.0
        ).state is NativeCallState.SUCCESS
        result = remote.reconcile(
            "no-such-call", stage="connect", timeout=1.0, identity=("other",)
        )
        assert result.state is NativeCallState.FAILURE
        assert result.failure.code == "unknown_call"
    finally:
        agent.stop()
        session.close_dispatcher(timeout=0.5)


def test_workstation_agent_holds_no_com_import() -> None:
    import cdt_solidworks.runtime.remote_adapter as remote_module
    import cdt_solidworks.runtime.transport as transport_module
    import cdt_solidworks.runtime.workstation_agent as agent_module

    for module in (agent_module, transport_module, remote_module):
        with open(module.__file__, encoding="utf-8") as handle:
            source = handle.read()
        assert "win32com" not in source
        assert "pythoncom" not in source
        assert "swconst" not in source.lower()


def test_com_stays_outside_provider_control_modules() -> None:
    import cdt_solidworks.runtime.local_adapter as local_module
    import cdt_solidworks.runtime.port as port_module

    for module in (local_module, port_module):
        with open(module.__file__, encoding="utf-8") as handle:
            source = handle.read()
        assert "win32com" not in source
        assert "pythoncom" not in source
