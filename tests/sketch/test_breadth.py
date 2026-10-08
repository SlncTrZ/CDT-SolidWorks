from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cdt_solidworks.part.runtime import (  # noqa: E402
    DocumentTarget,
    MutationReceipt,
    RebuildResult,
    ResolvedDocument,
)
from cdt_solidworks.sketch.models import (  # noqa: E402
    AngularDimension,
    Arc,
    ArcDirection,
    CenterLine,
    Circle,
    ConcentricConstraint,
    DefinitionState,
    DimensionTolerance,
    Ellipse,
    EqualConstraint,
    FixConstraint,
    LineSegment,
    MidpointConstraint,
    ParallelConstraint,
    PerpendicularConstraint,
    PlaneKind,
    Point2D,
    RadiusDimension,
    SketchDefinition,
    SketchDimensionSnapshot,
    SketchPlane,
    SketchPoint,
    SketchRelationSnapshot,
    SketchSnapshot,
    Spline,
    SymmetricConstraint,
    TangentConstraint,
    UnfixConstraint,
)
from cdt_solidworks.sketch.service import (  # noqa: E402
    SketchMutationError,
    SketchService,
    SketchValidationError,
)


class BreadthSketchRuntime:
    def __init__(self, definition: SketchDefinition) -> None:
        self.calls: list[str] = []
        self.resolved = ResolvedDocument(
            document_id="part-breadth",
            revision=4,
            document_type="part",
            units="mm",
            configuration="Default",
        )
        self.definition = definition
        self.relations: tuple[SketchRelationSnapshot, ...] = (
            SketchRelationSnapshot("rel-1", "horizontal", ("entity-1",)),
        )
        self.dimensions: dict[str, SketchDimensionSnapshot] = {}
        self.snapshot = SketchSnapshot(
            sketch_id="SketchBreadth",
            plane=definition.plane,
            entity_count=len(definition.entities),
            constraint_count=len(definition.constraints),
            dimension_values_mm={
                dimension.name: dimension.value_for_readback
                for dimension in definition.dimensions
            },
            definition_state=DefinitionState.UNDER_DEFINED,
        )

    def resolve_document(self, target: DocumentTarget) -> ResolvedDocument:
        self.calls.append("resolve_document")
        return self.resolved

    def create_sketch(self, document: ResolvedDocument, definition: SketchDefinition) -> MutationReceipt:
        self.calls.append("create_sketch")
        return MutationReceipt("SketchBreadth")

    def rebuild(self, document: ResolvedDocument) -> RebuildResult:
        self.calls.append("rebuild")
        return RebuildResult(ok=True)

    def get_sketch(self, document: ResolvedDocument, sketch_id: str) -> SketchSnapshot | None:
        self.calls.append("get_sketch")
        return self.snapshot

    def list_sketch_relations(
        self,
        document: ResolvedDocument,
        sketch_id: str,
    ) -> tuple[SketchRelationSnapshot, ...]:
        self.calls.append("list_sketch_relations")
        return self.relations

    def delete_sketch_relation(
        self,
        document: ResolvedDocument,
        sketch_id: str,
        relation_id: str,
    ) -> MutationReceipt:
        self.calls.append("delete_sketch_relation")
        self.relations = tuple(item for item in self.relations if item.relation_id != relation_id)
        return MutationReceipt(relation_id)

    def set_sketch_dimension(
        self,
        document: ResolvedDocument,
        sketch_id: str,
        name: str,
        value: float,
        unit: str,
    ) -> MutationReceipt:
        self.calls.append("set_sketch_dimension")
        self.dimensions[name] = SketchDimensionSnapshot(name, value, unit, True)
        return MutationReceipt(name)

    def get_sketch_dimension(
        self,
        document: ResolvedDocument,
        sketch_id: str,
        name: str,
    ) -> SketchDimensionSnapshot | None:
        self.calls.append("get_sketch_dimension")
        return self.dimensions.get(name)


class SketchBreadthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = DocumentTarget("part-breadth", expected_revision=4, expected_units="mm")

    def test_mixed_entity_relation_dimension_surface_is_accepted(self) -> None:
        entities = (
            LineSegment(Point2D(0.0, 0.0), Point2D(40.0, 0.0), construction=True),
            CenterLine(Point2D(0.0, -20.0), Point2D(0.0, 20.0)),
            Circle(Point2D(20.0, 10.0), radius_mm=5.0),
            Arc(
                center=Point2D(0.0, 0.0),
                start=Point2D(10.0, 0.0),
                end=Point2D(0.0, 10.0),
                direction=ArcDirection.COUNTER_CLOCKWISE,
            ),
            Ellipse(
                center=Point2D(0.0, 0.0),
                major_axis_point=Point2D(20.0, 0.0),
                minor_axis_point=Point2D(0.0, 10.0),
            ),
            SketchPoint(Point2D(5.0, 5.0)),
            Spline(points=(Point2D(0.0, 0.0), Point2D(5.0, 3.0), Point2D(10.0, 0.0)), degree=2),
        )
        definition = SketchDefinition(
            name="Breadth",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=entities,
            constraints=(
                ParallelConstraint(0, 1),
                PerpendicularConstraint(0, 1),
                ConcentricConstraint(2, 3),
                TangentConstraint(2, 3),
                EqualConstraint(0, 1),
                MidpointConstraint(5, 0),
                SymmetricConstraint(2, 3, 1),
                FixConstraint(5),
                UnfixConstraint(5),
            ),
            dimensions=(
                RadiusDimension(
                    name="r1",
                    entity_index=2,
                    value_mm=5.0,
                    tolerance=DimensionTolerance(lower=-0.1, upper=0.2),
                ),
                AngularDimension(
                    name="a1",
                    first_entity_index=0,
                    second_entity_index=1,
                    value_deg=90.0,
                    driving=False,
                ),
            ),
        )
        runtime = BreadthSketchRuntime(definition)

        snapshot = SketchService(runtime).create(self.target, definition)

        self.assertEqual("SketchBreadth", snapshot.sketch_id)
        self.assertEqual(
            ["resolve_document", "create_sketch", "rebuild", "get_sketch"],
            runtime.calls,
        )

    def test_centerline_requires_distinct_endpoints(self) -> None:
        point = Point2D(1.0, 1.0)
        definition = SketchDefinition(
            name="BadCenterline",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(CenterLine(point, point),),
        )
        runtime = BreadthSketchRuntime(definition)

        with self.assertRaisesRegex(SketchValidationError, "centerline endpoints"):
            SketchService(runtime).create(self.target, definition)

        self.assertEqual([], runtime.calls)

    def test_arc_rejects_center_equal_to_endpoint(self) -> None:
        definition = SketchDefinition(
            name="BadArc",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(
                Arc(
                    center=Point2D(0.0, 0.0),
                    start=Point2D(0.0, 0.0),
                    end=Point2D(1.0, 0.0),
                ),
            ),
        )
        runtime = BreadthSketchRuntime(definition)

        with self.assertRaisesRegex(SketchValidationError, "arc"):
            SketchService(runtime).create(self.target, definition)

    def test_ellipse_requires_nonzero_noncollinear_axes(self) -> None:
        definition = SketchDefinition(
            name="BadEllipse",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(
                Ellipse(
                    center=Point2D(0.0, 0.0),
                    major_axis_point=Point2D(10.0, 0.0),
                    minor_axis_point=Point2D(5.0, 0.0),
                ),
            ),
        )
        runtime = BreadthSketchRuntime(definition)

        with self.assertRaisesRegex(SketchValidationError, "ellipse axes"):
            SketchService(runtime).create(self.target, definition)

    def test_spline_is_bounded_and_requires_at_least_two_distinct_points(self) -> None:
        too_many = tuple(Point2D(float(index), 0.0) for index in range(65))
        definition = SketchDefinition(
            name="HugeSpline",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(Spline(points=too_many),),
        )
        runtime = BreadthSketchRuntime(definition)

        with self.assertRaisesRegex(SketchValidationError, "at most 64"):
            SketchService(runtime).create(self.target, definition)

    def test_pair_relation_rejects_self_reference(self) -> None:
        definition = SketchDefinition(
            name="BadRelation",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(LineSegment(Point2D(0.0, 0.0), Point2D(1.0, 0.0)),),
            constraints=(ParallelConstraint(0, 0),),
        )
        runtime = BreadthSketchRuntime(definition)

        with self.assertRaisesRegex(SketchValidationError, "distinct entities"):
            SketchService(runtime).create(self.target, definition)

    def test_angular_dimension_requires_distinct_line_targets_and_valid_angle(self) -> None:
        definition = SketchDefinition(
            name="BadAngle",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(
                LineSegment(Point2D(0.0, 0.0), Point2D(10.0, 0.0)),
                Circle(Point2D(5.0, 5.0), 2.0),
            ),
            dimensions=(AngularDimension("angle", 0, 1, 190.0),),
        )
        runtime = BreadthSketchRuntime(definition)

        with self.assertRaisesRegex(SketchValidationError, "angular dimension"):
            SketchService(runtime).create(self.target, definition)

    def test_radius_dimension_requires_circle_or_arc(self) -> None:
        definition = SketchDefinition(
            name="BadRadius",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(LineSegment(Point2D(0.0, 0.0), Point2D(10.0, 0.0)),),
            dimensions=(RadiusDimension("radius", 0, 2.0),),
        )
        runtime = BreadthSketchRuntime(definition)

        with self.assertRaisesRegex(SketchValidationError, "radius dimension"):
            SketchService(runtime).create(self.target, definition)

    def test_tolerance_lower_must_not_exceed_upper(self) -> None:
        definition = SketchDefinition(
            name="BadTolerance",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(Circle(Point2D(0.0, 0.0), 2.0),),
            dimensions=(
                RadiusDimension(
                    "radius",
                    0,
                    2.0,
                    tolerance=DimensionTolerance(lower=0.2, upper=-0.1),
                ),
            ),
        )
        runtime = BreadthSketchRuntime(definition)

        with self.assertRaisesRegex(SketchValidationError, "tolerance"):
            SketchService(runtime).create(self.target, definition)

    def test_relation_query_and_delete_are_rebuild_gated(self) -> None:
        definition = SketchDefinition(
            name="Relations",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(LineSegment(Point2D(0.0, 0.0), Point2D(10.0, 0.0)),),
        )
        runtime = BreadthSketchRuntime(definition)
        service = SketchService(runtime)

        relations = service.list_relations(self.target, "SketchBreadth")
        self.assertEqual(("rel-1",), tuple(item.relation_id for item in relations))
        remaining = service.delete_relation(self.target, "SketchBreadth", "rel-1")
        self.assertEqual((), remaining)
        self.assertEqual(
            [
                "resolve_document",
                "list_sketch_relations",
                "resolve_document",
                "delete_sketch_relation",
                "rebuild",
                "list_sketch_relations",
            ],
            runtime.calls,
        )

    def test_dimension_write_requires_rebuild_and_exact_readback(self) -> None:
        definition = SketchDefinition(
            name="Dims",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(LineSegment(Point2D(0.0, 0.0), Point2D(10.0, 0.0)),),
        )
        runtime = BreadthSketchRuntime(definition)

        snapshot = SketchService(runtime).set_dimension_value(
            self.target,
            "SketchBreadth",
            "width",
            25.0,
            unit="mm",
        )

        self.assertEqual(25.0, snapshot.value)
        self.assertEqual(
            ["resolve_document", "set_sketch_dimension", "rebuild", "get_sketch_dimension"],
            runtime.calls,
        )

    def test_readback_angle_uses_degrees_not_millimeters(self) -> None:
        definition = SketchDefinition(
            name="Angles",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(
                LineSegment(Point2D(0.0, 0.0), Point2D(10.0, 0.0)),
                LineSegment(Point2D(0.0, 0.0), Point2D(0.0, 10.0)),
            ),
            dimensions=(AngularDimension("angle", 0, 1, 90.0),),
        )
        runtime = BreadthSketchRuntime(definition)
        runtime.snapshot = SketchSnapshot(
            sketch_id="SketchBreadth",
            plane=definition.plane,
            entity_count=2,
            constraint_count=0,
            dimension_values_mm={"angle": 89.0},
            definition_state=DefinitionState.FULLY_DEFINED,
        )

        with self.assertRaisesRegex(SketchMutationError, "angle"):
            SketchService(runtime).create(self.target, definition)


if __name__ == "__main__":
    unittest.main()
