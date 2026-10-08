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
    Circle,
    CoincidentConstraint,
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
from cdt_solidworks.sketch.service import (  # noqa: E402
    SketchContextError,
    SketchMutationError,
    SketchService,
    SketchValidationError,
)


class FakeSketchRuntime:
    def __init__(self, definition: SketchDefinition) -> None:
        self.definition = definition
        self.calls: list[str] = []
        self.resolved = ResolvedDocument(
            document_id="part-1",
            revision=7,
            document_type="part",
            units="mm",
            configuration="Default",
        )
        self.receipt = MutationReceipt(object_id="Sketch1")
        self.rebuild_result = RebuildResult(ok=True)
        self.snapshot: SketchSnapshot | None = SketchSnapshot(
            sketch_id="Sketch1",
            plane=definition.plane,
            entity_count=len(definition.entities),
            constraint_count=len(definition.constraints),
            dimension_values_mm={dimension.name: dimension.value_mm for dimension in definition.dimensions},
            definition_state=DefinitionState.FULLY_DEFINED,
        )

    def resolve_document(self, target: DocumentTarget) -> ResolvedDocument:
        self.calls.append("resolve_document")
        return self.resolved

    def create_sketch(self, document: ResolvedDocument, definition: SketchDefinition) -> MutationReceipt:
        self.calls.append("create_sketch")
        self.definition = definition
        return self.receipt

    def rebuild(self, document: ResolvedDocument) -> RebuildResult:
        self.calls.append("rebuild")
        return self.rebuild_result

    def get_sketch(self, document: ResolvedDocument, sketch_id: str) -> SketchSnapshot | None:
        self.calls.append("get_sketch")
        return self.snapshot


class SketchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = DocumentTarget(document_id="part-1", expected_revision=7, expected_units="mm")
        self.definition = SketchDefinition(
            name="PlateProfile",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(
                LineSegment(Point2D(0.0, 0.0), Point2D(120.0, 0.0)),
                LineSegment(Point2D(120.0, 0.0), Point2D(120.0, 80.0)),
                LineSegment(Point2D(120.0, 80.0), Point2D(0.0, 80.0)),
                LineSegment(Point2D(0.0, 80.0), Point2D(0.0, 0.0)),
                Circle(Point2D(15.0, 15.0), radius_mm=3.0),
            ),
            constraints=(
                HorizontalConstraint(entity_index=0),
                VerticalConstraint(entity_index=1),
                HorizontalConstraint(entity_index=2),
                VerticalConstraint(entity_index=3),
                CoincidentConstraint(first_entity_index=0, second_entity_index=1),
            ),
            dimensions=(
                DistanceDimension(name="width", entity_index=0, value_mm=120.0),
                DistanceDimension(name="height", entity_index=1, value_mm=80.0),
                DiameterDimension(name="hole_diameter", entity_index=4, value_mm=6.0),
            ),
        )

    def test_create_sketch_uses_single_bounded_dispatch_then_rebuild_and_readback(self) -> None:
        runtime = FakeSketchRuntime(self.definition)
        service = SketchService(runtime)

        snapshot = service.create(self.target, self.definition)

        self.assertEqual("Sketch1", snapshot.sketch_id)
        self.assertEqual(DefinitionState.FULLY_DEFINED, snapshot.definition_state)
        self.assertEqual(
            ["resolve_document", "create_sketch", "rebuild", "get_sketch"],
            runtime.calls,
        )

    def test_under_defined_sketch_is_successful_but_state_remains_explicit(self) -> None:
        runtime = FakeSketchRuntime(self.definition)
        runtime.snapshot = SketchSnapshot(
            sketch_id="Sketch1",
            plane=self.definition.plane,
            entity_count=len(self.definition.entities),
            constraint_count=len(self.definition.constraints),
            dimension_values_mm={dimension.name: dimension.value_mm for dimension in self.definition.dimensions},
            definition_state=DefinitionState.UNDER_DEFINED,
        )

        snapshot = SketchService(runtime).create(self.target, self.definition)

        self.assertEqual(DefinitionState.UNDER_DEFINED, snapshot.definition_state)

    def test_over_defined_sketch_is_not_reported_as_success(self) -> None:
        runtime = FakeSketchRuntime(self.definition)
        runtime.snapshot = SketchSnapshot(
            sketch_id="Sketch1",
            plane=self.definition.plane,
            entity_count=len(self.definition.entities),
            constraint_count=len(self.definition.constraints),
            dimension_values_mm={dimension.name: dimension.value_mm for dimension in self.definition.dimensions},
            definition_state=DefinitionState.OVER_DEFINED,
        )

        with self.assertRaisesRegex(SketchMutationError, "over-defined"):
            SketchService(runtime).create(self.target, self.definition)

    def test_empty_profile_is_rejected_before_runtime_dispatch(self) -> None:
        definition = SketchDefinition(name="Empty", plane=SketchPlane(PlaneKind.FRONT), entities=())
        runtime = FakeSketchRuntime(definition)

        with self.assertRaisesRegex(SketchValidationError, "at least one entity"):
            SketchService(runtime).create(self.target, definition)

        self.assertEqual([], runtime.calls)

    def test_negative_dimension_is_rejected_before_runtime_dispatch(self) -> None:
        definition = SketchDefinition(
            name="BadDimension",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(LineSegment(Point2D(0.0, 0.0), Point2D(1.0, 0.0)),),
            dimensions=(DistanceDimension(name="bad", entity_index=0, value_mm=-1.0),),
        )
        runtime = FakeSketchRuntime(definition)

        with self.assertRaisesRegex(SketchValidationError, "positive"):
            SketchService(runtime).create(self.target, definition)

        self.assertEqual([], runtime.calls)

    def test_stale_document_revision_is_rejected_before_mutation(self) -> None:
        runtime = FakeSketchRuntime(self.definition)
        runtime.resolved = ResolvedDocument(
            document_id="part-1",
            revision=8,
            document_type="part",
            units="mm",
            configuration="Default",
        )

        with self.assertRaisesRegex(SketchContextError, "stale document"):
            SketchService(runtime).create(self.target, self.definition)

        self.assertEqual(["resolve_document"], runtime.calls)

    def test_unsupported_document_type_is_rejected_before_mutation(self) -> None:
        runtime = FakeSketchRuntime(self.definition)
        runtime.resolved = ResolvedDocument(
            document_id="part-1",
            revision=7,
            document_type="assembly",
            units="mm",
            configuration="Default",
        )

        with self.assertRaisesRegex(SketchContextError, "part document"):
            SketchService(runtime).create(self.target, self.definition)

        self.assertEqual(["resolve_document"], runtime.calls)

    def test_rebuild_failure_is_never_downgraded_to_success(self) -> None:
        runtime = FakeSketchRuntime(self.definition)
        runtime.rebuild_result = RebuildResult(ok=False, error_code="rebuild_failed", message="dangling relation")

        with self.assertRaisesRegex(SketchMutationError, "rebuild failed"):
            SketchService(runtime).create(self.target, self.definition)

        self.assertEqual(["resolve_document", "create_sketch", "rebuild"], runtime.calls)

    def test_postcondition_mismatch_is_failure(self) -> None:
        runtime = FakeSketchRuntime(self.definition)
        runtime.snapshot = SketchSnapshot(
            sketch_id="Sketch1",
            plane=self.definition.plane,
            entity_count=len(self.definition.entities) - 1,
            constraint_count=len(self.definition.constraints),
            dimension_values_mm={dimension.name: dimension.value_mm for dimension in self.definition.dimensions},
            definition_state=DefinitionState.FULLY_DEFINED,
        )

        with self.assertRaisesRegex(SketchMutationError, "entity count"):
            SketchService(runtime).create(self.target, self.definition)


if __name__ == "__main__":
    unittest.main()
