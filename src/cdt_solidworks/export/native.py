"""Lane-local SOLIDWORKS export execution and independent artifact inspection."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Protocol

from cdt_solidworks.native.models import NativeCallState

from .domain import (
    ArtifactInspection,
    ExportFormat,
    ExportRequest,
    ImportFormat,
    ImportPostconditionError,
    ImportRequest,
    ImportSnapshot,
    NativeExportResult,
)


class ExportPathPolicy(Protocol):
    def validate_open(self, path: str) -> str: ...

    def validate_save(self, path: str) -> str: ...


class SolidWorksExporter:
    """Executes bounded SaveAs conversions on the serialized SOLIDWORKS session.

    STEP 242 remains fail-closed until a dedicated PMI publish path is bound.
    Single-sheet PDF uses IExportPdfData with explicit sheet selection rather than
    relabeling a generic SaveAs as stronger export semantics.
    """

    _DOC_TYPES = {".sldprt": 1, ".sldasm": 2, ".slddrw": 3}
    _ACTIVATE_BEFORE_EXPORT = frozenset(
        {
            ExportFormat.STEP,
            ExportFormat.IGES,
            ExportFormat.PARASOLID,
            ExportFormat.STL,
            ExportFormat.THREE_MF,
        }
    )

    def __init__(
        self,
        session: Any,
        *,
        path_policy: ExportPathPolicy,
        default_timeout: float = 60.0,
    ) -> None:
        self._session = session
        self._api = session.api
        self._path_policy = path_policy
        self._default_timeout = float(default_timeout)

    def export(self, request: ExportRequest) -> NativeExportResult:
        if request.format is ExportFormat.STEP_242:
            return NativeExportResult(
                completed=False,
                partial=False,
                errors=("step_242_requires_pmi_publish_path",),
            )
        source = self._path_policy.validate_open(request.source_document_id)
        target = self._path_policy.validate_save(request.target_path)
        source_extension = PureWindowsPath(source).suffix.casefold()
        doc_type = self._DOC_TYPES.get(source_extension)
        if doc_type is None:
            return NativeExportResult(
                completed=False,
                partial=False,
                errors=("unsupported_source_document_extension",),
            )

        def operation(app: Any) -> NativeExportResult:
            model = self._api.get_open_document(app, source)
            owned = model is None
            if model is None:
                model, errors, warnings = self._api.open_document(
                    app,
                    source,
                    doc_type,
                    read_only=False,
                    silent=True,
                    configuration=request.source_configuration or "",
                )
                if model is None or int(errors) != 0:
                    return NativeExportResult(
                        completed=False,
                        partial=False,
                        errors=(f"document_open_failed:{int(errors)}:{int(warnings)}",),
                    )
            try:
                if int(self._api.document_type(model)) != doc_type:
                    return NativeExportResult(
                        completed=False,
                        partial=False,
                        errors=("source_document_type_mismatch",),
                    )
                if request.source_configuration is not None:
                    active_configuration = self._api.active_configuration(model)
                    if active_configuration != request.source_configuration:
                        return NativeExportResult(
                            completed=False,
                            partial=False,
                            errors=("source_configuration_mismatch",),
                        )

                if request.drawing_sheet is not None:
                    return self._export_single_sheet_pdf(
                        app, model, target, request.drawing_sheet
                    )

                if request.format in self._ACTIVATE_BEFORE_EXPORT:
                    activation = self._activate_document(app, model)
                    if activation != 0:
                        return NativeExportResult(
                            completed=False,
                            partial=False,
                            errors=(f"document_activation_failed:{activation}",),
                        )
                    self._api._member(model, "ClearSelection2", True)

                success, errors, warnings = self._api.save_as(model, target)
                if not success or int(errors) != 0:
                    return NativeExportResult(
                        completed=False,
                        partial=False,
                        errors=(f"save_as_failed:{int(errors)}:{int(warnings)}",),
                    )
                return NativeExportResult(completed=True, partial=False, errors=())
            finally:
                if owned:
                    title = str(self._api._member(model, "GetTitle") or "")
                    if title:
                        self._api.close_document(app, title)

        result = self._session.execute(
            operation,
            stage="export_save_as",
            timeout=self._default_timeout,
            mutation=True,
        )
        if result.state is not NativeCallState.SUCCESS or result.value is None:
            errors = [f"native_call_{result.state.value}"]
            if result.failure is not None:
                errors.append(result.failure.code)
            return NativeExportResult(
                completed=False,
                partial=result.state.value == "uncertain_after_dispatch",
                errors=tuple(errors),
                call_id=result.call_id,
            )
        return result.value

    def _export_single_sheet_pdf(
        self, app: Any, model: Any, target: str, sheet_name: str
    ) -> NativeExportResult:
        if int(self._api.document_type(model)) != 3:
            return NativeExportResult(
                completed=False,
                partial=False,
                errors=("single_sheet_pdf_requires_drawing",),
            )
        try:
            if not bool(self._api._member(model, "ActivateSheet", sheet_name)):
                return NativeExportResult(
                    completed=False,
                    partial=False,
                    errors=("drawing_sheet_not_found",),
                )
            sheet = self._api._member(model, "GetCurrentSheet")
            actual_name = str(self._api._member(sheet, "GetName") or "")
            if actual_name != sheet_name:
                return NativeExportResult(
                    completed=False,
                    partial=False,
                    errors=("drawing_sheet_identity_mismatch",),
                )
            export_data = self._api._member(app, "GetExportFileData", 1)
            if export_data is None:
                return NativeExportResult(
                    completed=False,
                    partial=False,
                    errors=("pdf_export_data_unavailable",),
                )
            sheet_names = self._api.string_array((sheet_name,))
            if not bool(self._api._member(export_data, "SetSheets", 3, sheet_names)):
                return NativeExportResult(
                    completed=False,
                    partial=False,
                    errors=("pdf_sheet_selection_failed",),
                )
            export_data.ViewPdfAfterSaving = False
            if int(self._api._member(export_data, "GetWhichSheets")) != 3:
                return NativeExportResult(
                    completed=False,
                    partial=False,
                    errors=("pdf_sheet_mode_readback_mismatch",),
                )
            selected = self._api._member(export_data, "GetSheets")
            selected_sheets = (
                tuple(str(item) for item in selected)
                if isinstance(selected, (tuple, list))
                else (str(selected),) if selected is not None else ()
            )
            if selected_sheets != (sheet_name,):
                return NativeExportResult(
                    completed=False,
                    partial=False,
                    errors=("pdf_sheet_readback_mismatch",),
                )
            errors = self._api._client.VARIANT(
                self._api._pythoncom.VT_BYREF | self._api._pythoncom.VT_I4, 0
            )
            warnings = self._api._client.VARIANT(
                self._api._pythoncom.VT_BYREF | self._api._pythoncom.VT_I4, 0
            )
            value = model.Extension.SaveAs(target, 0, 1, export_data, errors, warnings)
            success = bool(value[0]) if isinstance(value, (tuple, list)) else bool(value)
            if isinstance(value, (tuple, list)):
                if len(value) > 1 and value[1] is not None:
                    errors.value = int(value[1])
                if len(value) > 2 and value[2] is not None:
                    warnings.value = int(value[2])
            if not success or int(errors.value) != 0:
                return NativeExportResult(
                    completed=False,
                    partial=False,
                    errors=(f"pdf_save_as_failed:{int(errors.value)}:{int(warnings.value)}",),
                )
            return NativeExportResult(completed=True, partial=False, errors=())
        except Exception as exc:
            return NativeExportResult(
                completed=False,
                partial=False,
                errors=(f"pdf_export_data_unavailable:{type(exc).__name__}",),
            )

    def _activate_document(self, app: Any, model: Any) -> int:
        title = str(self._api._member(model, "GetTitle") or "")
        if not title:
            return -1
        errors = self._api._client.VARIANT(
            self._api._pythoncom.VT_BYREF | self._api._pythoncom.VT_I4,
            0,
        )
        active = app.ActivateDoc3(title, False, 0, errors)
        if active is None:
            return int(errors.value) or -1
        return int(errors.value)


class SolidWorksImporter:
    """Imports STEP/IGES/Parasolid into a persisted native SOLIDWORKS document."""

    def __init__(
        self,
        session: Any,
        *,
        path_policy: ExportPathPolicy,
        default_timeout: float = 90.0,
    ) -> None:
        self._session = session
        self._api = session.api
        self._path_policy = path_policy
        self._default_timeout = float(default_timeout)

    def import_model(self, request: ImportRequest) -> ImportSnapshot:
        source = self._path_policy.validate_open(request.source_path)
        target = self._path_policy.validate_save(request.target_document_id)

        def operation(app: Any) -> ImportSnapshot:
            errors = self._api._client.VARIANT(
                self._api._pythoncom.VT_BYREF | self._api._pythoncom.VT_I4, 0
            )
            try:
                import_data = app.GetImportFileData(source)
            except Exception:
                import_data = None
            if import_data is None:
                import_data = self._api.null_dispatch()
            model = app.LoadFile4(source, "r", import_data, errors)
            if isinstance(model, (tuple, list)):
                values = list(model)
                model = values[0] if values else None
                if len(values) > 1 and values[1] is not None:
                    errors.value = int(values[1])
            if model is None or int(errors.value) != 0:
                raise ImportPostconditionError(
                    "import_load_failed", f"errors={int(errors.value)}"
                )
            title = str(self._api._member(model, "GetTitle") or "")
            try:
                doc_type = int(self._api.document_type(model))
                if doc_type not in {1, 2}:
                    raise ImportPostconditionError("import_document_type_invalid")
                solid_count = (
                    len(tuple(self._api.bodies(model, 0, False))) if doc_type == 1 else 0
                )
                surface_count = (
                    len(tuple(self._api.bodies(model, 1, False))) if doc_type == 1 else 0
                )
                component_count = (
                    len(tuple(self._api.components(model, False))) if doc_type == 2 else 0
                )
                if solid_count + surface_count + component_count < 1:
                    raise ImportPostconditionError("import_geometry_missing")
                saved, save_errors, save_warnings = self._api.save_as(model, target)
                if not saved or int(save_errors) != 0:
                    raise ImportPostconditionError(
                        "import_save_failed",
                        f"errors={save_errors}, warnings={save_warnings}",
                    )
                native_path = str(self._api.document_path(model) or "")
                if native_path and PureWindowsPath(native_path) != PureWindowsPath(target):
                    raise ImportPostconditionError(
                        "import_target_identity_mismatch", native_path
                    )
                return ImportSnapshot(
                    source_path=request.source_path,
                    target_document_id=request.target_document_id,
                    format=request.format,
                    document_type=doc_type,
                    solid_body_count=solid_count,
                    surface_body_count=surface_count,
                    component_count=component_count,
                )
            finally:
                current_title = title
                try:
                    current_title = str(self._api._member(model, "GetTitle") or title)
                except Exception:
                    pass
                if current_title:
                    self._api.close_document(app, current_title)

        result = self._session.execute(
            operation,
            stage="import_foreign_model",
            timeout=self._default_timeout,
            mutation=True,
        )
        if result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH:
            detail = result.state.value
            if result.failure is not None:
                detail = f"{result.failure.code}@{result.failure.stage}"
            raise ImportPostconditionError("native_state_uncertain", detail)
        if result.state is not NativeCallState.SUCCESS or result.value is None:
            detail = result.state.value
            if result.failure is not None:
                detail = f"{result.failure.code}@{result.failure.stage}"
            raise ImportPostconditionError("native_import_failed", detail)
        return result.value


class SolidWorksGeometryVerifier:
    """Independently re-imports a foreign geometry artifact with LoadFile4.

    SOLIDWORKS documents use OpenDoc6, but official 2024 API guidance requires
    LoadFile4 for foreign STEP/IGES and related translator files.  Verification
    succeeds only when the imported document exposes native bodies/components.
    """

    _SUPPORTED = frozenset(
        {
            ExportFormat.STEP,
            ExportFormat.IGES,
            ExportFormat.PARASOLID,
            ExportFormat.STL,
            ExportFormat.THREE_MF,
        }
    )

    def __init__(
        self,
        session: Any,
        *,
        path_policy: ExportPathPolicy,
        default_timeout: float = 90.0,
        max_features: int = 100_000,
    ) -> None:
        self._session = session
        self._api = session.api
        self._path_policy = path_policy
        self._default_timeout = float(default_timeout)
        self._max_features = int(max_features)

    def __call__(self, request: ExportRequest) -> bool:
        if request.format not in self._SUPPORTED:
            return False
        target = self._path_policy.validate_open(request.target_path)

        def operation(app: Any) -> bool:
            errors = self._api._client.VARIANT(
                self._api._pythoncom.VT_BYREF | self._api._pythoncom.VT_I4,
                0,
            )
            try:
                import_data = app.GetImportFileData(target)
            except Exception:
                import_data = None
            if import_data is None:
                import_data = self._api.null_dispatch()
            model = app.LoadFile4(target, "r", import_data, errors)

            if isinstance(model, (tuple, list)):
                values = list(model)
                model = values[0] if values else None
                if len(values) > 1 and values[1] is not None:
                    errors.value = int(values[1])
            if model is None or int(errors.value) != 0:
                return False

            title = str(self._api._member(model, "GetTitle") or "")
            try:
                doc_type = int(self._api.document_type(model))
                if doc_type == 1:
                    solids = tuple(self._api.bodies(model, 0, False))
                    surfaces = tuple(self._api.bodies(model, 1, False))
                    if solids or surfaces:
                        return True
                    if request.format in {ExportFormat.STL, ExportFormat.THREE_MF}:
                        return self._has_mesh_geometry(model)
                    return False
                if doc_type == 2:
                    return bool(self._api.components(model, False))
                return False
            finally:
                if title:
                    self._api.close_document(app, title)

        result = self._session.execute(
            operation,
            stage="export_geometry_reopen",
            timeout=self._default_timeout,
            mutation=True,
        )
        return result.state is NativeCallState.SUCCESS and bool(result.value)

    def _has_mesh_geometry(self, model: Any) -> bool:
        pending: list[Any] = []
        feature = self._api.first_feature(model)
        queued = 0
        while feature is not None:
            queued += 1
            if queued > self._max_features:
                return False
            pending.append(feature)
            feature = self._api.next_feature(feature)

        visited = 0
        while pending:
            feature = pending.pop()
            visited += 1
            if visited > self._max_features:
                return False
            try:
                body = self._api._member(feature, "GetSpecificFeature2")
            except Exception:
                body = None
            if body is not None:
                try:
                    body_type = int(self._api._member(body, "GetType"))
                except Exception:
                    body_type = -1
                try:
                    if body_type == 7:
                        graphics = self._api._member(body, "GetGraphicsBody")
                        if graphics is not None and int(
                            self._api._member(graphics, "GetFacetCount")
                        ) > 0:
                            return True
                    elif body_type == 6:
                        mesh = self._api._member(body, "GetMeshBody")
                        if mesh is not None and int(
                            self._api._member(mesh, "GetFacetCount")
                        ) > 0:
                            return True
                except Exception:
                    pass

            try:
                child = self._api._member(feature, "GetFirstSubFeature")
            except Exception:
                child = None
            while child is not None:
                queued += 1
                if queued > self._max_features:
                    return False
                pending.append(child)
                try:
                    child = self._api._member(child, "GetNextSubFeature")
                except Exception:
                    child = None
        return False


class GeometryVerifier(Protocol):
    def __call__(self, request: ExportRequest) -> bool: ...


class FileArtifactInspector:
    """Checks bytes/signatures independently from the exporter.

    Geometry formats remain unverified unless a separate parser/reopen verifier is
    injected. This prevents a non-empty file from being treated as geometric proof.
    """

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

    def __init__(self, geometry_verifier: GeometryVerifier | None = None) -> None:
        self._geometry_verifier = geometry_verifier

    def inspect(self, request: ExportRequest) -> ArtifactInspection:
        path = Path(request.target_path)
        try:
            data = path.read_bytes()
        except (OSError, ValueError):
            data = b""
        readable = self._signature_matches(request.format, data)
        geometry_verified = False
        if (
            readable
            and request.format in self._GEOMETRY_FORMATS
            and self._geometry_verifier is not None
        ):
            geometry_verified = bool(self._geometry_verifier(request))
        return ArtifactInspection(
            detected_format=request.format,
            readable=readable,
            geometry_verified=geometry_verified,
            source_document_id=request.source_document_id,
            source_configuration=request.source_configuration,
            drawing_sheet=request.drawing_sheet,
            detected_extension=path.suffix.casefold() or None,
            byte_size=len(data),
        )

    @staticmethod
    def _signature_matches(export_format: ExportFormat, data: bytes) -> bool:
        if not data:
            return False
        prefix = data[:128].lstrip()
        upper = prefix.upper()
        if export_format is ExportFormat.PDF:
            return data.startswith(b"%PDF-")
        if export_format in {ExportFormat.STEP, ExportFormat.STEP_242}:
            return upper.startswith(b"ISO-10303-21")
        if export_format is ExportFormat.THREE_MF:
            return data.startswith(b"PK")
        if export_format is ExportFormat.DWG:
            return data.startswith(b"AC10")
        if export_format is ExportFormat.DXF:
            return b"SECTION" in upper or upper.startswith(b"0")
        if export_format is ExportFormat.IGES:
            return len(data) >= 80 or b"IGES" in upper
        # Parasolid can be text or binary; STL can be ASCII or binary; native
        # SOLIDWORKS files are binary containers. Their independent semantic
        # verification belongs to a parser/reopen verifier rather than a magic byte.
        return True
