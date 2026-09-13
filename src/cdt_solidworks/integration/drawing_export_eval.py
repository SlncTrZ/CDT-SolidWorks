"""Evidence-gated integration for drawing, export, and engineering evaluation."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable, TypeVar
import uuid

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.drawing.domain import (
    DrawingPostconditionError,
    DrawingRefusal,
    DrawingService,
)
from cdt_solidworks.drawing.native import SolidWorksDrawingAdapter
from cdt_solidworks.evaluation.domain import (
    EvaluationPostconditionError,
    EvaluationRefusal,
    EvaluationService,
)
from cdt_solidworks.evaluation.native import SolidWorksEvaluationAdapter
from cdt_solidworks.export.domain import (
    ExportFormat,
    ExportPostconditionError,
    ExportRefusal,
    ExportRequest,
    ExportService,
)
from cdt_solidworks.export.native import (
    FileArtifactInspector,
    SolidWorksExporter,
    SolidWorksGeometryVerifier,
)
from cdt_solidworks.native.errors import NativeRuntimeError, failure_from_exception
from cdt_solidworks.native.models import NativeCallResult, NativeCallState, NativeFailure


T = TypeVar("T")
_CALL_ID_PATTERN = re.compile(r"(?:^|\s)call_id=([^;\s]+)")
_PART_EXTENSIONS = frozenset({".sldprt"})
_DRAWING_EXTENSIONS = frozenset({".slddrw"})
_GEOMETRY_EXPORTS = {
    "step": ExportFormat.STEP,
    "iges": ExportFormat.IGES,
    "parasolid": ExportFormat.PARASOLID,
    "stl": ExportFormat.STL,
    "3mf": ExportFormat.THREE_MF,
}
_DRAWING_EXPORTS = {
    "pdf": ExportFormat.PDF,
    "dxf": ExportFormat.DXF,
    "dwg": ExportFormat.DWG,
}
_PROMOTED_EXPORTS = {**_GEOMETRY_EXPORTS, **_DRAWING_EXPORTS}
_EXPORT_EXTENSIONS = {
    "step": frozenset({".step", ".stp"}),
    "iges": frozenset({".iges", ".igs"}),
    "parasolid": frozenset({".x_t", ".x_b"}),
    "stl": frozenset({".stl"}),
    "3mf": frozenset({".3mf"}),
    "pdf": frozenset({".pdf"}),
    "dxf": frozenset({".dxf"}),
    "dwg": frozenset({".dwg"}),
}


def _local_failure(stage: str, message: str, *, code: str = "cad_validation_error") -> NativeCallResult[Any]:
    return NativeCallResult.failed(
        NativeFailure(code=code, stage=stage, message=message, retryable=False),
        call_id=uuid.uuid4().hex,
        dispatched=False,
    )


def _uncertain(stage: str, call_id: str, message: str) -> NativeCallResult[Any]:
    return NativeCallResult(
        state=NativeCallState.UNCERTAIN_AFTER_DISPATCH,
        call_id=call_id,
        failure=NativeFailure(
            code="native_state_uncertain",
            stage=stage,
            message=message,
            retryable=False,
        ),
        dispatched=True,
    )


class _PathGate:
    def __init__(self, policy: DocumentPathPolicy) -> None:
        self._policy = policy

    def allows(self, target_path: str) -> bool:
        try:
            self._policy.validate_save(target_path)
        except Exception:
            return False
        return True


class IntegratedDrawingService:
    """Expose only drawing operations with direct native acceptance evidence."""

    def __init__(
        self,
        session: Any | None = None,
        *,
        path_policy: DocumentPathPolicy,
        service: Any | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.path_policy = path_policy
        if service is None:
            if session is None:
                raise ValueError("session is required when drawing service is not injected")
            service = DrawingService(
                SolidWorksDrawingAdapter(
                    session,
                    path_policy=path_policy,
                    default_timeout=timeout,
                )
            )
        self.service = service

    def create(self, output_path: str) -> NativeCallResult[Any]:
        stage = "drawing_create"
        try:
            target = self.path_policy.validate_save(output_path)
            if Path(target).suffix.lower() not in _DRAWING_EXTENSIONS:
                raise NativeRuntimeError(
                    "document_extension_mismatch",
                    stage,
                    "Drawing output must use the .SLDDRW extension.",
                )
            if Path(target).exists():
                raise NativeRuntimeError(
                    "document_already_exists",
                    stage,
                    "Drawing creation refuses to overwrite an existing file.",
                )
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=uuid.uuid4().hex,
                dispatched=False,
            )
        return self._call(
            stage,
            lambda: self.service.create_drawing(target, None),
        )

    def create_sheet(self, path: str, sheet_name: str) -> NativeCallResult[Any]:
        stage = "drawing_sheet_create"
        target = self._validate_open(path, stage, _DRAWING_EXTENSIONS)
        if isinstance(target, NativeCallResult):
            return target
        if not isinstance(sheet_name, str) or not sheet_name.strip():
            return _local_failure(stage, "sheet_name must be a non-empty string.")
        return self._call(stage, lambda: self.service.create_sheet(target, sheet_name))

    def create_front_view(
        self,
        path: str,
        sheet_name: str,
        source_part_path: str,
    ) -> NativeCallResult[Any]:
        stage = "drawing_front_view_create"
        drawing = self._validate_open(path, stage, _DRAWING_EXTENSIONS)
        if isinstance(drawing, NativeCallResult):
            return drawing
        source = self._validate_open(source_part_path, stage, _PART_EXTENSIONS)
        if isinstance(source, NativeCallResult):
            return source
        if not isinstance(sheet_name, str) or not sheet_name.strip():
            return _local_failure(stage, "sheet_name must be a non-empty string.")
        return self._call(
            stage,
            lambda: self.service.create_view(
                drawing,
                sheet_name,
                "front",
                source,
                None,
            ),
        )

    def _validate_open(
        self,
        path: str,
        stage: str,
        extensions: frozenset[str],
    ) -> str | NativeCallResult[Any]:
        try:
            target = self.path_policy.validate_open(path)
            if Path(target).suffix.lower() not in extensions:
                raise NativeRuntimeError(
                    "document_type_mismatch",
                    stage,
                    "The requested drawing operation does not support this document type.",
                )
            return target
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=uuid.uuid4().hex,
                dispatched=False,
            )

    def _call(self, stage: str, operation: Callable[[], T]) -> NativeCallResult[T]:
        call_id = uuid.uuid4().hex
        try:
            value = operation()
        except DrawingPostconditionError as exc:
            if exc.reason == "native_state_uncertain":
                detail = exc.detail or str(exc)
                match = _CALL_ID_PATTERN.search(detail)
                return _uncertain(
                    stage,
                    match.group(1) if match is not None else call_id,
                    str(exc),
                )
            return NativeCallResult.failed(
                NativeFailure(exc.reason, stage, str(exc), False),
                call_id=call_id,
                dispatched=True,
            )
        except DrawingRefusal as exc:
            return NativeCallResult.failed(
                NativeFailure(exc.reason, stage, str(exc), False),
                call_id=call_id,
                dispatched=True,
            )
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=call_id,
                dispatched=True,
            )
        return NativeCallResult.success(value, call_id=call_id, dispatched=True)


class IntegratedExportService:
    """Expose only native-accepted source/format export combinations."""

    def __init__(
        self,
        session: Any | None = None,
        *,
        path_policy: DocumentPathPolicy,
        service: Any | None = None,
        timeout: float = 90.0,
    ) -> None:
        self.path_policy = path_policy
        if service is None:
            if session is None:
                raise ValueError("session is required when export service is not injected")
            exporter = SolidWorksExporter(
                session,
                path_policy=path_policy,
                default_timeout=timeout,
            )
            verifier = SolidWorksGeometryVerifier(
                session,
                path_policy=path_policy,
                default_timeout=timeout,
            )
            service = ExportService(
                exporter,
                FileArtifactInspector(geometry_verifier=verifier),
                _PathGate(path_policy),
            )
        self.service = service

    def export(
        self,
        source_path: str,
        target_path: str,
        format: str,
        *,
        source_configuration: str | None = None,
    ) -> NativeCallResult[Any]:
        stage = "export_document"
        normalized = str(format).strip().lower()
        export_format = _PROMOTED_EXPORTS.get(normalized)
        if export_format is None:
            return _local_failure(
                stage,
                "format must be step, iges, parasolid, stl, 3mf, pdf, dxf, or dwg.",
            )
        expected_source = (
            _PART_EXTENSIONS if normalized in _GEOMETRY_EXPORTS else _DRAWING_EXTENSIONS
        )
        try:
            source = self.path_policy.validate_open(source_path)
            if Path(source).suffix.lower() not in expected_source:
                raise NativeRuntimeError(
                    "document_type_mismatch",
                    stage,
                    "This export format is not native-accepted for the supplied source document type.",
                )
            target = self.path_policy.validate_save(target_path)
            if Path(target).suffix.lower() not in _EXPORT_EXTENSIONS[normalized]:
                raise NativeRuntimeError(
                    "format_extension_mismatch",
                    stage,
                    "Export target extension does not match the requested format.",
                )
            if Path(target).exists():
                raise NativeRuntimeError(
                    "document_already_exists",
                    stage,
                    "Export refuses to overwrite an existing artifact.",
                )
            if source_configuration is not None and (
                not isinstance(source_configuration, str) or not source_configuration.strip()
            ):
                raise NativeRuntimeError(
                    "cad_validation_error",
                    stage,
                    "source_configuration must be a non-empty string when supplied.",
                )
            request = ExportRequest(
                source_document_id=source,
                target_path=target,
                format=export_format,
                source_configuration=source_configuration,
                drawing_sheet=None,
            )
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=uuid.uuid4().hex,
                dispatched=False,
            )

        call_id = uuid.uuid4().hex
        try:
            value = self.service.export(request)
        except ExportPostconditionError as exc:
            if exc.reason == "native_state_uncertain":
                return _uncertain(stage, exc.call_id or call_id, str(exc))
            return NativeCallResult.failed(
                NativeFailure(exc.reason, stage, str(exc), False),
                call_id=call_id,
                dispatched=True,
            )
        except ExportRefusal as exc:
            return NativeCallResult.failed(
                NativeFailure(exc.reason, stage, str(exc), False),
                call_id=call_id,
                dispatched=True,
            )
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=call_id,
                dispatched=True,
            )
        return NativeCallResult.success(value, call_id=call_id, dispatched=True)


class IntegratedEvaluationService:
    """Expose accepted read-only engineering evaluation on native parts."""

    def __init__(
        self,
        session: Any | None = None,
        *,
        path_policy: DocumentPathPolicy,
        service: Any | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.path_policy = path_policy
        if service is None:
            if session is None:
                raise ValueError("session is required when evaluation service is not injected")
            service = EvaluationService(
                SolidWorksEvaluationAdapter(
                    session,
                    path_policy=path_policy,
                    default_timeout=timeout,
                )
            )
        self.service = service

    def mass_properties(
        self, path: str, configuration: str | None = None
    ) -> NativeCallResult[Any]:
        return self._evaluate(
            "evaluation_mass_properties",
            path,
            lambda target: self.service.mass_properties(target, configuration),
        )

    def bounding_box(
        self, path: str, configuration: str | None = None
    ) -> NativeCallResult[Any]:
        return self._evaluate(
            "evaluation_bounding_box",
            path,
            lambda target: self.service.bounding_box(target, configuration),
        )

    def geometry_sanity(
        self, path: str, configuration: str | None = None
    ) -> NativeCallResult[Any]:
        return self._evaluate(
            "evaluation_geometry_sanity",
            path,
            lambda target: self.service.require_clean_geometry(target, configuration),
        )

    def _evaluate(
        self,
        stage: str,
        path: str,
        operation: Callable[[str], T],
    ) -> NativeCallResult[T]:
        call_id = uuid.uuid4().hex
        try:
            target = self.path_policy.validate_open(path)
            if Path(target).suffix.lower() not in _PART_EXTENSIONS:
                raise NativeRuntimeError(
                    "document_type_mismatch",
                    stage,
                    "Public evaluation is native-accepted for part documents only.",
                )
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=call_id,
                dispatched=False,
            )
        try:
            value = operation(target)
        except EvaluationPostconditionError as exc:
            return NativeCallResult.failed(
                NativeFailure(exc.reason, stage, str(exc), False),
                call_id=call_id,
                dispatched=True,
            )
        except EvaluationRefusal as exc:
            return NativeCallResult.failed(
                NativeFailure(exc.reason, stage, str(exc), False),
                call_id=call_id,
                dispatched=True,
            )
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=call_id,
                dispatched=True,
            )
        return NativeCallResult.success(value, call_id=call_id, dispatched=True)
