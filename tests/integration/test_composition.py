from __future__ import annotations

import asyncio
from pathlib import Path

from cdt_solidworks.integration.runtime import IntegratedProviderRuntime
from cdt_solidworks.integration.server import build_integrated_server
from cdt_solidworks.native.models import ApplicationProbe, NativeCallResult
from cdt_solidworks.server.factory import ServerConfig


class _FakeSession:
    def __init__(self, result: NativeCallResult[ApplicationProbe]) -> None:
        self._result = result
        self.session_id = "session-1"

    def probe(self, *, version=None, timeout=3.0):
        return self._result


class _FakeDocumentService:
    pass


def _success_probe(*, registered: bool, running: bool) -> NativeCallResult[ApplicationProbe]:
    return NativeCallResult.success(
        ApplicationProbe(
            prog_id="SldWorks.Application",
            registered=registered,
            running=running,
            revision="34.0",
            version_year=2026,
        ),
        call_id="probe-1",
        dispatched=True,
    )


def test_runtime_context_exposes_only_integrated_native_capabilities() -> None:
    runtime = IntegratedProviderRuntime(
        allowed_roots=("/tmp",),
        session=_FakeSession(_success_probe(registered=True, running=True)),
        document_service=_FakeDocumentService(),
    )
    context = runtime.runtime_context()
    states = {item.name: item for item in context.capabilities}
    assert context.dependencies[0].available is True
    assert context.dependencies[0].version == "2026"
    assert states["solidworks.document.lifecycle"].implemented is True
    assert states["solidworks.document.lifecycle"].available is True
    assert states["solidworks.document.query"].implemented is True
    assert states["solidworks.part.parametric"].implemented is False
    assert states["solidworks.part.parametric"].reason == "native_adapter_not_integrated"
    assert states["solidworks.assembly.mates"].implemented is False


def test_runtime_context_reports_unregistered_solidworks_unavailable() -> None:
    runtime = IntegratedProviderRuntime(
        allowed_roots=("/tmp",),
        session=_FakeSession(_success_probe(registered=False, running=False)),
        document_service=_FakeDocumentService(),
    )
    context = runtime.runtime_context()
    states = {item.name: item for item in context.capabilities}
    assert context.dependencies[0].available is False
    assert context.dependencies[0].reason == "solidworks_not_registered"
    assert states["solidworks.document.lifecycle"].available is False
    assert states["solidworks.document.lifecycle"].reason == "solidworks_not_registered"


def test_integrated_server_registers_native_document_tools(tmp_path: Path) -> None:
    guide = tmp_path / "guide.md"
    guide.write_text("# Integrated provider\n", encoding="utf-8")
    runtime = IntegratedProviderRuntime(
        allowed_roots=(tmp_path,),
        session=_FakeSession(_success_probe(registered=True, running=True)),
        document_service=_FakeDocumentService(),
    )
    server = build_integrated_server(ServerConfig.in_process(guide_path=guide), runtime=runtime)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert {"help", "system_status", "system_capabilities", "application_probe", "application_connect", "application_disconnect", "document_open", "document_info", "document_save", "document_save_as", "document_close", "document_reopen", "document_list_features", "document_list_bodies", "document_list_components", "document_rebuild", "document_reconcile"} <= names


def test_runtime_context_exposes_license_probe_as_unimplemented() -> None:
    runtime = IntegratedProviderRuntime(
        allowed_roots=("/tmp",),
        session=_FakeSession(_success_probe(registered=True, running=True)),
        document_service=_FakeDocumentService(),
    )
    states = {item.name: item for item in runtime.runtime_context().capabilities}
    assert states["solidworks.license"].implemented is False
    assert states["solidworks.license"].available is False
    assert states["solidworks.license"].reason == "license_probe_not_integrated"
