from __future__ import annotations

import asyncio
from pathlib import Path

from cdt_solidworks.integration.runtime import IntegratedProviderRuntime
from cdt_solidworks.integration.server import build_integrated_server
from cdt_solidworks.native.models import ApplicationProbe, NativeCallResult
from cdt_solidworks.server.factory import ServerConfig
from cdt_solidworks.server.validation import _TOOL_ARGUMENTS


class _FakeSession:
    def __init__(self, result: NativeCallResult[ApplicationProbe]) -> None:
        self._result = result
        self.session_id = "session-1"

    def probe(self, *, version=None, timeout=3.0):
        return self._result


class _FakeDocumentService:
    pass


class _FakeCadService:
    pass


class _FakeSketchService:
    pass


class _FakePartFeatureService:
    pass


class _FakeBodyService:
    pass


class _FakeSurfaceService:
    pass


class _FakeSheetMetalService:
    pass


class _FakeWeldmentService:
    pass


class _FakeAssemblyService:
    pass


class _FakeConfigurationService:
    pass


class _FakeDrawingService:
    pass


class _FakeExportService:
    pass


class _FakeEvaluationService:
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
        cad_service=_FakeCadService(),
        sketch_service=_FakeSketchService(),
        part_feature_service=_FakePartFeatureService(),
        body_service=_FakeBodyService(),
        surface_service=_FakeSurfaceService(),
        sheetmetal_service=_FakeSheetMetalService(),
        weldment_service=_FakeWeldmentService(),
        assembly_service=_FakeAssemblyService(),
        configuration_service=_FakeConfigurationService(),
        drawing_service=_FakeDrawingService(),
        export_service=_FakeExportService(),
        evaluation_service=_FakeEvaluationService(),
    )
    context = runtime.runtime_context()
    states = {item.name: item for item in context.capabilities}
    assert context.dependencies[0].available is True
    assert context.dependencies[0].version == "2026"
    assert states["solidworks.document.lifecycle"].implemented is True
    assert states["solidworks.document.lifecycle"].available is True
    assert states["solidworks.document.query"].implemented is True
    assert states["solidworks.part.parametric"].implemented is False
    assert states["solidworks.part.parametric"].reason == "partial_native_support"
    assert states["solidworks.sketch.rectangle"].implemented is True
    assert states["solidworks.sketch.rectangle"].available is True
    assert states["solidworks.sketch.geometry"].implemented is True
    assert states["solidworks.sketch.geometry"].available is True
    assert states["solidworks.part.cut_extrude"].implemented is True
    assert states["solidworks.part.cut_extrude"].available is True
    assert states["solidworks.part.combine"].implemented is True
    assert states["solidworks.sheet_metal.base_flange"].implemented is True
    assert states["solidworks.surface.extrude"].implemented is True
    assert states["solidworks.body.inspect"].implemented is True
    assert states["solidworks.body.combine"].available is True
    assert states["solidworks.surface.thicken"].available is True
    assert states["solidworks.surface.knit"].implemented is False
    assert states["solidworks.sheet_metal.inspect"].available is True
    assert states["solidworks.sheet_metal.flat_pattern"].available is True
    assert states["solidworks.sheet_metal.edge_flange"].implemented is False
    assert states["solidworks.weldment.cut_list"].available is True
    assert states["solidworks.weldment.structural_member"].available is True
    assert states["solidworks.assembly.components"].implemented is True
    assert states["solidworks.assembly.component_state"].available is True
    assert states["solidworks.assembly.component_configuration"].available is True
    assert states["solidworks.assembly.coincident_mate"].implemented is True
    assert states["solidworks.assembly.common_mates"].available is True
    assert states["solidworks.assembly.coincident_mate_suppression"].available is True
    assert states["solidworks.assembly.distance_mate_value"].available is True
    assert states["solidworks.assembly.mates"].implemented is False
    assert states["solidworks.assembly.mates"].reason == "partial_native_support"
    assert states["solidworks.configuration.lifecycle"].available is True
    assert states["solidworks.configuration.dimension"].available is True
    assert states["solidworks.configuration.properties"].available is True
    assert states["solidworks.configuration.feature_suppression"].available is True
    assert states["solidworks.configuration.equations"].available is True
    assert states["solidworks.configurations"].implemented is False
    assert states["solidworks.configurations"].reason == "partial_native_support"
    assert states["solidworks.drawing.lifecycle"].available is True
    assert states["solidworks.drawing.front_view"].available is True
    assert states["solidworks.drawing"].implemented is False
    assert states["solidworks.drawing"].reason == "partial_native_support"
    for export_name in ("step", "iges", "parasolid", "stl", "3mf", "pdf", "dxf", "dwg"):
        assert states[f"solidworks.export.{export_name}"].available is True
    assert states["solidworks.export"].implemented is False
    assert states["solidworks.export"].reason == "partial_native_support"
    assert states["solidworks.evaluation.mass_properties"].available is True
    assert states["solidworks.evaluation.bounding_box"].available is True
    assert states["solidworks.evaluation.geometry_sanity"].available is True
    assert states["solidworks.evaluation"].implemented is False
    assert states["solidworks.evaluation"].reason == "partial_native_support"
    assert states["solidworks.mbd"].implemented is False
    assert states["solidworks.simulation.study"].implemented is False
    assert states["solidworks.simulation.study"].reason == "native_adapter_not_integrated"
    assert states["solidworks.flow_simulation"].implemented is False
    assert states["solidworks.flow_simulation"].reason == "dependency_probe_not_integrated"
    assert states["solidworks.electrical"].implemented is False
    assert states["solidworks.electrical"].reason == "dependency_probe_not_integrated"


