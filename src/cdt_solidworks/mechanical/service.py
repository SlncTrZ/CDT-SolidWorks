"""Mechanical service — Gear orchestration through ordinary sketch/part primitives.
Wing: code | Topic: agent3-mechanical-service | Updated: 2026-09-15 11:18
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from cdt_solidworks.mechanical.gear import (
    GEAR_DIMENSION_NAMES,
    GearValidationError,
    SpurGearParameters,
    SpurGearSpec,
    build_spur_gear_sketch,
    calculate_spur_gear,
)
from cdt_solidworks.part.models import ExtrudeSpec, FeatureKind, FeatureSnapshot, ProfileRef
from cdt_solidworks.part.runtime import DocumentTarget

_PARAMETER_TOLERANCE = 1e-6


class GearBuildMutationError(RuntimeError):
    """A gear build mutated the document before later verification failed."""


@dataclass(frozen=True, slots=True)
class SpurGearBuildResult:
    sketch_id: str
    feature_id: str
    parameters: SpurGearParameters


class SpurGearService:
    """Build and inspect gears without direct COM, macros, or transient selections."""

    def __init__(self, sketch_service: Any, part_service: Any) -> None:
        self._sketches = sketch_service
        self._parts = part_service

    def create(
        self, target: DocumentTarget, name: str, spec: SpurGearSpec
    ) -> SpurGearBuildResult:
        if not isinstance(name, str) or not name.strip():
            raise GearValidationError("gear name must be a non-empty string")
        normalized_name = name.strip()
        parameters = calculate_spur_gear(spec)
        definition = build_spur_gear_sketch(f"{normalized_name}_Profile", spec)
        sketch = self._sketches.create(target, definition)
        try:
            feature = self._parts.extrude(
                target,
                ExtrudeSpec(
                    name=normalized_name,
                    profile=ProfileRef(sketch.sketch_id),
                    depth_mm=parameters.face_width_mm,
                ),
            ).feature
            if feature.kind is not FeatureKind.EXTRUDE or feature.feature_id != normalized_name:
                raise GearBuildMutationError("gear extrusion read-back identity/type mismatch")
        except Exception as exc:
            if hasattr(exc, "result"):
                raise
            if isinstance(exc, GearBuildMutationError):
                raise
            raise GearBuildMutationError(
                "gear sketch was created but extrusion/read-back did not complete"
            ) from exc
        return SpurGearBuildResult(
            sketch_id=sketch.sketch_id,
            feature_id=feature.feature_id,
            parameters=parameters,
        )

    def parameters_get(
        self, target: DocumentTarget, sketch_id: str, feature_id: str
    ) -> SpurGearParameters:
        sketch = self._sketches.get(target, sketch_id)
        values = dict(sketch.dimension_values)
        required = (
            "pitch_diameter_mm",
            "base_diameter_mm",
            "outside_diameter_mm",
            "root_diameter_mm",
        )
        missing = [key for key in required if GEAR_DIMENSION_NAMES[key] not in values]
        if missing:
            raise GearValidationError(
                "gear sketch is missing named engineering dimensions: " + ", ".join(missing)
            )
        pitch = float(values[GEAR_DIMENSION_NAMES["pitch_diameter_mm"]])
        base = float(values[GEAR_DIMENSION_NAMES["base_diameter_mm"]])
        outside = float(values[GEAR_DIMENSION_NAMES["outside_diameter_mm"]])
        root = float(values[GEAR_DIMENSION_NAMES["root_diameter_mm"]])
        module = 0.5 * (outside - pitch)
        if not math.isfinite(module) or module <= 0.0:
            raise GearValidationError("named gear dimensions imply an invalid module")
        tooth_count_float = pitch / module
        tooth_count = round(tooth_count_float)
        if tooth_count <= 0 or not math.isclose(
            tooth_count_float, float(tooth_count), rel_tol=0.0, abs_tol=_PARAMETER_TOLERANCE
        ):
            raise GearValidationError("named gear dimensions do not imply an integer tooth count")
        ratio = base / pitch
        if not 0.0 < ratio <= 1.0:
            raise GearValidationError("named gear dimensions imply an invalid pressure angle")
        pressure_angle = math.degrees(math.acos(ratio))
        bore = float(values.get(GEAR_DIMENSION_NAMES["bore_diameter_mm"], 0.0))
        feature: FeatureSnapshot = self._parts.get_feature(target, feature_id)
        if feature.kind is not FeatureKind.EXTRUDE or feature.feature_id != feature_id:
            raise GearValidationError("gear feature identity/type mismatch")
        face_width = feature.parameters.get("depth_mm")
        if isinstance(face_width, bool) or not isinstance(face_width, (int, float)):
            raise GearValidationError("gear feature is missing bounded face-width read-back")
        spec = SpurGearSpec(
            tooth_count=tooth_count,
            module_mm=module,
            pressure_angle_deg=pressure_angle,
            face_width_mm=float(face_width),
            bore_diameter_mm=bore,
        )
        parameters = calculate_spur_gear(spec)
        checks = {
            "pitch": (parameters.pitch_diameter_mm, pitch),
            "base": (parameters.base_diameter_mm, base),
            "outside": (parameters.outside_diameter_mm, outside),
            "root": (parameters.root_diameter_mm, root),
        }
        for label, (actual, expected) in checks.items():
            if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=_PARAMETER_TOLERANCE):
                raise GearValidationError(f"gear {label} diameter read-back is internally inconsistent")
        return parameters

    def parameter_set(
        self,
        target: DocumentTarget,
        feature_id: str,
        parameter: str,
        value: float,
    ) -> FeatureSnapshot:
        if parameter != "face_width_mm":
            raise ValueError("only face_width_mm is editable without regenerating involute geometry")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
            raise GearValidationError("face_width_mm must be positive and finite")
        feature = self._parts.set_feature_parameter(
            target, feature_id, "depth_mm", float(value)
        )
        if feature.feature_id != feature_id or feature.kind is not FeatureKind.EXTRUDE:
            raise GearValidationError("gear face-width edit changed feature identity/type")
        return feature
