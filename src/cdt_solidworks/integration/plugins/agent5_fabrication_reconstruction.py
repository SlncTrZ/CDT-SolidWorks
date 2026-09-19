"""Agent-5 integration plugin: fabrication public-close + bounded reconstruction."""

from __future__ import annotations

from typing import Any

from cdt_solidworks.integration.fabrication_reconstruction import (
    FabricationReconstructionFacade,
    result_payload,
)
from cdt_solidworks.reconstruction.native_ports import bind_native_step_ports

PLUGIN_CONTRACT_VERSION = 1
PLUGIN_ID = "agent5.fabrication_reconstruction"
PLUGIN_ORDER = 5


def register_tools(server: Any, runtime: Any) -> None:
    bind_native_step_ports(runtime)
    facade = FabricationReconstructionFacade(runtime)

    @server.tool(name="body_move_copy", description="Move or copy explicitly named solid bodies by a bounded XYZ translation in millimeters.")
    def body_move_copy(
        path: str,
        body_names: list[str],
        translation_mm: list[float],
        copy: bool = False,
        copies: int = 1,
    ) -> dict[str, Any]:
        return result_payload(facade.body_move_copy(
            path=path, body_names=body_names, translation_mm=tuple(translation_mm),
            copy=copy, copies=copies,
        ))

    @server.tool(name="body_delete_keep", description="Create a bounded Delete/Keep Body feature for explicit solid-body identities.")
    def body_delete_keep(path: str, body_names: list[str], keep: bool) -> dict[str, Any]:
        return result_payload(facade.body_delete_keep(path=path, body_names=body_names, keep=keep))

    @server.tool(name="surface_offset", description="Offset one explicitly named single-face surface body by a finite positive millimeter distance.")
    def surface_offset(
        path: str, surface_body_name: str, distance_mm: float, reverse: bool = False
    ) -> dict[str, Any]:
        return result_payload(facade.surface_offset(
            path=path, surface_body_name=surface_body_name,
            distance_mm=distance_mm, reverse=reverse,
        ))

    @server.tool(name="sheet_metal_add_edge_flange", description="Add a bounded Edge Flange to one supported bbox perimeter selector with native read-back.")
    def sheet_metal_add_edge_flange(
        path: str,
        edge_selector: str,
        length_mm: float,
        angle_deg: float = 90.0,
        bend_radius_mm: float | None = None,
    ) -> dict[str, Any]:
        return result_payload(facade.sheet_metal_add_edge_flange(
            path=path, edge_selector=edge_selector, length_mm=length_mm,
            angle_deg=angle_deg, bend_radius_mm=bend_radius_mm,
        ))

    @server.tool(name="sheet_metal_add_hem", description="Add a bounded open Hem on one supported sheet-metal perimeter selector.")
    def sheet_metal_add_hem(
        path: str,
        edge_selector: str,
        length_mm: float,
        gap_mm: float,
        position: str = "outside",
        reverse: bool = False,
    ) -> dict[str, Any]:
        return result_payload(facade.sheet_metal_add_hem(
            path=path, edge_selector=edge_selector, length_mm=length_mm,
            gap_mm=gap_mm, position=position, reverse=reverse,
        ))

    @server.tool(name="sheet_metal_add_sketched_bend", description="Create one bounded Sketched Bend with explicit line position, angle, radius, and direction.")
    def sheet_metal_add_sketched_bend(
        path: str,
        line_x_mm: float,
        angle_deg: float,
        bend_radius_mm: float,
        reverse: bool = False,
    ) -> dict[str, Any]:
        return result_payload(facade.sheet_metal_add_sketched_bend(
            path=path, line_x_mm=line_x_mm, angle_deg=angle_deg,
            bend_radius_mm=bend_radius_mm, reverse=reverse,
        ))

    @server.tool(name="sheet_metal_unfold_bend", description="Unfold one explicit native OneBend using a bounded fixed-face selector.")
    def sheet_metal_unfold_bend(
        path: str, bend_feature_name: str, fixed_x_mm: float
    ) -> dict[str, Any]:
        return result_payload(facade.sheet_metal_unfold_bend(
            path=path, bend_feature_name=bend_feature_name, fixed_x_mm=fixed_x_mm,
        ))

    @server.tool(name="sheet_metal_fold_bend", description="Refold one explicit UnFold/OneBend pair using a bounded fixed-face selector.")
    def sheet_metal_fold_bend(
        path: str, unfold_feature_name: str, bend_feature_name: str, fixed_x_mm: float
    ) -> dict[str, Any]:
        return result_payload(facade.sheet_metal_fold_bend(
            path=path, unfold_feature_name=unfold_feature_name,
            bend_feature_name=bend_feature_name, fixed_x_mm=fixed_x_mm,
        ))

    @server.tool(name="weldment_trim_extend", description="Trim one explicit weldment body against one explicit boundary body with native cut-list read-back.")
    def weldment_trim_extend(
        path: str, body_to_trim_name: str, boundary_body_name: str
    ) -> dict[str, Any]:
        return result_payload(facade.weldment_trim_extend(
            path=path, body_to_trim_name=body_to_trim_name,
            boundary_body_name=boundary_body_name,
        ))

    @server.tool(name="weldment_set_cut_list_property", description="Set one explicit weldment cut-list property and verify exact native read-back.")
    def weldment_set_cut_list_property(
        path: str, cut_list_name: str, property_name: str, value: str
    ) -> dict[str, Any]:
        return result_payload(facade.weldment_set_cut_list_property(
            path=path, cut_list_name=cut_list_name,
            property_name=property_name, value=value,
        ))

    @server.tool(name="reconstruction_assess", description="Classify and assess a STEP/IGES/Parasolid/native-imported/STL source with hash, units/frame confidence, topology or mesh statistics, primitive fits, missing semantics, and a bounded strategy.")
    def reconstruction_assess(source_path: str) -> dict[str, Any]:
        return result_payload(facade.reconstruction_assess(source_path=source_path))

    @server.tool(name="reconstruction_step_to_editable", description="Rebuild a controlled STEP benchmark as ordinary editable SOLIDWORKS features through injected topology/part ports, verify critical dimensions and optional intended edit after reopen.")
    def reconstruction_step_to_editable(
        source_path: str,
        output_path: str,
        benchmark_class: str,
        tolerance_mm: float,
        intended_edit: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        return result_payload(facade.reconstruction_step_to_editable(
            source_path=source_path, output_path=output_path,
            benchmark_class=benchmark_class, tolerance_mm=tolerance_mm,
            intended_edit=intended_edit,
        ))

    @server.tool(name="reconstruction_mesh_to_parametric", description="Approximate a controlled STL benchmark as editable SOLIDWORKS features only when mesh confidence/residuals satisfy the declared tolerance; ambiguity is refused.")
    def reconstruction_mesh_to_parametric(
        source_path: str,
        output_path: str,
        benchmark_class: str,
        approximation_tolerance_mm: float,
        intended_edit: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        return result_payload(facade.reconstruction_mesh_to_parametric(
            source_path=source_path, output_path=output_path,
            benchmark_class=benchmark_class,
            approximation_tolerance_mm=approximation_tolerance_mm,
            intended_edit=intended_edit,
        ))

    @server.tool(name="reconstruction_compare", description="Compare two explicit critical-dimension ledgers in millimeters and report quantitative deviation against a declared tolerance.")
    def reconstruction_compare(
        expected_dimensions_mm: dict[str, float],
        actual_dimensions_mm: dict[str, float],
        tolerance_mm: float,
    ) -> dict[str, Any]:
        return result_payload(facade.reconstruction_compare(
            expected_dimensions_mm=expected_dimensions_mm,
            actual_dimensions_mm=actual_dimensions_mm,
            tolerance_mm=tolerance_mm,
        ))


def capability_descriptors(runtime: Any) -> list[dict[str, Any]]:
    bind_native_step_ports(runtime)
    native_available, native_reason = _solidworks_dependency(runtime)
    descriptors: list[dict[str, Any]] = []

    fabrication = (
        ("solidworks.body.move_copy", "body_service", "move_copy"),
        ("solidworks.body.delete_keep", "body_service", "delete_keep"),
        ("solidworks.surface.offset", "surface_service", "offset"),
        ("solidworks.sheet_metal.edge_flange", "sheetmetal_service", "add_edge_flange"),
        ("solidworks.sheet_metal.hem", "sheetmetal_service", "add_hem"),
        ("solidworks.sheet_metal.sketched_bend", "sheetmetal_service", "add_sketched_bend"),
        ("solidworks.sheet_metal.unfold", "sheetmetal_service", "unfold_bend"),
        ("solidworks.sheet_metal.fold", "sheetmetal_service", "fold_bend"),
        ("solidworks.weldment.trim_extend", "weldment_service", "trim_extend"),
        ("solidworks.weldment.cut_list_property", "weldment_service", "set_cut_list_property"),
    )
    for name, service_name, method_name in fabrication:
        service = getattr(runtime, service_name, None)
        implemented = callable(getattr(service, method_name, None))
        available = implemented and native_available
        reason = None if available else (native_reason if implemented else "native_adapter_not_integrated")
        descriptors.append(_descriptor(name, implemented, available, reason, dependencies=("solidworks",)))

    topology = getattr(runtime, "reconstruction_topology_port", None)
    part = getattr(runtime, "reconstruction_part_port", None)
    mesh = getattr(runtime, "reconstruction_mesh_port", None)
    assess_implemented = topology is not None or mesh is not None
    assess_available = assess_implemented and native_available
    assess_reason = None if assess_available else (
        native_reason if assess_implemented else "reconstruction_inspection_port_unavailable"
    )
    descriptors.append(_descriptor(
        "solidworks.reconstruction.assess", True, assess_available,
        assess_reason,
        backend="provider_orchestration", dependencies=("solidworks",),
    ))
    step_reason = _port_reason(("topology_port", topology), ("part_port", part))
    step_available = step_reason is None and native_available
    if step_reason is None and not native_available:
        step_reason = native_reason
    descriptors.append(_descriptor(
        "solidworks.reconstruction.step_editable", True, step_available, step_reason,
        backend="provider_orchestration", dependencies=("solidworks",),
    ))
    mesh_reason = _port_reason(("mesh_port", mesh), ("part_port", part))
    mesh_available = mesh_reason is None and native_available
    if mesh_reason is None and not native_available:
        mesh_reason = native_reason
    descriptors.append(_descriptor(
        "solidworks.reconstruction.mesh_parametric", True, mesh_available, mesh_reason,
        backend="provider_orchestration", dependencies=("solidworks",),
    ))
    descriptors.append(_descriptor(
        "solidworks.reconstruction.compare", True, True, None,
        backend="provider_orchestration", dependencies=(),
    ))
    return descriptors


def _solidworks_dependency(runtime: Any) -> tuple[bool, str | None]:
    direct_probe = getattr(runtime, "solidworks_dependency_state", None)
    if callable(direct_probe):
        try:
            dependency = direct_probe()
        except Exception:
            return False, "runtime_discovery_failed"
        if getattr(dependency, "available", False) is True:
            return True, None
        return False, str(
            getattr(dependency, "reason", None) or "solidworks_unavailable"
        )
    runtime_context = getattr(runtime, "runtime_context", None)
    if not callable(runtime_context):
        return False, "runtime_discovery_unavailable"
    try:
        context = runtime_context()
    except Exception:
        return False, "runtime_discovery_failed"
    for dependency in getattr(context, "dependencies", ()):
        if getattr(dependency, "name", None) == "solidworks":
            if getattr(dependency, "available", False) is True:
                return True, None
            return False, str(getattr(dependency, "reason", None) or "solidworks_unavailable")
    return False, "solidworks_dependency_unknown"


def _port_reason(*ports: tuple[str, object]) -> str | None:
    for name, value in ports:
        if value is None:
            return f"{name}_unavailable"
    return None


def _descriptor(
    name: str,
    implemented: bool,
    available: bool,
    reason: str | None,
    *,
    backend: str = "solidworks_com",
    dependencies: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "name": name,
        "implemented": implemented,
        "available": available,
        "reason": reason,
        "backend": backend,
        "dependencies": dependencies,
    }
