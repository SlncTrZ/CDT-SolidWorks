from cdt_solidworks.integration.registrar import _error_code
from cdt_solidworks.platform.errors import ErrorCode


def test_domain_validation_codes_do_not_surface_as_internal_error() -> None:
    for code in (
        "invalid_component_load_state",
        "unsupported_mate_kind",
        "mate_alignment_not_supported",
        "cad_validation_error",
        "format_extension_mismatch",
        "unsupported_view_kind",
    ):
        assert _error_code(code) == ErrorCode.VALIDATION_ERROR.value


def test_domain_missing_codes_map_to_not_found() -> None:
    for code in (
        "missing_component",
        "missing_mate",
        "missing_configuration",
        "missing_equation",
        "missing_sheet",
    ):
        assert _error_code(code) == ErrorCode.NOT_FOUND.value


def test_domain_postcondition_and_state_conflicts_map_to_conflict() -> None:
    for code in (
        "cad_selection_failed",
        "cad_mutation_failed",
        "cad_postcondition_failed",
        "reconciliation_mismatch",
        "configuration_exists",
        "cannot_delete_active_configuration",
        "cannot_delete_last_configuration",
        "equation_exists",
        "rebuild_failed",
        "component_configuration_readback_mismatch",
        "native_state_uncertain",
        "native_export_incomplete",
        "artifact_format_mismatch",
        "artifact_extension_mismatch",
        "artifact_empty",
        "artifact_unreadable",
        "geometry_not_verified",
        "drawing_identity_readback_mismatch",
        "feature_errors_present",
        "invalid_mass_properties",
        "invalid_bounding_box",
        "invalid_geometry_sanity",
        "drawing_readback_missing",
        "view_readback_missing",
        "dangling_view",
    ):
        assert _error_code(code) == ErrorCode.CONFLICT.value
