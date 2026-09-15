from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cdt_solidworks.part.models import (
    ExtrudeSpec,
    HoleSpec,
    LinearPatternSpec,
    ProfileRef,
    RevolveSpec,
)
from cdt_solidworks.part.runtime import DocumentTarget
from cdt_solidworks.sketch.models import (
    CenterLine,
    DistanceDimension,
    HorizontalConstraint,
    LineSegment,
    PlaneKind,
    Point2D,
    SketchDefinition,
    SketchPlane,
    VerticalConstraint,
)


@dataclass(frozen=True, slots=True)
class FixtureResult:
    sketch_id: str
    feature_ids: tuple[str, ...]


class StandardPartFixtures:
    """Deterministic reference builders composed only from sketch/part primitives."""

    def __init__(self, sketch_service: Any, part_service: Any) -> None:
        self._sketches = sketch_service
        self._parts = part_service

    def mounting_plate(
        self,
        target: DocumentTarget,
        *,
        name: str = "MountingPlate",
        width_mm: float = 120.0,
        height_mm: float = 80.0,
        thickness_mm: float = 10.0,
        hole_diameter_mm: float = 6.0,
        inset_mm: float = 15.0,
    ) -> FixtureResult:
        self._positive(width_mm, height_mm, thickness_mm, hole_diameter_mm, inset_mm)
        if 2.0 * inset_mm >= min(width_mm, height_mm):
            raise ValueError("mounting plate inset must leave a positive interior span")
        definition = self._rectangle_definition(f"{name}_Profile", width_mm, height_mm)
        sketch = self._sketches.create(target, definition)
        base = self._parts.extrude(
            target, ExtrudeSpec(name, ProfileRef(sketch.sketch_id), thickness_mm)
        )
        feature_ids = [base.feature.feature_id]
        centers = (
            (inset_mm, inset_mm),
            (width_mm - inset_mm, inset_mm),
            (inset_mm, height_mm - inset_mm),
            (width_mm - inset_mm, height_mm - inset_mm),
        )
        for index, center in enumerate(centers, start=1):
            result = self._parts.hole(
                target,
                HoleSpec(
                    name=f"{name}_Hole{index}",
                    diameter_mm=hole_diameter_mm,
                    face_ref="bbox:+z",
                    centers_mm=(center,),
                    through_all=True,
                ),
            )
            feature_ids.append(result.feature.feature_id)
        return FixtureResult(sketch.sketch_id, tuple(feature_ids))

    def stepped_shaft(
        self,
        target: DocumentTarget,
        *,
        name: str = "SteppedShaft",
        first_diameter_mm: float = 20.0,
        first_length_mm: float = 30.0,
        second_diameter_mm: float = 30.0,
        second_length_mm: float = 40.0,
    ) -> FixtureResult:
        self._positive(first_diameter_mm, first_length_mm, second_diameter_mm, second_length_mm)
        r1 = first_diameter_mm / 2.0
        r2 = second_diameter_mm / 2.0
        x0, x1, x2 = 0.0, first_length_mm, first_length_mm + second_length_mm
        entities = (
            LineSegment(Point2D(x0, 0.0), Point2D(x0, r1)),
            LineSegment(Point2D(x0, r1), Point2D(x1, r1)),
            LineSegment(Point2D(x1, r1), Point2D(x1, r2)),
            LineSegment(Point2D(x1, r2), Point2D(x2, r2)),
            LineSegment(Point2D(x2, r2), Point2D(x2, 0.0)),
            LineSegment(Point2D(x2, 0.0), Point2D(x0, 0.0)),
            CenterLine(Point2D(x0, 0.0), Point2D(x2, 0.0)),
        )
        definition = SketchDefinition(
            name=f"{name}_Profile",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=entities,
        )
        sketch = self._sketches.create(target, definition)
        feature = self._parts.revolve(
            target,
            RevolveSpec(name, ProfileRef(sketch.sketch_id), "profile_centerline", 360.0),
        )
        return FixtureResult(sketch.sketch_id, (feature.feature.feature_id,))

    def hole_pattern_family(
        self,
        target: DocumentTarget,
        *,
        name: str = "HolePattern",
        count: int = 4,
        spacing_mm: float = 20.0,
    ) -> FixtureResult:
        if isinstance(count, bool) or not isinstance(count, int) or count < 2:
            raise ValueError("hole pattern count must be an integer >= 2")
        self._positive(spacing_mm)
        definition = self._rectangle_definition(f"{name}_Profile", 100.0, 50.0)
        sketch = self._sketches.create(target, definition)
        base = self._parts.extrude(target, ExtrudeSpec(f"{name}_Base", ProfileRef(sketch.sketch_id), 8.0))
        seed = self._parts.hole(
            target,
            HoleSpec(
                name=f"{name}_Seed",
                diameter_mm=6.0,
                face_ref="bbox:+z",
                centers_mm=((10.0, 25.0),),
                through_all=True,
            ),
        )
        pattern = self._parts.linear_pattern(
            target,
            LinearPatternSpec(
                name=f"{name}_Linear",
                seed_feature_ids=(seed.feature.feature_id,),
                direction_ref="bbox:edge:+y:+z",
                count=count,
                spacing_mm=spacing_mm,
            ),
        )
        return FixtureResult(
            sketch.sketch_id,
            (base.feature.feature_id, seed.feature.feature_id, pattern.feature.feature_id),
        )

    @staticmethod
    def _rectangle_definition(name: str, width: float, height: float) -> SketchDefinition:
        entities = (
            LineSegment(Point2D(0.0, 0.0), Point2D(width, 0.0)),
            LineSegment(Point2D(width, 0.0), Point2D(width, height)),
            LineSegment(Point2D(width, height), Point2D(0.0, height)),
            LineSegment(Point2D(0.0, height), Point2D(0.0, 0.0)),
        )
        return SketchDefinition(
            name=name,
            plane=SketchPlane(PlaneKind.FRONT),
            entities=entities,
            constraints=(
                HorizontalConstraint(0),
                VerticalConstraint(1),
                HorizontalConstraint(2),
                VerticalConstraint(3),
            ),
            dimensions=(
                DistanceDimension("width", 0, width),
                DistanceDimension("height", 1, height),
            ),
        )

    @staticmethod
    def _positive(*values: float) -> None:
        import math

        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0.0
            for value in values
        ):
            raise ValueError("fixture dimensions must be positive and finite")