def test_runtime_context_reports_unregistered_solidworks_unavailable() -> None:
    runtime = IntegratedProviderRuntime(
        allowed_roots=("/tmp",),
        session=_FakeSession(_success_probe(registered=False, running=False)),
        document_service=_FakeDocumentService(),
        cad_service=_FakeCadService(),
    )
    context = runtime.runtime_context()
    states = {item.name: item for item in context.capabilities}
    assert context.dependencies[0].available is False
    assert context.dependencies[0].reason == "solidworks_not_registered"
    assert states["solidworks.document.lifecycle"].available is False
    assert states["solidworks.document.lifecycle"].reason == "solidworks_not_registered"
    assert states["solidworks.part.extrude"].implemented is True
    assert states["solidworks.part.extrude"].available is False
    assert states["solidworks.part.extrude"].reason == "solidworks_not_registered"


def test_integrated_server_registers_native_document_tools(tmp_path: Path) -> None:
    guide = tmp_path / "guide.md"
    guide.write_text("# Integrated provider\n", encoding="utf-8")
    runtime = IntegratedProviderRuntime(
        allowed_roots=(tmp_path,),
        session=_FakeSession(_success_probe(registered=True, running=True)),
        document_service=_FakeDocumentService(),
        cad_service=_FakeCadService(),
        sketch_service=_FakeSketchService(),
        part_feature_service=_FakePartFeatureService(),
        body_service=_FakeBodyService(),
        surface_service=_FakeSurfaceService(),
        sheetmetal_service=_FakeSheetMetalService(),
        weldment_service=_FakeWeldmentService(),
        assembly_service=_FakeAssemblyService(),
        configuration_service=_FakeConfigurationService(),
        drawing_service=_FakeDrawingService(),
        export_service=_FakeExportService(),
        evaluation_service=_FakeEvaluationService(),
    )
    server = build_integrated_server(ServerConfig.in_process(guide_path=guide), runtime=runtime)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert {
        "help", "system_status", "system_capabilities",
        "application_probe", "application_connect", "application_disconnect",
        "document_open", "document_info", "document_save", "document_save_as",
        "document_close", "document_reopen", "document_list_features",
        "document_list_bodies", "document_list_components", "document_rebuild",
        "document_reconcile", "sketch_create_geometry", "sketch_get",
        "part_cut_extrude", "body_inspect", "body_combine", "surface_thicken",
        "sheet_metal_inspect", "sheet_metal_set_flattened",
        "weldment_inspect", "weldment_create_structural_member",
        "sketch_create_rectangle", "part_create_rect_extrude",
        "part_add_rect_extrude", "part_combine_all_bodies", "part_split_by_plane",
        "sheet_metal_create_base_flange", "surface_create_extrude",
        "assembly_create", "assembly_add_coincident_plane_mate",
        "assembly_components_list", "assembly_component_set_fixed",
        "assembly_component_set_load_state", "assembly_component_set_configuration",
        "assembly_mate_create", "assembly_mates_list",
        "assembly_coincident_mate_set_suppressed", "assembly_distance_mate_set_value",
        "configuration_list", "configuration_create", "configuration_rename",
        "configuration_delete", "configuration_activate",
        "configuration_set_dimension", "configuration_set_property",
        "configuration_delete_property", "configuration_set_feature_suppressed",
        "configuration_equations_list", "configuration_equation_add",
        "configuration_equation_set", "configuration_equation_delete",
        "drawing_create", "drawing_sheet_create", "drawing_front_view_create",
        "export_document", "evaluation_mass_properties",
        "evaluation_bounding_box", "evaluation_geometry_sanity",
    } <= names
    assert names <= set(_TOOL_ARGUMENTS)


def test_runtime_context_exposes_license_probe_as_unimplemented() -> None:
    runtime = IntegratedProviderRuntime(
        allowed_roots=("/tmp",),
        session=_FakeSession(_success_probe(registered=True, running=True)),
        document_service=_FakeDocumentService(),
        cad_service=_FakeCadService(),
    )
    states = {item.name: item for item in runtime.runtime_context().capabilities}
    assert states["solidworks.license"].implemented is False
    assert states["solidworks.license"].available is False
    assert states["solidworks.license"].reason == "license_probe_not_integrated"
