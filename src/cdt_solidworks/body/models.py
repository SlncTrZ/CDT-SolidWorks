"""Typed body-domain models for Mechanical 90 lane B."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class BodyKind(str, Enum):
    SOLID = "solid"
    SURFACE = "surface"


class CombineOperation(str, Enum):
    ADD = "add"
    SUBTRACT = "subtract"
    COMMON = "common"


@dataclass(frozen=True, slots=True)
class BodySnapshot:
    body_id: str
    name: str
    kind: BodyKind
    visible: bool = True


@dataclass(frozen=True, slots=True)
class CombineSpec:
    name: str
    operation: CombineOperation
    body_ids: tuple[str, ...]
    main_body_id: str | None = None


@dataclass(frozen=True, slots=True)
class MutationReceipt:
    feature_id: str
    parameters: Mapping[str, float | str | bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BodyMutationResult:
    feature_id: str
    bodies: tuple[BodySnapshot, ...]
