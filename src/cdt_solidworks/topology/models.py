"""Typed bounded topology query and persistent-reference models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TopologyItem:
    kind: str
    reference: str
    body_name: str | None
    ordinal: int


@dataclass(frozen=True)
class TopologyQueryResult:
    items: tuple[TopologyItem, ...]
    counts: dict[str, int]


@dataclass(frozen=True)
class TopologyResolution:
    kind: str
    reference: str
    body_name: str | None
