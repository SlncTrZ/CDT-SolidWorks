"""Mechanical integration — Public envelope for Agent-3 spur gear workflows.
Wing: code | Topic: agent3-mechanical-integration | Updated: 2026-09-15 11:25
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any
import uuid

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.mechanical.gear import GearValidationError, SpurGearSpec
from cdt_solidworks.mechanical.service import GearBuildMutationError, SpurGearService
from cdt_solidworks.native.models import NativeCallResult, NativeFailure
from cdt_solidworks.part.runtime import DocumentTarget
from cdt_solidworks.part.service import PartContextError, PartMutationError, PartValidationError
from cdt_solidworks.sketch.service import SketchContextError, SketchMutationError, SketchValidationError

_PART_EXT = ".sldprt"


class IntegratedMechanicalService:
    """Expose bounded mechanical workflows while preserving native uncertain call IDs."""

    def __init__(
        self,
        *,
        path_policy: DocumentPathPolicy,
        sketch_service: Any | None = None,
        part_feature_service: Any | None = None,
        service: Any | None = None,
    ) -> None:
        self.path_policy = path_policy
        if service is None:
            sketch_domain = getattr(sketch_service, "service", None)
            part_domain = getattr(part_feature_service, "service", None)
            if sketch_domain is None or part_domain is None:
                raise ValueError("gear integration requires sketch and part service dependencies")
            service = SpurGearService(sketch_domain, part_domain)
        self.service = service

    def gear_create_spur(
        self,
        *,
        path: str,
        expected_revision: int,
        name: str,
        tooth_count: int,
        module_mm: float,
        pressure_angle_deg: float,
        face_width_mm: float,
        bore_diameter_mm: float,
        configuration: str | None = None,
        keyway_width_mm: float | None = None,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            target = self._target(path, expected_revision, configuration)
            if not isinstance(name, str) or not name.strip():
                raise GearValidationError("gear name must be a non-empty string")
            spec = SpurGearSpec(
                tooth_count=self._integer(tooth_count, "tooth_count"),
                module_mm=self._number(module_mm, "module_mm"),
                pressure_angle_deg=self._number(pressure_angle_deg, "pressure_angle_deg"),
                face_width_mm=self._number(face_width_mm, "face_width_mm"),
                bore_diameter_mm=self._number(bore_diameter_mm, "bore_diameter_mm"),
                keyway_width_mm=(
                    None
                    if keyway_width_mm is None
                    else self._number(keyway_width_mm, "keyway_width_mm")
                ),
            )
            return self.service.create(target, name.strip(), spec)

        return self._call("part_gear_create_spur", operation, mutation=True)

    def gear_parameters_get(
        self,
        *,
        path: str,
        expected_revision: int,
        sketch_id: str,
        feature_id: str,
        configuration: str | None = None,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            return self.service.parameters_get(
                self._target(path, expected_revision, configuration),
                self._identity(sketch_id, "sketch_id"),
                self._identity(feature_id, "feature_id"),
            )

        return self._call("part_gear_parameters_get", operation, mutation=False)

    def gear_parameters_set(
        self,
        *,
        path: str,
        expected_revision: int,
        feature_id: str,
        parameter: str,
        value: float,
        configuration: str | None = None,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            return self.service.parameter_set(
                self._target(path, expected_revision, configuration),
                self._identity(feature_id, "feature_id"),
                self._identity(parameter, "parameter"),
                self._number(value, "value"),
            )

        return self._call("part_gear_parameters_set", operation, mutation=True)

    def _target(
        self, path: str, expected_revision: int, configuration: str | None
    ) -> DocumentTarget:
        source = self.path_policy.validate_open(path)
        if Path(source).suffix.lower() != _PART_EXT:
            raise GearValidationError("spur gear workflow requires a native .SLDPRT document")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise GearValidationError("expected_revision must be a non-negative integer")
        if configuration is not None and (not isinstance(configuration, str) or not configuration.strip()):
            raise GearValidationError("configuration must be a non-empty string when supplied")
        return DocumentTarget(
            source,
            expected_revision,
            "mm",
            expected_configuration=configuration.strip() if configuration is not None else None,
        )

    def _call(self, stage: str, operation: Any, *, mutation: bool) -> NativeCallResult[Any]:
        call_id = uuid.uuid4().hex
        try:
            value = operation()
            return NativeCallResult.success(value, call_id=call_id, dispatched=mutation)
        except Exception as exc:
            interrupted = getattr(exc, "result", None)
            if isinstance(interrupted, NativeCallResult):
                return interrupted
            validation_types = (
                GearValidationError,
                ValueError,
                PartValidationError,
                PartContextError,
                SketchValidationError,
                SketchContextError,
            )
            mutation_types = (GearBuildMutationError, PartMutationError, SketchMutationError)
            if isinstance(exc, validation_types):
                failure = NativeFailure(
                    code="validation_error",
                    stage=stage,
                    message=str(exc),
                    retryable=False,
                )
            else:
                failure = NativeFailure(
                    code="native_call_failed",
                    stage=stage,
                    message=f"Mechanical operation failed ({type(exc).__name__}).",
                    retryable=False,
                )
            return NativeCallResult.failed(
                failure,
                call_id=call_id,
                dispatched=mutation and isinstance(exc, mutation_types),
            )

    @staticmethod
    def _identity(value: Any, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise GearValidationError(f"{label} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _number(value: Any, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GearValidationError(f"{label} must be numeric")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise GearValidationError(f"{label} must be finite")
        return normalized

    @staticmethod
    def _integer(value: Any, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise GearValidationError(f"{label} must be an integer")
        return value
