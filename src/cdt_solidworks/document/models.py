"""Typed SolidWorks document identity and query models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path


class DocumentType(IntEnum):
    PART = 1
    ASSEMBLY = 2
    DRAWING = 3

    @classmethod
    def from_path(cls, path: str | Path) -> "DocumentType":
        extension = Path(path).suffix.lower()
        mapping = {
            ".sldprt": cls.PART,
            ".sldasm": cls.ASSEMBLY,
            ".slddrw": cls.DRAWING,
        }
        try:
            return mapping[extension]
        except KeyError as exc:
            raise ValueError(f"Unsupported SolidWorks native document extension: {extension or '<none>'}") from exc

    @property
    def native_extension(self) -> str:
        return {
            DocumentType.PART: ".sldprt",
            DocumentType.ASSEMBLY: ".sldasm",
            DocumentType.DRAWING: ".slddrw",
        }[self]


@dataclass(frozen=True)
class DocumentContext:
    session_id: str
    path: str
    title: str
    document_type: DocumentType
    configuration: str | None
    update_stamp: int | None


@dataclass(frozen=True)
class DocumentInfo:
    context: DocumentContext
    dirty: bool


@dataclass(frozen=True)
class FeatureInfo:
    name: str
    type_name: str
    error_code: int
    is_warning: bool


@dataclass(frozen=True)
class BodyInfo:
    name: str
    body_type: str


@dataclass(frozen=True)
class ComponentInfo:
    name: str
    path: str
    suppressed: bool
