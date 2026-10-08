"""Typed bounded topology query, inspection, and persistent-reference models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True, slots=True)
class Point3D:
    """Model-space point in explicit millimeters."""

    x_mm: float
    y_mm: float
    z_mm: float


@dataclass(frozen=True, slots=True)
class Vector3D:
    """Dimensionless model-space direction vector."""

    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True)
class BodyGeometry:
    body_kind: str
    name: str | None = None
    bbox_mm: tuple[float, float, float, float, float, float] | None = None
    volume_mm3: float | None = None
    area_mm2: float | None = None


@dataclass(frozen=True, slots=True)
class FaceGeometry:
    surface_type: str
    origin_mm: Point3D | None = None
    normal: Vector3D | None = None
    axis: Vector3D | None = None
    radius_mm: float | None = None
    half_angle_deg: float | None = None
    area_mm2: float | None = None
    uv_bounds: tuple[float, float, float, float] | None = None


@dataclass(frozen=True, slots=True)
class EdgeGeometry:
    curve_type: str
    start_mm: Point3D | None = None
    end_mm: Point3D | None = None
    length_mm: float | None = None
    radius_mm: float | None = None
    closed: bool | None = None


@dataclass(frozen=True, slots=True)
class VertexGeometry:
    point_mm: Point3D


TopologyGeometry: TypeAlias = BodyGeometry | FaceGeometry | EdgeGeometry | VertexGeometry


@dataclass(frozen=True, slots=True)
class TopologyItem:
    kind: str
    reference: str
    body_name: str | None
    ordinal: int
    component_id: str | None = None


@dataclass(frozen=True, slots=True)
class TopologyQueryResult:
    items: tuple[TopologyItem, ...]
    counts: dict[str, int]
    component_id: str | None = None


@dataclass(frozen=True, slots=True)
class TopologyResolution:
    kind: str
    reference: str
    body_name: str | None
    component_id: str | None = None


@dataclass(frozen=True, slots=True)
class TopologyInspection:
    kind: str
    reference: str
    body_name: str | None
    component_id: str | None
    geometry: TopologyGeometry
