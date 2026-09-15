from __future__ import annotations

import pytest

from cdt_solidworks.mechanical.gear import SpurGearSpec
from cdt_solidworks.mechanical.service import SpurGearService
from cdt_solidworks.part.models import BodySnapshot, Bounds3D, ExtrudeSpec, FeatureKind, FeatureSnapshot, PartMutationResult
from cdt_solidworks.part.runtime import DocumentTarget
from cdt_solidworks.sketch.models import SketchSnapshot


class SketchPort:
    def __init__(self) -> None:
        self.definition = None
        self.snapshot = None

    def create(self, target, definition):
        self.definition = definition
        values = {dimension.name: dimension.value_for_readback for dimension in definition.dimensions}
        self.snapshot = SketchSnapshot(
            sketch_id=definition.name,
            plane=definition.plane,
            entity_count=len(definition.entities),
            constraint_count=len(definition.constraints),
            dimension_values_mm=values,
        )
        return self.snapshot

    def get(self, target, sketch_id):
        assert self.snapshot is not None and self.snapshot.sketch_id == sketch_id
        return self.snapshot


class PartPort:
    def __init__(self) -> None:
        self.feature = None
        self.calls = []

    def extrude(self, target, spec: ExtrudeSpec, *, postconditions=None):
        self.calls.append(("extrude", spec))
        self.feature = FeatureSnapshot(
            feature_id=spec.name, name=spec.name, kind=FeatureKind.EXTRUDE, parameters={"depth_mm": spec.depth_mm}
        )
        return PartMutationResult(
            self.feature,
            (BodySnapshot("GearBody", Bounds3D(-30, -30, 0, 30, 30, spec.depth_mm)),),
        )

    def get_feature(self, target, feature_id):
        assert self.feature is not None and self.feature.feature_id == feature_id
        return self.feature

    def set_feature_parameter(self, target, feature_id, parameter, value):
        assert self.feature is not None and self.feature.feature_id == feature_id
        self.calls.append(("set", feature_id, parameter, value))
        self.feature = FeatureSnapshot(
            feature_id, feature_id, FeatureKind.EXTRUDE, {"depth_mm": value}
        )
        return self.feature


def test_create_gear_uses_sketch_and_part_primitives_only() -> None:
    sketches = SketchPort()
    parts = PartPort()
    service = SpurGearService(sketches, parts)
    target = DocumentTarget("gear.SLDPRT", 1, "mm", expected_configuration="Default")
    spec = SpurGearSpec(24, 2.0, 20.0, 12.0, 10.0)

    result = service.create(target, "DriveGear", spec)

    assert result.sketch_id == "DriveGear_Profile"
    assert result.feature_id == "DriveGear"
    assert result.parameters.pitch_diameter_mm == pytest.approx(48.0)
    assert sketches.definition is not None
    assert parts.calls[0][0] == "extrude"
    assert parts.calls[0][1].profile.sketch_id == result.sketch_id


def test_parameters_reconstruct_from_named_sketch_dimensions_after_reopen() -> None:
    sketches = SketchPort()
    parts = PartPort()
    service = SpurGearService(sketches, parts)
    target = DocumentTarget("gear.SLDPRT", 1, "mm")
    created = service.create(target, "DriveGear", SpurGearSpec(24, 2.0, 20.0, 12.0, 10.0))

    reopened = DocumentTarget("gear.SLDPRT", 2, "mm")
    params = service.parameters_get(reopened, created.sketch_id, created.feature_id)

    assert params.tooth_count == 24
    assert params.module_mm == pytest.approx(2.0)
    assert params.pressure_angle_deg == pytest.approx(20.0)
    assert params.face_width_mm == pytest.approx(12.0)
    assert params.bore_diameter_mm == pytest.approx(10.0)


def test_parameter_set_is_bounded_to_face_width_and_preserves_feature_identity() -> None:
    sketches = SketchPort()
    parts = PartPort()
    service = SpurGearService(sketches, parts)
    target = DocumentTarget("gear.SLDPRT", 1, "mm")
    created = service.create(target, "DriveGear", SpurGearSpec(24, 2.0, 20.0, 12.0, 10.0))

    updated = service.parameter_set(target, created.feature_id, "face_width_mm", 18.0)
    assert updated.feature_id == created.feature_id
    assert updated.parameters["depth_mm"] == pytest.approx(18.0)

    with pytest.raises(ValueError, match="face_width_mm"):
        service.parameter_set(target, created.feature_id, "module_mm", 2.5)
