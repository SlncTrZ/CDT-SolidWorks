"""Drawing domain contract for lane D."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
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
class DrawingSnapshot:
    identity: str
    path: str
    sheet_names: tuple[str, ...]
    template_path: str | None


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
    position: tuple[float, float] | None = None
    scale_decimal: float | None = None
    display_style: int | None = None
    parent_view_id: str | None = None


@dataclass(frozen=True)
class AnnotationSnapshot:
    identity: str
    view_id: str
    annotation_kind: str
    text: str
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
    column_count: int = 0
    rows: tuple[tuple[str, ...], ...] = ()
    component_ids: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class DrawingUpdateSnapshot:
    view_count: int
    dimension_count: int
    bom_count: int
    current: bool


class DrawingAdapter(Protocol):
    def create_drawing(self, drawing_id: str, template_path: str | None) -> None: ...

    def read_drawing(self, drawing_id: str) -> DrawingSnapshot | None: ...

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

    def create_projected_view(
        self, drawing_id: str, parent_view_id: str, x: float, y: float
    ) -> str: ...

    def create_section_view(
        self,
        drawing_id: str,
        parent_view_id: str,
        line_start: tuple[float, float],
        line_end: tuple[float, float],
        x: float,
        y: float,
        label: str,
    ) -> str: ...

    def read_view(self, drawing_id: str, view_id: str) -> ViewSnapshot | None: ...

    def add_note(self, drawing_id: str, view_id: str, text: str) -> str: ...

    def import_model_annotations(self, drawing_id: str, view_id: str) -> tuple[str, ...]: ...

    def auto_insert_center_marks(
        self, drawing_id: str, view_id: str
    ) -> tuple[str, ...]: ...

    def read_annotation(
        self, drawing_id: str, annotation_id: str
    ) -> AnnotationSnapshot | None: ...

    def add_dimension(
        self,
        drawing_id: str,
        view_id: str,
        source_ref: str,
        x_mm: float = 0.0,
        y_mm: float = 0.0,
    ) -> str: ...

    def read_dimension(
        self, drawing_id: str, dimension_id: str
    ) -> DimensionSnapshot | None: ...

    def list_dimensions(self, drawing_id: str) -> tuple[DimensionSnapshot, ...]: ...

    def supports_bom(self, drawing_id: str) -> bool: ...

    def create_bom(
        self, drawing_id: str, view_id: str, source_configuration: str | None
    ) -> str: ...

    def read_bom(self, drawing_id: str, bom_id: str) -> BomSnapshot | None: ...

    def list_boms(self, drawing_id: str) -> tuple[BomSnapshot, ...]: ...

    def list_views(self, drawing_id: str) -> tuple[ViewSnapshot, ...]: ...

    def rebuild_drawing(self, drawing_id: str) -> RebuildReport: ...


class DrawingService:
    """Validates drawing source association and refuses optimistic success."""

    def __init__(self, adapter: DrawingAdapter) -> None:
        self._adapter = adapter

    def create_drawing(
        self, drawing_id: str, template_path: str | None = None
    ) -> DrawingSnapshot:
        self._require_identity("drawing_id", drawing_id)
        if template_path is not None:
            self._require_identity("template_path", template_path)
        self._adapter.create_drawing(drawing_id, template_path)
        drawing = self._adapter.read_drawing(drawing_id)
        if drawing is None:
            raise DrawingPostconditionError("drawing_readback_missing", drawing_id)
        if drawing.identity != drawing_id or drawing.path != drawing_id:
            raise DrawingPostconditionError("drawing_identity_readback_mismatch", drawing_id)
        if not drawing.sheet_names or any(not name.strip() for name in drawing.sheet_names):
            raise DrawingPostconditionError("drawing_sheet_readback_missing", drawing_id)
        if template_path is not None and drawing.template_path != template_path:
            raise DrawingPostconditionError("drawing_template_readback_mismatch", drawing_id)
        return drawing

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

    def create_projected_view(
        self, drawing_id: str, parent_view_id: str, x: float, y: float
    ) -> ViewSnapshot:
        self._require_identity("drawing_id", drawing_id)
        self._require_identity("parent_view_id", parent_view_id)
        self._require_position(x, y)
        parent = self._adapter.read_view(drawing_id, parent_view_id)
        if parent is None or parent.dangling:
            raise DrawingRefusal("invalid_parent_view", parent_view_id)
        view_id = self._adapter.create_projected_view(
            drawing_id, parent_view_id, float(x), float(y)
        )
        self._require_identity("view_id", view_id)
        self._require_rebuild(drawing_id)
        view = self._adapter.read_view(drawing_id, view_id)
        if view is None:
            raise DrawingPostconditionError("view_readback_missing", view_id)
        if view.dangling:
            raise DrawingPostconditionError("dangling_view", view_id)
        if view.view_kind != "projected" or view.parent_view_id != parent_view_id:
            raise DrawingPostconditionError("projected_view_identity_mismatch", view_id)
        if (
            view.sheet_name != parent.sheet_name
            or view.source_model_path != parent.source_model_path
            or view.source_configuration != parent.source_configuration
        ):
            raise DrawingPostconditionError("projected_view_source_mismatch", view_id)
        if view.position is None or any(
            abs(actual - expected) > 1e-9
            for actual, expected in zip(view.position, (float(x), float(y)))
        ):
            raise DrawingPostconditionError("view_position_readback_mismatch", view_id)
        return view

    def create_section_view(
        self,
        drawing_id: str,
        parent_view_id: str,
        line_start: tuple[float, float],
        line_end: tuple[float, float],
        x: float,
        y: float,
        label: str,
    ) -> ViewSnapshot:
        self._require_identity("drawing_id", drawing_id)
        self._require_identity("parent_view_id", parent_view_id)
        self._require_point("section_line_start", line_start)
        self._require_point("section_line_end", line_end)
        self._require_position(x, y)
        self._require_view_label(label)
        if all(abs(float(a) - float(b)) <= 1e-12 for a, b in zip(line_start, line_end)):
            raise DrawingRefusal("invalid_section_line")
        parent = self._adapter.read_view(drawing_id, parent_view_id)
        if parent is None or parent.dangling:
            raise DrawingRefusal("invalid_parent_view", parent_view_id)
        view_id = self._adapter.create_section_view(
            drawing_id,
            parent_view_id,
            (float(line_start[0]), float(line_start[1])),
            (float(line_end[0]), float(line_end[1])),
            float(x),
            float(y),
            label.strip().upper(),
        )
        return self._require_derived_view(
            drawing_id,
            view_id,
            parent,
            parent_view_id,
            "section",
            (float(x), float(y)),
        )

    def add_note(
        self, drawing_id: str, view_id: str, text: str
    ) -> AnnotationSnapshot:
        self._require_identity("drawing_id", drawing_id)
        self._require_identity("view_id", view_id)
        self._require_identity("annotation_text", text)
        self._require_valid_view(drawing_id, view_id, "invalid_annotation_view")
        annotation_id = self._adapter.add_note(drawing_id, view_id, text)
        self._require_identity("annotation_id", annotation_id)
        self._require_rebuild(drawing_id)
        annotation = self._adapter.read_annotation(drawing_id, annotation_id)
        if annotation is None:
            raise DrawingPostconditionError("annotation_readback_missing", annotation_id)
        self._validate_annotation(annotation, view_id)
        if annotation.annotation_kind != "note" or annotation.text != text:
            raise DrawingPostconditionError("annotation_text_readback_mismatch", annotation_id)
        return annotation

    def import_model_annotations(
        self, drawing_id: str, view_id: str
    ) -> tuple[AnnotationSnapshot, ...]:
        self._require_identity("drawing_id", drawing_id)
        self._require_identity("view_id", view_id)
        self._require_valid_view(drawing_id, view_id, "invalid_annotation_view")
        annotation_ids = self._adapter.import_model_annotations(drawing_id, view_id)
        if not annotation_ids:
            raise DrawingPostconditionError("model_annotations_readback_empty", view_id)
        self._require_rebuild(drawing_id)
        seen: set[str] = set()
        snapshots: list[AnnotationSnapshot] = []
        for annotation_id in annotation_ids:
            self._require_identity("annotation_id", annotation_id)
            if annotation_id in seen:
                raise DrawingPostconditionError("duplicate_annotation_identity", annotation_id)
            seen.add(annotation_id)
            annotation = self._adapter.read_annotation(drawing_id, annotation_id)
            if annotation is None:
                raise DrawingPostconditionError("annotation_readback_missing", annotation_id)
            self._validate_annotation(annotation, view_id)
            snapshots.append(annotation)
        return tuple(snapshots)

    def auto_insert_center_marks(
        self, drawing_id: str, view_id: str
    ) -> tuple[AnnotationSnapshot, ...]:
        self._require_identity("drawing_id", drawing_id)
        self._require_identity("view_id", view_id)
        self._require_valid_view(drawing_id, view_id, "invalid_annotation_view")
        annotation_ids = self._adapter.auto_insert_center_marks(drawing_id, view_id)
        if not annotation_ids:
            raise DrawingPostconditionError("center_marks_readback_empty", view_id)
        self._require_rebuild(drawing_id)
        seen: set[str] = set()
        snapshots: list[AnnotationSnapshot] = []
        for annotation_id in annotation_ids:
            self._require_identity("annotation_id", annotation_id)
            if annotation_id in seen:
                raise DrawingPostconditionError("duplicate_annotation_identity", annotation_id)
            seen.add(annotation_id)
            annotation = self._adapter.read_annotation(drawing_id, annotation_id)
            if annotation is None:
                raise DrawingPostconditionError("annotation_readback_missing", annotation_id)
            self._validate_annotation(annotation, view_id)
            if annotation.annotation_kind != "center_mark":
                raise DrawingPostconditionError(
                    "center_mark_kind_readback_mismatch", annotation_id
                )
            snapshots.append(annotation)
        return tuple(snapshots)

    def add_dimension(
        self,
        drawing_id: str,
        view_id: str,
        source_ref: str,
        *,
        source_model_path: str | None = None,
        source_configuration: str | None = None,
        x_mm: float = 0.0,
        y_mm: float = 0.0,
    ) -> DimensionSnapshot:
        self._require_identity("drawing_id", drawing_id)
        self._require_identity("view_id", view_id)
        self._require_identity("source_ref", source_ref)
        view = self._adapter.read_view(drawing_id, view_id)
        if view is None or view.dangling:
            raise DrawingRefusal("invalid_dimension_view", view_id)
        explicit_binding = source_model_path is not None or source_configuration is not None
        if source_model_path is not None:
            self._require_identity("source_model_path", source_model_path)
            if view.source_model_path != source_model_path:
                raise DrawingRefusal("dimension_source_identity_mismatch", source_model_path)
        if source_configuration is not None:
            self._require_identity("source_configuration", source_configuration)
            if view.source_configuration != source_configuration:
                raise DrawingRefusal(
                    "dimension_source_configuration_mismatch", source_configuration
                )
        if explicit_binding and not source_ref.startswith("swref1."):
            raise DrawingRefusal("invalid_topology_reference", source_ref)
        self._require_position(x_mm, y_mm)
        if explicit_binding:
            dimension_id = self._adapter.add_dimension(
                drawing_id, view_id, source_ref, float(x_mm), float(y_mm)
            )
        else:
            dimension_id = self._adapter.add_dimension(drawing_id, view_id, source_ref)
        self._require_identity("dimension_id", dimension_id)
        self._require_rebuild(drawing_id)
        dimension = self._adapter.read_dimension(drawing_id, dimension_id)
        if dimension is None:
            raise DrawingPostconditionError(
                "dimension_readback_missing", dimension_id
            )
        self._validate_dimension(dimension, view_id, source_ref)
        return dimension

    def list_dimensions(
        self, drawing_id: str, view_id: str | None = None
    ) -> tuple[DimensionSnapshot, ...]:
        self._require_identity("drawing_id", drawing_id)
        if view_id is not None:
            self._require_identity("view_id", view_id)
        reader = getattr(self._adapter, "list_dimensions", None)
        if reader is None:
            raise DrawingRefusal("unsupported_capability", "solidworks.drawing.dimensions_list")
        dimensions = tuple(reader(drawing_id))
        result: list[DimensionSnapshot] = []
        seen: set[str] = set()
        for dimension in dimensions:
            if view_id is not None and dimension.view_id != view_id:
                continue
            if dimension.identity in seen:
                raise DrawingPostconditionError("duplicate_dimension_identity", dimension.identity)
            seen.add(dimension.identity)
            self._validate_dimension(dimension, dimension.view_id, dimension.source_ref)
            result.append(dimension)
        return tuple(result)

    def create_bom(
        self,
        drawing_id: str,
        view_id: str,
        source_configuration: str | None = None,
        *,
        source_model_path: str | None = None,
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
        if source_model_path is not None:
            self._require_identity("source_model_path", source_model_path)
            if view.source_model_path != source_model_path:
                raise DrawingRefusal("bom_source_identity_mismatch", source_model_path)
        if (
            source_configuration is not None
            and view.source_configuration != source_configuration
        ):
            raise DrawingRefusal("bom_configuration_mismatch", source_configuration)
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
        if bom.row_count < 1 or bom.column_count < 1:
            raise DrawingPostconditionError("bom_empty_readback", bom_id)
        self._validate_bom(bom)
        return bom

    def read_bom(self, drawing_id: str, bom_id: str) -> BomSnapshot:
        self._require_identity("drawing_id", drawing_id)
        self._require_identity("bom_id", bom_id)
        bom = self._adapter.read_bom(drawing_id, bom_id)
        if bom is None:
            raise DrawingRefusal("bom_not_found", bom_id)
        self._validate_bom(bom)
        return bom

    def update_drawing(
        self,
        drawing_id: str,
        *,
        source_model_path: str,
        source_configuration: str | None = None,
    ) -> DrawingUpdateSnapshot:
        self._require_identity("drawing_id", drawing_id)
        self._require_identity("source_model_path", source_model_path)
        if source_configuration is not None:
            self._require_identity("source_configuration", source_configuration)
        list_views = getattr(self._adapter, "list_views", None)
        list_dimensions = getattr(self._adapter, "list_dimensions", None)
        list_boms = getattr(self._adapter, "list_boms", None)
        if list_views is None or list_dimensions is None or list_boms is None:
            raise DrawingRefusal("unsupported_capability", "solidworks.drawing.update")
        before = tuple(list_views(drawing_id))
        if not before:
            raise DrawingRefusal("drawing_views_missing", drawing_id)
        self._validate_update_source(before, source_model_path, source_configuration)
        self._require_rebuild(drawing_id)
        views = tuple(list_views(drawing_id))
        self._validate_update_source(views, source_model_path, source_configuration)
        for view in views:
            if view.dangling:
                raise DrawingPostconditionError("dangling_view", view.identity)
        dimensions = tuple(list_dimensions(drawing_id))
        for dimension in dimensions:
            self._validate_dimension(dimension, dimension.view_id, dimension.source_ref)
        boms = tuple(list_boms(drawing_id))
        for bom in boms:
            self._validate_bom(bom)
        return DrawingUpdateSnapshot(
            view_count=len(views),
            dimension_count=len(dimensions),
            bom_count=len(boms),
            current=True,
        )

    @staticmethod
    def _validate_dimension(
        dimension: DimensionSnapshot, view_id: str, source_ref: str
    ) -> None:
        if dimension.dangling:
            raise DrawingPostconditionError("dangling_dimension", dimension.identity)
        if dimension.view_id != view_id or dimension.source_ref != source_ref:
            raise DrawingPostconditionError(
                "dimension_source_readback_mismatch", dimension.identity
            )
        if not dimension.display_text.strip():
            raise DrawingPostconditionError(
                "dimension_display_readback_missing", dimension.identity
            )

    @staticmethod
    def _validate_bom(bom: BomSnapshot) -> None:
        if bom.row_count < 1 or bom.column_count < 1:
            raise DrawingPostconditionError("bom_empty_readback", bom.identity)
        if len(bom.rows) != bom.row_count or any(
            len(row) != bom.column_count for row in bom.rows
        ):
            raise DrawingPostconditionError("bom_table_shape_mismatch", bom.identity)
        if bom.component_ids and len(bom.component_ids) != bom.row_count:
            raise DrawingPostconditionError("bom_component_shape_mismatch", bom.identity)

    @staticmethod
    def _validate_update_source(
        views: tuple[ViewSnapshot, ...],
        source_model_path: str,
        source_configuration: str | None,
    ) -> None:
        for view in views:
            if view.dangling:
                raise DrawingPostconditionError("dangling_view", view.identity)
            if view.source_model_path != source_model_path:
                raise DrawingRefusal(
                    "drawing_source_identity_mismatch", view.source_model_path
                )
            if (
                source_configuration is not None
                and view.source_configuration != source_configuration
            ):
                raise DrawingRefusal(
                    "drawing_source_configuration_mismatch",
                    str(view.source_configuration),
                )

    def _require_derived_view(
        self,
        drawing_id: str,
        view_id: str,
        parent: ViewSnapshot,
        parent_view_id: str,
        kind: str,
        position: tuple[float, float],
    ) -> ViewSnapshot:
        self._require_identity("view_id", view_id)
        self._require_rebuild(drawing_id)
        view = self._adapter.read_view(drawing_id, view_id)
        if view is None:
            raise DrawingPostconditionError("view_readback_missing", view_id)
        if view.dangling:
            raise DrawingPostconditionError("dangling_view", view_id)
        if view.view_kind != kind or view.parent_view_id != parent_view_id:
            raise DrawingPostconditionError(f"{kind}_view_identity_mismatch", view_id)
        if (
            view.sheet_name != parent.sheet_name
            or view.source_model_path != parent.source_model_path
            or view.source_configuration != parent.source_configuration
        ):
            raise DrawingPostconditionError(f"{kind}_view_source_mismatch", view_id)
        if view.position is None or any(
            abs(actual - expected) > 1e-9
            for actual, expected in zip(view.position, position)
        ):
            raise DrawingPostconditionError("view_position_readback_mismatch", view_id)
        return view

    def _require_valid_view(self, drawing_id: str, view_id: str, reason: str) -> ViewSnapshot:
        view = self._adapter.read_view(drawing_id, view_id)
        if view is None or view.dangling:
            raise DrawingRefusal(reason, view_id)
        return view

    @staticmethod
    def _validate_annotation(annotation: AnnotationSnapshot, view_id: str) -> None:
        if annotation.dangling:
            raise DrawingPostconditionError("dangling_annotation", annotation.identity)
        if annotation.view_id != view_id:
            raise DrawingPostconditionError("annotation_view_readback_mismatch", annotation.identity)
        if not annotation.annotation_kind.strip() or not annotation.text.strip():
            raise DrawingPostconditionError("annotation_readback_incomplete", annotation.identity)

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

    @staticmethod
    def _require_position(x: float, y: float) -> None:
        values = (x, y)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
            for value in values
        ):
            raise DrawingRefusal("invalid_view_position")

    @staticmethod
    def _require_point(label: str, value: tuple[float, float]) -> None:
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            raise DrawingRefusal(f"invalid_{label}")
        if any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not isfinite(float(item))
            for item in value
        ):
            raise DrawingRefusal(f"invalid_{label}")

    @staticmethod
    def _require_view_label(value: str) -> None:
        if not isinstance(value, str) or len(value.strip()) != 1 or not value.strip().isalpha():
            raise DrawingRefusal("invalid_view_label")
