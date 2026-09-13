"""Lane-local SOLIDWORKS COM adapter for drawing lifecycle and standard views."""

from __future__ import annotations

from pathlib import PureWindowsPath
from typing import Any, Callable, Protocol, TypeVar

from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.rebuild import rebuild_document

from .domain import (
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
    ) -> None:
        self._session = session
        self._api = session.api
        self._path_policy = path_policy
        self._default_timeout = float(default_timeout)
        self._max_features = int(max_features)
        self._templates: dict[str, str | None] = {}
        self._view_metadata: dict[tuple[str, str], tuple[str, str, str, str | None]] = {}

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
        )
        if drawing_path != drawing_id:
            self._view_metadata[(drawing_path, view_id)] = (
                sheet_name,
                view_kind,
                source_model_path,
                source_configuration,
            )
        return view_id

    def read_view(self, drawing_id: str, view_id: str) -> ViewSnapshot | None:
        source = self._path_policy.validate_open(drawing_id)
        metadata = self._view_metadata.get((drawing_id, view_id), self._view_metadata.get((source, view_id)))
        if metadata is None:
            return None
        sheet_name, view_kind, requested_model_path, requested_configuration = metadata

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
            return ViewSnapshot(
                identity=view_id,
                sheet_name=sheet_name,
                view_kind=view_kind,
                source_model_path=requested_model_path,
                source_configuration=requested_configuration,
                dangling=dangling,
            )

        return self._with_drawing(source, stage="drawing_read_view", reader=read)

    def add_dimension(self, drawing_id: str, view_id: str, source_ref: str) -> str:
        raise DrawingRefusal(
            "unsupported_capability",
            "solidworks.drawing.dimension_requires_entity_identity_binding",
        )

    def read_dimension(
        self, drawing_id: str, dimension_id: str
    ) -> DimensionSnapshot | None:
        return None

    def supports_bom(self, drawing_id: str) -> bool:
        return False

    def create_bom(
        self, drawing_id: str, view_id: str, source_configuration: str | None
    ) -> str:
        raise DrawingRefusal(
            "unsupported_capability",
            "solidworks.drawing.bom_requires_template_and_table_identity_binding",
        )

    def read_bom(self, drawing_id: str, bom_id: str) -> BomSnapshot | None:
        return None

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
        if result.state is not NativeCallState.SUCCESS:
            detail = result.state.value
            if result.failure is not None:
                detail = f"{result.failure.code}@{result.failure.stage}"
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

    def _find_view(self, model: Any, view_id: str) -> Any | None:
        view = self._api._member(model, "GetFirstView")
        while view is not None:
            if str(self._api._member(view, "Name") or "") == view_id:
                return view
            view = self._api._member(view, "GetNextView")
        return None

    def _sheet_names(self, model: Any) -> tuple[str, ...]:
        value = self._api._member(model, "GetSheetNames")
        if value is None:
            return ()
        if isinstance(value, (tuple, list)):
            return tuple(str(item) for item in value)
        return (str(value),)
