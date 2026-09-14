"""Lane-local native body adapter over the accepted serialized SOLIDWORKS session."""

from __future__ import annotations

import math
from pathlib import Path
import uuid
from typing import Any, Iterable

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.errors import NativeRuntimeError, failure_from_exception
from cdt_solidworks.native.models import NativeCallResult
from cdt_solidworks.native.rebuild import rebuild_document

from .models import CombineOperation

_SW_BODY_OPERATION = {
    CombineOperation.ADD: 15903,
    CombineOperation.SUBTRACT: 15902,
    CombineOperation.COMMON: 15901,
}
_PART_EXT = ".sldprt"


class BodyNativeAdapter:
    """Bounded body operations; no caller-supplied COM member names or arbitrary scripts."""

    def __init__(
        self,
        session: Any,
        *,
        path_policy: DocumentPathPolicy | None = None,
        default_timeout: float = 60.0,
        max_features: int = 100_000,
    ) -> None:
        self.session = session
        self.api = session.api
        self.path_policy = path_policy or DocumentPathPolicy()
        self.default_timeout = default_timeout
        self.max_features = max_features

    def inspect(self, path: str | Path, *, timeout: float | None = None) -> NativeCallResult[dict[str, Any]]:
        try:
            source = self._validate_part_path(path)
        except Exception as exc:
            return self._local_failure(exc, "body_inspect")

        def operation(app: Any) -> dict[str, Any]:
            model, owned = self._open_part(app, source)
            try:
                solids = self._body_rows(model, 0)
                surfaces = self._body_rows(model, 1)
                return {"path": source, "solid_bodies": solids, "surface_bodies": surfaces}
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(operation, stage="body_inspect", timeout=self._timeout(timeout))

    def combine(
        self,
        path: str | Path,
        *,
        operation: CombineOperation | str,
        body_names: Iterable[str],
        main_body_name: str | None = None,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        try:
            source = self._validate_part_path(path)
            op = CombineOperation(operation)
            names = tuple(str(name).strip() for name in body_names)
            if len(names) < 2 or len(set(names)) != len(names) or any(not name for name in names):
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "body_combine",
                    "Combine requires at least two unique non-empty solid-body names.",
                )
            if op is CombineOperation.SUBTRACT:
                main = str(main_body_name or "").strip()
                if not main or main not in names:
                    raise NativeRuntimeError(
                        "cad_validation_error",
                        "body_combine",
                        "Subtract combine requires main_body_name included in body_names.",
                    )
            elif main_body_name is not None:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "body_combine",
                    "main_body_name is only valid for subtract combine.",
                )
            else:
                main = None
        except Exception as exc:
            return self._local_failure(exc, "body_combine")

        def operation_fn(app: Any) -> dict[str, Any]:
            model, owned = self._open_part(app, source)
            try:
                before = tuple(self.api.bodies(model, 0, False))
                by_name = {self._body_name(body): body for body in before}
                missing = [name for name in names if name not in by_name]
                if missing:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "body_combine",
                        "Requested solid-body identity is not present in the part.",
                        details={"missing_body": missing[0]},
                    )
                self.api._member(model, "ClearSelection2", True)
                selection_manager = self.api._member(model, "SelectionManager")
                for name in names:
                    mark = 1 if op is CombineOperation.SUBTRACT and name == main else 2
                    select_data = self.api._member(selection_manager, "CreateSelectData")
                    select_data.Mark = mark
                    if not bool(self.api._member(by_name[name], "Select2", True, select_data)):
                        raise NativeRuntimeError(
                            "cad_selection_failed",
                            "body_combine",
                            "A requested body could not be selected for Combine.",
                            details={"body": name},
                        )
                manager = self.api._member(model, "FeatureManager")
                feature = self.api._member(
                    manager,
                    "InsertCombineFeature",
                    _SW_BODY_OPERATION[op],
                    self.api.null_dispatch(),
                    self.api.empty_variant_array(),
                )
                if feature is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "body_combine",
                        "SOLIDWORKS did not create the Combine feature.",
                    )
                self._require_clean_rebuild(model, "body_combine")
                after = tuple(self.api.bodies(model, 0, False))
                expected = len(before) - (len(names) - 1)
                if len(after) != expected:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "body_combine",
                        "Combine solid-body count read-back does not match the requested operation.",
                        details={"before": len(before), "after": len(after), "expected": expected},
                    )
                self._save(model, "body_combine")
                return {
                    "path": source,
                    "operation": op.value,
                    "feature_name": self.api.feature_name(feature),
                    "body_count_before": len(before),
                    "body_count_after": len(after),
                    "body_names_after": [self._body_name(body) for body in after],
                }
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation_fn,
            stage="body_combine",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def _validate_part_path(self, path: str | Path) -> str:
        source = self.path_policy.validate_open(path)
        if Path(source).suffix.lower() != _PART_EXT:
            raise NativeRuntimeError(
                "document_extension_mismatch",
                "path_validation",
                "Body operations require a native .sldprt document.",
            )
        return source

    def _open_part(self, app: Any, source: str) -> tuple[Any, bool]:
        existing = self.api.get_open_document(app, source)
        if existing is not None:
            return existing, False
        model, errors, warnings = self.api.open_document(
            app, source, 1, read_only=False, silent=True, configuration=""
        )
        if model is None or int(errors) != 0:
            raise NativeRuntimeError(
                "document_open_failed",
                "fabrication_open",
                "SOLIDWORKS failed to open the fabrication part.",
                details={"errors": int(errors), "warnings": int(warnings)},
            )
        return model, True

    def _body_rows(self, model: Any, body_type: int) -> list[dict[str, Any]]:
        kind = "solid" if body_type == 0 else "surface"
        rows = []
        for body in self.api.bodies(model, body_type, False):
            rows.append(
                {
                    "name": self._body_name(body),
                    "kind": kind,
                    "visible": bool(self.api._member(body, "Visible")),
                }
            )
        return rows

    def _body_name(self, body: Any) -> str:
        return str(self.api._member(body, "Name") or "").strip()

    def _require_clean_rebuild(self, model: Any, stage: str) -> None:
        result = rebuild_document(model, self.api, max_features=self.max_features)
        if not result.success:
            raise NativeRuntimeError(
                "rebuild_failed",
                stage,
                "SOLIDWORKS rebuild or feature-error verification failed.",
                details={
                    "native_rebuild_ok": result.native_rebuild_ok,
                    "feature_error_count": sum(not issue.is_warning for issue in result.feature_issues),
                },
            )

    def _save(self, model: Any, stage: str) -> None:
        success, errors, warnings = self.api.save_document(model)
        if not success or int(errors) != 0:
            raise NativeRuntimeError(
                "document_save_failed",
                stage,
                "SOLIDWORKS failed to save the mutated fabrication part.",
                details={"errors": int(errors), "warnings": int(warnings)},
            )

    def _close_quietly(self, app: Any, model: Any) -> None:
        try:
            self.api.close_document(app, self.api.document_title(model))
        except Exception:
            pass

    def _timeout(self, timeout: float | None) -> float:
        if timeout is None:
            return self.default_timeout
        numeric = float(timeout)
        return numeric if math.isfinite(numeric) and numeric >= 0 else 0.0

    @staticmethod
    def _local_failure(exc: Exception, stage: str) -> NativeCallResult[Any]:
        return NativeCallResult.failed(
            failure_from_exception(exc, stage),
            call_id=uuid.uuid4().hex,
            dispatched=False,
        )
