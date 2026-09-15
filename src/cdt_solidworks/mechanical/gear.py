"""Spur gear geometry — Deterministic full-depth involute construction.
Wing: code | Topic: agent3-spur-gear | Updated: 2026-09-15 11:10
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from cdt_solidworks.sketch.models import (
    Arc,
    ArcDirection,
    Circle,
    DiameterDimension,
    LineSegment,
    PlaneKind,
    Point2D,
    SketchDefinition,
    SketchPlane,
    Spline,
)

_MIN_PRESSURE_ANGLE_DEG = 14.5
_MAX_PRESSURE_ANGLE_DEG = 25.0
_MAX_TOOTH_COUNT = 40
_MIN_FLANK_SAMPLES = 4
_MAX_FLANK_SAMPLES = 64

GEAR_DIMENSION_NAMES = {
    "pitch_diameter_mm": "GEAR_PITCH_DIAMETER",
    "base_diameter_mm": "GEAR_BASE_DIAMETER",
    "outside_diameter_mm": "GEAR_OUTSIDE_DIAMETER",
    "root_diameter_mm": "GEAR_ROOT_DIAMETER",
    "bore_diameter_mm": "GEAR_BORE_DIAMETER",
}


class GearValidationError(ValueError):
    """Requested gear is outside the bounded geometry-only validity domain."""


@dataclass(frozen=True, slots=True)
class SpurGearSpec:
    tooth_count: int
    module_mm: float
    pressure_angle_deg: float
    face_width_mm: float
    bore_diameter_mm: float
    keyway_width_mm: float | None = None


@dataclass(frozen=True, slots=True)
class SpurGearParameters:
    tooth_count: int
    module_mm: float
    pressure_angle_deg: float
    face_width_mm: float
    bore_diameter_mm: float
    pitch_diameter_mm: float
    base_diameter_mm: float
    outside_diameter_mm: float
    root_diameter_mm: float
    circular_pitch_mm: float
    tooth_thickness_mm: float
    minimum_tooth_count: int


def calculate_spur_gear(spec: SpurGearSpec) -> SpurGearParameters:
    """Validate and calculate a standard external full-depth involute spur gear."""
    _validate_spec(spec)
    alpha = math.radians(float(spec.pressure_angle_deg))
    minimum_teeth = math.ceil(2.0 / (math.sin(alpha) ** 2))
    if spec.tooth_count < minimum_teeth:
        raise GearValidationError(
            f"tooth_count={spec.tooth_count} enters the ideal rack undercut domain; "
            f"minimum is {minimum_teeth} at {spec.pressure_angle_deg} degrees"
        )

    pitch = float(spec.module_mm) * spec.tooth_count
    base = pitch * math.cos(alpha)
    outside = float(spec.module_mm) * (spec.tooth_count + 2.0)
    root = float(spec.module_mm) * (spec.tooth_count - 2.5)
    if root <= 0.0:
        raise GearValidationError("root diameter must remain positive")

    minimum_root_rim = 0.5 * float(spec.module_mm)
    actual_root_rim = 0.5 * (root - float(spec.bore_diameter_mm))
    if actual_root_rim < minimum_root_rim:
        raise GearValidationError(
            "bore diameter leaves insufficient root rim for the bounded geometry generator"
        )

    return SpurGearParameters(
        tooth_count=spec.tooth_count,
        module_mm=float(spec.module_mm),
        pressure_angle_deg=float(spec.pressure_angle_deg),
        face_width_mm=float(spec.face_width_mm),
        bore_diameter_mm=float(spec.bore_diameter_mm),
        pitch_diameter_mm=pitch,
        base_diameter_mm=base,
        outside_diameter_mm=outside,
        root_diameter_mm=root,
        circular_pitch_mm=math.pi * float(spec.module_mm),
        tooth_thickness_mm=0.5 * math.pi * float(spec.module_mm),
        minimum_tooth_count=minimum_teeth,
    )


def build_spur_gear_sketch(
    name: str,
    spec: SpurGearSpec,
    *,
    flank_samples: int = 8,
) -> SketchDefinition:
    """Build one editable native-sketch recipe from involute splines and circular arcs."""
    if not isinstance(name, str) or not name.strip():
        raise GearValidationError("gear sketch name must not be empty")
    if isinstance(flank_samples, bool) or not isinstance(flank_samples, int):
        raise GearValidationError("flank_samples must be an integer")
    if flank_samples < _MIN_FLANK_SAMPLES or flank_samples > _MAX_FLANK_SAMPLES:
        raise GearValidationError(
            f"flank_samples must be in [{_MIN_FLANK_SAMPLES}, {_MAX_FLANK_SAMPLES}]"
        )

    parameters = calculate_spur_gear(spec)
    center = Point2D(0.0, 0.0)
    entities: list[object] = [
        Circle(center, parameters.pitch_diameter_mm / 2.0, construction=True),
        Circle(center, parameters.base_diameter_mm / 2.0, construction=True),
        Circle(center, parameters.outside_diameter_mm / 2.0, construction=True),
        Circle(center, parameters.root_diameter_mm / 2.0, construction=True),
    ]
    dimensions = [
        DiameterDimension(GEAR_DIMENSION_NAMES["pitch_diameter_mm"], 0, parameters.pitch_diameter_mm),
        DiameterDimension(GEAR_DIMENSION_NAMES["base_diameter_mm"], 1, parameters.base_diameter_mm),
        DiameterDimension(GEAR_DIMENSION_NAMES["outside_diameter_mm"], 2, parameters.outside_diameter_mm),
        DiameterDimension(GEAR_DIMENSION_NAMES["root_diameter_mm"], 3, parameters.root_diameter_mm),
    ]
    if parameters.bore_diameter_mm > 0.0:
        bore_index = len(entities)
        entities.append(Circle(center, parameters.bore_diameter_mm / 2.0, construction=False))
        dimensions.append(
            DiameterDimension(
                GEAR_DIMENSION_NAMES["bore_diameter_mm"],
                bore_index,
                parameters.bore_diameter_mm,
            )
        )

    root_radius = parameters.root_diameter_mm / 2.0
    base_radius = parameters.base_diameter_mm / 2.0
    outside_radius = parameters.outside_diameter_mm / 2.0
    alpha = math.radians(parameters.pressure_angle_deg)
    pitch_involute = math.tan(alpha) - alpha
    outer_t = math.sqrt(max(0.0, (outside_radius / base_radius) ** 2 - 1.0))
    outer_involute = outer_t - math.atan(outer_t)
    half_tooth = math.pi / (2.0 * parameters.tooth_count)
    base_half_angle = half_tooth + pitch_involute
    tip_half_angle = base_half_angle - outer_involute
    if tip_half_angle <= 0.0 or base_half_angle >= math.pi / parameters.tooth_count:
        raise GearValidationError("involute angular span is degenerate for the requested gear")

    pitch_angle = 2.0 * math.pi / parameters.tooth_count
    for tooth_index in range(parameters.tooth_count):
        tooth_center = tooth_index * pitch_angle
        left_root = _polar(root_radius, tooth_center - base_half_angle)
        left_base = _polar(base_radius, tooth_center - base_half_angle)
        right_base = _polar(base_radius, tooth_center + base_half_angle)
        right_root = _polar(root_radius, tooth_center + base_half_angle)
        left_flank = tuple(
            _involute_point(
                base_radius,
                base_radius + (outside_radius - base_radius) * sample / (flank_samples - 1),
                tooth_center,
                base_half_angle,
                side=-1,
            )
            for sample in range(flank_samples)
        )
        right_flank = tuple(
            _involute_point(
                base_radius,
                base_radius + (outside_radius - base_radius) * sample / (flank_samples - 1),
                tooth_center,
                base_half_angle,
                side=1,
            )
            for sample in range(flank_samples)
        )
        left_tip = left_flank[-1]
        right_tip = right_flank[-1]
        entities.extend(
            (
                LineSegment(left_root, left_base),
                Spline(left_flank, degree=3),
                Arc(center, left_tip, right_tip, ArcDirection.COUNTER_CLOCKWISE),
                Spline(tuple(reversed(right_flank)), degree=3),
                LineSegment(right_base, right_root),
            )
        )
        next_left_root = _polar(
            root_radius,
            tooth_center + pitch_angle - base_half_angle,
        )
        entities.append(
            Arc(center, right_root, next_left_root, ArcDirection.COUNTER_CLOCKWISE)
        )

    return SketchDefinition(
        name=name.strip(),
        plane=SketchPlane(PlaneKind.FRONT),
        entities=tuple(entities),
        dimensions=tuple(dimensions),
    )


def _validate_spec(spec: SpurGearSpec) -> None:
    if isinstance(spec.tooth_count, bool) or not isinstance(spec.tooth_count, int):
        raise GearValidationError("tooth_count must be an integer")
    if spec.tooth_count < 3 or spec.tooth_count > _MAX_TOOTH_COUNT:
        raise GearValidationError(f"tooth_count must be in [3, {_MAX_TOOTH_COUNT}]")
    _positive_finite(spec.module_mm, "module_mm")
    _positive_finite(spec.face_width_mm, "face_width_mm")
    if not math.isfinite(float(spec.pressure_angle_deg)) or not (
        _MIN_PRESSURE_ANGLE_DEG <= float(spec.pressure_angle_deg) <= _MAX_PRESSURE_ANGLE_DEG
    ):
        raise GearValidationError(
            f"pressure_angle_deg must be in [{_MIN_PRESSURE_ANGLE_DEG}, {_MAX_PRESSURE_ANGLE_DEG}]"
        )
    if not math.isfinite(float(spec.bore_diameter_mm)) or float(spec.bore_diameter_mm) < 0.0:
        raise GearValidationError("bore_diameter_mm must be finite and non-negative")
    if spec.keyway_width_mm is not None:
        raise GearValidationError(
            "keyway generation is unsupported until bounded native verification exists"
        )


def _positive_finite(value: float, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GearValidationError(f"{label} must be numeric")
    if not math.isfinite(float(value)) or float(value) <= 0.0:
        raise GearValidationError(f"{label} must be positive and finite")


def _polar(radius: float, angle: float) -> Point2D:
    return Point2D(radius * math.cos(angle), radius * math.sin(angle))


def _involute_point(
    base_radius: float,
    radius: float,
    tooth_center: float,
    base_half_angle: float,
    *,
    side: int,
) -> Point2D:
    t = math.sqrt(max(0.0, (radius / base_radius) ** 2 - 1.0))
    involute = t - math.atan(t)
    half_angle = base_half_angle - involute
    angle = tooth_center + side * half_angle
    return _polar(radius, angle)
