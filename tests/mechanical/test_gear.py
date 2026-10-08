from __future__ import annotations

import math

import pytest

from cdt_solidworks.mechanical.gear import (
    GEAR_DIMENSION_NAMES,
    GearValidationError,
    SpurGearSpec,
    build_spur_gear_sketch,
    calculate_spur_gear,
)
from cdt_solidworks.sketch.models import Arc, Circle, Spline


def test_standard_spur_gear_formula_is_deterministic() -> None:
    spec = SpurGearSpec(
        tooth_count=24,
        module_mm=2.0,
        pressure_angle_deg=20.0,
        face_width_mm=12.0,
        bore_diameter_mm=10.0,
    )
    first = calculate_spur_gear(spec)
    second = calculate_spur_gear(spec)
    assert first == second
    assert first.pitch_diameter_mm == pytest.approx(48.0)
    assert first.base_diameter_mm == pytest.approx(48.0 * math.cos(math.radians(20.0)))
    assert first.outside_diameter_mm == pytest.approx(52.0)
    assert first.root_diameter_mm == pytest.approx(43.0)
    assert first.circular_pitch_mm == pytest.approx(math.pi * 2.0)
    assert first.tooth_thickness_mm == pytest.approx(math.pi)
    assert first.minimum_tooth_count == 18


def test_undercut_domain_is_refused_before_geometry_generation() -> None:
    with pytest.raises(GearValidationError, match="undercut"):
        calculate_spur_gear(SpurGearSpec(12, 2.0, 20.0, 10.0, 5.0))


def test_bore_must_leave_bounded_root_rim() -> None:
    with pytest.raises(GearValidationError, match="root rim"):
        calculate_spur_gear(SpurGearSpec(24, 2.0, 20.0, 10.0, 42.0))


def test_keyway_is_fail_closed_until_native_evidence_exists() -> None:
    with pytest.raises(GearValidationError, match="keyway"):
        calculate_spur_gear(SpurGearSpec(24, 2.0, 20.0, 10.0, 8.0, keyway_width_mm=3.0))


def test_sketch_uses_editable_involute_splines_and_named_engineering_dimensions() -> None:
    spec = SpurGearSpec(18, 1.5, 20.0, 8.0, 6.0)
    definition = build_spur_gear_sketch("DriveGearProfile", spec, flank_samples=6)
    assert definition.name == "DriveGearProfile"
    assert sum(isinstance(entity, Spline) for entity in definition.entities) == spec.tooth_count * 2
    assert sum(isinstance(entity, Arc) for entity in definition.entities) == spec.tooth_count * 2
    assert sum(isinstance(entity, Circle) and entity.construction for entity in definition.entities) == 4
    assert sum(isinstance(entity, Circle) and not entity.construction for entity in definition.entities) == 1
    assert {dimension.name for dimension in definition.dimensions} == set(GEAR_DIMENSION_NAMES.values())
    assert all(spline.degree == 3 for spline in definition.entities if isinstance(spline, Spline))



def test_tooth_count_is_bounded_for_native_sketch_scalability() -> None:
    with pytest.raises(GearValidationError, match="tooth_count"):
        calculate_spur_gear(SpurGearSpec(41, 2.0, 20.0, 10.0, 8.0))

def test_sketch_generation_is_byte_stable_at_point_level() -> None:
    spec = SpurGearSpec(20, 2.5, 20.0, 15.0, 12.0)
    first = build_spur_gear_sketch("GearProfile", spec, flank_samples=8)
    second = build_spur_gear_sketch("GearProfile", spec, flank_samples=8)
    assert first == second
