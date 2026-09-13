"""Export domain contract for lane D."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PureWindowsPath
from typing import Protocol


class ExportRefusal(RuntimeError):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else f"{reason}: {detail}")


class ExportPostconditionError(RuntimeError):
    def __init__(
        self,
        reason: str,
        detail: str | None = None,
        *,
        call_id: str | None = None,
    ) -> None:
        self.reason = reason
        self.detail = detail
        self.call_id = call_id
        super().__init__(reason if detail is None else f"{reason}: {detail}")


class ExportFormat(str, Enum):
    STEP = "step"
    STEP_242 = "step_242"
    IGES = "iges"
    PARASOLID = "parasolid"
    STL = "stl"
    THREE_MF = "3mf"
    PDF = "pdf"
    DXF = "dxf"
    DWG = "dwg"
    NATIVE = "native"


@dataclass(frozen=True)
class ExportRequest:
    source_document_id: str
    target_path: str
    format: ExportFormat
    source_configuration: str | None = None
    drawing_sheet: str | None = None


@dataclass(frozen=True)
class NativeExportResult:
    completed: bool
    partial: bool
    errors: tuple[str, ...] = ()
    call_id: str | None = None


@dataclass(frozen=True)
class ArtifactInspection:
    detected_format: ExportFormat
    readable: bool
    geometry_verified: bool
    source_document_id: str | None
    source_configuration: str | None
    drawing_sheet: str | None
    detected_extension: str | None = None
    byte_size: int | None = None


class Exporter(Protocol):
    def export(self, request: ExportRequest) -> NativeExportResult: ...


class ArtifactInspector(Protocol):
    def inspect(self, request: ExportRequest) -> ArtifactInspection: ...


class PathPolicy(Protocol):
    def allows(self, target_path: str) -> bool: ...


class ExportService:
    """Separates native export execution from independent artifact verification."""

    _EXTENSIONS: dict[ExportFormat, frozenset[str]] = {
        ExportFormat.STEP: frozenset({".step", ".stp"}),
        ExportFormat.STEP_242: frozenset({".step", ".stp"}),
        ExportFormat.IGES: frozenset({".iges", ".igs"}),
        ExportFormat.PARASOLID: frozenset({".x_t", ".x_b"}),
        ExportFormat.STL: frozenset({".stl"}),
        ExportFormat.THREE_MF: frozenset({".3mf"}),
        ExportFormat.PDF: frozenset({".pdf"}),
        ExportFormat.DXF: frozenset({".dxf"}),
        ExportFormat.DWG: frozenset({".dwg"}),
        ExportFormat.NATIVE: frozenset({".sldprt", ".sldasm", ".slddrw"}),
    }
    _GEOMETRY_FORMATS = frozenset(
        {
            ExportFormat.STEP,
            ExportFormat.STEP_242,
            ExportFormat.IGES,
            ExportFormat.PARASOLID,
            ExportFormat.STL,
            ExportFormat.THREE_MF,
        }
    )

    def __init__(
        self,
        exporter: Exporter,
        inspector: ArtifactInspector,
        path_policy: PathPolicy,
    ) -> None:
        self._exporter = exporter
        self._inspector = inspector
        self._path_policy = path_policy

    def export(self, request: ExportRequest) -> ArtifactInspection:
        self._validate_request(request)
        if not self._path_policy.allows(request.target_path):
            raise ExportRefusal("path_not_allowed", request.target_path)

        native = self._exporter.export(request)
        if native.partial:
            raise ExportPostconditionError(
                "native_state_uncertain",
                "; ".join(native.errors),
                call_id=native.call_id,
            )
        if not native.completed or native.errors:
            raise ExportPostconditionError(
                "native_export_incomplete", "; ".join(native.errors)
            )

        inspection = self._inspector.inspect(request)
        if inspection.detected_format is not request.format:
            raise ExportPostconditionError(
                "artifact_format_mismatch", inspection.detected_format.value
            )
        expected_extension = PureWindowsPath(request.target_path).suffix.casefold()
        if inspection.detected_extension is not None:
            if inspection.detected_extension.casefold() != expected_extension:
                raise ExportPostconditionError(
                    "artifact_extension_mismatch", inspection.detected_extension
                )
        elif request.format is ExportFormat.NATIVE:
            raise ExportPostconditionError("native_artifact_type_unverified")
        if inspection.byte_size is not None and inspection.byte_size <= 0:
            raise ExportPostconditionError("artifact_empty")
        if not inspection.readable:
            raise ExportPostconditionError("artifact_unreadable")
        if request.format in self._GEOMETRY_FORMATS and not inspection.geometry_verified:
            raise ExportPostconditionError("geometry_not_verified")

        if (
            inspection.source_document_id is not None
            and inspection.source_document_id != request.source_document_id
        ):
            raise ExportPostconditionError(
                "source_document_readback_mismatch", inspection.source_document_id
            )
        if request.source_configuration is not None:
            if inspection.source_configuration != request.source_configuration:
                raise ExportPostconditionError(
                    "source_configuration_readback_mismatch",
                    str(inspection.source_configuration),
                )
        if request.drawing_sheet is not None:
            if inspection.drawing_sheet != request.drawing_sheet:
                raise ExportPostconditionError(
                    "drawing_sheet_readback_mismatch", str(inspection.drawing_sheet)
                )
        return inspection

    def _validate_request(self, request: ExportRequest) -> None:
        self._require_identity("source_document_id", request.source_document_id)
        self._require_identity("target_path", request.target_path)
        if not isinstance(request.format, ExportFormat):
            raise ExportRefusal("invalid_format")
        if request.source_configuration is not None:
            self._require_identity(
                "source_configuration", request.source_configuration
            )
        if request.drawing_sheet is not None:
            self._require_identity("drawing_sheet", request.drawing_sheet)
            if request.format is not ExportFormat.PDF:
                raise ExportRefusal("drawing_sheet_requires_pdf")
        suffix = PureWindowsPath(request.target_path).suffix.casefold()
        if suffix not in self._EXTENSIONS[request.format]:
            raise ExportRefusal(
                "format_extension_mismatch",
                f"format={request.format.value}, extension={suffix or '<none>'}",
            )

    @staticmethod
    def _require_identity(label: str, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ExportRefusal(f"invalid_{label}")
