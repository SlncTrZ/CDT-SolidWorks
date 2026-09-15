"""Lane-local SOLIDWORKS COM adapter for drawing lifecycle and standard views."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Protocol, TypeVar

from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.rebuild import rebuild_document

from .domain import (
    AnnotationSnapshot,
    BomSnapshot,
    DimensionSnapshot,
    DrawingPostconditionError,
    DrawingRefusal,
    DrawingSnapshot,
    RebuildReport,
    SheetSnapshot,
    ViewSnapshot,
)


T = TypeVar("T")


class DrawingPathPolicy(Protocol):
    def validate_open(self, path: str) -> str: ...

    def validate_save(self, path: str) -> str: ...


class _TopologyDrawingReferenceSelector:
    """Select a topology-resolved source entity inside one drawing view."""

    def __init__(self, topology_service: Any, api: Any) -> None:
        self._topology_service = topology_service
        self._api = api

    def select_for_drawing(
        self,
        *,
        model: Any,
        view: Any,
        source_model_path: str,
        source_configuration: str | None,
        source_ref: str,
    ) -> bool:
        resolver = getattr(
            self._topology_service, "resolve_native_for_open_document", None
        )
        if not callable(resolver):
            raise DrawingRefusal("topology_reference_unavailable", source_ref)
        source_document = self._api._member(view, "ReferencedDocument")
        if source_document is None:
            raise DrawingRefusal("topology_reference_unavailable", source_ref)
        result = resolver(source_document, source_ref)
        if getattr(result, "state", None) is not NativeCallState.SUCCESS:
            failure = getattr(result, "failure", None)
            reason = getattr(failure, "code", None) or "topology_reference_unavailable"
            raise DrawingRefusal(str(reason), source_ref)
        payload = getattr(result, "value", None)
        entity = payload.get("native_entity") if isinstance(payload, dict) else None
        if entity is None:
            raise DrawingRefusal("topology_reference_unavailable", source_ref)
        return bool(self._api._member(view, "SelectEntity", entity, False))


class SolidWorksDrawingAdapter:
    """Native drawing adapter with explicit source-path and view identity read-back."""

    _VIEW_NAMES = {
        "front": "*Front",
        "back": "*Back",
        "left": "*Left",
        "right": "*Right",
        "top": "*Top",
        "bottom": "*Bottom",
        "isometric": "*Isometric",
    }

    def __init__(
        self,
        session: Any,
        *,
        path_policy: DrawingPathPolicy,
        default_timeout: float = 60.0,
        max_features: int = 100_000,
        bom_template_path: str | None = None,
        reference_selector: Any | None = None,
        max_table_rows: int = 512,
        max_table_columns: int = 64,
    ) -> None:
        self._session = session
        self._api = session.api
        self._path_policy = path_policy
        self._default_timeout = float(default_timeout)
        self._max_features = int(max_features)
        self._max_table_rows = int(max_table_rows)
        self._max_table_columns = int(max_table_columns)
        if self._max_table_rows < 1 or self._max_table_columns < 1:
            raise ValueError("table traversal limits must be positive")
        self._templates: dict[str, str | None] = {}
        self._bom_template_path = bom_template_path
        self._reference_selector = reference_selector
        self._view_metadata: dict[
            tuple[str, str], tuple[str, str, str, str | None, str | None]
        ] = {}
        self._annotation_metadata: dict[
            tuple[str, str], AnnotationSnapshot
        ] = {}
        self._dimension_metadata: dict[tuple[str, str], DimensionSnapshot] = {}
        self._bom_metadata: dict[tuple[str, str], BomSnapshot] = {}

    def bind_topology_service(self, topology_service: Any | None) -> None:
        """Bind the opaque topology port used by source-linked drawing dimensions."""
        self._reference_selector = (
            None
            if topology_service is None
            else _TopologyDrawingReferenceSelector(topology_service, self._api)
        )

    def create_drawing(self, drawing_id: str, template_path: str | None) -> None:
        target = self._path_policy.validate_save(drawing_id)
        if PureWindowsPath(target).suffix.casefold() != ".slddrw":
            raise DrawingRefusal("drawing_extension_mismatch", target)
        explicit_template = (
            self._path_policy.validate_open(template_path)
            if template_path is not None
            else None
        )

        def operation(app: Any) -> str:
            template = explicit_template
            if template is None:
                template = str(
                    self._api._member(
                        app, "GetDocumentTemplate", 3, "", 0, 0.0, 0.0
                    )
                    or ""
                )
            if not template:
                raise DrawingPostconditionError("drawing_template_unavailable")
            model = self._api._member(app, "NewDocument", template, 0, 0.0, 0.0)
            if model is None:
                raise DrawingPostconditionError("drawing_create_failed")
            try:
                if int(self._api.document_type(model)) != 3:
                    raise DrawingPostconditionError("drawing_type_readback_mismatch")
                save_ok, errors, warnings = self._api.save_as(model, target)
                if not save_ok or int(errors) != 0:
                    raise DrawingPostconditionError(
                        "drawing_save_failed", f"errors={errors}, warnings={warnings}"
                    )
                sheets = self._sheet_names(model)
                if not sheets:
                    raise DrawingPostconditionError("drawing_sheet_readback_missing")
                return template
            finally:
                title = str(self._api._member(model, "GetTitle") or "")
                if title:
                    self._api.close_document(app, title)

        actual_template = self._execute(
            operation,
            stage="drawing_create_document",
            mutation=True,
        )
        self._templates[target] = actual_template
        if target != drawing_id:
            self._templates[drawing_id] = actual_template

    def read_drawing(self, drawing_id: str) -> DrawingSnapshot | None:
        source = self._path_policy.validate_open(drawing_id)

        def read(model: Any) -> DrawingSnapshot:
            native_path = str(self._api.document_path(model) or "")
            if native_path and PureWindowsPath(native_path) != PureWindowsPath(source):
                raise DrawingPostconditionError("drawing_path_readback_mismatch", native_path)
            return DrawingSnapshot(
                identity=drawing_id,
                path=drawing_id,
                sheet_names=self._sheet_names(model),
                template_path=self._templates.get(drawing_id, self._templates.get(source)),
            )

        return self._with_drawing(source, stage="drawing_read_document", reader=read)

    def create_sheet(self, drawing_id: str, sheet_name: str) -> None:
        source = self._path_policy.validate_open(drawing_id)

        def mutate(model: Any) -> None:
            if sheet_name in self._sheet_names(model):
                raise DrawingRefusal("sheet_already_exists", sheet_name)
            created = bool(
                self._api._member(
                    model,
                    "NewSheet3",
                    sheet_name,
                    12,
                    13,
                    1.0,
                    1.0,
                    False,
                    "",
                    0.297,
                    0.210,
                    "",
                )
            )
            if not created:
                raise DrawingPostconditionError("sheet_create_failed", sheet_name)
            self._persist(model, "sheet_create")

        self._with_drawing(source, stage="drawing_create_sheet", reader=mutate, mutation=True)

    def read_sheet(self, drawing_id: str, sheet_name: str) -> SheetSnapshot | None:
        source = self._path_policy.validate_open(drawing_id)
        return self._with_drawing(
            source,
            stage="drawing_read_sheet",
            reader=lambda model: SheetSnapshot(sheet_name)
            if sheet_name in self._sheet_names(model)
            else None,
        )

    def create_view(
        self,
        drawing_id: str,
        sheet_name: str,
        view_kind: str,
        source_model_path: str,
        source_configuration: str | None,
    ) -> str:
        drawing_path = self._path_policy.validate_open(drawing_id)
        model_path = self._path_policy.validate_open(source_model_path)
        model_view_name = self._VIEW_NAMES.get(view_kind.casefold())
        if model_view_name is None:
            raise DrawingRefusal("unsupported_view_kind", view_kind)

        def mutate(model: Any) -> str:
            if not bool(self._api._member(model, "ActivateSheet", sheet_name)):
                raise DrawingRefusal("missing_sheet", sheet_name)
            view = self._api._member(
                model,
                "CreateDrawViewFromModelView3",
                model_path,
                model_view_name,
                0.15,
                0.10,
                0.0,
            )
            if view is None:
                raise DrawingPostconditionError("view_create_failed", view_kind)
            if source_configuration is not None:
                try:
                    view.ReferencedConfiguration = source_configuration
                except Exception as exc:
                    raise DrawingPostconditionError(
                        "view_configuration_apply_failed", source_configuration
                    ) from exc
            view_id = str(self._api._member(view, "Name") or "")
            if not view_id:
                raise DrawingPostconditionError("view_identity_missing")
            self._persist(model, "view_create")
            return view_id

        view_id = self._with_drawing(
            drawing_path,
            stage="drawing_create_view",
            reader=mutate,
            mutation=True,
        )
        self._view_metadata[(drawing_id, view_id)] = (
            sheet_name,
            view_kind,
            source_model_path,
            source_configuration,
            None,
        )
        if drawing_path != drawing_id:
            self._view_metadata[(drawing_path, view_id)] = (
                sheet_name,
                view_kind,
                source_model_path,
                source_configuration,
                None,
            )
        return view_id

    def create_projected_view(
        self, drawing_id: str, parent_view_id: str, x: float, y: float
    ) -> str:
        drawing_path = self._path_policy.validate_open(drawing_id)
        metadata = self._view_metadata.get(
            (drawing_id, parent_view_id),
            self._view_metadata.get((drawing_path, parent_view_id)),
        )
        if metadata is None:
            raise DrawingRefusal("invalid_parent_view", parent_view_id)
        sheet_name, _, source_model_path, source_configuration, _ = metadata

        def mutate(model: Any) -> str:
            if not bool(self._api._member(model, "ActivateSheet", sheet_name)):
                raise DrawingRefusal("missing_sheet", sheet_name)
            parent = self._find_view(model, parent_view_id)
            if parent is None:
                raise DrawingRefusal("invalid_parent_view", parent_view_id)
            try:
                self._api._member(model, "ClearSelection2", True)
            except Exception:
                pass
            extension = self._api._member(model, "Extension")
            callout = self._api.null_dispatch()
            selected = bool(
                self._api._member(
                    extension,
                    "SelectByID2",
                    parent_view_id,
                    "DRAWINGVIEW",
                    0.0,
                    0.0,
                    0.0,
                    False,
                    0,
                    callout,
                    0,
                )
            )
            if not selected:
                raise DrawingPostconditionError("parent_view_selection_failed", parent_view_id)
            view = self._api._member(
                model, "CreateUnfoldedViewAt3", float(x), float(y), 0.0, False
            )
            if view is None:
                raise DrawingPostconditionError("projected_view_create_failed", parent_view_id)
            view_id = str(self._api._member(view, "Name") or "")
            if not view_id:
                raise DrawingPostconditionError("view_identity_missing")
            self._persist(model, "projected_view_create")
            return view_id

        view_id = self._with_drawing(
            drawing_path,
            stage="drawing_create_projected_view",
            reader=mutate,
            mutation=True,
        )
        entry = (
            sheet_name,
            "projected",
            source_model_path,
            source_configuration,
            parent_view_id,
        )
        self._view_metadata[(drawing_id, view_id)] = entry
        if drawing_path != drawing_id:
            self._view_metadata[(drawing_path, view_id)] = entry
        return view_id

    def create_section_view(
        self,
        drawing_id: str,
        parent_view_id: str,
        line_start: tuple[float, float],
        line_end: tuple[float, float],
        x: float,
        y: float,
        label: str,
    ) -> str:
        drawing_path = self._path_policy.validate_open(drawing_id)
        metadata = self._view_metadata.get(
            (drawing_id, parent_view_id),
            self._view_metadata.get((drawing_path, parent_view_id)),
        )
        if metadata is None:
            raise DrawingRefusal("invalid_parent_view", parent_view_id)
        sheet_name, _, source_model_path, source_configuration, _ = metadata

        def mutate(model: Any) -> str:
            if not bool(self._api._member(model, "ActivateSheet", sheet_name)):
                raise DrawingRefusal("missing_sheet", sheet_name)
            if not bool(self._api._member(model, "ActivateView", parent_view_id)):
                raise DrawingPostconditionError("view_activation_failed", parent_view_id)
            sketch_manager = self._api._member(model, "SketchManager")
            line = self._api._member(
                sketch_manager,
                "CreateLine",
                float(line_start[0]),
                float(line_start[1]),
                0.0,
                float(line_end[0]),
                float(line_end[1]),
                0.0,
            )
            if line is None:
                raise DrawingPostconditionError("section_line_create_failed", parent_view_id)
            if not bool(self._api._member(line, "Select4", False, self._api.null_dispatch())):
                raise DrawingPostconditionError("section_line_selection_failed", parent_view_id)
            view = self._api._member(
                model,
                "CreateSectionViewAt5",
                float(x),
                float(y),
                0.0,
                label,
                0,
                self._api.null_dispatch(),
                0.0,
            )
            if view is None:
                raise DrawingPostconditionError("section_view_create_failed", parent_view_id)
            section = self._api._member(view, "GetSection")
            if section is None:
                raise DrawingPostconditionError("section_view_readback_missing", parent_view_id)
            actual_label = str(self._api._member(section, "GetLabel") or "")
            if actual_label and actual_label.casefold() != label.casefold():
                raise DrawingPostconditionError("section_label_readback_mismatch", actual_label)
            view_id = str(self._api._member(view, "Name") or "")
            if not view_id:
                raise DrawingPostconditionError("view_identity_missing")
            self._persist(model, "section_view_create")
            return view_id

        view_id = self._with_drawing(
            drawing_path,
            stage="drawing_create_section_view",
            reader=mutate,
            mutation=True,
        )
        entry = (
            sheet_name,
            "section",
            source_model_path,
            source_configuration,
            parent_view_id,
        )
        self._view_metadata[(drawing_id, view_id)] = entry
        if drawing_path != drawing_id:
            self._view_metadata[(drawing_path, view_id)] = entry
        return view_id

    def read_view(self, drawing_id: str, view_id: str) -> ViewSnapshot | None:
        source = self._path_policy.validate_open(drawing_id)
        metadata = self._view_metadata.get((drawing_id, view_id), self._view_metadata.get((source, view_id)))
        if metadata is None:
            return None
        (
            sheet_name,
            view_kind,
            requested_model_path,
            requested_configuration,
            parent_view_id,
        ) = metadata

        def read(model: Any) -> ViewSnapshot | None:
            if not bool(self._api._member(model, "ActivateSheet", sheet_name)):
                return None
            view = self._find_view(model, view_id)
            if view is None:
                return None
            referenced = self._api._member(view, "ReferencedDocument")
            native_source = "" if referenced is None else str(self._api.document_path(referenced) or "")
            dangling = referenced is None or not native_source
            if not dangling and PureWindowsPath(native_source) != PureWindowsPath(
                self._path_policy.validate_open(requested_model_path)
            ):
                raise DrawingPostconditionError("view_source_readback_mismatch", native_source)
            if requested_configuration is not None:
                actual_configuration = str(
                    self._api._member(view, "ReferencedConfiguration") or ""
                )
                if actual_configuration != requested_configuration:
                    raise DrawingPostconditionError(
                        "view_configuration_readback_mismatch", actual_configuration
                    )
            position = self._view_position(view)
            scale_decimal = self._float_member(view, "ScaleDecimal")
            display_style = self._int_member(view, "GetDisplayMode2")
            return ViewSnapshot(
                identity=view_id,
                sheet_name=sheet_name,
                view_kind=view_kind,
                source_model_path=requested_model_path,
                source_configuration=requested_configuration,
                dangling=dangling,
                position=position,
                scale_decimal=scale_decimal,
                display_style=display_style,
                parent_view_id=parent_view_id,
            )

        return self._with_drawing(source, stage="drawing_read_view", reader=read)

    def add_note(self, drawing_id: str, view_id: str, text: str) -> str:
        source = self._path_policy.validate_open(drawing_id)
        metadata = self._view_metadata.get(
            (drawing_id, view_id), self._view_metadata.get((source, view_id))
        )
        if metadata is None:
            raise DrawingRefusal("invalid_annotation_view", view_id)
        sheet_name = metadata[0]

        def mutate(model: Any) -> tuple[str, AnnotationSnapshot]:
            if not bool(self._api._member(model, "ActivateSheet", sheet_name)):
                raise DrawingRefusal("missing_sheet", sheet_name)
            if not bool(self._api._member(model, "ActivateView", view_id)):
                raise DrawingPostconditionError("view_activation_failed", view_id)
            note = self._api._member(model, "InsertNote", text)
            if note is None:
                raise DrawingPostconditionError("note_create_failed", view_id)
            annotation = self._api._member(note, "GetAnnotation")
            snapshot = self._annotation_snapshot(annotation, view_id, "note")
            if snapshot.text != text:
                raise DrawingPostconditionError(
                    "annotation_text_readback_mismatch", snapshot.identity
                )
            self._persist(model, "note_create")
            return snapshot.identity, snapshot

        annotation_id, snapshot = self._with_drawing(
            source, stage="drawing_add_note", reader=mutate, mutation=True
        )
        self._annotation_metadata[(drawing_id, annotation_id)] = snapshot
        if source != drawing_id:
            self._annotation_metadata[(source, annotation_id)] = snapshot
        return annotation_id

    def import_model_annotations(self, drawing_id: str, view_id: str) -> tuple[str, ...]:
        source = self._path_policy.validate_open(drawing_id)
        metadata = self._view_metadata.get(
            (drawing_id, view_id), self._view_metadata.get((source, view_id))
        )
        if metadata is None:
            raise DrawingRefusal("invalid_annotation_view", view_id)
        sheet_name = metadata[0]

        def mutate(model: Any) -> tuple[AnnotationSnapshot, ...]:
            if not bool(self._api._member(model, "ActivateSheet", sheet_name)):
                raise DrawingRefusal("missing_sheet", sheet_name)
            if not bool(self._api._member(model, "ActivateView", view_id)):
                raise DrawingPostconditionError("view_activation_failed", view_id)
            value = self._api._member(
                model,
                "InsertModelAnnotations4",
                0,
                32768,
                False,
                True,
                False,
                False,
                False,
                False,
            )
            annotations = self._as_tuple(value)
            if not annotations:
                raise DrawingPostconditionError(
                    "model_annotations_readback_empty", view_id
                )
            snapshots = tuple(
                self._annotation_snapshot(annotation, view_id, None)
                for annotation in annotations
            )
            self._persist(model, "model_annotations_import")
            return snapshots

        snapshots = self._with_drawing(
            source,
            stage="drawing_import_model_annotations",
            reader=mutate,
            mutation=True,
        )
        for snapshot in snapshots:
            self._annotation_metadata[(drawing_id, snapshot.identity)] = snapshot
            if source != drawing_id:
                self._annotation_metadata[(source, snapshot.identity)] = snapshot
        return tuple(snapshot.identity for snapshot in snapshots)

    def auto_insert_center_marks(
        self, drawing_id: str, view_id: str
    ) -> tuple[str, ...]:
        source = self._path_policy.validate_open(drawing_id)
        metadata = self._view_metadata.get(
            (drawing_id, view_id), self._view_metadata.get((source, view_id))
        )
        if metadata is None:
            raise DrawingRefusal("invalid_annotation_view", view_id)
        sheet_name = metadata[0]

        def mutate(model: Any) -> tuple[AnnotationSnapshot, ...]:
            if not bool(self._api._member(model, "ActivateSheet", sheet_name)):
                raise DrawingRefusal("missing_sheet", sheet_name)
            if not bool(self._api._member(model, "ActivateView", view_id)):
                raise DrawingPostconditionError("view_activation_failed", view_id)
            view = self._api._member(model, "ActiveDrawingView")
            if view is None:
                raise DrawingPostconditionError("active_view_readback_missing", view_id)
            before = {
                snapshot.identity
                for snapshot in self._center_mark_snapshots(view, view_id)
            }
            inserted = bool(
                self._api._member(
                    view,
                    "AutoInsertCenterMarks2",
                    7,
                    11,
                    True,
                    True,
                    True,
                    0.0,
                    0.0,
                    True,
                    True,
                    0.0,
                )
            )
            if not inserted:
                raise DrawingPostconditionError("center_mark_create_failed", view_id)
            snapshots = tuple(
                snapshot
                for snapshot in self._center_mark_snapshots(view, view_id)
                if snapshot.identity not in before
            )
            if not snapshots:
                raise DrawingPostconditionError("center_marks_readback_empty", view_id)
            self._persist(model, "center_marks_create")
            return snapshots

        snapshots = self._with_drawing(
            source,
            stage="drawing_auto_insert_center_marks",
            reader=mutate,
            mutation=True,
        )
        for snapshot in snapshots:
            self._annotation_metadata[(drawing_id, snapshot.identity)] = snapshot
            if source != drawing_id:
                self._annotation_metadata[(source, snapshot.identity)] = snapshot
        return tuple(snapshot.identity for snapshot in snapshots)

    def read_annotation(
        self, drawing_id: str, annotation_id: str
    ) -> AnnotationSnapshot | None:
        source = self._path_policy.validate_open(drawing_id)
        return self._annotation_metadata.get(
            (drawing_id, annotation_id),
            self._annotation_metadata.get((source, annotation_id)),
        )

    def supports_dimensions(self) -> bool:
        return self._reference_selector is not None

    def add_dimension(
        self,
        drawing_id: str,
        view_id: str,
        source_ref: str,
        x_mm: float = 0.0,
        y_mm: float = 0.0,
    ) -> str:
        if self._reference_selector is None:
            raise DrawingRefusal(
                "unsupported_capability",
                "solidworks.drawing.dimension_reference_selector_unavailable",
            )
        source = self._path_policy.validate_open(drawing_id)
        metadata = self._view_metadata.get(
            (drawing_id, view_id), self._view_metadata.get((source, view_id))
        )
        if metadata is None:
            raise DrawingRefusal("invalid_dimension_view", view_id)
        sheet_name, _, source_model_path, source_configuration, _ = metadata

        def mutate(model: Any) -> DimensionSnapshot:
            if not bool(self._api._member(model, "ActivateSheet", sheet_name)):
                raise DrawingRefusal("missing_sheet", sheet_name)
            self._api._member(model, "ClearSelection2", True)
            if not bool(self._api._member(model, "ActivateView", view_id)):
                raise DrawingPostconditionError("view_activation_failed", view_id)
            view = self._find_view(model, view_id)
            if view is None:
                raise DrawingRefusal("invalid_dimension_view", view_id)
            try:
                selected = bool(
                    self._reference_selector.select_for_drawing(
                        model=model,
                        view=view,
                        source_model_path=source_model_path,
                        source_configuration=source_configuration,
                        source_ref=source_ref,
                    )
                )
            except DrawingRefusal:
                raise
            except Exception as exc:
                raise DrawingRefusal("topology_reference_unavailable", source_ref) from exc
            if not selected:
                raise DrawingRefusal("topology_reference_unavailable", source_ref)
            display_dimension = self._api._member(
                model,
                "AddDimension2",
                float(x_mm) / 1000.0,
                float(y_mm) / 1000.0,
                0.0,
            )
            if display_dimension is None:
                raise DrawingPostconditionError("dimension_create_failed", source_ref)
            snapshot = self._dimension_snapshot(
                model, display_dimension, view_id, source_ref
            )
            self._persist(model, "dimension_create")
            return snapshot

        snapshot = self._with_drawing(
            source, stage="drawing_add_dimension", reader=mutate, mutation=True
        )
        self._dimension_metadata[(drawing_id, snapshot.identity)] = snapshot
        if source != drawing_id:
            self._dimension_metadata[(source, snapshot.identity)] = snapshot
        return snapshot.identity

    def read_dimension(
        self, drawing_id: str, dimension_id: str
    ) -> DimensionSnapshot | None:
        source = self._path_policy.validate_open(drawing_id)
        cached = self._dimension_metadata.get(
            (drawing_id, dimension_id),
            self._dimension_metadata.get((source, dimension_id)),
        )
        if cached is not None:
            return cached
        return next(
            (
                item
                for item in self.list_dimensions(drawing_id)
                if item.identity == dimension_id
            ),
            None,
        )

    def list_dimensions(self, drawing_id: str) -> tuple[DimensionSnapshot, ...]:
        source = self._path_policy.validate_open(drawing_id)

        def read(model: Any) -> tuple[DimensionSnapshot, ...]:
            result: list[DimensionSnapshot] = []
            seen: set[str] = set()
            for view_id, view in self._iter_model_views(model):
                display = self._first_display_dimension(view)
                while display is not None:
                    cached_ref = None
                    try:
                        annotation = self._api._member(display, "GetAnnotation")
                        identity = self._annotation_identity(annotation)
                        cached = self._dimension_metadata.get(
                            (drawing_id, identity),
                            self._dimension_metadata.get((source, identity)),
                        )
                        cached_ref = cached.source_ref if cached is not None else None
                    except Exception:
                        identity = ""
                    snapshot = self._dimension_snapshot(
                        model,
                        display,
                        view_id,
                        cached_ref or f"native:{identity or view_id}",
                    )
                    if snapshot.identity not in seen:
                        seen.add(snapshot.identity)
                        result.append(snapshot)
                    display = self._next_display_dimension(display)
            for (key_drawing, _), snapshot in self._dimension_metadata.items():
                if key_drawing in {drawing_id, source} and snapshot.identity not in seen:
                    seen.add(snapshot.identity)
                    result.append(snapshot)
            return tuple(result)

        return self._with_drawing(source, stage="drawing_list_dimensions", reader=read)

    def supports_bom(self, drawing_id: str) -> bool:
        try:
            self._validated_bom_template_path()
            return True
        except Exception:
            return False

    def _validated_bom_template_path(self) -> str:
        if self._bom_template_path is None:
            raise DrawingRefusal("unsupported_capability", "solidworks.drawing.bom")
        candidate = Path(self._bom_template_path).expanduser().resolve(strict=False)
        if not candidate.is_absolute():
            raise DrawingRefusal("bom_template_path_not_absolute", str(candidate))
        if candidate.suffix.casefold() != ".sldbomtbt":
            raise DrawingRefusal("bom_template_extension_mismatch", str(candidate))
        if not candidate.is_file():
            raise DrawingRefusal("bom_template_not_found", str(candidate))
        return str(candidate)

    def create_bom(
        self, drawing_id: str, view_id: str, source_configuration: str | None
    ) -> str:
        if not self.supports_bom(drawing_id):
            raise DrawingRefusal("unsupported_capability", "solidworks.drawing.bom")
        source = self._path_policy.validate_open(drawing_id)
        template = self._validated_bom_template_path()
        metadata = self._view_metadata.get(
            (drawing_id, view_id), self._view_metadata.get((source, view_id))
        )
        if metadata is None:
            raise DrawingRefusal("invalid_bom_view", view_id)
        sheet_name = metadata[0]

        def mutate(model: Any) -> BomSnapshot:
            if not bool(self._api._member(model, "ActivateSheet", sheet_name)):
                raise DrawingRefusal("missing_sheet", sheet_name)
            view = self._find_view(model, view_id)
            if view is None:
                raise DrawingRefusal("invalid_bom_view", view_id)
            table = self._api._member(
                view,
                "InsertBomTable5",
                False,
                0.02,
                0.02,
                0,
                1,
                source_configuration or "",
                template,
                False,
                0,
                False,
                False,
            )
            if table is None:
                raise DrawingPostconditionError("bom_create_failed", view_id)
            bom_id = self._table_identity(table)
            rows = self._table_rows(table)
            if not rows or not rows[0]:
                raise DrawingPostconditionError("bom_empty_readback", bom_id)
            component_ids = self._table_component_ids(
                table, len(rows), source_configuration
            )
            snapshot = BomSnapshot(
                identity=bom_id,
                view_id=view_id,
                source_configuration=source_configuration,
                row_count=len(rows),
                column_count=len(rows[0]),
                rows=rows,
                component_ids=component_ids,
            )
            self._persist(model, "bom_create")
            return snapshot

        snapshot = self._with_drawing(
            source, stage="drawing_create_bom", reader=mutate, mutation=True
        )
        self._bom_metadata[(drawing_id, snapshot.identity)] = snapshot
        if source != drawing_id:
            self._bom_metadata[(source, snapshot.identity)] = snapshot
        return snapshot.identity

    def read_bom(self, drawing_id: str, bom_id: str) -> BomSnapshot | None:
        source = self._path_policy.validate_open(drawing_id)
        cached = self._bom_metadata.get(
            (drawing_id, bom_id), self._bom_metadata.get((source, bom_id))
        )
        if cached is not None:
            return cached
        return next(
            (item for item in self.list_boms(drawing_id) if item.identity == bom_id),
            None,
        )

    def list_boms(self, drawing_id: str) -> tuple[BomSnapshot, ...]:
        source = self._path_policy.validate_open(drawing_id)

        def read(model: Any) -> tuple[BomSnapshot, ...]:
            result: list[BomSnapshot] = []
            seen: set[str] = set()
            for view_id, view in self._iter_model_views(model):
                table = self._first_table_annotation(view)
                while table is not None:
                    try:
                        bom_id = self._table_identity(table)
                        rows = self._table_rows(table)
                    except DrawingPostconditionError:
                        raise
                    except Exception:
                        table = self._next_table_annotation(table)
                        continue
                    if bom_id not in seen and rows and rows[0]:
                        seen.add(bom_id)
                        configuration = str(
                            self._api._member(view, "ReferencedConfiguration") or ""
                        ) or None
                        component_ids = self._table_component_ids(
                            table, len(rows), configuration
                        )
                        result.append(
                            BomSnapshot(
                                identity=bom_id,
                                view_id=view_id,
                                source_configuration=configuration,
                                row_count=len(rows),
                                column_count=len(rows[0]),
                                rows=rows,
                                component_ids=component_ids,
                            )
                        )
                    table = self._next_table_annotation(table)
            for (key_drawing, _), snapshot in self._bom_metadata.items():
                if key_drawing in {drawing_id, source} and snapshot.identity not in seen:
                    seen.add(snapshot.identity)
                    result.append(snapshot)
            return tuple(result)

        return self._with_drawing(source, stage="drawing_list_boms", reader=read)

    def list_views(self, drawing_id: str) -> tuple[ViewSnapshot, ...]:
        source = self._path_policy.validate_open(drawing_id)

        def read(model: Any) -> tuple[ViewSnapshot, ...]:
            snapshots: list[ViewSnapshot] = []
            for view_id, view in self._iter_model_views(model):
                referenced = self._api._member(view, "ReferencedDocument")
                native_source = "" if referenced is None else str(
                    self._api.document_path(referenced) or ""
                )
                metadata = self._view_metadata.get(
                    (drawing_id, view_id), self._view_metadata.get((source, view_id))
                )
                sheet_name = metadata[0] if metadata is not None else self._view_sheet_name(view)
                view_kind = metadata[1] if metadata is not None else "unknown"
                parent_view_id = metadata[4] if metadata is not None else None
                dangling = referenced is None or not native_source
                source_model_path = (
                    metadata[2] if dangling and metadata is not None else native_source
                )
                if dangling and metadata is not None:
                    configuration = metadata[3]
                else:
                    configuration = str(
                        self._api._member(view, "ReferencedConfiguration") or ""
                    ) or None
                snapshots.append(
                    ViewSnapshot(
                        identity=view_id,
                        sheet_name=sheet_name,
                        view_kind=view_kind,
                        source_model_path=source_model_path,
                        source_configuration=configuration,
                        dangling=dangling,
                        position=self._view_position(view),
                        scale_decimal=self._float_member(view, "ScaleDecimal"),
                        display_style=self._int_member(view, "GetDisplayMode2"),
                        parent_view_id=parent_view_id,
                    )
                )
            return tuple(snapshots)

        return self._with_drawing(source, stage="drawing_list_views", reader=read)

    def rebuild_drawing(self, drawing_id: str) -> RebuildReport:
        source = self._path_policy.validate_open(drawing_id)

        def rebuild(model: Any) -> RebuildReport:
            result = rebuild_document(model, self._api, max_features=self._max_features)
            errors = tuple(
                f"{issue.feature_name}:{issue.error_code}"
                for issue in result.feature_issues
                if not issue.is_warning
            )
            if result.success:
                save_ok, save_errors, save_warnings = self._api.save_document(model)
                if not save_ok or int(save_errors) != 0:
                    errors += (f"save:{save_errors}:{save_warnings}",)
            return RebuildReport(ok=result.success and not errors, errors=errors)

        return self._with_drawing(
            source,
            stage="drawing_rebuild",
            reader=rebuild,
            mutation=True,
        )

    def _with_drawing(
        self,
        source: str,
        *,
        stage: str,
        reader: Callable[[Any], T],
        mutation: bool = False,
    ) -> T:
        def operation(app: Any) -> T:
            model = self._api.get_open_document(app, source)
            owned = model is None
            if model is None:
                model, errors, warnings = self._api.open_document(
                    app,
                    source,
                    3,
                    read_only=not mutation,
                    silent=True,
                    configuration="",
                )
                if model is None or int(errors) != 0:
                    raise DrawingPostconditionError(
                        "drawing_open_failed", f"errors={errors}, warnings={warnings}"
                    )
            try:
                if int(self._api.document_type(model)) != 3:
                    raise DrawingPostconditionError("drawing_type_readback_mismatch")
                return reader(model)
            finally:
                if owned:
                    title = str(self._api._member(model, "GetTitle") or "")
                    if title:
                        self._api.close_document(app, title)

        return self._execute(operation, stage=stage, mutation=mutation)

    def _execute(self, operation: Callable[[Any], T], *, stage: str, mutation: bool) -> T:
        result = self._session.execute(
            operation,
            stage=stage,
            timeout=self._default_timeout,
            mutation=mutation,
        )
        if result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH:
            detail = result.state.value
            if result.failure is not None:
                detail = (
                    f"{result.failure.code}@{result.failure.stage}: "
                    f"{result.failure.message}"
                )
            raise DrawingPostconditionError(
                "native_state_uncertain", f"call_id={result.call_id}; {detail}"
            )
        if result.state is not NativeCallState.SUCCESS:
            detail = result.state.value
            if result.failure is not None:
                detail = (
                    f"{result.failure.code}@{result.failure.stage}: "
                    f"{result.failure.message}"
                )
            raise DrawingPostconditionError("native_drawing_failed", detail)
        return result.value

    def _persist(self, model: Any, stage: str) -> None:
        rebuild = rebuild_document(model, self._api, max_features=self._max_features)
        if not rebuild.success:
            raise DrawingPostconditionError("rebuild_failed", stage)
        save_ok, errors, warnings = self._api.save_document(model)
        if not save_ok or int(errors) != 0:
            raise DrawingPostconditionError(
                "drawing_save_failed", f"errors={errors}, warnings={warnings}"
            )

    def _dimension_snapshot(
        self,
        model: Any,
        display_dimension: Any,
        view_id: str,
        source_ref: str,
    ) -> DimensionSnapshot:
        annotation = self._api._member(display_dimension, "GetAnnotation")
        identity = self._annotation_identity(annotation)
        fallback_text = self._annotation_text(annotation, identity)
        text = self._dimension_user_value_text(
            model, display_dimension, fallback=fallback_text
        )
        dangling = self._annotation_dangling(annotation)
        return DimensionSnapshot(
            identity=identity,
            view_id=view_id,
            source_ref=source_ref,
            display_text=text,
            dangling=dangling,
        )

    def _dimension_user_value_text(
        self, model: Any, display_dimension: Any, *, fallback: str
    ) -> str:
        try:
            dimension = self._api._member(display_dimension, "GetDimension2", 0)
            if dimension is None:
                return fallback
            number = float(self._api._member(dimension, "GetUserValueIn", model))
            if number != number or number in (float("inf"), float("-inf")):
                return fallback
            return format(number, ".15g")
        except Exception:
            return fallback

    def _annotation_identity(self, annotation: Any) -> str:
        if annotation is None:
            raise DrawingPostconditionError("annotation_readback_missing")
        identity = str(self._api._member(annotation, "GetName") or "")
        if not identity:
            raise DrawingPostconditionError("annotation_identity_missing")
        return identity

    def _annotation_snapshot(
        self, annotation: Any, view_id: str, forced_kind: str | None
    ) -> AnnotationSnapshot:
        if annotation is None:
            raise DrawingPostconditionError("annotation_readback_missing")
        identity = self._annotation_identity(annotation)
        try:
            annotation_type = int(self._api._member(annotation, "GetType"))
        except Exception:
            annotation_type = -1
        kind = forced_kind or {
            2: "datum",
            4: "display_dimension",
            5: "gtol",
            6: "note",
            7: "surface_finish",
            8: "weld_symbol",
            19: "pmi",
        }.get(annotation_type, "other")
        text = self._annotation_text(annotation, identity)
        dangling = self._annotation_dangling(annotation)
        return AnnotationSnapshot(
            identity=identity,
            view_id=view_id,
            annotation_kind=kind,
            text=text,
            dangling=dangling,
        )

    def _annotation_text(self, annotation: Any, fallback: str) -> str:
        try:
            specific = self._api._member(annotation, "GetSpecificAnnotation")
        except Exception:
            specific = None
        if specific is not None:
            for name in ("GetText", "GetText2"):
                try:
                    value = self._api._member(specific, name)
                except Exception:
                    continue
                text = str(value or "").strip()
                if text:
                    return text
        return fallback.strip()

    def _annotation_dangling(self, annotation: Any) -> bool:
        try:
            values = self._as_tuple(
                self._api._member(annotation, "GetAttachedEntityTypes")
            )
        except Exception as exc:
            raise DrawingPostconditionError("attachment_read_failed") from exc
        return any(int(value) == 0 for value in values)

    def _center_mark_snapshots(
        self, view: Any, view_id: str
    ) -> tuple[AnnotationSnapshot, ...]:
        snapshots: list[AnnotationSnapshot] = []
        center_mark = self._api._member(view, "GetFirstCenterMark")
        seen = 0
        while center_mark is not None:
            if seen >= 100_000:
                raise DrawingPostconditionError("center_mark_traversal_limit", view_id)
            annotation = self._api._member(center_mark, "GetAnnotation")
            snapshots.append(
                self._annotation_snapshot(annotation, view_id, "center_mark")
            )
            center_mark = self._api._member(center_mark, "GetNext")
            seen += 1
        return tuple(snapshots)

    def _table_identity(self, table: Any) -> str:
        for getter in ("GetFeature", "GetAnnotation"):
            try:
                owner = self._api._member(table, getter)
                if owner is None:
                    continue
                for name in ("Name", "GetName"):
                    try:
                        value = str(self._api._member(owner, name) or "")
                    except Exception:
                        continue
                    if value:
                        return value
            except Exception:
                continue
        raise DrawingPostconditionError("bom_identity_missing")

    def _table_rows(self, table: Any) -> tuple[tuple[str, ...], ...]:
        row_count = self._int_member(table, "RowCount") or 0
        column_count = self._int_member(table, "ColumnCount") or 0
        if row_count < 1 or column_count < 1:
            return ()
        if row_count > self._max_table_rows or column_count > self._max_table_columns:
            raise DrawingPostconditionError(
                "bom_table_limit_exceeded",
                f"rows={row_count}, columns={column_count}",
            )
        rows: list[tuple[str, ...]] = []
        for row_index in range(row_count):
            row: list[str] = []
            for column_index in range(column_count):
                text = ""
                for name, args in (
                    ("DisplayedText2", (row_index, column_index, False)),
                    ("Text2", (row_index, column_index, False)),
                    ("Text", (row_index, column_index)),
                ):
                    try:
                        value = self._api._member(table, name, *args)
                    except Exception:
                        continue
                    text = str(value or "")
                    break
                row.append(text)
            rows.append(tuple(row))
        return tuple(rows)

    def _table_component_ids(
        self,
        table: Any,
        row_count: int,
        source_configuration: str | None,
    ) -> tuple[tuple[str, ...], ...]:
        specific = table
        if getattr(specific, "GetComponents2", None) is None:
            try:
                specific = self._api._member(table, "GetSpecificAnnotation")
            except Exception:
                specific = None
        rows: list[tuple[str, ...]] = []
        any_component = False
        for row_index in range(row_count):
            if row_index == 0:
                rows.append(())
                continue
            try:
                components = self._as_tuple(
                    self._api._member(
                        specific,
                        "GetComponents2",
                        row_index,
                        source_configuration or "",
                    )
                )
            except Exception as exc:
                raise DrawingPostconditionError(
                    "bom_component_identity_read_failed", f"row={row_index}"
                ) from exc
            identities = tuple(
                identity
                for component in components
                if component is not None
                for identity in (self._component_identity(component),)
                if identity
            )
            if not identities:
                raise DrawingPostconditionError(
                    "bom_component_identity_missing", f"row={row_index}"
                )
            any_component = True
            rows.append(identities)
        if row_count > 1 and not any_component:
            raise DrawingPostconditionError("bom_component_identity_missing")
        return tuple(rows)

    def _component_identity(self, component: Any) -> str:
        for name in ("GetSelectByIDString", "Name2", "Name"):
            try:
                value = str(self._api._member(component, name) or "").strip()
            except Exception:
                continue
            if value:
                return value
        try:
            path = str(self._api._member(component, "GetPathName") or "").strip()
        except Exception:
            path = ""
        try:
            configuration = str(
                self._api._member(component, "ReferencedConfiguration") or ""
            ).strip()
        except Exception:
            configuration = ""
        if path:
            return f"{path}|{configuration}" if configuration else path
        return ""

    def _view_position(self, view: Any) -> tuple[float, float] | None:
        try:
            value = self._api._member(view, "Position")
        except Exception:
            return None
        if isinstance(value, (tuple, list)) and len(value) >= 2:
            return float(value[0]), float(value[1])
        return None

    def _float_member(self, obj: Any, name: str) -> float | None:
        try:
            return float(self._api._member(obj, name))
        except Exception:
            return None

    def _int_member(self, obj: Any, name: str) -> int | None:
        try:
            return int(self._api._member(obj, name))
        except Exception:
            return None

    @staticmethod
    def _as_tuple(value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, (tuple, list)):
            return tuple(value)
        return (value,)

    def _find_view(self, model: Any, view_id: str) -> Any | None:
        for current_id, view in self._iter_model_views(model):
            if current_id == view_id:
                return view
        return None

    def _iter_model_views(self, model: Any):
        view = self._api._member(model, "GetFirstView")
        seen = 0
        first = True
        while view is not None:
            if seen >= self._max_features:
                raise DrawingPostconditionError("view_traversal_limit")
            if first:
                first = False
            else:
                view_id = str(self._api._member(view, "Name") or "")
                if view_id:
                    yield view_id, view
            view = self._api._member(view, "GetNextView")
            seen += 1

    def _first_display_dimension(self, view: Any) -> Any | None:
        for name in ("GetFirstDisplayDimension5", "GetFirstDisplayDimension"):
            try:
                return self._api._member(view, name)
            except Exception:
                continue
        return None

    def _next_display_dimension(self, display: Any) -> Any | None:
        for name in ("GetNext5", "GetNext"):
            try:
                return self._api._member(display, name)
            except Exception:
                continue
        return None

    def _first_table_annotation(self, view: Any) -> Any | None:
        try:
            return self._api._member(view, "GetFirstTableAnnotation")
        except Exception:
            return None

    def _next_table_annotation(self, table: Any) -> Any | None:
        for name in ("GetNext", "GetNextTableAnnotation"):
            try:
                return self._api._member(table, name)
            except Exception:
                continue
        return None

    def _view_sheet_name(self, view: Any) -> str:
        try:
            sheet = self._api._member(view, "GetSheet")
            if sheet is not None:
                return str(self._api._member(sheet, "GetName") or "")
        except Exception:
            pass
        return ""

    def _sheet_names(self, model: Any) -> tuple[str, ...]:
        value = self._api._member(model, "GetSheetNames")
        if value is None:
            return ()
        if isinstance(value, (tuple, list)):
            return tuple(str(item) for item in value)
        return (str(value),)
