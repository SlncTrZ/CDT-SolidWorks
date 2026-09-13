"""Integrated provider runtime — compose platform status with native document services."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.document.service import DocumentService
from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import SolidWorksSession
from cdt_solidworks.platform.models import CapabilityState, DependencyState, RuntimeContext


class IntegratedProviderRuntime:
    """Own the native session and expose capability-honest provider composition."""

    def __init__(
        self,
        *,
        allowed_roots: Iterable[str | Path],
        version: int | None = None,
        session: Any | None = None,
        document_service: Any | None = None,
        cad_service: Any | None = None,
    ) -> None:
        self.version = version
        self.session = session if session is not None else SolidWorksSession()
        self.path_policy = DocumentPathPolicy(allowed_roots)
        self.document_service = (
            document_service
            if document_service is not None
            else DocumentService(self.session, path_policy=self.path_policy)
        )
        self.cad_service = cad_service
        if self.cad_service is None and hasattr(self.session, "api"):
            self.cad_service = CadCoreService(self.session, path_policy=self.path_policy)

    def runtime_context(self) -> RuntimeContext:
        result = self.session.probe(version=self.version, timeout=3.0)
        available = False
        reason: str | None = None
        version: str | None = None

        if result.state is NativeCallState.SUCCESS and result.value is not None:
            probe = result.value
            version = str(probe.version_year) if probe.version_year is not None else probe.revision
            if not probe.registered:
                reason = "solidworks_not_registered"
            elif not probe.running:
                reason = "solidworks_not_running_or_license_unverified"
            else:
                available = True
        else:
            reason = result.failure.code if result.failure is not None else "solidworks_probe_failed"

        dependency = DependencyState(
            name="solidworks",
            available=available,
            reason=reason,
            version=version,
            license=None,
        )
        integrated_reason = None if available else reason
        deferred_reason = "native_adapter_not_integrated"
        partial_reason = "partial_native_support"
        cad_implemented = self.cad_service is not None
        cad_available = cad_implemented and available
        cad_reason = integrated_reason if cad_implemented else deferred_reason
        capabilities = (
            CapabilityState(
                name="solidworks.application",
                implemented=True,
                available=available,
                reason=integrated_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.document.lifecycle",
                implemented=True,
                available=available,
                reason=integrated_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.document.query",
                implemented=True,
                available=available,
                reason=integrated_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.license",
                implemented=False,
                available=False,
                reason="license_probe_not_integrated",
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.part.parametric",
                implemented=False,
                available=False,
                reason=partial_reason if cad_implemented else deferred_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.sketch.rectangle",
                implemented=cad_implemented,
                available=cad_available,
                reason=cad_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.part.extrude",
                implemented=cad_implemented,
                available=cad_available,
                reason=cad_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.part.multibody",
                implemented=cad_implemented,
                available=cad_available,
                reason=cad_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.part.combine",
                implemented=cad_implemented,
                available=cad_available,
                reason=cad_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.part.split",
                implemented=cad_implemented,
                available=cad_available,
                reason=cad_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.sheet_metal.base_flange",
                implemented=cad_implemented,
                available=cad_available,
                reason=cad_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.surface.extrude",
                implemented=cad_implemented,
                available=cad_available,
                reason=cad_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.assembly.components",
                implemented=cad_implemented,
                available=cad_available,
                reason=cad_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.assembly.coincident_mate",
                implemented=cad_implemented,
                available=cad_available,
                reason=cad_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.assembly.mates",
                implemented=False,
                available=False,
                reason=partial_reason if cad_implemented else deferred_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.configurations",
                implemented=False,
                available=False,
                reason=deferred_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.drawing",
                implemented=False,
                available=False,
                reason=deferred_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.export",
                implemented=False,
                available=False,
                reason=deferred_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.simulation.study",
                implemented=False,
                available=False,
                reason="native_adapter_not_integrated",
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.flow_simulation",
                implemented=False,
                available=False,
                reason="dependency_probe_not_integrated",
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.electrical",
                implemented=False,
                available=False,
                reason="dependency_probe_not_integrated",
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
        )
        return RuntimeContext(
            backend="solidworks_com",
            dependencies=(dependency,),
            capabilities=capabilities,
        )

    def register_tools(self, server: Any) -> None:
        from .registrar import register_runtime_tools

        register_runtime_tools(server, self)
