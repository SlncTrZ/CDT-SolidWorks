"""Agent-5 public facade for fabrication breadth and bounded reconstruction orchestration."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Mapping
import uuid

from cdt_solidworks.native.errors import failure_from_exception
from cdt_solidworks.native.models import NativeCallResult, NativeCallState, NativeFailure
from cdt_solidworks.reconstruction.service import (
    ReconstructionPostconditionError,
    ReconstructionService,
    ReconstructionUnavailableError,
    ReconstructionValidationError,
)


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {str(key): jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    return str(value)


def result_payload(result: NativeCallResult[Any]) -> dict[str, Any]:
    """Serialize the frozen public result envelope without depending on registrar internals."""
    if result.state is NativeCallState.SUCCESS:
        return {
            "state": "success",
            "call_id": result.call_id,
            "dispatched": result.dispatched,
            "value": jsonable(result.value),
            "error": None,
        }
    failure = result.failure
    public_state = (
        "uncertain"
        if result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
        else "not_started"
        if result.state is NativeCallState.TIMEOUT_BEFORE_DISPATCH
        else "failed"
    )
    native_code = failure.code if failure is not None else "native_call_failed"
    return {
        "state": public_state,
        "call_id": result.call_id,
        "dispatched": result.dispatched,
        "value": None,
        "error": {
            "code": _public_error_code(native_code),
            "native_code": native_code,
            "message": failure.message if failure is not None else "Native operation failed.",
            "retryable": failure.retryable if failure is not None else False,
        },
    }


def _public_error_code(native_code: str) -> str:
    if native_code == "capability_unavailable":
        return "provider_unavailable"
    if native_code in {"cad_validation_error", "unsupported_reconstruction_source"} or native_code.startswith("path_"):
        return "validation_error"
    if native_code in {"cad_postcondition_failed", "reconstruction_postcondition_failed"}:
        return "conflict"
    if native_code.startswith("timeout"):
        return "timeout"
    return "internal_error"


class FabricationReconstructionFacade:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def body_move_copy(self, **kwargs: Any) -> NativeCallResult[Any]:
        return self._fabrication("body_service", "move_copy", "body_move_copy", **kwargs)

    def body_delete_keep(self, **kwargs: Any) -> NativeCallResult[Any]:
        return self._fabrication("body_service", "delete_keep", "body_delete_keep", **kwargs)

    def surface_offset(self, **kwargs: Any) -> NativeCallResult[Any]:
        return self._fabrication("surface_service", "offset", "surface_offset", **kwargs)

    def sheet_metal_add_edge_flange(self, **kwargs: Any) -> NativeCallResult[Any]:
        return self._fabrication(
            "sheetmetal_service", "add_edge_flange", "sheet_metal_add_edge_flange", **kwargs
        )

    def sheet_metal_add_hem(self, **kwargs: Any) -> NativeCallResult[Any]:
        return self._fabrication("sheetmetal_service", "add_hem", "sheet_metal_add_hem", **kwargs)

    def sheet_metal_add_sketched_bend(self, **kwargs: Any) -> NativeCallResult[Any]:
        return self._fabrication(
            "sheetmetal_service", "add_sketched_bend", "sheet_metal_add_sketched_bend", **kwargs
        )

    def sheet_metal_unfold_bend(self, **kwargs: Any) -> NativeCallResult[Any]:
        return self._fabrication(
            "sheetmetal_service", "unfold_bend", "sheet_metal_unfold_bend", **kwargs
        )

    def sheet_metal_fold_bend(self, **kwargs: Any) -> NativeCallResult[Any]:
        return self._fabrication("sheetmetal_service", "fold_bend", "sheet_metal_fold_bend", **kwargs)

    def weldment_trim_extend(self, **kwargs: Any) -> NativeCallResult[Any]:
        return self._fabrication("weldment_service", "trim_extend", "weldment_trim_extend", **kwargs)

    def weldment_set_cut_list_property(self, **kwargs: Any) -> NativeCallResult[Any]:
        return self._fabrication(
            "weldment_service", "set_cut_list_property", "weldment_set_cut_list_property", **kwargs
        )

    def reconstruction_assess(self, *, source_path: str) -> NativeCallResult[Any]:
        return self._reconstruction_call(
            "reconstruction_assess",
            lambda service: service.assess(self._source_path(source_path)),
            mutation=True,
        )

    def reconstruction_step_to_editable(
        self,
        *,
        source_path: str,
        output_path: str,
        benchmark_class: str,
        tolerance_mm: float,
        intended_edit: Mapping[str, object] | None,
    ) -> NativeCallResult[Any]:
        return self._reconstruction_call(
            "reconstruction_step_to_editable",
            lambda service: service.step_to_editable(
                self._source_path(source_path),
                self._output_path(output_path),
                benchmark_class=benchmark_class,
                tolerance_mm=tolerance_mm,
                intended_edit=intended_edit,
            ),
            mutation=True,
        )

    def reconstruction_mesh_to_parametric(
        self,
        *,
        source_path: str,
        output_path: str,
        benchmark_class: str,
        approximation_tolerance_mm: float,
        intended_edit: Mapping[str, object] | None,
    ) -> NativeCallResult[Any]:
        return self._reconstruction_call(
            "reconstruction_mesh_to_parametric",
            lambda service: service.mesh_to_parametric(
                self._source_path(source_path),
                self._output_path(output_path),
                benchmark_class=benchmark_class,
                approximation_tolerance_mm=approximation_tolerance_mm,
                intended_edit=intended_edit,
            ),
            mutation=True,
        )

    def reconstruction_compare(
        self,
        *,
        expected_dimensions_mm: Mapping[str, float],
        actual_dimensions_mm: Mapping[str, float],
        tolerance_mm: float,
    ) -> NativeCallResult[Any]:
        return self._reconstruction_call(
            "reconstruction_compare",
            lambda service: service.compare(
                expected_dimensions_mm,
                actual_dimensions_mm,
                tolerance_mm=tolerance_mm,
            ),
            mutation=False,
        )

    def _fabrication(
        self,
        service_name: str,
        method_name: str,
        stage: str,
        **kwargs: Any,
    ) -> NativeCallResult[Any]:
        adapter = getattr(self.runtime, service_name, None)
        method = getattr(adapter, method_name, None) if adapter is not None else None
        if not callable(method):
            return self._unavailable(stage, f"{service_name}.{method_name} is unavailable")
        try:
            result = method(**kwargs)
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=uuid.uuid4().hex,
                dispatched=False,
            )
        if not isinstance(result, NativeCallResult):
            return NativeCallResult.failed(
                NativeFailure(
                    "cad_postcondition_failed",
                    stage,
                    "fabrication adapter violated the NativeCallResult contract",
                    retryable=False,
                ),
                call_id=uuid.uuid4().hex,
                dispatched=True,
            )
        return result

    def _reconstruction_service(self) -> ReconstructionService:
        return ReconstructionService(
            topology_port=getattr(self.runtime, "reconstruction_topology_port", None),
            part_port=getattr(self.runtime, "reconstruction_part_port", None),
            drawing_port=getattr(self.runtime, "reconstruction_drawing_port", None),
            mesh_port=getattr(self.runtime, "reconstruction_mesh_port", None),
        )

    def _reconstruction_call(
        self,
        stage: str,
        operation: Any,
        *,
        mutation: bool,
    ) -> NativeCallResult[Any]:
        call_id = uuid.uuid4().hex
        try:
            value = operation(self._reconstruction_service())
            return NativeCallResult.success(value, call_id=call_id, dispatched=mutation)
        except ReconstructionPostconditionError as exc:
            return NativeCallResult.failed(
                NativeFailure("reconstruction_postcondition_failed", stage, str(exc), retryable=False),
                call_id=call_id,
                dispatched=True,
            )
        except ReconstructionUnavailableError as exc:
            return NativeCallResult.failed(
                NativeFailure("capability_unavailable", stage, str(exc), retryable=False),
                call_id=call_id,
                dispatched=False,
            )
        except ReconstructionValidationError as exc:
            return NativeCallResult.failed(
                NativeFailure("cad_validation_error", stage, str(exc), retryable=False),
                call_id=call_id,
                dispatched=False,
            )
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=call_id,
                dispatched=False,
            )

    def _source_path(self, path: str) -> str:
        policy = getattr(self.runtime, "path_policy", None)
        validator = getattr(policy, "validate_open", None) if policy is not None else None
        return validator(path) if callable(validator) else path

    def _output_path(self, path: str) -> str:
        policy = getattr(self.runtime, "path_policy", None)
        validator = getattr(policy, "validate_save", None) if policy is not None else None
        return validator(path) if callable(validator) else path

    @staticmethod
    def _unavailable(stage: str, message: str) -> NativeCallResult[Any]:
        return NativeCallResult.failed(
            NativeFailure("capability_unavailable", stage, message, retryable=False),
            call_id=uuid.uuid4().hex,
            dispatched=False,
        )
