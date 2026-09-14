"""Evaluation domain — bounded engineering read-back and validation services."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol


class EvaluationRefusal(RuntimeError):
    """The request is invalid or unsupported before native evaluation."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else f"{reason}: {detail}")


class EvaluationPostconditionError(RuntimeError):
    """Native read-back is missing, inconsistent, or unsafe to promote."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else f"{reason}: {detail}")


@dataclass(frozen=True, slots=True)
class MassProperties:
    mass_kg: float
    volume_m3: float
    surface_area_m2: float
    center_of_mass_m: tuple[float, float, float]
    inertia_kg_m2: tuple[float, float, float, float, float, float]


@dataclass(frozen=True, slots=True)
class BoundingBox:
    min_m: tuple[float, float, float]
    max_m: tuple[float, float, float]
    approximate: bool = True


@dataclass(frozen=True, slots=True)
class MeasureSnapshot:
    distance_m: float | None
    angle_rad: float | None
    radius_m: float | None
    diameter_m: float | None = None


@dataclass(frozen=True, slots=True)
class InterferenceSnapshot:
    identity: str
    component_a: str
    component_b: str
    volume_m3: float


@dataclass(frozen=True, slots=True)
class GeometrySanity:
    solid_body_count: int
    surface_body_count: int
    component_count: int
    feature_error_count: int


class EvaluationAdapter(Protocol):
    def mass_properties(
        self, document_id: str, configuration: str | None
    ) -> MassProperties | None: ...

    def bounding_box(
        self, document_id: str, configuration: str | None
    ) -> BoundingBox | None: ...

    def measure(
        self, document_id: str, refs: tuple[str, ...]
    ) -> MeasureSnapshot | None: ...

    def interferences(
        self, assembly_id: str, configuration: str | None
    ) -> tuple[InterferenceSnapshot, ...]: ...

    def geometry_sanity(
        self, document_id: str, configuration: str | None
    ) -> GeometrySanity | None: ...


class EvaluationService:
    """Validates native engineering facts instead of accepting optimistic values."""

    def __init__(self, adapter: EvaluationAdapter) -> None:
        self._adapter = adapter

    def mass_properties(
        self, document_id: str, configuration: str | None = None
    ) -> MassProperties:
        self._require_identity("document_id", document_id)
        self._optional_identity("configuration", configuration)
        result = self._adapter.mass_properties(document_id, configuration)
        if result is None or not self._valid_mass_properties(result):
            raise EvaluationPostconditionError("invalid_mass_properties")
        return result

    def bounding_box(
        self, document_id: str, configuration: str | None = None
    ) -> BoundingBox:
        self._require_identity("document_id", document_id)
        self._optional_identity("configuration", configuration)
        result = self._adapter.bounding_box(document_id, configuration)
        if result is None or not self._valid_bounds(result):
            raise EvaluationPostconditionError("invalid_bounding_box")
        return result

    def measure(
        self, document_id: str, first_ref: str, second_ref: str | None = None
    ) -> MeasureSnapshot:
        self._require_identity("first_ref", first_ref)
        refs = (first_ref,) if second_ref is None else (first_ref, second_ref)
        if second_ref is not None:
            self._require_identity("second_ref", second_ref)
        return self.measure_refs(document_id, refs)

    def measure_refs(
        self, document_id: str, refs: tuple[str, ...]
    ) -> MeasureSnapshot:
        self._require_identity("document_id", document_id)
        if len(refs) not in (1, 2):
            raise EvaluationRefusal("invalid_measurement_reference_count")
        for index, reference in enumerate(refs):
            self._require_identity(f"measurement_ref_{index}", reference)
        result = self._adapter.measure(document_id, refs)
        if result is None:
            raise EvaluationPostconditionError("measurement_missing")
        values = tuple(
            value
            for value in (
                result.distance_m,
                result.angle_rad,
                result.radius_m,
                result.diameter_m,
            )
            if value is not None
        )
        if not values or any(not math.isfinite(value) or value < 0 for value in values):
            raise EvaluationPostconditionError("invalid_measurement")
        return result

    def interferences(
        self, assembly_id: str, configuration: str | None = None
    ) -> tuple[InterferenceSnapshot, ...]:
        self._require_identity("assembly_id", assembly_id)
        self._optional_identity("configuration", configuration)
        result = tuple(self._adapter.interferences(assembly_id, configuration))
        seen: set[str] = set()
        for item in result:
            if not item.identity.strip() or item.identity in seen:
                raise EvaluationPostconditionError("invalid_interference_identity")
            seen.add(item.identity)
            if (
                not item.component_a.strip()
                or not item.component_b.strip()
                or item.component_a == item.component_b
            ):
                raise EvaluationPostconditionError("invalid_interference_components")
            if not math.isfinite(item.volume_m3) or item.volume_m3 <= 0:
                raise EvaluationPostconditionError("invalid_interference_volume")
        return result

    def require_clean_geometry(
        self, document_id: str, configuration: str | None = None
    ) -> GeometrySanity:
        self._require_identity("document_id", document_id)
        self._optional_identity("configuration", configuration)
        result = self._adapter.geometry_sanity(document_id, configuration)
        if result is None:
            raise EvaluationPostconditionError("geometry_sanity_missing")
        values = (
            result.solid_body_count,
            result.surface_body_count,
            result.component_count,
            result.feature_error_count,
        )
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
            raise EvaluationPostconditionError("invalid_geometry_sanity")
        if result.feature_error_count:
            raise EvaluationPostconditionError(
                "feature_errors_present", str(result.feature_error_count)
            )
        return result

    @staticmethod
    def _valid_mass_properties(result: MassProperties) -> bool:
        scalars = (result.mass_kg, result.volume_m3, result.surface_area_m2)
        vectors = result.center_of_mass_m + result.inertia_kg_m2
        return (
            len(result.center_of_mass_m) == 3
            and len(result.inertia_kg_m2) == 6
            and all(math.isfinite(value) for value in scalars + vectors)
            and all(value >= 0 for value in scalars)
        )

    @staticmethod
    def _valid_bounds(result: BoundingBox) -> bool:
        if len(result.min_m) != 3 or len(result.max_m) != 3:
            return False
        values = result.min_m + result.max_m
        return all(math.isfinite(value) for value in values) and all(
            low <= high for low, high in zip(result.min_m, result.max_m, strict=True)
        )

    @classmethod
    def _optional_identity(cls, label: str, value: str | None) -> None:
        if value is not None:
            cls._require_identity(label, value)

    @staticmethod
    def _require_identity(label: str, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise EvaluationRefusal(f"invalid_{label}")
