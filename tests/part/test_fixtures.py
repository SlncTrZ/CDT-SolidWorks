from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cdt_solidworks.part.models import (  # noqa: E402
    BodySnapshot,
    Bounds3D,
    CutSpec,
    ExtrudeSpec,
    FeatureKind,
    FeatureSnapshot,
    HoleFact,
    PartPostconditions,
    ProfileRef,
    RevolveSpec,
)
from cdt_solidworks.part.runtime import (  # noqa: E402
    DocumentTarget,
    MutationReceipt,
    RebuildResult,
    ResolvedDocument,
)
from cdt_solidworks.part.service import PartService  # noqa: E402
from cdt_solidworks.sketch.models import (  # noqa: E402
    Circle,
    DefinitionState,
    DiameterDimension,
    DistanceDimension,
    HorizontalConstraint,
    LineSegment,
    PlaneKind,
    Point2D,
    SketchDefinition,
    SketchPlane,
    SketchSnapshot,
    VerticalConstraint,
)
from cdt_solidworks.sketch.service import SketchService  # noqa: E402


PLATE_BOUNDS = Bounds3D(0.0, 0.0, 0.0, 120.0, 80.0, 10.0)
PLATE_HOLES = (
    HoleFact(15.0, 15.0, 3.0, through=True),
    HoleFact(105.0, 15.0, 3.0, through=True),
    HoleFact(15.0, 65.0, 3.0, through=True),
    HoleFact(105.0, 65.0, 3.0, through=True),
)


class FixtureRuntime:
    """Deterministic protocol fake; it does not claim native SolidWorks execution."""

    def __init__(self) -> None:
        self.resolved = ResolvedDocument(
            document_id="fixture-part",
            revision=1,
            document_type="part",
            units="mm",
            configuration="Default",
        )
        self.sketches: dict[str, SketchSnapshot] = {}
        self.features: dict[str, FeatureSnapshot] = {}
        self.bodies: tuple[BodySnapshot, ...] = ()

    def resolve_document(self, target: DocumentTarget) -> ResolvedDocument:
        return self.resolved

    def create_sketch(self, document: ResolvedDocument, definition: SketchDefinition) -> MutationReceipt:
        sketch_id = f"Sketch-{definition.name}"
        self.sketches[sketch_id] = SketchSnapshot(
            sketch_id=sketch_id,
            plane=definition.plane,
            entity_count=len(definition.entities),
            constraint_count=len(definition.constraints),
            dimension_values_mm={dimension.name: dimension.value_mm for dimension in definition.dimensions},
            definition_state=DefinitionState.FULLY_DEFINED,
        )
        return MutationReceipt(sketch_id)

    def get_sketch(self, document: ResolvedDocument, sketch_id: str) -> SketchSnapshot | None:
        return self.sketches.get(sketch_id)

    def create_extrude(self, document: ResolvedDocument, spec: ExtrudeSpec) -> MutationReceipt:
        feature_id = "Boss-Extrude1"
        self.features[feature_id] = FeatureSnapshot(
            feature_id=feature_id,
            name=spec.name,
            kind=FeatureKind.EXTRUDE,
            parameters={"depth_mm": spec.depth_mm},
        )
        self.bodies = (BodySnapshot(body_id="Body1", bounds=PLATE_BOUNDS),)
        return MutationReceipt(feature_id)

    def create_cut(self, document: ResolvedDocument, spec: CutSpec) -> MutationReceipt:
        feature_id = "Cut-Extrude1"
        parameters: dict[str, float | str | bool] = {"through_all": spec.through_all}
        if spec.depth_mm is not None:
            parameters["depth_mm"] = spec.depth_mm
        self.features[feature_id] = FeatureSnapshot(
            feature_id=feature_id,
            name=spec.name,
            kind=FeatureKind.CUT,
            parameters=parameters,
        )
        self.bodies = (BodySnapshot(body_id="Body1", bounds=PLATE_BOUNDS, holes=PLATE_HOLES),)
        return MutationReceipt(feature_id)

    def create_revolve(self, document: ResolvedDocument, spec: RevolveSpec) -> MutationReceipt:
        feature_id = "Revolve1"
        self.features[feature_id] = FeatureSnapshot(
            feature_id=feature_id,
            name=spec.name,
            kind=FeatureKind.REVOLVE,
            parameters={"angle_deg": spec.angle_deg, "axis_ref": spec.axis_ref},
        )
        self.bodies = (
            BodySnapshot(
                body_id="RevolveBody",
                bounds=Bounds3D(-10.0, -10.0, 0.0, 10.0, 10.0, 20.0),
            ),
        )
        return MutationReceipt(feature_id)

    def rebuild(self, document: ResolvedDocument) -> RebuildResult:
        return RebuildResult(ok=True)

    def get_feature(self, document: ResolvedDocument, feature_id: str) -> FeatureSnapshot | None:
        return self.features.get(feature_id)

    def list_features(self, document: ResolvedDocument) -> tuple[FeatureSnapshot, ...]:
        return tuple(self.features.values())

    def list_bodies(self, document: ResolvedDocument) -> tuple[BodySnapshot, ...]:
        return self.bodies


class PartFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = DocumentTarget("fixture-part", expected_revision=1, expected_units="mm")

    def test_mounting_plate_120x80x10_with_four_through_holes(self) -> None:
        runtime = FixtureRuntime()
        sketches = SketchService(runtime)
        parts = PartService(runtime)

        plate_profile = SketchDefinition(
            name="PlateProfile",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(
                LineSegment(Point2D(0.0, 0.0), Point2D(120.0, 0.0)),
                LineSegment(Point2D(120.0, 0.0), Point2D(120.0, 80.0)),
                LineSegment(Point2D(120.0, 80.0), Point2D(0.0, 80.0)),
                LineSegment(Point2D(0.0, 80.0), Point2D(0.0, 0.0)),
            ),
            constraints=(
                HorizontalConstraint(0),
                VerticalConstraint(1),
                HorizontalConstraint(2),
                VerticalConstraint(3),
            ),
            dimensions=(
                DistanceDimension("width", 0, 120.0),
                DistanceDimension("height", 1, 80.0),
            ),
        )
        plate_sketch = sketches.create(self.target, plate_profile)
        self.assertEqual(DefinitionState.FULLY_DEFINED, plate_sketch.definition_state)

        parts.extrude(
            self.target,
            ExtrudeSpec("BaseExtrude", ProfileRef(plate_sketch.sketch_id), 10.0),
            postconditions=PartPostconditions(body_count=1, bounds=(PLATE_BOUNDS,)),
        )

        hole_profile = SketchDefinition(
            name="HoleProfile",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=tuple(Circle(Point2D(h.center_x_mm, h.center_y_mm), h.radius_mm) for h in PLATE_HOLES),
            dimensions=tuple(
                DiameterDimension(f"hole_{index + 1}_diameter", index, 6.0)
                for index in range(len(PLATE_HOLES))
            ),
        )
        hole_sketch = sketches.create(self.target, hole_profile)

        parts.cut(
            self.target,
            CutSpec("MountHoles", ProfileRef(hole_sketch.sketch_id), through_all=True),
            postconditions=PartPostconditions(
                body_count=1,
                bounds=(PLATE_BOUNDS,),
                holes=PLATE_HOLES,
                tolerance_mm=1e-6,
            ),
        )

        snapshot = parts.inspect(self.target)
        self.assertEqual(2, len(snapshot.features))
        self.assertEqual(1, len(snapshot.bodies))
        self.assertEqual(PLATE_BOUNDS, snapshot.bodies[0].bounds)
        self.assertEqual(PLATE_HOLES, snapshot.bodies[0].holes)

    def test_revolve_fixture_is_verified_independently_from_extrusion(self) -> None:
        runtime = FixtureRuntime()
        sketches = SketchService(runtime)
        parts = PartService(runtime)

        revolve_profile = SketchDefinition(
            name="RevolveProfile",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(
                LineSegment(Point2D(5.0, 0.0), Point2D(10.0, 0.0)),
                LineSegment(Point2D(10.0, 0.0), Point2D(10.0, 20.0)),
                LineSegment(Point2D(10.0, 20.0), Point2D(5.0, 20.0)),
                LineSegment(Point2D(5.0, 20.0), Point2D(5.0, 0.0)),
            ),
            constraints=(HorizontalConstraint(0), VerticalConstraint(1), HorizontalConstraint(2), VerticalConstraint(3)),
            dimensions=(DistanceDimension("height", 1, 20.0),),
        )
        sketch = sketches.create(self.target, revolve_profile)

        result = parts.revolve(
            self.target,
            RevolveSpec("Knob", ProfileRef(sketch.sketch_id), axis_ref="Axis1", angle_deg=360.0),
            postconditions=PartPostconditions(
                body_count=1,
                bounds=(Bounds3D(-10.0, -10.0, 0.0, 10.0, 10.0, 20.0),),
            ),
        )

        self.assertEqual(FeatureKind.REVOLVE, result.feature.kind)
        self.assertEqual(360.0, result.feature.parameters["angle_deg"])


if __name__ == "__main__":
    unittest.main()
