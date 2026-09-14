"""Typed sheet-metal models for Mechanical 90 lane B."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BaseFlangeSpec:
    name: str
    profile_id: str
    thickness_mm: float
    bend_radius_mm: float
    k_factor: float = 0.5


@dataclass(frozen=True, slots=True)
class EdgeFlangeSpec:
    name: str
    edge_id: str
    length_mm: float
    angle_deg: float = 90.0
    bend_radius_mm: float | None = None


@dataclass(frozen=True, slots=True)
class HemSpec:
    name: str
    edge_id: str
    length_mm: float
    gap_mm: float
    position: str = "outside"
    reverse: bool = False


@dataclass(frozen=True, slots=True)
class SketchedBendSpec:
    name: str
    line_x_mm: float
    angle_deg: float
    bend_radius_mm: float
    reverse: bool = False


@dataclass(frozen=True, slots=True)
class UnfoldSpec:
    bend_feature_id: str
    fixed_x_mm: float


@dataclass(frozen=True, slots=True)
class FoldSpec:
    unfold_feature_id: str
    bend_feature_id: str
    fixed_x_mm: float


@dataclass(frozen=True, slots=True)
class SheetMetalState:
    is_sheet_metal: bool
    thickness_mm: float | None
    bend_radius_mm: float | None
    k_factor: float | None
    flattened: bool
    flat_pattern_id: str | None = None


@dataclass(frozen=True, slots=True)
class SheetMetalMutationResult:
    feature_id: str
    state: SheetMetalState
