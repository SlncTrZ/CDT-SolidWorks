"""Agent 3 plugin — Generic profile part tools and external spur gears.
Wing: code | Topic: agent3-plugin | Updated: 2026-09-15 11:31
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from cdt_solidworks.integration.mechanical import IntegratedMechanicalService
from cdt_solidworks.native.models import NativeCallResult, NativeCallState
from cdt_solidworks.platform.errors import ErrorCode

PLUGIN_CONTRACT_VERSION = 1
PLUGIN_ID = "agent3_part_mechanical"
PLUGIN_ORDER = 3


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
    if native_code in {"validation_error", "cad_validation_error"} or native_code.startswith(("invalid_", "unsupported_", "path_")):
        return ErrorCode.VALIDATION_ERROR.value
    if native_code in {"document_not_found", "document_not_open"}:
        return ErrorCode.NOT_FOUND.value
    if native_code.startswith("solidworks_") or native_code == "session_not_connected":
        return ErrorCode.PROVIDER_UNAVAILABLE.value
    if native_code in {
        "cad_precondition_failed",
        "cad_selection_failed",
        "cad_mutation_failed",
        "cad_postcondition_failed",
        "stale_document_context",
        "document_context_mismatch",
        "rebuild_failed",
        "reconciliation_mismatch",
    }:
        return ErrorCode.CONFLICT.value
    return ErrorCode.INTERNAL_ERROR.value


def _payload(result: NativeCallResult[Any]) -> dict[str, Any]:
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
    native_code = failure.code if failure is not None else "native_call_failed"
    return {
        "state": state,
        "call_id": result.call_id,
        "dispatched": result.dispatched,
        "value": None,
        "error": {
            "code": _error_code(native_code),
            "native_code": native_code,
            "message": failure.message if failure is not None else "Native operation failed.",
            "retryable": failure.retryable if failure is not None else False,
        },
    }


def _mechanical(runtime: Any) -> IntegratedMechanicalService | None:
    sketch = getattr(runtime, "sketch_service", None)
    part = getattr(runtime, "part_feature_service", None)
    path_policy = getattr(runtime, "path_policy", None)
    if sketch is None or part is None or path_policy is None:
        return None
    try:
        return IntegratedMechanicalService(
            path_policy=path_policy,
            sketch_service=sketch,
            part_feature_service=part,
        )
    except ValueError:
        return None


def register_tools(server: Any, runtime: Any) -> None:
    """Register only Agent-3 public additions; shared registrar remains untouched."""
    part = getattr(runtime, "part_feature_service", None)
    mechanical = _mechanical(runtime)

    if part is not None:
        @server.tool(name="part_profile_extrude", description="Boss/base extrude an explicitly inspected closed profile with mm depth.")
        def part_profile_extrude(path: str, expected_revision: int, sketch_id: str, name: str, depth_mm: float, configuration: str | None = None) -> dict[str, Any]:
            return _payload(part.profile_extrude(path=path, expected_revision=expected_revision, sketch_id=sketch_id, name=name, depth_mm=depth_mm, configuration=configuration))

        @server.tool(name="part_profile_cut", description="Cut an explicitly inspected closed profile; supports blind or through-all.")
        def part_profile_cut(path: str, expected_revision: int, sketch_id: str, name: str, through_all: bool, depth_mm: float | None = None, configuration: str | None = None) -> dict[str, Any]:
            return _payload(part.profile_cut(path=path, expected_revision=expected_revision, sketch_id=sketch_id, name=name, through_all=through_all, depth_mm=depth_mm, configuration=configuration))

        @server.tool(name="part_feature_parameters_get", description="Read bounded promoted parameters from an explicit feature identity.")
        def part_feature_parameters_get(path: str, expected_revision: int, feature_id: str, configuration: str | None = None) -> dict[str, Any]:
            return _payload(part.feature_parameters_get(path=path, expected_revision=expected_revision, feature_id=feature_id, configuration=configuration))

        @server.tool(name="part_feature_parameter_set", description="Edit one bounded promoted feature parameter with rebuild/read-back verification.")
        def part_feature_parameter_set(path: str, expected_revision: int, feature_id: str, parameter: str, value: float, configuration: str | None = None) -> dict[str, Any]:
            return _payload(part.feature_parameter_set(path=path, expected_revision=expected_revision, feature_id=feature_id, parameter=parameter, value=value, configuration=configuration))

    if mechanical is not None:
        @server.tool(name="part_gear_create_spur", description="Create an editable full-depth involute external spur gear from bounded engineering inputs.")
        def part_gear_create_spur(path: str, expected_revision: int, name: str, tooth_count: int, module_mm: float, pressure_angle_deg: float, face_width_mm: float, bore_diameter_mm: float, configuration: str | None = None, keyway_width_mm: float | None = None) -> dict[str, Any]:
            return _payload(mechanical.gear_create_spur(path=path, expected_revision=expected_revision, name=name, tooth_count=tooth_count, module_mm=module_mm, pressure_angle_deg=pressure_angle_deg, face_width_mm=face_width_mm, bore_diameter_mm=bore_diameter_mm, configuration=configuration, keyway_width_mm=keyway_width_mm))

        @server.tool(name="part_gear_parameters_get", description="Reconstruct gear engineering parameters from named sketch dimensions and feature read-back.")
        def part_gear_parameters_get(path: str, expected_revision: int, sketch_id: str, feature_id: str, configuration: str | None = None) -> dict[str, Any]:
            return _payload(mechanical.gear_parameters_get(path=path, expected_revision=expected_revision, sketch_id=sketch_id, feature_id=feature_id, configuration=configuration))

        @server.tool(name="part_gear_parameters_set", description="Edit the bounded gear face-width parameter while preserving feature identity.")
        def part_gear_parameters_set(path: str, expected_revision: int, feature_id: str, parameter: str, value: float, configuration: str | None = None) -> dict[str, Any]:
            return _payload(mechanical.gear_parameters_set(path=path, expected_revision=expected_revision, feature_id=feature_id, parameter=parameter, value=value, configuration=configuration))


def capability_descriptors(runtime: Any) -> tuple[dict[str, Any], ...]:
    part = getattr(runtime, "part_feature_service", None)
    profile_port = getattr(part, "profile_port", None) if part is not None else None
    mechanical = _mechanical(runtime)
    profile_available = part is not None and profile_port is not None
    part_available = part is not None
    gear_available = mechanical is not None
    return (
        {
            "name": "part.profile_features",
            "implemented": True,
            "available": profile_available,
            "reason": None if profile_available else "profile_inspection_dependency_unavailable",
            "backend": "solidworks_native",
            "dependencies": ("solidworks", "sketch.profile.inspect"),
        },
        {
            "name": "part.feature_parameters",
            "implemented": True,
            "available": part_available,
            "reason": None if part_available else "part_feature_service_unavailable",
            "backend": "solidworks_native",
            "dependencies": ("solidworks",),
        },
        {
            "name": "mechanical.spur_gear",
            "implemented": True,
            "available": gear_available,
            "reason": None if gear_available else "sketch_or_part_service_unavailable",
            "backend": "solidworks_native",
            "dependencies": ("solidworks", "sketch", "part"),
        },
    )
