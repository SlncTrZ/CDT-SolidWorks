"""Wire-level request validation for provider-owned MCP tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import inspect
from typing import Any

from mcp.server.context import HandlerResult, ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS


_TOOL_ARGUMENTS: dict[str, frozenset[str]] = {
    "help": frozenset(),
    "system_status": frozenset(),
    "system_observability": frozenset(),
    "system_capabilities": frozenset(),
    "application_probe": frozenset({"version"}),
    "application_connect": frozenset({"policy", "version", "visible"}),
    "application_disconnect": frozenset(),
    "document_open": frozenset({"path", "expected_type", "configuration", "read_only"}),
    "document_info": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp"}),
    "document_save": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp"}),
    "document_save_as": frozenset({"target_path", "session_id", "path", "title", "document_type", "configuration", "update_stamp"}),
    "document_close": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp"}),
    "document_reopen": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp"}),
    "document_list_features": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp"}),
    "document_list_bodies": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp", "visible_only"}),
    "document_list_components": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp", "top_level_only"}),
    "document_rebuild": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp"}),
    "document_reconcile": frozenset({"call_id", "path", "expected_type", "should_be_open"}),
    "sketch_create_geometry": frozenset({"path", "expected_revision", "name", "plane", "entities", "constraints", "dimensions"}),
    "sketch_get": frozenset({"path", "expected_revision", "sketch_id"}),
    "sketch_relations_list": frozenset({"path", "expected_revision", "sketch_id"}),
    "sketch_relation_delete": frozenset({"path", "expected_revision", "sketch_id", "relation_id"}),
    "sketch_dimension_set": frozenset({"path", "expected_revision", "sketch_id", "name", "value", "unit"}),
    "part_cut_extrude": frozenset({"path", "expected_revision", "sketch_id", "name", "through_all", "depth_mm"}),
    "part_cut_reconcile": frozenset({"call_id", "path", "name", "through_all", "depth_mm"}),
    "part_simple_hole": frozenset({"path", "expected_revision", "name", "diameter_mm", "face_ref", "center_mm", "through_all", "depth_mm"}),
    "part_simple_hole_reconcile": frozenset({"call_id", "path", "name", "diameter_mm", "face_ref", "center_mm", "through_all", "depth_mm"}),
    "part_revolve": frozenset({"path", "expected_revision", "sketch_id", "name", "axis_ref", "angle_deg"}),
    "part_revolve_cut": frozenset({"path", "expected_revision", "sketch_id", "name", "axis_ref", "angle_deg"}),
    "part_revolve_reconcile": frozenset({"call_id", "path", "name", "axis_ref", "angle_deg", "is_cut"}),
    "part_hole_wizard": frozenset({"path", "expected_revision", "name", "size", "center_mm", "face_ref"}),
    "part_fillet": frozenset({"path", "expected_revision", "name", "edge_refs", "radius_mm", "tangent_propagation"}),
    "part_chamfer": frozenset({"path", "expected_revision", "name", "edge_refs", "distance_mm", "angle_deg"}),
    "part_shell": frozenset({"path", "expected_revision", "name", "face_refs", "thickness_mm", "outward"}),
    "part_draft": frozenset({"path", "expected_revision", "name", "face_refs", "neutral_plane_ref", "angle_deg", "reverse_direction"}),
    "part_rib": frozenset({"path", "expected_revision", "name", "sketch_id", "thickness_mm", "both_sides"}),
    "part_linear_pattern": frozenset({"path", "expected_revision", "name", "seed_feature_ids", "direction_ref", "count", "spacing_mm", "geometry_pattern"}),
    "part_circular_pattern": frozenset({"path", "expected_revision", "name", "seed_feature_ids", "axis_ref", "count", "angle_deg", "geometry_pattern"}),
    "part_mirror": frozenset({"path", "expected_revision", "name", "seed_feature_ids", "mirror_ref", "geometry_pattern"}),
    "part_reference_plane": frozenset({"path", "expected_revision", "name", "reference", "offset_mm", "reverse_direction"}),
    "part_reference_axis": frozenset({"path", "expected_revision", "name", "first_ref", "second_ref"}),
    "part_reference_point": frozenset({"path", "expected_revision", "name", "reference"}),
    "part_feature_get": frozenset({"path", "expected_revision", "feature_id"}),
    "part_feature_rename": frozenset({"path", "expected_revision", "feature_id", "new_name"}),
    "part_feature_set_suppressed": frozenset({"path", "expected_revision", "feature_id", "suppressed"}),
    "part_feature_set_parameter": frozenset({"path", "expected_revision", "feature_id", "parameter", "value"}),
    "body_inspect": frozenset({"path"}),
    "body_combine": frozenset({"path", "operation", "body_names", "main_body_name"}),
    "surface_thicken": frozenset({"path", "surface_body_name", "thickness_mm", "side", "merge"}),
    "sheet_metal_inspect": frozenset({"path"}),
    "sheet_metal_set_flattened": frozenset({"path", "flattened"}),
    "weldment_inspect": frozenset({"path"}),
    "weldment_create_structural_member": frozenset({
        "path", "sketch_feature_name", "profile_path", "profile_configuration",
        "apply_corner_treatment", "corner_treatment_type"
    }),
    "sketch_create_rectangle": frozenset({"output_path", "width_mm", "height_mm", "plane", "center_x_mm", "center_y_mm"}),
    "part_create_rect_extrude": frozenset({"output_path", "width_mm", "height_mm", "depth_mm", "plane", "center_x_mm", "center_y_mm"}),
    "part_add_rect_extrude": frozenset({"path", "width_mm", "height_mm", "depth_mm", "plane", "center_x_mm", "center_y_mm", "merge"}),
    "part_combine_all_bodies": frozenset({"path"}),
    "part_split_by_plane": frozenset({"path", "plane"}),
    "sheet_metal_create_base_flange": frozenset({"output_path", "width_mm", "height_mm", "thickness_mm", "bend_radius_mm"}),
    "surface_create_extrude": frozenset({"output_path", "line_length_mm", "depth_mm", "plane"}),
    "assembly_create": frozenset({"output_path", "component_paths", "placements_mm"}),
    "assembly_add_coincident_plane_mate": frozenset({"path", "component_name", "component_plane", "assembly_plane"}),
    "assembly_components_list": frozenset({"path", "recursive"}),
    "assembly_component_set_fixed": frozenset({"path", "component_id", "fixed"}),
    "assembly_component_set_load_state": frozenset({"path", "component_id", "state"}),
    "assembly_component_set_configuration": frozenset({"path", "component_id", "configuration"}),
    "assembly_component_delete": frozenset({"path", "component_id"}),
    "assembly_component_replace": frozenset({"path", "component_id", "source_path", "configuration"}),
    "assembly_component_set_transform": frozenset({"path", "component_id", "transform"}),
    "assembly_component_pattern_create": frozenset({"path", "seed_component_ids", "direction_ref", "spacing_m", "total_instances"}),
    "assembly_mate_create": frozenset({"path", "kind", "selection_refs", "value", "alignment", "constraint"}),
    "assembly_mates_list": frozenset({"path"}),
    "assembly_mate_set_suppressed": frozenset({"path", "mate_id", "suppressed"}),
    "assembly_mate_set_value": frozenset({"path", "mate_id", "value"}),
    "assembly_coincident_mate_set_suppressed": frozenset({"path", "mate_id", "suppressed"}),
    "assembly_distance_mate_set_value": frozenset({"path", "mate_id", "value"}),
    "configuration_list": frozenset({"path"}),
    "configuration_create": frozenset({"path", "name", "parent"}),
    "configuration_rename": frozenset({"path", "old_name", "new_name"}),
    "configuration_delete": frozenset({"path", "name"}),
    "configuration_activate": frozenset({"path", "name"}),
    "configuration_set_dimension": frozenset({"path", "configuration", "dimension_name", "value"}),
    "configuration_set_property": frozenset({"path", "property_name", "value", "configuration"}),
    "configuration_delete_property": frozenset({"path", "property_name", "configuration"}),
    "configuration_set_feature_suppressed": frozenset({"path", "configuration", "feature_id", "suppressed"}),
    "configuration_set_material": frozenset({"path", "configuration", "database", "material_name"}),
    "configuration_display_states_list": frozenset({"path", "configuration"}),
    "configuration_display_state_create": frozenset({"path", "configuration", "name"}),
    "configuration_display_state_rename": frozenset({"path", "configuration", "old_name", "new_name"}),
    "configuration_display_state_delete": frozenset({"path", "configuration", "name"}),
    "configuration_equations_list": frozenset({"path"}),
    "configuration_equation_add": frozenset({"path", "expression"}),
    "configuration_equation_set": frozenset({"path", "identity", "expression"}),
    "configuration_equation_delete": frozenset({"path", "identity"}),
    "drawing_create": frozenset({"output_path"}),
    "drawing_sheet_create": frozenset({"path", "sheet_name"}),
    "drawing_front_view_create": frozenset({"path", "sheet_name", "source_part_path", "source_configuration"}),
    "drawing_standard_view_create": frozenset({"path", "sheet_name", "source_part_path", "view_kind", "source_configuration"}),
    "drawing_projected_view_create": frozenset({"path", "parent_view_id", "x", "y"}),
    "drawing_section_view_create": frozenset({"path", "parent_view_id", "line_start_x", "line_start_y", "line_end_x", "line_end_y", "x", "y", "label"}),
    "drawing_note_add": frozenset({"path", "view_id", "text"}),
    "drawing_center_marks_auto_insert": frozenset({"path", "view_id"}),
    "export_document": frozenset({"source_path", "target_path", "format", "source_configuration", "drawing_sheet"}),
    "import_document": frozenset({"source_path", "target_path", "format"}),
    "evaluation_mass_properties": frozenset({"path", "configuration"}),
    "evaluation_bounding_box": frozenset({"path", "configuration"}),
    "evaluation_geometry_sanity": frozenset({"path", "configuration"}),
    "evaluation_measure": frozenset({"path", "first_ref", "second_ref"}),
    "evaluation_interferences": frozenset({"path", "configuration"}),
}


def seal_tool_input_schemas(server: Any) -> dict[str, frozenset[str]]:
    """Seal every advertised tool schema and return its runtime argument allowlist.

    Provider-owned legacy tools are checked against the pinned static contract;
    plugin/extension tools derive their closed allowlist from the SDK-generated
    signature schema. No advertised tool may retain a ``**kwargs`` escape hatch.
    """

    manager = getattr(server, "_tool_manager", None)
    if manager is None or not hasattr(manager, "list_tools"):
        raise RuntimeError("MCP tool manager is unavailable for schema sealing.")

    sealed: dict[str, frozenset[str]] = {}
    for tool in manager.list_tools():
        name = str(tool.name)
        parameters = tool.parameters
        properties = parameters.get("properties", {})
        generated = frozenset(str(key) for key in properties)
        pinned = _TOOL_ARGUMENTS.get(name)
        if pinned is not None and generated != pinned:
            raise RuntimeError(
                f"Tool argument schema mismatch for {name}: "
                f"schema={sorted(generated)}, allowlist={sorted(pinned)}"
            )

        func = getattr(tool, "fn", None)
        if callable(func):
            signature = inspect.signature(func)
            if any(
                item.kind is inspect.Parameter.VAR_KEYWORD
                for item in signature.parameters.values()
            ):
                raise RuntimeError(f"Tool {name} may not expose **kwargs.")

        parameters["additionalProperties"] = False
        sealed[name] = pinned if pinned is not None else generated
    return sealed


def strict_tool_input_middleware(
    allowed_by_tool: Mapping[str, frozenset[str]],
) -> Callable[
    [
        ServerRequestContext[Any, Any],
        Callable[[ServerRequestContext[Any, Any]], Awaitable[HandlerResult]],
    ],
    Awaitable[HandlerResult],
]:
    """Build isolated fail-closed input validation for one server tool catalog."""

    async def middleware(
        ctx: ServerRequestContext[Any, Any],
        call_next: Callable[[ServerRequestContext[Any, Any]], Awaitable[HandlerResult]],
    ) -> HandlerResult:
        if ctx.method != "tools/call":
            return await call_next(ctx)

        params = ctx.params
        if not isinstance(params, dict):
            return await call_next(ctx)

        tool_name = str(params.get("name"))
        allowed = allowed_by_tool.get(tool_name)
        if allowed is None:
            return await call_next(ctx)

        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise MCPError(
                code=INVALID_PARAMS,
                message="Invalid params: arguments must be an object.",
            )
        unexpected_keys = sorted(str(key) for key in arguments if str(key) not in allowed)
        if unexpected_keys:
            unexpected = ", ".join(unexpected_keys)
            raise MCPError(
                code=INVALID_PARAMS,
                message=f"Invalid params: unexpected field(s): {unexpected}",
            )
        return await call_next(ctx)

    return middleware


async def strict_platform_tool_inputs(
    ctx: ServerRequestContext[Any, Any],
    call_next: Callable[[ServerRequestContext[Any, Any]], Awaitable[HandlerResult]],
) -> HandlerResult:
    """Compatibility middleware for tests and legacy direct construction."""

    return await strict_tool_input_middleware(_TOOL_ARGUMENTS)(ctx, call_next)
