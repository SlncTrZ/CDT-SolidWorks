"""MCP registrar for the integrated application/document native surface."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Literal

from cdt_solidworks.document.models import DocumentContext, DocumentType
from cdt_solidworks.native.models import NativeCallResult, NativeCallState
from cdt_solidworks.native.session import AttachPolicy
from cdt_solidworks.platform.errors import ErrorCode


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return str(value)


def _error_code(native_code: str) -> str:
    if native_code.startswith("timeout"):
        return ErrorCode.TIMEOUT.value
    if native_code in {"document_not_found", "document_not_open"} or native_code.startswith("missing_"):
        return ErrorCode.NOT_FOUND.value
    if native_code in {
        "cad_precondition_failed",
        "cad_selection_failed",
        "cad_mutation_failed",
        "cad_postcondition_failed",
        "document_already_exists",
        "stale_document_context",
        "document_context_mismatch",
        "uncertain_state",
        "native_state_uncertain",
        "configuration_exists",
        "cannot_delete_active_configuration",
        "cannot_delete_last_configuration",
        "equation_exists",
        "rebuild_failed",
        "reconciliation_mismatch",
        "native_export_incomplete",
        "artifact_format_mismatch",
        "artifact_extension_mismatch",
        "artifact_empty",
        "artifact_unreadable",
        "geometry_not_verified",
        "feature_errors_present",
        "invalid_mass_properties",
        "invalid_bounding_box",
        "invalid_geometry_sanity",
    } or (
        native_code.endswith("_readback_mismatch")
        or native_code.endswith("_readback_missing")
        or native_code.startswith("dangling_")
    ):
        return ErrorCode.CONFLICT.value
    if (
        native_code.startswith("path_")
        or native_code.startswith("invalid_")
        or native_code.startswith("unsupported_")
        or native_code in {
            "cad_validation_error",
            "document_type_mismatch",
            "document_extension_mismatch",
            "document_type_unknown",
            "format_extension_mismatch",
            "mate_alignment_not_supported",
            "mate_value_not_supported",
            "mate_value_required",
        }
    ):
        return ErrorCode.VALIDATION_ERROR.value
    if native_code.startswith("solidworks_") or native_code == "session_not_connected":
        return ErrorCode.PROVIDER_UNAVAILABLE.value
    return ErrorCode.INTERNAL_ERROR.value


def _result_payload(result: NativeCallResult[Any]) -> dict[str, Any]:
    if result.state is NativeCallState.SUCCESS:
        return {
            "state": "success",
            "call_id": result.call_id,
            "dispatched": result.dispatched,
            "value": _jsonable(result.value),
            "error": None,
        }
    failure = result.failure
    state = (
        "uncertain"
        if result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
        else "not_started"
        if result.state is NativeCallState.TIMEOUT_BEFORE_DISPATCH
        else "failed"
    )
    return {
        "state": state,
        "call_id": result.call_id,
        "dispatched": result.dispatched,
        "value": None,
        "error": {
            "code": _error_code(failure.code if failure is not None else "native_call_failed"),
            "native_code": failure.code if failure is not None else "native_call_failed",
            "message": failure.message if failure is not None else "Native operation failed.",
            "retryable": failure.retryable if failure is not None else False,
        },
    }


def _document_type(value: str) -> DocumentType:
    normalized = value.strip().lower()
    mapping = {
        "part": DocumentType.PART,
        "assembly": DocumentType.ASSEMBLY,
        "drawing": DocumentType.DRAWING,
    }
    if normalized not in mapping:
        raise ValueError("document_type must be part, assembly, or drawing")
    return mapping[normalized]


def _context(
    session_id: str,
    path: str,
    title: str,
    document_type: Literal["part", "assembly", "drawing"],
    configuration: str | None,
    update_stamp: int | None,
) -> DocumentContext:
    return DocumentContext(
        session_id=session_id,
        path=path,
        title=title,
        document_type=_document_type(document_type),
        configuration=configuration,
        update_stamp=update_stamp,
    )


def register_runtime_tools(server: Any, runtime: Any) -> None:
    """Register only the native/document tools backed by the accepted B-lane runtime."""

    @server.tool(name="application_probe", description="Probe SolidWorks registration/running state without mutation.")
    def application_probe(version: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.session.probe(version=version, timeout=3.0))

    @server.tool(name="application_connect", description="Attach to or start an explicit SolidWorks application session.")
    def application_connect(
        policy: Literal["attach_only", "attach_or_start", "start_new"] = "attach_or_start",
        version: int | None = None,
        visible: bool = True,
    ) -> dict[str, Any]:
        try:
            attach_policy = AttachPolicy(policy)
        except ValueError:
            return {
                "state": "not_started",
                "call_id": "validation",
                "dispatched": False,
                "value": None,
                "error": {
                    "code": ErrorCode.VALIDATION_ERROR.value,
                    "native_code": "invalid_attach_policy",
                    "message": "policy must be attach_only, attach_or_start, or start_new",
                    "retryable": False,
                },
            }
        return _result_payload(
            runtime.session.connect(policy=attach_policy, version=version, visible=visible)
        )

    @server.tool(name="application_disconnect", description="Disconnect the provider session; only provider-owned applications may be exited.")
    def application_disconnect() -> dict[str, Any]:
        return _result_payload(runtime.session.disconnect())

    @server.tool(name="document_open", description="Open a SolidWorks document within configured path roots and return explicit identity context.")
    def document_open(
        path: str,
        expected_type: Literal["part", "assembly", "drawing"] | None = None,
        configuration: str = "",
        read_only: bool = False,
    ) -> dict[str, Any]:
        doc_type = _document_type(expected_type) if expected_type is not None else None
        return _result_payload(
            runtime.document_service.open(
                path,
                expected_type=doc_type,
                configuration=configuration,
                read_only=read_only,
            )
        )

    def ctx(
        session_id: str,
        path: str,
        title: str,
        document_type: Literal["part", "assembly", "drawing"],
        configuration: str | None,
        update_stamp: int | None,
    ) -> DocumentContext:
        return _context(session_id, path, title, document_type, configuration, update_stamp)

    @server.tool(name="document_info", description="Read document information using explicit identity/revision context.")
    def document_info(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.document_service.info(ctx(session_id, path, title, document_type, configuration, update_stamp)))

    @server.tool(name="document_save", description="Save an explicitly identified document and verify postconditions.")
    def document_save(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.document_service.save(ctx(session_id, path, title, document_type, configuration, update_stamp)))

    @server.tool(name="document_save_as", description="Save-as within configured path roots and verify native identity moved to the target.")
    def document_save_as(target_path: str, session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.document_service.save_as(ctx(session_id, path, title, document_type, configuration, update_stamp), target_path))

    @server.tool(name="document_close", description="Close an explicitly identified document and verify it is no longer open.")
    def document_close(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.document_service.close(ctx(session_id, path, title, document_type, configuration, update_stamp)))

    @server.tool(name="document_reopen", description="Close/reopen an explicit document and return refreshed identity context.")
    def document_reopen(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.document_service.reopen(ctx(session_id, path, title, document_type, configuration, update_stamp)))

    @server.tool(name="document_list_features", description="List bounded feature state for an explicit document.")
    def document_list_features(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.document_service.list_features(ctx(session_id, path, title, document_type, configuration, update_stamp)))

    @server.tool(name="document_list_bodies", description="List bounded part bodies for an explicit document.")
    def document_list_bodies(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None, visible_only: bool = False) -> dict[str, Any]:
        return _result_payload(runtime.document_service.list_bodies(ctx(session_id, path, title, document_type, configuration, update_stamp), visible_only=visible_only))

    @server.tool(name="document_list_components", description="List bounded assembly components for an explicit document.")
    def document_list_components(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None, top_level_only: bool = False) -> dict[str, Any]:
        return _result_payload(runtime.document_service.list_components(ctx(session_id, path, title, document_type, configuration, update_stamp), top_level_only=top_level_only))

    @server.tool(name="document_rebuild", description="Rebuild and reject success when SolidWorks feature/error state is not clean.")
    def document_rebuild(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.document_service.rebuild(ctx(session_id, path, title, document_type, configuration, update_stamp)))

    @server.tool(name="document_reconcile", description="Reconcile an uncertain document mutation before dependent writes continue.")
    def document_reconcile(call_id: str, path: str, expected_type: Literal["part", "assembly", "drawing"], should_be_open: bool) -> dict[str, Any]:
        return _result_payload(runtime.document_service.reconcile_document_state(call_id, path=path, expected_type=_document_type(expected_type), should_be_open=should_be_open))


    if runtime.sketch_service is not None:
        @server.tool(
            name="sketch_create_geometry",
            description=(
                "Create bounded native sketch geometry in an explicitly opened millimeter part. "
                "Supports line, centerline, circle, arc, ellipse, point, and cubic spline entities "
                "on front/top/right planes; call document_save separately to persist the mutation."
            ),
        )
        def sketch_create_geometry(
            path: str,
            expected_revision: int,
            name: str,
            entities: list[dict[str, Any]],
            plane: Literal["front", "top", "right"] = "front",
        ) -> dict[str, Any]:
            return _result_payload(
                runtime.sketch_service.create_geometry(
                    path=path,
                    expected_revision=expected_revision,
                    name=name,
                    plane=plane,
                    entities=entities,
                )
            )

        @server.tool(
            name="sketch_get",
            description=(
                "Read one native sketch from an explicitly opened millimeter part using path and revision identity."
            ),
        )
        def sketch_get(path: str, expected_revision: int, sketch_id: str) -> dict[str, Any]:
            return _result_payload(
                runtime.sketch_service.get(
                    path=path, expected_revision=expected_revision, sketch_id=sketch_id
                )
            )

    if runtime.part_feature_service is not None:
        @server.tool(
            name="part_cut_extrude",
            description=(
                "Create a native blind or through-all Cut Extrude from one explicitly named sketch "
                "in an opened millimeter part using path + revision identity; call document_save separately to persist."
            ),
        )
        def part_cut_extrude(
            path: str,
            expected_revision: int,
            sketch_id: str,
            name: str,
            through_all: bool,
            depth_mm: float | None = None,
        ) -> dict[str, Any]:
            return _result_payload(
                runtime.part_feature_service.cut_extrude(
                    path=path,
                    expected_revision=expected_revision,
                    sketch_id=sketch_id,
                    name=name,
                    through_all=through_all,
                    depth_mm=depth_mm,
                )
            )

        @server.tool(
            name="part_cut_reconcile",
            description=(
                "Reconcile an uncertain Cut Extrude using its native call ID and expected feature "
                "postcondition; quarantine clears only after feature, rebuild, and body verification pass."
            ),
        )
        def part_cut_reconcile(
            call_id: str,
            path: str,
            name: str,
            through_all: bool,
            depth_mm: float | None = None,
        ) -> dict[str, Any]:
            return _result_payload(
                runtime.part_feature_service.reconcile_cut(
                    call_id=call_id,
                    path=path,
                    name=name,
                    through_all=through_all,
                    depth_mm=depth_mm,
                )
            )

    if runtime.body_service is not None:
        @server.tool(name="body_inspect", description="Inspect named native solid and surface bodies in a SOLIDWORKS part.")
        def body_inspect(path: str) -> dict[str, Any]:
            return _result_payload(runtime.body_service.inspect(path))

        @server.tool(name="body_combine", description="Combine explicitly named solid bodies using add, subtract, or common Boolean semantics.")
        def body_combine(
            path: str,
            operation: Literal["add", "subtract", "common"],
            body_names: list[str],
            main_body_name: str | None = None,
        ) -> dict[str, Any]:
            return _result_payload(runtime.body_service.combine(
                path, operation=operation, body_names=body_names, main_body_name=main_body_name
            ))

    if runtime.surface_service is not None:
        @server.tool(name="surface_thicken", description="Thicken one explicitly named native surface body and verify resulting solid-body state.")
        def surface_thicken(
            path: str,
            surface_body_name: str,
            thickness_mm: float,
            side: int = 0,
            merge: bool = False,
        ) -> dict[str, Any]:
            return _result_payload(runtime.surface_service.thicken(
                path, surface_body_name=surface_body_name, thickness_mm=thickness_mm,
                side=side, merge=merge
            ))

    if runtime.sheetmetal_service is not None:
        @server.tool(name="sheet_metal_inspect", description="Read bounded native sheet-metal Base Flange and Flat Pattern state.")
        def sheet_metal_inspect(path: str) -> dict[str, Any]:
            return _result_payload(runtime.sheetmetal_service.inspect(path))

        @server.tool(name="sheet_metal_set_flattened", description="Set persisted Flat Pattern suppression state for a native Base Flange sheet-metal part.")
        def sheet_metal_set_flattened(path: str, flattened: bool) -> dict[str, Any]:
            return _result_payload(runtime.sheetmetal_service.set_flattened(path, flattened=flattened))

    if runtime.weldment_service is not None:
        @server.tool(name="weldment_inspect", description="Read bounded weldment structural-member and cut-list state.")
        def weldment_inspect(path: str) -> dict[str, Any]:
            return _result_payload(runtime.weldment_service.inspect(path))

        @server.tool(name="weldment_create_structural_member", description="Create a structural member from one named path sketch and an allowed .sldlfp profile; custom profiles may specify an explicit configuration.")
        def weldment_create_structural_member(
            path: str,
            sketch_feature_name: str,
            profile_path: str,
            profile_configuration: str = "",
            apply_corner_treatment: bool = True,
            corner_treatment_type: int = 0,
        ) -> dict[str, Any]:
            return _result_payload(runtime.weldment_service.create_structural_member(
                path, sketch_feature_name=sketch_feature_name, profile_path=profile_path,
                profile_configuration=profile_configuration,
                apply_corner_treatment=apply_corner_treatment,
                corner_treatment_type=corner_treatment_type,
            ))

    if runtime.assembly_service is not None:
        @server.tool(name="assembly_components_list", description="List native assembly components using explicit path identity; recursive traversal is bounded by the lane adapter.")
        def assembly_components_list(path: str, recursive: bool = False) -> dict[str, Any]:
            return _result_payload(runtime.assembly_service.list_components(path, recursive=recursive))

        @server.tool(name="assembly_component_set_fixed", description="Set one explicitly identified assembly component fixed or floating and verify read-back.")
        def assembly_component_set_fixed(path: str, component_id: str, fixed: bool) -> dict[str, Any]:
            return _result_payload(runtime.assembly_service.set_component_fixed(path, component_id, fixed))

        @server.tool(name="assembly_component_set_load_state", description="Set one component to resolved or suppressed; lightweight is intentionally not exposed without native acceptance evidence.")
        def assembly_component_set_load_state(
            path: str, component_id: str, state: Literal["resolved", "suppressed"]
        ) -> dict[str, Any]:
            return _result_payload(runtime.assembly_service.set_component_load_state(path, component_id, state))

        @server.tool(name="assembly_component_set_configuration", description="Set and read back the referenced configuration for one explicitly identified assembly component.")
        def assembly_component_set_configuration(path: str, component_id: str, configuration: str) -> dict[str, Any]:
            return _result_payload(runtime.assembly_service.set_component_configuration(path, component_id, configuration))

        @server.tool(name="assembly_mate_create", description="Create one native-accepted common mate: coincident, parallel, perpendicular, distance, or angle, using explicit bounded selection references.")
        def assembly_mate_create(
            path: str,
            kind: Literal["coincident", "parallel", "perpendicular", "distance", "angle"],
            selection_refs: list[str],
            value: float | None = None,
            alignment: Literal["aligned", "anti_aligned", "closest"] | None = None,
        ) -> dict[str, Any]:
            return _result_payload(runtime.assembly_service.create_mate(
                path, kind=kind, selection_refs=selection_refs, value=value, alignment=alignment
            ))

        @server.tool(name="assembly_mates_list", description="List native assembly mates with stable feature identity and bounded mate-family classification.")
        def assembly_mates_list(path: str) -> dict[str, Any]:
            return _result_payload(runtime.assembly_service.list_mates(path))

        @server.tool(name="assembly_coincident_mate_set_suppressed", description="Suppress or unsuppress one mate only when read-back identifies it as a native-accepted coincident mate.")
        def assembly_coincident_mate_set_suppressed(path: str, mate_id: str, suppressed: bool) -> dict[str, Any]:
            return _result_payload(runtime.assembly_service.set_coincident_mate_suppressed(path, mate_id, suppressed))

        @server.tool(name="assembly_distance_mate_set_value", description="Edit one native-accepted distance mate value in system units and verify solved read-back.")
        def assembly_distance_mate_set_value(path: str, mate_id: str, value: float) -> dict[str, Any]:
            return _result_payload(runtime.assembly_service.set_distance_mate_value(path, mate_id, value))

    if runtime.configuration_service is not None:
        @server.tool(name="configuration_list", description="List stable configuration identities for an explicitly addressed native part or assembly.")
        def configuration_list(path: str) -> dict[str, Any]:
            return _result_payload(runtime.configuration_service.list(path))

        @server.tool(name="configuration_create", description="Create a native configuration, optionally derived from one explicit parent, while restoring prior active context.")
        def configuration_create(path: str, name: str, parent: str | None = None) -> dict[str, Any]:
            return _result_payload(runtime.configuration_service.create(path, name, parent))

        @server.tool(name="configuration_rename", description="Rename one explicit native configuration with identity read-back.")
        def configuration_rename(path: str, old_name: str, new_name: str) -> dict[str, Any]:
            return _result_payload(runtime.configuration_service.rename(path, old_name, new_name))

        @server.tool(name="configuration_delete", description="Delete one non-active native configuration and verify absence after rebuild.")
        def configuration_delete(path: str, name: str) -> dict[str, Any]:
            return _result_payload(runtime.configuration_service.delete(path, name))

        @server.tool(name="configuration_activate", description="Activate one explicit native configuration and verify exact active-configuration read-back.")
        def configuration_activate(path: str, name: str) -> dict[str, Any]:
            return _result_payload(runtime.configuration_service.activate(path, name))

        @server.tool(name="configuration_set_dimension", description="Set one configuration-specific model dimension in SOLIDWORKS system units, rebuild, and verify read-back.")
        def configuration_set_dimension(path: str, configuration: str, dimension_name: str, value: float) -> dict[str, Any]:
            return _result_payload(runtime.configuration_service.set_dimension(path, configuration, dimension_name, value))

        @server.tool(name="configuration_set_property", description="Set one document-level or configuration-specific custom property and verify read-back.")
        def configuration_set_property(
            path: str, property_name: str, value: str, configuration: str | None = None
        ) -> dict[str, Any]:
            return _result_payload(runtime.configuration_service.set_property(path, configuration, property_name, value))

        @server.tool(name="configuration_delete_property", description="Delete one document-level or configuration-specific custom property and verify absence.")
        def configuration_delete_property(
            path: str, property_name: str, configuration: str | None = None
        ) -> dict[str, Any]:
            return _result_payload(runtime.configuration_service.delete_property(path, configuration, property_name))

        @server.tool(name="configuration_set_feature_suppressed", description="Set one feature suppression state in one explicit configuration and verify configuration-specific read-back.")
        def configuration_set_feature_suppressed(
            path: str, configuration: str, feature_id: str, suppressed: bool
        ) -> dict[str, Any]:
            return _result_payload(runtime.configuration_service.set_feature_suppressed(path, configuration, feature_id, suppressed))

        @server.tool(name="configuration_equations_list", description="List bounded equation/global-variable identities, canonical expressions, values, and disabled state.")
        def configuration_equations_list(path: str) -> dict[str, Any]:
            return _result_payload(runtime.configuration_service.list_equations(path))

        @server.tool(name="configuration_equation_add", description="Add one equation/global variable, rebuild, and verify canonical identity/read-back.")
        def configuration_equation_add(path: str, expression: str) -> dict[str, Any]:
            return _result_payload(runtime.configuration_service.add_equation(path, expression))

        @server.tool(name="configuration_equation_set", description="Edit one existing equation without changing its stable left-hand identity, then rebuild and verify read-back.")
        def configuration_equation_set(path: str, identity: str, expression: str) -> dict[str, Any]:
            return _result_payload(runtime.configuration_service.set_equation(path, identity, expression))

        @server.tool(name="configuration_equation_delete", description="Delete one explicit equation/global-variable identity and verify absence after rebuild.")
        def configuration_equation_delete(path: str, identity: str) -> dict[str, Any]:
            return _result_payload(runtime.configuration_service.delete_equation(path, identity))

    if runtime.drawing_service is not None:
        @server.tool(
            name="drawing_create",
            description=(
                "Create a native SOLIDWORKS drawing using the active default drawing template; "
                "the output must be a new .SLDDRW path under an allowed root."
            ),
        )
        def drawing_create(output_path: str) -> dict[str, Any]:
            return _result_payload(runtime.drawing_service.create(output_path))

        @server.tool(
            name="drawing_sheet_create",
            description=(
                "Create and rebuild one additional sheet in an explicitly addressed native drawing."
            ),
        )
        def drawing_sheet_create(path: str, sheet_name: str) -> dict[str, Any]:
            return _result_payload(runtime.drawing_service.create_sheet(path, sheet_name))

        @server.tool(
            name="drawing_front_view_create",
            description=(
                "Create the native-accepted Front model view on one explicit drawing sheet from "
                "one native part source; broader view families remain unavailable."
            ),
        )
        def drawing_front_view_create(
            path: str, sheet_name: str, source_part_path: str
        ) -> dict[str, Any]:
            return _result_payload(
                runtime.drawing_service.create_front_view(
                    path, sheet_name, source_part_path
                )
            )

    if runtime.export_service is not None:
        @server.tool(
            name="export_document",
            description=(
                "Export only native-accepted source/format pairs: part to STEP/IGES/Parasolid/"
                "STL/3MF, or drawing to PDF/DXF/DWG. Existing targets are never overwritten."
            ),
        )
        def export_document(
            source_path: str,
            target_path: str,
            format: Literal[
                "step", "iges", "parasolid", "stl", "3mf", "pdf", "dxf", "dwg"
            ],
            source_configuration: str | None = None,
        ) -> dict[str, Any]:
            return _result_payload(
                runtime.export_service.export(
                    source_path,
                    target_path,
                    format,
                    source_configuration=source_configuration,
                )
            )

    if runtime.evaluation_service is not None:
        @server.tool(
            name="evaluation_mass_properties",
            description=(
                "Read validated mass, volume, surface area, center of mass, and inertia from "
                "one native part; this tool is read-only."
            ),
        )
        def evaluation_mass_properties(
            path: str, configuration: str | None = None
        ) -> dict[str, Any]:
            return _result_payload(
                runtime.evaluation_service.mass_properties(path, configuration)
            )

        @server.tool(
            name="evaluation_bounding_box",
            description=(
                "Read a validated approximate native part bounding box in SOLIDWORKS system units."
            ),
        )
        def evaluation_bounding_box(
            path: str, configuration: str | None = None
        ) -> dict[str, Any]:
            return _result_payload(
                runtime.evaluation_service.bounding_box(path, configuration)
            )

        @server.tool(
            name="evaluation_geometry_sanity",
            description=(
                "Read bounded part body/feature-error sanity and fail when native feature errors exist."
            ),
        )
        def evaluation_geometry_sanity(
            path: str, configuration: str | None = None
        ) -> dict[str, Any]:
            return _result_payload(
                runtime.evaluation_service.geometry_sanity(path, configuration)
            )

    if runtime.cad_service is None:
        return

    @server.tool(name="sketch_create_rectangle", description="Create a native SOLIDWORKS part containing one rectangular 2D sketch on a standard plane.")
    def sketch_create_rectangle(
        output_path: str,
        width_mm: float,
        height_mm: float,
        plane: Literal["front", "top", "right"] = "front",
        center_x_mm: float = 0.0,
        center_y_mm: float = 0.0,
    ) -> dict[str, Any]:
        return _result_payload(runtime.cad_service.create_rectangle_sketch(
            output_path, width_mm=width_mm, height_mm=height_mm, plane=plane,
            center_x_mm=center_x_mm, center_y_mm=center_y_mm
        ))

    @server.tool(name="part_create_rect_extrude", description="Create a rectangular sketch and native solid extrude in a new SOLIDWORKS part.")
    def part_create_rect_extrude(
        output_path: str,
        width_mm: float,
        height_mm: float,
        depth_mm: float,
        plane: Literal["front", "top", "right"] = "front",
        center_x_mm: float = 0.0,
        center_y_mm: float = 0.0,
    ) -> dict[str, Any]:
        return _result_payload(runtime.cad_service.create_rect_extrude(
            output_path, width_mm=width_mm, height_mm=height_mm, depth_mm=depth_mm,
            plane=plane, center_x_mm=center_x_mm, center_y_mm=center_y_mm
        ))

    @server.tool(name="part_add_rect_extrude", description="Add a rectangular extrusion to an existing part; merge=false creates a separate solid body.")
    def part_add_rect_extrude(
        path: str,
        width_mm: float,
        height_mm: float,
        depth_mm: float,
        plane: Literal["front", "top", "right"] = "front",
        center_x_mm: float = 0.0,
        center_y_mm: float = 0.0,
        merge: bool = True,
    ) -> dict[str, Any]:
        return _result_payload(runtime.cad_service.add_rect_extrude(
            path, width_mm=width_mm, height_mm=height_mm, depth_mm=depth_mm,
            plane=plane, center_x_mm=center_x_mm, center_y_mm=center_y_mm, merge=merge
        ))

    @server.tool(name="part_combine_all_bodies", description="Combine all solid bodies in a native SOLIDWORKS part using Boolean Add semantics.")
    def part_combine_all_bodies(path: str) -> dict[str, Any]:
        return _result_payload(runtime.cad_service.combine_all_bodies(path))

    @server.tool(name="part_split_by_plane", description="Split a solid part by one standard reference plane and retain resulting bodies in the part.")
    def part_split_by_plane(path: str, plane: Literal["front", "top", "right"]) -> dict[str, Any]:
        return _result_payload(runtime.cad_service.split_by_plane(path, plane=plane))

    @server.tool(name="sheet_metal_create_base_flange", description="Create a native SOLIDWORKS sheet-metal base flange with explicit thickness and bend radius.")
    def sheet_metal_create_base_flange(
        output_path: str,
        width_mm: float,
        height_mm: float,
        thickness_mm: float,
        bend_radius_mm: float,
    ) -> dict[str, Any]:
        return _result_payload(runtime.cad_service.create_sheet_metal_base_flange(
            output_path, width_mm=width_mm, height_mm=height_mm,
            thickness_mm=thickness_mm, bend_radius_mm=bend_radius_mm
        ))

    @server.tool(name="surface_create_extrude", description="Create a native extruded surface from a line sketch on a standard reference plane.")
    def surface_create_extrude(
        output_path: str,
        line_length_mm: float,
        depth_mm: float,
        plane: Literal["front", "top", "right"] = "front",
    ) -> dict[str, Any]:
        return _result_payload(runtime.cad_service.create_surface_extrude(
            output_path, line_length_mm=line_length_mm, depth_mm=depth_mm, plane=plane
        ))

    @server.tool(name="assembly_create", description="Create a native assembly from explicit component paths and xyz placements in millimeters.")
    def assembly_create(
        output_path: str,
        component_paths: list[str],
        placements_mm: list[list[float]],
    ) -> dict[str, Any]:
        return _result_payload(runtime.cad_service.create_assembly(
            output_path, component_paths=component_paths, placements_mm=placements_mm
        ))

    @server.tool(name="assembly_add_coincident_plane_mate", description="Create a coincident mate between a component standard plane and an assembly standard plane.")
    def assembly_add_coincident_plane_mate(
        path: str,
        component_name: str,
        component_plane: Literal["front", "top", "right"],
        assembly_plane: Literal["front", "top", "right"],
    ) -> dict[str, Any]:
        return _result_payload(runtime.cad_service.add_coincident_plane_mate(
            path, component_name=component_name, component_plane=component_plane,
            assembly_plane=assembly_plane
        ))
