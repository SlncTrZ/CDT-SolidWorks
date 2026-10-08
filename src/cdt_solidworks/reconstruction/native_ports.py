"""Native STEP reconstruction ports composed from already accepted provider services.

The ports deliberately support only two controlled, axis-aligned classes:
- a rectangular prismatic bracket with exactly one cylindrical through-hole along +Z;
- a turned/shaft-like body with cylindrical faces sharing the global X axis.

Anything outside those measured contracts remains unclassified and is refused by
the reconstruction service rather than guessed.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import math
from pathlib import Path
import struct
from typing import Any, Mapping, Sequence
import uuid

from cdt_solidworks.evaluation.domain import BoundingBox, GeometrySanity
from cdt_solidworks.native.models import NativeCallResult, NativeCallState
from cdt_solidworks.topology.models import (
    FaceGeometry,
    TopologyInspection,
    TopologyQueryResult,
)

from .service import ReconstructionPostconditionError, ReconstructionValidationError


_NEUTRAL_FORMATS = {
    ".step": "step",
    ".stp": "step",
    ".iges": "iges",
    ".igs": "iges",
    ".x_t": "parasolid",
    ".x_b": "parasolid",
}
_AXIS_NAMES = ("x", "y", "z")
_AXIS_ALIGNMENT = 0.999
_SPAN_ABS_TOL_MM = 0.05
_SPAN_REL_TOL = 0.005
_MAX_STL_BYTES = 64 * 1024 * 1024
_MAX_STL_TRIANGLES = 1_000_000
_MIN_HOLE_RING_POINTS = 8


def _native_value(result: object, label: str) -> Any:
    if not isinstance(result, NativeCallResult):
        raise ReconstructionPostconditionError(
            f"{label} violated the NativeCallResult contract"
        )
    if result.state is not NativeCallState.SUCCESS or result.value is None:
        failure = result.failure
        detail = (
            f"{failure.code}: {failure.message}"
            if failure is not None
            else result.state.value
        )
        raise ReconstructionPostconditionError(f"{label} failed: {detail}")
    return result.value


def _context_revision(context: object) -> int:
    revision = getattr(context, "update_stamp", None)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ReconstructionPostconditionError(
            "native document did not expose a usable revision stamp"
        )
    return revision


def _axis_index(vector: object | None) -> int | None:
    if vector is None:
        return None
    values = (
        float(getattr(vector, "x", math.nan)),
        float(getattr(vector, "y", math.nan)),
        float(getattr(vector, "z", math.nan)),
    )
    if not all(math.isfinite(value) for value in values):
        return None
    absolute = tuple(abs(value) for value in values)
    index = max(range(3), key=absolute.__getitem__)
    if absolute[index] < _AXIS_ALIGNMENT:
        return None
    if any(absolute[i] > 1.0 - _AXIS_ALIGNMENT for i in range(3) if i != index):
        return None
    return index


def _span_mm(bounds: BoundingBox) -> tuple[float, float, float]:
    spans = tuple(
        (float(high) - float(low)) * 1000.0
        for low, high in zip(bounds.min_m, bounds.max_m, strict=True)
    )
    if any(not math.isfinite(value) or value <= 0.0 for value in spans):
        raise ReconstructionValidationError(
            "native bounding box does not contain three positive finite spans"
        )
    return spans


def _near(first: float, second: float) -> bool:
    return math.isclose(
        first,
        second,
        rel_tol=_SPAN_REL_TOL,
        abs_tol=_SPAN_ABS_TOL_MM,
    )


def _coincident_cylinders(first: FaceGeometry, second: FaceGeometry) -> bool:
    first_axis = _axis_index(first.axis)
    second_axis = _axis_index(second.axis)
    if first_axis is None or first_axis != second_axis:
        return False
    if first.radius_mm is None or second.radius_mm is None:
        return False
    if not _near(float(first.radius_mm), float(second.radius_mm)):
        return False
    if first.origin_mm is None or second.origin_mm is None:
        return False
    first_origin = (
        float(first.origin_mm.x_mm),
        float(first.origin_mm.y_mm),
        float(first.origin_mm.z_mm),
    )
    second_origin = (
        float(second.origin_mm.x_mm),
        float(second.origin_mm.y_mm),
        float(second.origin_mm.z_mm),
    )
    return all(
        _near(first_origin[index], second_origin[index])
        for index in range(3)
        if index != first_axis
    )


def _unique_cylinders(faces: Sequence[FaceGeometry]) -> list[FaceGeometry]:
    unique: list[FaceGeometry] = []
    for face in faces:
        if any(_coincident_cylinders(face, existing) for existing in unique):
            continue
        unique.append(face)
    return unique


class NativeStlMeshPort:
    """Inspect bounded STL meshes without inventing unit metadata or design intent."""

    def inspect_mesh(
        self,
        source_path: str,
        *,
        scale_to_mm: float | None = None,
    ) -> dict[str, object]:
        source = Path(source_path).expanduser().resolve(strict=False)
        if source.suffix.lower() != ".stl":
            raise ReconstructionValidationError("native mesh port supports STL only")
        if not source.is_file():
            raise ReconstructionValidationError("STL mesh source does not exist")
        size = source.stat().st_size
        if size <= 0 or size > _MAX_STL_BYTES:
            raise ReconstructionValidationError("STL mesh size is outside the bounded parser limit")
        triangles = self._parse_stl(source.read_bytes())
        if scale_to_mm is None:
            metrics = self._mesh_metrics(triangles)
            return {
                "valid": True,
                "mixed_representation": False,
                "body_count": metrics["component_count"],
                "triangle_count": len(triangles),
                "watertight": metrics["watertight"],
                "manifold": metrics["manifold"],
                "component_count": metrics["component_count"],
                "degenerate_triangle_count": metrics["degenerate_triangle_count"],
                "boundary_edge_count": metrics["boundary_edge_count"],
                "nonmanifold_edge_count": metrics["nonmanifold_edge_count"],
                "source_scale_to_mm": None,
                "unit": None,
                "unit_confidence": 0.0,
                "frame_confidence": 0.0,
                "recognized_class": None,
                "dimensions_mm": {},
                "primitives": [],
            }
        scale = self._positive_scale(scale_to_mm)
        scaled = [
            tuple(tuple(float(value) * scale for value in vertex) for vertex in triangle)
            for triangle in triangles
        ]
        return self._evidence_from_triangles(scaled, source_scale_to_mm=scale)

    @classmethod
    def _evidence_from_triangles(
        cls,
        triangles: Sequence[Sequence[Sequence[float]]],
        *,
        source_scale_to_mm: float,
    ) -> dict[str, object]:
        metrics = cls._mesh_metrics(triangles)
        points = [vertex for triangle in triangles for vertex in triangle]
        mins = tuple(min(float(vertex[index]) for vertex in points) for index in range(3))
        maxs = tuple(max(float(vertex[index]) for vertex in points) for index in range(3))
        spans = tuple(maxs[index] - mins[index] for index in range(3))
        tolerance = max(1e-6, max(spans) * 1e-8)
        recognized: str | None = None
        dimensions: dict[str, float] = {}
        primitives: list[dict[str, object]] = []
        frame_confidence = 0.0

        bracket = cls._bracket_fit(
            triangles,
            mins=mins,
            maxs=maxs,
            spans=spans,
            tolerance=tolerance,
            metrics=metrics,
        )
        if bracket is not None:
            recognized = "prismatic_bracket"
            frame_confidence = 1.0
            dimensions = {
                "width": spans[0],
                "height": spans[1],
                "depth": spans[2],
                "hole_diameter": 2.0 * bracket["radius_mm"],
            }
            primitives = [
                {
                    "kind": "plane",
                    "confidence": 1.0,
                    "fit_residual_mm": 0.0,
                    "axis": "z",
                },
                {
                    "kind": "cylinder",
                    "confidence": bracket["confidence"],
                    "fit_residual_mm": bracket["fit_residual_mm"],
                    "radius_mm": bracket["radius_mm"],
                    "axis": "z",
                    "center_x_mm": bracket["center_x_mm"],
                    "center_y_mm": bracket["center_y_mm"],
                },
            ]

        return {
            "valid": True,
            "mixed_representation": False,
            "body_count": metrics["component_count"],
            "triangle_count": len(triangles),
            "watertight": metrics["watertight"],
            "manifold": metrics["manifold"],
            "component_count": metrics["component_count"],
            "degenerate_triangle_count": metrics["degenerate_triangle_count"],
            "boundary_edge_count": metrics["boundary_edge_count"],
            "nonmanifold_edge_count": metrics["nonmanifold_edge_count"],
            "source_scale_to_mm": source_scale_to_mm,
            "unit": "mm",
            "unit_confidence": 1.0,
            "frame_confidence": frame_confidence,
            "recognized_class": recognized,
            "dimensions_mm": dimensions,
            "primitives": primitives,
        }

    @classmethod
    def _mesh_metrics(
        cls,
        triangles: Sequence[Sequence[Sequence[float]]],
    ) -> dict[str, object]:
        if not triangles:
            raise ReconstructionValidationError("STL mesh does not contain triangles")
        points = [vertex for triangle in triangles for vertex in triangle]
        mins = tuple(min(float(vertex[index]) for vertex in points) for index in range(3))
        maxs = tuple(max(float(vertex[index]) for vertex in points) for index in range(3))
        spans = tuple(maxs[index] - mins[index] for index in range(3))
        if any(not math.isfinite(value) for vertex in points for value in vertex):
            raise ReconstructionValidationError("STL mesh contains non-finite coordinates")
        if any(value <= 0.0 for value in spans):
            raise ReconstructionValidationError("STL mesh bounding box is degenerate")
        tolerance = max(1e-9, max(spans) * 1e-9)

        def key(vertex: Sequence[float]) -> tuple[int, int, int]:
            return tuple(int(round(float(value) / tolerance)) for value in vertex)  # type: ignore[return-value]

        parent = list(range(len(triangles)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(first: int, second: int) -> None:
            left, right = find(first), find(second)
            if left != right:
                parent[right] = left

        edge_owners: dict[tuple[tuple[int, int, int], tuple[int, int, int]], list[int]] = defaultdict(list)
        degenerate = 0
        for index, triangle in enumerate(triangles):
            if len(triangle) != 3:
                raise ReconstructionValidationError("STL facet does not contain three vertices")
            vertex_keys = [key(vertex) for vertex in triangle]
            if len(set(vertex_keys)) < 3 or cls._double_area(triangle) <= tolerance * tolerance:
                degenerate += 1
            for first, second in ((0, 1), (1, 2), (2, 0)):
                edge = tuple(sorted((vertex_keys[first], vertex_keys[second])))
                edge_owners[edge].append(index)
        for owners in edge_owners.values():
            for other in owners[1:]:
                union(owners[0], other)
        edge_counts = Counter(len(owners) for owners in edge_owners.values())
        boundary = edge_counts.get(1, 0)
        nonmanifold = sum(count for multiplicity, count in edge_counts.items() if multiplicity > 2)
        components = len({find(index) for index in range(len(triangles))})
        manifold = degenerate == 0 and nonmanifold == 0
        return {
            "watertight": manifold and boundary == 0,
            "manifold": manifold,
            "component_count": components,
            "degenerate_triangle_count": degenerate,
            "boundary_edge_count": boundary,
            "nonmanifold_edge_count": nonmanifold,
        }

    @classmethod
    def _bracket_fit(
        cls,
        triangles: Sequence[Sequence[Sequence[float]]],
        *,
        mins: tuple[float, float, float],
        maxs: tuple[float, float, float],
        spans: tuple[float, float, float],
        tolerance: float,
        metrics: Mapping[str, object],
    ) -> dict[str, float] | None:
        if (
            metrics.get("watertight") is not True
            or metrics.get("manifold") is not True
            or metrics.get("component_count") != 1
            or spans[2] >= min(spans[0], spans[1])
            or min(spans[0], spans[1]) < 1.5 * spans[2]
        ):
            return None

        xy_levels: dict[tuple[int, int], set[int]] = defaultdict(set)
        xy_values: dict[tuple[int, int], tuple[float, float]] = {}
        xy_tolerance = max(tolerance, 1e-6)
        unique_vertices = {
            tuple(float(value) for value in vertex)
            for triangle in triangles
            for vertex in triangle
        }
        for x, y, z in unique_vertices:
            level = 0 if abs(z - mins[2]) <= tolerance else 1 if abs(z - maxs[2]) <= tolerance else None
            if level is None:
                continue
            if (
                abs(x - mins[0]) <= tolerance
                or abs(x - maxs[0]) <= tolerance
                or abs(y - mins[1]) <= tolerance
                or abs(y - maxs[1]) <= tolerance
            ):
                continue
            key = (int(round(x / xy_tolerance)), int(round(y / xy_tolerance)))
            xy_levels[key].add(level)
            xy_values[key] = (x, y)
        ring = [xy_values[key] for key, levels in xy_levels.items() if levels == {0, 1}]
        if len(ring) < _MIN_HOLE_RING_POINTS:
            return None
        center_x = sum(point[0] for point in ring) / len(ring)
        center_y = sum(point[1] for point in ring) / len(ring)
        radii = [math.hypot(x - center_x, y - center_y) for x, y in ring]
        radius = sum(radii) / len(radii)
        if not math.isfinite(radius) or radius <= tolerance:
            return None
        radial_residual = max(abs(value - radius) for value in radii)
        if radial_residual > max(_SPAN_ABS_TOL_MM, radius * 0.01):
            return None
        angles = sorted(math.atan2(y - center_y, x - center_x) for x, y in ring)
        gaps = [angles[index + 1] - angles[index] for index in range(len(angles) - 1)]
        gaps.append((angles[0] + 2.0 * math.pi) - angles[-1])
        maximum_gap = max(gaps)
        if maximum_gap > math.pi / 2.0:
            return None
        sagitta = radius * (1.0 - math.cos(maximum_gap / 2.0))
        fit_residual = max(radial_residual, sagitta)

        for triangle in triangles:
            normal = cls._unit_normal(triangle)
            if normal is None:
                return None
            absolute = tuple(abs(value) for value in normal)
            if max(absolute) >= _AXIS_ALIGNMENT:
                continue
            if absolute[2] > 0.05:
                return None
            centroid_x = sum(float(vertex[0]) for vertex in triangle) / 3.0
            centroid_y = sum(float(vertex[1]) for vertex in triangle) / 3.0
            centroid_radius = math.hypot(centroid_x - center_x, centroid_y - center_y)
            if abs(centroid_radius - radius) > max(0.15, radius * 0.05):
                return None
        confidence = max(0.8, min(1.0, 1.0 - fit_residual / radius))
        return {
            "center_x_mm": center_x,
            "center_y_mm": center_y,
            "radius_mm": radius,
            "fit_residual_mm": fit_residual,
            "confidence": confidence,
        }

    @staticmethod
    def _double_area(triangle: Sequence[Sequence[float]]) -> float:
        first, second, third = triangle
        ab = tuple(float(second[index]) - float(first[index]) for index in range(3))
        ac = tuple(float(third[index]) - float(first[index]) for index in range(3))
        cross = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        return math.sqrt(sum(value * value for value in cross))

    @classmethod
    def _unit_normal(
        cls, triangle: Sequence[Sequence[float]]
    ) -> tuple[float, float, float] | None:
        first, second, third = triangle
        ab = tuple(float(second[index]) - float(first[index]) for index in range(3))
        ac = tuple(float(third[index]) - float(first[index]) for index in range(3))
        cross = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        magnitude = math.sqrt(sum(value * value for value in cross))
        if magnitude <= 0.0:
            return None
        return tuple(value / magnitude for value in cross)

    @classmethod
    def _parse_stl(
        cls, payload: bytes
    ) -> list[tuple[tuple[float, float, float], ...]]:
        if len(payload) >= 84:
            triangle_count = struct.unpack_from("<I", payload, 80)[0]
            if triangle_count > _MAX_STL_TRIANGLES:
                raise ReconstructionValidationError("STL triangle count exceeds the bounded parser limit")
            expected = 84 + triangle_count * 50
            if triangle_count > 0 and expected == len(payload):
                triangles: list[tuple[tuple[float, float, float], ...]] = []
                offset = 84
                for _ in range(triangle_count):
                    values = struct.unpack_from("<12fH", payload, offset)
                    offset += 50
                    triangles.append(
                        (
                            tuple(float(value) for value in values[3:6]),
                            tuple(float(value) for value in values[6:9]),
                            tuple(float(value) for value in values[9:12]),
                        )
                    )
                return triangles
        try:
            text = payload.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ReconstructionValidationError("STL payload is neither valid binary nor ASCII") from exc
        vertices: list[tuple[float, float, float]] = []
        for line in text.splitlines():
            fields = line.strip().split()
            if len(fields) != 4 or fields[0].lower() != "vertex":
                continue
            try:
                vertex = tuple(float(value) for value in fields[1:])
            except ValueError as exc:
                raise ReconstructionValidationError("ASCII STL contains an invalid vertex") from exc
            vertices.append(vertex)  # type: ignore[arg-type]
            if len(vertices) // 3 > _MAX_STL_TRIANGLES:
                raise ReconstructionValidationError("STL triangle count exceeds the bounded parser limit")
        if not vertices or len(vertices) % 3 != 0:
            raise ReconstructionValidationError("ASCII STL does not contain complete triangle facets")
        return [tuple(vertices[index:index + 3]) for index in range(0, len(vertices), 3)]

    @staticmethod
    def _positive_scale(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ReconstructionValidationError("scale_to_mm must be a positive finite number")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0.0:
            raise ReconstructionValidationError("scale_to_mm must be a positive finite number")
        return numeric


class NativeStepTopologyPort:
    """Measure a neutral CAD source through transient native import and topology read-back."""

    def __init__(
        self,
        *,
        import_service: Any,
        evaluation_service: Any,
        document_service: Any,
        topology_service: Any,
    ) -> None:
        self.import_service = import_service
        self.evaluation_service = evaluation_service
        self.document_service = document_service
        self.topology_service = topology_service

    def inspect_source(self, source_path: str) -> dict[str, object]:
        source = Path(source_path).expanduser().resolve(strict=False)
        source_format = _NEUTRAL_FORMATS.get(source.suffix.lower())
        if source_format is None:
            raise ReconstructionValidationError(
                "native STEP topology port supports STEP/IGES/Parasolid only"
            )
        if not source.is_file():
            raise ReconstructionValidationError("neutral CAD source does not exist")

        target = source.with_name(
            f".{source.stem}.cdt-recon-assess-{uuid.uuid4().hex}.SLDPRT"
        )
        context = None
        cleanup_error: Exception | None = None
        try:
            _native_value(
                self.import_service.import_model(
                    str(source), str(target), source_format
                ),
                "neutral CAD import",
            )
            bounds = _native_value(
                self.evaluation_service.bounding_box(str(target)),
                "imported bounding-box evaluation",
            )
            sanity = _native_value(
                self.evaluation_service.geometry_sanity(str(target)),
                "imported geometry-sanity evaluation",
            )
            context = _native_value(
                self.document_service.open(str(target), read_only=True),
                "temporary imported document open",
            )
            query = _native_value(
                self.topology_service.query(
                    context,
                    kinds=("body", "face", "edge", "vertex"),
                ),
                "temporary imported topology query",
            )
            inspections: list[TopologyInspection] = []
            for item in getattr(query, "items", ()):
                if getattr(item, "kind", None) != "face":
                    continue
                inspection = _native_value(
                    self.topology_service.inspect(
                        context,
                        item.reference,
                        expected_kind="face",
                    ),
                    "temporary imported face inspection",
                )
                inspections.append(inspection)
            return self.evidence_from_native(
                bounds=bounds,
                sanity=sanity,
                query=query,
                inspections=tuple(inspections),
            )
        finally:
            if context is not None:
                try:
                    _native_value(
                        self.document_service.close(context),
                        "temporary imported document close",
                    )
                except Exception as exc:
                    cleanup_error = exc
            try:
                target.unlink(missing_ok=True)
            except Exception as exc:
                cleanup_error = cleanup_error or exc
            if cleanup_error is not None:
                raise ReconstructionPostconditionError(
                    f"temporary reconstruction assessment cleanup failed: {cleanup_error}"
                )

    @staticmethod
    def evidence_from_native(
        *,
        bounds: BoundingBox,
        sanity: GeometrySanity,
        query: TopologyQueryResult,
        inspections: Sequence[TopologyInspection],
    ) -> dict[str, object]:
        if not isinstance(bounds, BoundingBox):
            raise ReconstructionValidationError(
                "native reconstruction assessment requires BoundingBox evidence"
            )
        if not isinstance(sanity, GeometrySanity):
            raise ReconstructionValidationError(
                "native reconstruction assessment requires GeometrySanity evidence"
            )
        if not isinstance(query, TopologyQueryResult):
            raise ReconstructionValidationError(
                "native reconstruction assessment requires topology query evidence"
            )
        if (
            sanity.solid_body_count != 1
            or sanity.surface_body_count != 0
            or sanity.feature_error_count != 0
        ):
            return {
                "valid": False,
                "mixed_representation": sanity.surface_body_count > 0,
                "body_count": sanity.solid_body_count,
                "face_count": int(query.counts.get("face", 0)),
                "edge_count": int(query.counts.get("edge", 0)),
                "vertex_count": int(query.counts.get("vertex", 0)),
                "unit": "mm",
                "unit_confidence": 1.0,
                "frame_confidence": 0.0,
                "recognized_class": None,
                "dimensions_mm": {},
                "primitives": [],
            }

        spans = _span_mm(bounds)
        faces = [
            item.geometry
            for item in inspections
            if isinstance(item, TopologyInspection)
            and isinstance(item.geometry, FaceGeometry)
        ]
        planar = [face for face in faces if face.surface_type == "planar"]
        cylindrical_faces = [
            face
            for face in faces
            if face.surface_type == "cylindrical"
            and face.radius_mm is not None
            and face.radius_mm > 0.0
        ]
        cylindrical = _unique_cylinders(cylindrical_faces)
        unsupported_faces = [
            face
            for face in faces
            if face.surface_type not in {"planar", "cylindrical"}
        ]

        plane_axis_counts = [0, 0, 0]
        for face in planar:
            axis = _axis_index(face.normal)
            if axis is not None:
                plane_axis_counts[axis] += 1

        cylinder_axes = [_axis_index(face.axis) for face in cylindrical]
        recognized: str | None = None
        dimensions: dict[str, float] = {}
        frame_confidence = 0.0

        bracket_shape = (
            not unsupported_faces
            and len(planar) >= 6
            and len(cylindrical) == 1
            and all(count >= 2 for count in plane_axis_counts)
            and cylinder_axes == [2]
            and spans[2] < min(spans[0], spans[1])
            and min(spans[0], spans[1]) >= 1.5 * spans[2]
        )
        if bracket_shape:
            hole = cylindrical[0]
            recognized = "prismatic_bracket"
            frame_confidence = 1.0
            dimensions = {
                "width": spans[0],
                "height": spans[1],
                "depth": spans[2],
                "hole_diameter": 2.0 * float(hole.radius_mm),
            }

        shaft_shape = (
            recognized is None
            and not unsupported_faces
            and bool(cylindrical)
            and all(axis == 0 for axis in cylinder_axes)
            and plane_axis_counts[0] >= 2
            and _near(spans[1], spans[2])
            and spans[0] >= 1.2 * max(spans[1], spans[2])
        )
        if shaft_shape:
            outer_diameter = 2.0 * max(float(face.radius_mm) for face in cylindrical)
            if _near(outer_diameter, max(spans[1], spans[2])):
                recognized = "turned_shaft"
                frame_confidence = 1.0
                dimensions = {
                    "length": spans[0],
                    "outer_diameter": outer_diameter,
                }

        primitives: list[dict[str, object]] = []
        for face in planar:
            axis = _axis_index(face.normal)
            primitives.append(
                {
                    "kind": "plane",
                    "confidence": 1.0 if axis is not None else 0.8,
                    "fit_residual_mm": 0.0,
                    "axis": _AXIS_NAMES[axis] if axis is not None else "other",
                }
            )
        for face in cylindrical:
            axis = _axis_index(face.axis)
            row: dict[str, object] = {
                "kind": "cylinder",
                "confidence": 1.0 if axis is not None else 0.8,
                "fit_residual_mm": 0.0,
                "radius_mm": float(face.radius_mm),
                "axis": _AXIS_NAMES[axis] if axis is not None else "other",
            }
            if face.origin_mm is not None:
                row.update(
                    {
                        "center_x_mm": float(face.origin_mm.x_mm),
                        "center_y_mm": float(face.origin_mm.y_mm),
                        "center_z_mm": float(face.origin_mm.z_mm),
                    }
                )
            primitives.append(row)

        return {
            "valid": True,
            "mixed_representation": False,
            "body_count": sanity.solid_body_count,
            "face_count": int(query.counts.get("face", 0)),
            "edge_count": int(query.counts.get("edge", 0)),
            "vertex_count": int(query.counts.get("vertex", 0)),
            "unit": "mm",
            "unit_confidence": 1.0,
            "frame_confidence": frame_confidence,
            "recognized_class": recognized,
            "dimensions_mm": dimensions,
            "primitives": primitives,
        }


class NativeStepPartPort:
    """Build controlled editable parts from measured reconstruction plans."""

    def __init__(
        self,
        *,
        cad_service: Any,
        document_service: Any,
        sketch_service: Any,
        part_service: Any,
        evaluation_service: Any,
    ) -> None:
        self.cad_service = cad_service
        self.document_service = document_service
        self.sketch_service = sketch_service
        self.part_service = part_service
        self.evaluation_service = evaluation_service

    def rebuild_editable(
        self, output_path: str, plan: Mapping[str, object]
    ) -> dict[str, object]:
        benchmark = str(plan.get("benchmark_class") or "").strip()
        dimensions = self._dimensions(plan.get("critical_dimensions_mm"))
        if benchmark == "prismatic_bracket":
            return self._rebuild_bracket(output_path, dimensions, plan)
        if benchmark == "turned_shaft":
            return self._rebuild_shaft(output_path, dimensions)
        raise ReconstructionValidationError(
            "native STEP rebuild supports prismatic_bracket or turned_shaft only"
        )

    def reopen_and_edit(
        self, output_path: str, edit: Mapping[str, object]
    ) -> dict[str, object]:
        feature_id = str(edit.get("feature_id") or "").strip()
        parameter = str(edit.get("parameter") or "").strip()
        value_raw = edit.get("value_mm")
        if (
            not feature_id
            or parameter not in {"depth_mm", "radius_mm"}
            or isinstance(value_raw, bool)
            or not isinstance(value_raw, (int, float))
            or not math.isfinite(float(value_raw))
            or float(value_raw) <= 0.0
        ):
            raise ReconstructionValidationError(
                "native intended edit requires feature_id, depth_mm/radius_mm, and a positive value"
            )
        value = float(value_raw)

        context = _native_value(
            self.document_service.open(output_path),
            "editable result open",
        )
        context = _native_value(
            self.document_service.refresh(context),
            "editable result refresh before reopen",
        )
        context = _native_value(
            self.document_service.reopen(context),
            "editable result reopen before intended edit",
        )
        _native_value(
            self.part_service.feature_parameter_set(
                path=output_path,
                expected_revision=_context_revision(context),
                feature_id=feature_id,
                parameter=parameter,
                value=value,
            ),
            "intended feature parameter edit",
        )
        context = _native_value(
            self.document_service.refresh(context),
            "editable result refresh after intended edit",
        )
        context = _native_value(
            self.document_service.save(context),
            "editable result save after intended edit",
        )
        context = _native_value(
            self.document_service.reopen(context),
            "editable result reopen after intended edit",
        )
        parameters = _native_value(
            self.part_service.feature_parameters_get(
                path=output_path,
                expected_revision=_context_revision(context),
                feature_id=feature_id,
            ),
            "intended feature parameter read-back",
        )
        readback = (
            parameters.get(parameter)
            if isinstance(parameters, Mapping)
            else None
        )
        context = _native_value(
            self.document_service.refresh(context),
            "editable result refresh before close after intended edit",
        )
        _native_value(
            self.document_service.close(context),
            "editable result close after intended edit",
        )
        return {
            "path": output_path,
            "edited": True,
            "reopen_verified": True,
            "readback_mm": readback,
        }

    def _rebuild_bracket(
        self,
        output_path: str,
        dimensions: Mapping[str, float],
        plan: Mapping[str, object],
    ) -> dict[str, object]:
        required = self._required(dimensions, ("width", "height", "depth"))
        base = _native_value(
            self.cad_service.create_rect_extrude(
                output_path,
                width_mm=required["width"],
                height_mm=required["height"],
                depth_mm=required["depth"],
                plane="front",
                center_x_mm=0.0,
                center_y_mm=0.0,
            ),
            "prismatic bracket base rebuild",
        )
        if not isinstance(base, Mapping):
            raise ReconstructionPostconditionError(
                "prismatic bracket base result must be a mapping"
            )
        sketch_id = str(base.get("sketch_name") or "").strip()
        base_id = str(base.get("feature_name") or "").strip()
        if not sketch_id or not base_id:
            raise ReconstructionPostconditionError(
                "prismatic bracket base did not expose stable feature identities"
            )

        context = _native_value(
            self.document_service.open(output_path),
            "prismatic bracket open",
        )
        renamed = _native_value(
            self.part_service.rename_feature(
                path=output_path,
                expected_revision=_context_revision(context),
                feature_id=base_id,
                new_name="ReconstructionBase",
            ),
            "prismatic bracket base rename",
        )
        base_id = str(getattr(renamed, "feature_id", "") or "").strip()
        if base_id != "ReconstructionBase":
            raise ReconstructionPostconditionError(
                "prismatic bracket base rename did not persist the requested identity"
            )
        feature_ids = [sketch_id, base_id]
        context = _native_value(
            self.document_service.refresh(context),
            "prismatic bracket refresh after base rename",
        )
        hole_id: str | None = None
        if "hole_diameter" in dimensions:
            cylinder = self._z_cylinder(plan.get("primitive_fits"))
            if cylinder is None:
                raise ReconstructionValidationError(
                    "bracket hole dimension lacks one measured Z-axis cylinder primitive"
                )
            parameters = cylinder.get("parameters")
            if not isinstance(parameters, Mapping):
                raise ReconstructionValidationError(
                    "bracket cylinder primitive parameters are missing"
                )
            center = (
                self._finite(parameters.get("center_x_mm", 0.0), "hole center_x_mm"),
                self._finite(parameters.get("center_y_mm", 0.0), "hole center_y_mm"),
            )
            hole_result = _native_value(
                self.part_service.simple_hole(
                    path=output_path,
                    expected_revision=_context_revision(context),
                    name="ReconstructionHole",
                    diameter_mm=float(dimensions["hole_diameter"]),
                    face_ref="bbox:+z",
                    center_mm=center,
                    through_all=True,
                    depth_mm=None,
                ),
                "prismatic bracket hole rebuild",
            )
            feature = getattr(hole_result, "feature", None)
            hole_id = str(getattr(feature, "feature_id", "") or "").strip()
            if not hole_id:
                raise ReconstructionPostconditionError(
                    "prismatic bracket hole did not expose a stable feature identity"
                )
            feature_ids.append(hole_id)
            context = _native_value(
                self.document_service.refresh(context),
                "prismatic bracket refresh after hole",
            )

        context = _native_value(
            self.document_service.save(context),
            "prismatic bracket save",
        )
        context = _native_value(
            self.document_service.reopen(context),
            "prismatic bracket reopen",
        )
        actual = self._read_dimensions(
            output_path,
            "prismatic_bracket",
            context,
            hole_id=hole_id,
        )
        context = _native_value(
            self.document_service.refresh(context),
            "prismatic bracket refresh before close",
        )
        _native_value(
            self.document_service.close(context),
            "prismatic bracket close",
        )
        return {
            "path": output_path,
            "feature_ids": feature_ids,
            "dimensions_mm": actual,
            "reopen_verified": True,
        }

    def _rebuild_shaft(
        self,
        output_path: str,
        dimensions: Mapping[str, float],
    ) -> dict[str, object]:
        required = self._required(dimensions, ("length", "outer_diameter"))
        _native_value(
            self.cad_service.create_empty_part(output_path),
            "turned shaft empty-part creation",
        )
        context = _native_value(
            self.document_service.open(output_path),
            "turned shaft open",
        )
        length = required["length"]
        radius = required["outer_diameter"] / 2.0
        entities = [
            {"type": "line", "start_mm": [0.0, 0.0], "end_mm": [0.0, radius]},
            {"type": "line", "start_mm": [0.0, radius], "end_mm": [length, radius]},
            {"type": "line", "start_mm": [length, radius], "end_mm": [length, 0.0]},
            {"type": "line", "start_mm": [length, 0.0], "end_mm": [0.0, 0.0]},
            {"type": "centerline", "start_mm": [0.0, 0.0], "end_mm": [length, 0.0]},
        ]
        sketch = _native_value(
            self.sketch_service.create_geometry(
                path=output_path,
                expected_revision=_context_revision(context),
                name="ReconstructionShaftProfile",
                plane="front",
                entities=entities,
                constraints=None,
                dimensions=None,
            ),
            "turned shaft profile creation",
        )
        sketch_id = str(getattr(sketch, "sketch_id", "") or "").strip()
        if not sketch_id:
            raise ReconstructionPostconditionError(
                "turned shaft sketch did not expose a stable identity"
            )
        context = _native_value(
            self.document_service.refresh(context),
            "turned shaft refresh after sketch",
        )
        revolve = _native_value(
            self.part_service.revolve(
                path=output_path,
                expected_revision=_context_revision(context),
                sketch_id=sketch_id,
                name="ReconstructionShaft",
                axis_ref="profile_centerline",
                angle_deg=360.0,
            ),
            "turned shaft revolve",
        )
        feature = getattr(revolve, "feature", None)
        feature_id = str(getattr(feature, "feature_id", "") or "").strip()
        if not feature_id:
            raise ReconstructionPostconditionError(
                "turned shaft revolve did not expose a stable feature identity"
            )
        context = _native_value(
            self.document_service.refresh(context),
            "turned shaft refresh after revolve",
        )
        context = _native_value(
            self.document_service.save(context),
            "turned shaft save",
        )
        context = _native_value(
            self.document_service.reopen(context),
            "turned shaft reopen",
        )
        actual = self._read_dimensions(
            output_path,
            "turned_shaft",
            context,
        )
        context = _native_value(
            self.document_service.refresh(context),
            "turned shaft refresh before close",
        )
        _native_value(
            self.document_service.close(context),
            "turned shaft close",
        )
        return {
            "path": output_path,
            "feature_ids": [sketch_id, feature_id],
            "dimensions_mm": actual,
            "reopen_verified": True,
        }

    def _read_dimensions(
        self,
        path: str,
        benchmark: str,
        context: object,
        *,
        hole_id: str | None = None,
    ) -> dict[str, float]:
        sanity = _native_value(
            self.evaluation_service.geometry_sanity(path),
            "reconstructed geometry sanity",
        )
        if (
            not isinstance(sanity, GeometrySanity)
            or sanity.solid_body_count != 1
            or sanity.surface_body_count != 0
            or sanity.feature_error_count != 0
        ):
            raise ReconstructionPostconditionError(
                "reconstructed native part failed geometry sanity"
            )
        bounds = _native_value(
            self.evaluation_service.bounding_box(path),
            "reconstructed bounding-box read-back",
        )
        if not isinstance(bounds, BoundingBox):
            raise ReconstructionPostconditionError(
                "reconstructed bounding-box result has the wrong type"
            )
        spans = _span_mm(bounds)
        if benchmark == "prismatic_bracket":
            result = {
                "width": spans[0],
                "height": spans[1],
                "depth": spans[2],
            }
            if hole_id is not None:
                parameters = _native_value(
                    self.part_service.feature_parameters_get(
                        path=path,
                        expected_revision=_context_revision(context),
                        feature_id=hole_id,
                    ),
                    "reconstructed hole parameter read-back",
                )
                diameter = (
                    parameters.get("diameter_mm")
                    if isinstance(parameters, Mapping)
                    else None
                )
                result["hole_diameter"] = self._positive(
                    diameter, "hole_diameter"
                )
            return result
        return {
            "length": spans[0],
            "outer_diameter": max(spans[1], spans[2]),
        }

    @staticmethod
    def _dimensions(value: object) -> dict[str, float]:
        if not isinstance(value, Mapping):
            raise ReconstructionValidationError(
                "critical_dimensions_mm must be a mapping"
            )
        result: dict[str, float] = {}
        for key, raw in value.items():
            result[str(key)] = NativeStepPartPort._positive(
                raw, f"critical dimension {key}"
            )
        return result

    @staticmethod
    def _required(
        dimensions: Mapping[str, float],
        names: Sequence[str],
    ) -> dict[str, float]:
        missing = [name for name in names if name not in dimensions]
        if missing:
            raise ReconstructionValidationError(
                f"native reconstruction critical dimensions are missing: {missing}"
            )
        return {name: float(dimensions[name]) for name in names}

    @staticmethod
    def _positive(value: object, label: str) -> float:
        numeric = NativeStepPartPort._finite(value, label)
        if numeric <= 0.0:
            raise ReconstructionValidationError(f"{label} must be positive")
        return numeric

    @staticmethod
    def _finite(value: object, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ReconstructionValidationError(f"{label} must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ReconstructionValidationError(f"{label} must be finite")
        return numeric

    @staticmethod
    def _z_cylinder(value: object) -> Mapping[str, object] | None:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return None
        matches: list[Mapping[str, object]] = []
        for item in value:
            if not isinstance(item, Mapping) or item.get("kind") != "cylinder":
                continue
            parameters = item.get("parameters")
            if isinstance(parameters, Mapping) and parameters.get("axis") == "z":
                matches.append(item)
        return matches[0] if len(matches) == 1 else None


def bind_native_mesh_port(runtime: Any) -> None:
    """Attach the bounded STL parser only to a path-contained integrated runtime."""

    if (
        getattr(runtime, "reconstruction_mesh_port", None) is None
        and getattr(runtime, "path_policy", None) is not None
    ):
        runtime.reconstruction_mesh_port = NativeStlMeshPort()


def bind_native_step_ports(runtime: Any) -> None:
    """Attach native STEP ports when the integrated runtime has all required services."""

    if getattr(runtime, "reconstruction_topology_port", None) is None:
        dependencies = {
            "import_service": getattr(runtime, "import_service", None),
            "evaluation_service": getattr(runtime, "evaluation_service", None),
            "document_service": getattr(runtime, "document_service", None),
            "topology_service": getattr(runtime, "topology_service", None),
        }
        if all(value is not None for value in dependencies.values()):
            runtime.reconstruction_topology_port = NativeStepTopologyPort(
                **dependencies
            )

    if getattr(runtime, "reconstruction_part_port", None) is None:
        dependencies = {
            "cad_service": getattr(runtime, "cad_service", None),
            "document_service": getattr(runtime, "document_service", None),
            "sketch_service": getattr(runtime, "sketch_service", None),
            "part_service": getattr(runtime, "part_feature_service", None),
            "evaluation_service": getattr(runtime, "evaluation_service", None),
        }
        if all(value is not None for value in dependencies.values()):
            runtime.reconstruction_part_port = NativeStepPartPort(**dependencies)
