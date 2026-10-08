from __future__ import annotations

from types import SimpleNamespace

from cdt_solidworks.part.fixtures import StandardPartFixtures
from cdt_solidworks.part.models import FeatureKind, FeatureSnapshot
from cdt_solidworks.part.runtime import DocumentTarget
from cdt_solidworks.sketch.models import SketchSnapshot


class Sketches:
    def __init__(self):
        self.definitions = []

    def create(self, target, definition):
        self.definitions.append(definition)
        return SketchSnapshot(
            sketch_id=definition.name,
            plane=definition.plane,
            entity_count=len(definition.entities),
            constraint_count=len(definition.constraints),
        )


class Parts:
    def __init__(self):
        self.calls = []

    @staticmethod
    def _result(name, kind, parameters=None):
        return SimpleNamespace(feature=FeatureSnapshot(name, name, kind, parameters or {}))

    def extrude(self, target, spec):
        self.calls.append(("extrude", spec))
        return self._result(spec.name, FeatureKind.EXTRUDE, {"depth_mm": spec.depth_mm})

    def hole(self, target, spec):
        self.calls.append(("hole", spec))
        return self._result(spec.name, FeatureKind.HOLE, {"diameter_mm": spec.diameter_mm})

    def revolve(self, target, spec):
        self.calls.append(("revolve", spec))
        return self._result(spec.name, FeatureKind.REVOLVE, {"angle_deg": spec.angle_deg})

    def linear_pattern(self, target, spec):
        self.calls.append(("linear_pattern", spec))
        return self._result(spec.name, FeatureKind.LINEAR_PATTERN, {"count": spec.count})


def test_mounting_plate_is_reusable_ordinary_feature_tree_fixture() -> None:
    sketches, parts = Sketches(), Parts()
    result = StandardPartFixtures(sketches, parts).mounting_plate(DocumentTarget("part", 1))
    assert result.sketch_id == "MountingPlate_Profile"
    assert len(result.feature_ids) == 5
    assert [kind for kind, _ in parts.calls] == ["extrude", "hole", "hole", "hole", "hole"]


def test_stepped_shaft_uses_profile_centerline_revolve() -> None:
    sketches, parts = Sketches(), Parts()
    result = StandardPartFixtures(sketches, parts).stepped_shaft(DocumentTarget("part", 1))
    assert result.feature_ids == ("SteppedShaft",)
    kind, spec = parts.calls[-1]
    assert kind == "revolve"
    assert spec.axis_ref == "profile_centerline"
    assert spec.angle_deg == 360.0


def test_hole_family_uses_seed_feature_then_linear_pattern() -> None:
    sketches, parts = Sketches(), Parts()
    result = StandardPartFixtures(sketches, parts).hole_pattern_family(
        DocumentTarget("part", 1), count=5, spacing_mm=18.0
    )
    assert result.feature_ids == ("HolePattern_Base", "HolePattern_Seed", "HolePattern_Linear")
    kind, spec = parts.calls[-1]
    assert kind == "linear_pattern"
    assert spec.seed_feature_ids == ("HolePattern_Seed",)
    assert spec.count == 5
    assert spec.spacing_mm == 18.0
