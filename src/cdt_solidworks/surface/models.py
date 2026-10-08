"""Typed surface-domain models for Mechanical 90 lane B."""

from __future__ import annotations

from dataclasses import dataclass

from cdt_solidworks.body.models import BodySnapshot


@dataclass(frozen=True, slots=True)
class SurfaceKnitSpec:
    name: str
    surface_body_ids: tuple[str, ...]
    tolerance_mm: float = 0.01
    try_form_solid: bool = True


@dataclass(frozen=True, slots=True)
class ThickenSpec:
    name: str
    surface_body_id: str
    thickness_mm: float
    side: int = 0
    merge: bool = False


@dataclass(frozen=True, slots=True)
class OffsetSurfaceSpec:
    name: str
    surface_body_id: str
    distance_mm: float
    reverse: bool = False


@dataclass(frozen=True, slots=True)
class SurfaceMutationResult:
    feature_id: str
    surface_bodies: tuple[BodySnapshot, ...]
    solid_bodies: tuple[BodySnapshot, ...]
