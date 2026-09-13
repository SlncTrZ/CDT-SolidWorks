"""Integrated provider runtime — compose platform status with native document services."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.document.service import DocumentService
from cdt_solidworks.body.native import BodyNativeAdapter
from cdt_solidworks.surface.native import SurfaceNativeAdapter
from cdt_solidworks.sheetmetal.native import SheetMetalNativeAdapter
from cdt_solidworks.weldment.native import WeldmentNativeAdapter
from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import SolidWorksSession
from cdt_solidworks.integration.sketch import IntegratedSketchService
from cdt_solidworks.platform.models import CapabilityState, DependencyState, RuntimeContext


class IntegratedProviderRuntime:
    """Own the native session and expose capability-honest provider composition."""

    def __init__(
        self,
        *,
        allowed_roots: Iterable[str | Path],
        weldment_profile_roots: Iterable[str | Path] = (),
        version: int | None = None,
        session: Any | None = None,
        document_service: Any | None = None,
        cad_service: Any | None = None,
        sketch_service: Any | None = None,
        body_service: Any | None = None,
        surface_service: Any | None = None,
        sheetmetal_service: Any | None = None,
        weldment_service: Any | None = None,
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
        self.sketch_service = sketch_service
        if self.sketch_service is None and hasattr(self.session, "api"):
            self.sketch_service = IntegratedSketchService(
                self.session, path_policy=self.path_policy
            )
        self.body_service = body_service
        self.surface_service = surface_service
        self.sheetmetal_service = sheetmetal_service
        self.weldment_profile_roots = tuple(weldment_profile_roots)
        self.weldment_service = weldment_service
        self.weldment_profiles_configured = bool(self.weldment_profile_roots) or weldment_service is not None
        if hasattr(self.session, "api"):
            if self.body_service is None:
                self.body_service = BodyNativeAdapter(self.session, path_policy=self.path_policy)
            if self.surface_service is None:
                self.surface_service = SurfaceNativeAdapter(self.session, path_policy=self.path_policy)
            if self.sheetmetal_service is None:
                self.sheetmetal_service = SheetMetalNativeAdapter(self.session, path_policy=self.path_policy)
            if self.weldment_service is None:
                self.weldment_service = WeldmentNativeAdapter(
                    self.session,
                    path_policy=self.path_policy,
                    profile_roots=self.weldment_profile_roots,
                )

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
        sketch_implemented = self.sketch_service is not None
        sketch_available = sketch_implemented and available
        sketch_reason = integrated_reason if sketch_implemented else deferred_reason
        body_implemented = self.body_service is not None
        body_available = body_implemented and available
        body_reason = integrated_reason if body_implemented else deferred_reason
        surface_implemented = self.surface_service is not None
        surface_available = surface_implemented and available
        surface_reason = integrated_reason if surface_implemented else deferred_reason
        sheetmetal_implemented = self.sheetmetal_service is not None
        sheetmetal_available = sheetmetal_implemented and available
        sheetmetal_reason = integrated_reason if sheetmetal_implemented else deferred_reason
        weldment_implemented = self.weldment_service is not None
        weldment_available = weldment_implemented and available
        weldment_reason = integrated_reason if weldment_implemented else deferred_reason
        if not available:
            structural_member_available = False
            structural_member_reason = integrated_reason
        elif not weldment_implemented:
            structural_member_available = False
            structural_member_reason = deferred_reason
        elif not self.weldment_profiles_configured:
            structural_member_available = False
            structural_member_reason = "weldment_profile_roots_not_configured"
        else:
            structural_member_available = True
            structural_member_reason = None
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
                name="solidworks.sketch.geometry",
                implemented=sketch_implemented,
                available=sketch_available,
                reason=sketch_reason,
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
                name="solidworks.body.inspect",
                implemented=body_implemented,
                available=body_available,
                reason=body_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.body.combine",
                implemented=body_implemented,
                available=body_available,
                reason=body_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.surface.thicken",
                implemented=surface_implemented,
                available=surface_available,
                reason=surface_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.surface.knit",
                implemented=False,
                available=False,
                reason="native_evidence_pending",
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.sheet_metal.inspect",
                implemented=sheetmetal_implemented,
                available=sheetmetal_available,
                reason=sheetmetal_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.sheet_metal.flat_pattern",
                implemented=sheetmetal_implemented,
                available=sheetmetal_available,
                reason=sheetmetal_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.sheet_metal.edge_flange",
                implemented=False,
                available=False,
                reason="persistent_edge_identity_not_integrated",
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.weldment.cut_list",
                implemented=weldment_implemented,
                available=weldment_available,
                reason=weldment_reason,
                backend="solidworks_com",
                dependencies=("solidworks",),
            ),
            CapabilityState(
                name="solidworks.weldment.structural_member",
                implemented=weldment_implemented,
                available=structural_member_available,
                reason=structural_member_reason,
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
