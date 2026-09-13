from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Callable, TypeVar

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cdt_solidworks.part.runtime import DocumentTarget, RebuildResult  # noqa: E402
from cdt_solidworks.sketch.models import (  # noqa: E402
    Circle,
    HorizontalConstraint,
    LineSegment,
    PlaneKind,
    Point2D,
    SketchDefinition,
    SketchPlane,
)
from cdt_solidworks.sketch.native import (  # noqa: E402
    NativeSketchBinding,
    NativeSketchUnsupportedError,
    SketchNativeRuntime,
)
from cdt_solidworks.sketch.service import SketchService  # noqa: E402

T = TypeVar("T")


class FakeSegment:
    def __init__(self, segment_id: int) -> None:
        self.segment_id = segment_id
        self.ConstructionGeometry = False

    def GetID(self) -> tuple[int, int]:
        return (self.segment_id, 0)


class FakePoint:
    def __init__(self, point_id: int, point_type: int) -> None:
        self.point_id = point_id
        self.Type = point_type

    def GetID(self) -> tuple[int, int]:
        return (self.point_id, 1)


class FakeRelationManager:
    def GetRelationsCount(self, filter_value: int) -> int:
        return 0


class FakeSketch:
    def __init__(self) -> None:
        self.segments: list[FakeSegment] = []
        self.points: list[Any] = []
        self.RelationManager = FakeRelationManager()

    def GetSketchSegments(self) -> tuple[FakeSegment, ...]:
        return tuple(self.segments)

    def GetUserPoints2(self) -> tuple[Any, ...]:
        return tuple(self.points)

    def GetSketchPoints2(self) -> tuple[Any, ...]:
        return tuple(self.points)

    def GetConstrainedStatus(self) -> int:
        return 2


class FakeFeature:
    def __init__(self, sketch: FakeSketch) -> None:
        self.Name = "Sketch1"
        self._sketch = sketch

    def GetSpecificFeature2(self) -> FakeSketch:
        return self._sketch


class FakeSketchManager:
    def __init__(self, sketch: FakeSketch) -> None:
        self.ActiveSketch: FakeSketch | None = None
        self.AddToDB = False
        self.DisplayWhenAdded = True
        self._sketch = sketch
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def InsertSketch(self, update_edit_rebuild: bool) -> None:
        self.ActiveSketch = self._sketch if self.ActiveSketch is None else None

    def CreateLine(self, *args: float) -> FakeSegment:
        self.calls.append(("CreateLine", args))
        segment = FakeSegment(len(self._sketch.segments) + 1)
        self._sketch.segments.append(segment)
        return segment

    def CreateCircleByRadius(self, *args: float) -> FakeSegment:
        self.calls.append(("CreateCircleByRadius", args))
        segment = FakeSegment(len(self._sketch.segments) + 1)
        self._sketch.segments.append(segment)
        return segment


class FakeModel:
    def __init__(self) -> None:
        self.sketch = FakeSketch()
        self.SketchManager = FakeSketchManager(self.sketch)
        self.feature = FakeFeature(self.sketch)

    def FeatureByName(self, name: str) -> FakeFeature | None:
        return self.feature if name == self.feature.Name else None


class SketchNativeRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = FakeModel()
        self.plane_calls: list[SketchPlane] = []

        def executor(
            operation: Callable[[Any], T],
            *,
            stage: str,
            mutation: bool,
        ) -> T:
            return operation(object())

        def resolver(app: Any, target: DocumentTarget) -> NativeSketchBinding:
            return NativeSketchBinding(
                model=self.model,
                document_id=target.document_id,
                revision=target.expected_revision,
                units=target.expected_units,
            )

        def plane_selector(model: Any, plane: SketchPlane) -> None:
            self.plane_calls.append(plane)

        def feature_resolver(model: Any, sketch: Any, requested_name: str) -> FakeFeature:
            return self.model.feature

        def plane_resolver(model: Any, sketch: Any, sketch_id: str) -> SketchPlane:
            return SketchPlane(PlaneKind.FRONT)

        def member(obj: Any, name: str, *args: object) -> Any:
            value = getattr(obj, name)
            if args:
                return value(*args)
            return value() if callable(value) else value

        self.runtime = SketchNativeRuntime(
            executor=executor,
            binding_resolver=resolver,
            plane_selector=plane_selector,
            sketch_feature_resolver=feature_resolver,
            plane_resolver=plane_resolver,
            member=member,
            rebuild_verifier=lambda model: RebuildResult(ok=True),
        )
        self.target = DocumentTarget("part-native", expected_revision=1, expected_units="mm")

    def test_geometry_creation_converts_mm_to_m_and_reads_native_segments(self) -> None:
        definition = SketchDefinition(
            name="NativeSketch",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(
                LineSegment(Point2D(0.0, 0.0), Point2D(25.0, 10.0), construction=True),
                Circle(Point2D(5.0, 5.0), 2.5),
            ),
        )

        snapshot = SketchService(self.runtime).create(self.target, definition)

        line_call = self.model.SketchManager.calls[0]
        circle_call = self.model.SketchManager.calls[1]
        self.assertEqual("CreateLine", line_call[0])
        self.assertEqual((0.0, 0.0, 0.0, 0.025, 0.01, 0.0), line_call[1])
        self.assertEqual("CreateCircleByRadius", circle_call[0])
        self.assertEqual((0.005, 0.005, 0.0, 0.0025), circle_call[1])
        self.assertTrue(self.model.sketch.segments[0].ConstructionGeometry)
        self.assertEqual(2, snapshot.entity_count)
        self.assertEqual(("1:0", "2:0"), snapshot.entity_ids)
        self.assertEqual([SketchPlane(PlaneKind.FRONT)], self.plane_calls)

    def test_sketch_query_uses_persistent_plane_resolver_without_create_cache(self) -> None:
        self.model.feature.Name = "PersistedSketch"
        self.model.sketch.segments.append(FakeSegment(7))
        self.model.sketch.points.extend((FakePoint(8, 1), FakePoint(9, 2), FakePoint(10, 9)))
        document = self.runtime.resolve_document(self.target)

        snapshot = self.runtime.get_sketch(document, "PersistedSketch")

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(SketchPlane(PlaneKind.FRONT), snapshot.plane)
        self.assertEqual(2, snapshot.entity_count)
        self.assertEqual(("7:0", "8:1"), snapshot.entity_ids)

    def test_native_adapter_fails_closed_for_unpromoted_relations(self) -> None:
        definition = SketchDefinition(
            name="Relations",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(LineSegment(Point2D(0.0, 0.0), Point2D(10.0, 0.0)),),
            constraints=(HorizontalConstraint(0),),
        )
        document = self.runtime.resolve_document(self.target)

        with self.assertRaisesRegex(NativeSketchUnsupportedError, "relation creation"):
            self.runtime.create_sketch(document, definition)

        self.assertEqual([], self.model.SketchManager.calls)


if __name__ == "__main__":
    unittest.main()
