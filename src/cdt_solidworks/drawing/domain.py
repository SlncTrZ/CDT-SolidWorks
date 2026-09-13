"""Drawing domain contract for lane D."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class DrawingRefusal(RuntimeError):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else f"{reason}: {detail}")


class DrawingPostconditionError(RuntimeError):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else f"{reason}: {detail}")


@dataclass(frozen=True)
class RebuildReport:
    ok: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class SheetSnapshot:
    name: str


@dataclass(frozen=True)
class ViewSnapshot:
    identity: str
    sheet_name: str
    view_kind: str
    source_model_path: str
    source_configuration: str | None
    dangling: bool


@dataclass(frozen=True)
class DimensionSnapshot:
    identity: str
    view_id: str
    source_ref: str
    display_text: str
    dangling: bool


@dataclass(frozen=True)
class BomSnapshot:
    identity: str
    view_id: str
    source_configuration: str | None
    row_count: int


class DrawingAdapter(Protocol):
    def create_sheet(self, drawing_id: str, sheet_name: str) -> None: ...

    def read_sheet(self, drawing_id: str, sheet_name: str) -> SheetSnapshot | None: ...

    def create_view(
        self,
        drawing_id: str,
        sheet_name: str,
        view_kind: str,
        source_model_path: str,
        source_configuration: str | None,
    ) -> str: ...

    def read_view(self, drawing_id: str, view_id: str) -> ViewSnapshot | None: ...

    def add_dimension(self, drawing_id: str, view_id: str, source_ref: str) -> str: ...

    def read_dimension(
        self, drawing_id: str, dimension_id: str
    ) -> DimensionSnapshot | None: ...

    def supports_bom(self, drawing_id: str) -> bool: ...

    def create_bom(
        self, drawing_id: str, view_id: str, source_configuration: str | None
    ) -> str: ...

    def read_bom(self, drawing_id: str, bom_id: str) -> BomSnapshot | None: ...

    def rebuild_drawing(self, drawing_id: str) -> RebuildReport: ...


class DrawingService:
    """Validates drawing source association and refuses optimistic success."""

    def __init__(self, adapter: DrawingAdapter) -> None:
        self._adapter = adapter

    def create_sheet(self, drawing_id: str, sheet_name: str) -> SheetSnapshot:
        self._require_identity("drawing_id", drawing_id)
        self._require_identity("sheet_name", sheet_name)
        self._adapter.create_sheet(drawing_id, sheet_name)
        self._require_rebuild(drawing_id)
        sheet = self._adapter.read_sheet(drawing_id, sheet_name)
        if sheet is None or sheet.name != sheet_name:
            raise DrawingPostconditionError("sheet_readback_mismatch", sheet_name)
        return sheet

    def create_view(
        self,
        drawing_id: str,
        sheet_name: str,
        view_kind: str,
        source_model_path: str,
        source_configuration: str | None = None,
    ) -> ViewSnapshot:
        self._require_identity("drawing_id", drawing_id)
        self._require_identity("sheet_name", sheet_name)
        self._require_identity("view_kind", view_kind)
        self._require_identity("source_model_path", source_model_path)
        if source_configuration is not None:
            self._require_identity("source_configuration", source_configuration)
        sheet = self._adapter.read_sheet(drawing_id, sheet_name)
        if sheet is None:
            raise DrawingRefusal("missing_sheet", sheet_name)
        view_id = self._adapter.create_view(
            drawing_id,
            sheet_name,
            view_kind,
            source_model_path,
            source_configuration,
        )
        self._require_identity("view_id", view_id)
        self._require_rebuild(drawing_id)
        view = self._adapter.read_view(drawing_id, view_id)
        if view is None:
            raise DrawingPostconditionError("view_readback_missing", view_id)
        if view.dangling:
            raise DrawingPostconditionError("dangling_view", view_id)
        if view.sheet_name != sheet_name or view.view_kind != view_kind:
            raise DrawingPostconditionError("view_identity_readback_mismatch", view_id)
        if view.source_model_path != source_model_path:
            raise DrawingPostconditionError(
                "view_source_readback_mismatch", view.source_model_path
            )
        if view.source_configuration != source_configuration:
            raise DrawingPostconditionError(
                "view_configuration_readback_mismatch",
                str(view.source_configuration),
            )
        return view

    def add_dimension(
        self, drawing_id: str, view_id: str, source_ref: str
    ) -> DimensionSnapshot:
        self._require_identity("drawing_id", drawing_id)
        self._require_identity("view_id", view_id)
        self._require_identity("source_ref", source_ref)
        view = self._adapter.read_view(drawing_id, view_id)
        if view is None or view.dangling:
            raise DrawingRefusal("invalid_dimension_view", view_id)
        dimension_id = self._adapter.add_dimension(drawing_id, view_id, source_ref)
        self._require_identity("dimension_id", dimension_id)
        self._require_rebuild(drawing_id)
        dimension = self._adapter.read_dimension(drawing_id, dimension_id)
        if dimension is None:
            raise DrawingPostconditionError(
                "dimension_readback_missing", dimension_id
            )
        if dimension.dangling:
            raise DrawingPostconditionError("dangling_dimension", dimension_id)
        if dimension.view_id != view_id or dimension.source_ref != source_ref:
            raise DrawingPostconditionError(
                "dimension_source_readback_mismatch", dimension_id
            )
        if not dimension.display_text.strip():
            raise DrawingPostconditionError(
                "dimension_display_readback_missing", dimension_id
            )
        return dimension

    def create_bom(
        self,
        drawing_id: str,
        view_id: str,
        source_configuration: str | None = None,
    ) -> BomSnapshot:
        self._require_identity("drawing_id", drawing_id)
        self._require_identity("view_id", view_id)
        if source_configuration is not None:
            self._require_identity("source_configuration", source_configuration)
        if not self._adapter.supports_bom(drawing_id):
            raise DrawingRefusal("unsupported_capability", "solidworks.drawing.bom")
        view = self._adapter.read_view(drawing_id, view_id)
        if view is None or view.dangling:
            raise DrawingRefusal("invalid_bom_view", view_id)
        bom_id = self._adapter.create_bom(
            drawing_id, view_id, source_configuration
        )
        self._require_identity("bom_id", bom_id)
        self._require_rebuild(drawing_id)
        bom = self._adapter.read_bom(drawing_id, bom_id)
        if bom is None:
            raise DrawingPostconditionError("bom_readback_missing", bom_id)
        if bom.view_id != view_id:
            raise DrawingPostconditionError("bom_view_readback_mismatch", bom_id)
        if bom.source_configuration != source_configuration:
            raise DrawingPostconditionError(
                "bom_configuration_readback_mismatch", bom_id
            )
        if bom.row_count < 1:
            raise DrawingPostconditionError("bom_empty_readback", bom_id)
        return bom

    def _require_rebuild(self, drawing_id: str) -> None:
        report = self._adapter.rebuild_drawing(drawing_id)
        if not report.ok or report.errors:
            raise DrawingPostconditionError(
                "rebuild_failed", "; ".join(report.errors)
            )

    @staticmethod
    def _require_identity(label: str, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise DrawingRefusal(f"invalid_{label}")
