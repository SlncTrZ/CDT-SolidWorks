"""Typed weldment-domain models for Mechanical 90 lane B."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True, slots=True)
class StructuralMemberSpec:
    name: str
    path_ids: tuple[str, ...]
    profile_path: str
    group_name: str = "Group1"
    corner_treatment: int = 0
    profile_configuration: str = ""


@dataclass(frozen=True, slots=True)
class CutListItem:
    item_id: str
    name: str
    quantity: int
    properties: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CutListPropertySpec:
    cut_list_id: str
    property_name: str
    value: str


@dataclass(frozen=True, slots=True)
class WeldmentState:
    has_weldment: bool
    structural_member_count: int
    cut_items: tuple[CutListItem, ...] = ()


@dataclass(frozen=True, slots=True)
class WeldmentMutationResult:
    feature_id: str
    state: WeldmentState
