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

    def move_copy(
        self,
        path: str | Path,
        *,
        body_names: Iterable[str],
        translation_mm: tuple[float, float, float],
        copy: bool = False,
        copies: int = 1,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        """Move or copy selected solid bodies using a bounded translation-only contract."""
        try:
            source = self._validate_part_path(path)
            names = tuple(str(name).strip() for name in body_names)
            if not names or len(set(names)) != len(names) or any(not name for name in names):
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "body_move_copy",
                    "Move/copy requires unique non-empty solid-body names.",
                )
            if len(translation_mm) != 3:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "body_move_copy",
                    "translation_mm must contain exactly three components.",
                )
            translation = tuple(float(value) for value in translation_mm)
            if any(not math.isfinite(value) for value in translation) or all(
                abs(value) <= 1e-12 for value in translation
            ):
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "body_move_copy",
                    "translation_mm must be finite and non-zero.",
                )
            copy_count = int(copies)
            if bool(copy):
                if copy_count < 1 or copy_count > 100:
                    raise NativeRuntimeError(
                        "cad_validation_error",
                        "body_move_copy",
                        "copies must be in the range 1..100 for copy operations.",
                    )
            elif copy_count != 1:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "body_move_copy",
                    "copies must equal 1 for move operations.",
                )
        except Exception as exc:
            return self._local_failure(exc, "body_move_copy")

        def operation_fn(app: Any) -> dict[str, Any]:
            model, owned = self._open_part(app, source)
            try:
                before = tuple(self.api.bodies(model, 0, False))
                by_name = {self._body_name(body): body for body in before}
                missing = [name for name in names if name not in by_name]
                if missing:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "body_move_copy",
                        "Requested solid-body identity is not present in the part.",
                        details={"missing_body": missing[0]},
                    )
                before_boxes = {name: self._body_box(by_name[name]) for name in names}
                self._select_bodies(model, tuple(by_name[name] for name in names), "body_move_copy")
                manager = self.api._member(model, "FeatureManager")
                dx_m, dy_m, dz_m = (value / 1000.0 for value in translation)
                feature = self.api._member(
                    manager,
                    "InsertMoveCopyBody2",
                    dx_m,
                    dy_m,
                    dz_m,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    bool(copy),
                    copy_count,
                )
                if feature is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "body_move_copy",
                        "SOLIDWORKS did not create the Move/Copy Body feature.",
                    )
                self._require_clean_rebuild(model, "body_move_copy")
                after = tuple(self.api.bodies(model, 0, False))
                expected_count = len(before) + (len(names) * copy_count if copy else 0)
                if len(after) != expected_count:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "body_move_copy",
                        "Move/copy body-count read-back does not match the requested operation.",
                        details={"before": len(before), "after": len(after), "expected": expected_count},
                    )
                after_boxes = tuple(self._body_box(body) for body in after)
                delta = (dx_m, dy_m, dz_m)
                for name in names:
                    original = before_boxes[name]
                    translated = self._translated_box(original, delta)
                    if copy and not any(self._boxes_close(box, original) for box in after_boxes):
                        raise NativeRuntimeError(
                            "cad_postcondition_failed",
                            "body_move_copy",
                            "Copy operation did not preserve the source body geometry.",
                            details={"body": name},
                        )
                    if not any(self._boxes_close(box, translated) for box in after_boxes):
                        raise NativeRuntimeError(
                            "cad_postcondition_failed",
                            "body_move_copy",
                            "Move/copy bounding-box read-back does not match the requested translation.",
                            details={"body": name},
                        )
                self._save(model, "body_move_copy")
                return {
                    "path": source,
                    "feature_name": self.api.feature_name(feature),
                    "copy": bool(copy),
                    "copies": copy_count,
                    "translation_mm": translation,
                    "body_count_before": len(before),
                    "body_count_after": len(after),
                    "body_names_after": [self._body_name(body) for body in after],
                }
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation_fn,
            stage="body_move_copy",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def delete_keep(
        self,
        path: str | Path,
        *,
        body_names: Iterable[str],
        keep: bool,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        """Create a Body-Delete/Keep feature for explicit solid-body identities."""
        try:
            source = self._validate_part_path(path)
            names = tuple(str(name).strip() for name in body_names)
            if not names or len(set(names)) != len(names) or any(not name for name in names):
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "body_delete_keep",
                    "Body-Delete/Keep requires unique non-empty solid-body names.",
                )
        except Exception as exc:
            return self._local_failure(exc, "body_delete_keep")

        def operation_fn(app: Any) -> dict[str, Any]:
            model, owned = self._open_part(app, source)
            try:
                before = tuple(self.api.bodies(model, 0, False))
                by_name = {self._body_name(body): body for body in before}
                missing = [name for name in names if name not in by_name]
                if missing:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "body_delete_keep",
                        "Requested solid-body identity is not present in the part.",
                        details={"missing_body": missing[0]},
                    )
                self._select_bodies(model, tuple(by_name[name] for name in names), "body_delete_keep")
                manager = self.api._member(model, "FeatureManager")
                feature = self.api._member(manager, "InsertDeleteBody2", bool(keep))
                if feature is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "body_delete_keep",
                        "SOLIDWORKS did not create the Body-Delete/Keep feature.",
                    )
                self._require_clean_rebuild(model, "body_delete_keep")
                after = tuple(self.api.bodies(model, 0, False))
                expected_count = len(names) if keep else len(before) - len(names)
                if len(after) != expected_count:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "body_delete_keep",
                        "Body-Delete/Keep count read-back does not match the requested operation.",
                        details={"before": len(before), "after": len(after), "expected": expected_count},
                    )
                self._save(model, "body_delete_keep")
                return {
                    "path": source,
                    "feature_name": self.api.feature_name(feature),
                    "keep": bool(keep),
                    "body_count_before": len(before),
                    "body_count_after": len(after),
                    "body_names_after": [self._body_name(body) for body in after],
                }
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation_fn,
            stage="body_delete_keep",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def _select_bodies(self, model: Any, bodies: tuple[Any, ...], stage: str) -> None:
        self.api._member(model, "ClearSelection2", True)
        selection_manager = self.api._member(model, "SelectionManager")
        for body in bodies:
            select_data = self.api._member(selection_manager, "CreateSelectData")
            select_data.Mark = 1
            if not bool(self.api._member(body, "Select2", True, select_data)):
                raise NativeRuntimeError(
                    "cad_selection_failed",
                    stage,
                    "A requested body could not be selected.",
                    details={"body": self._body_name(body)},
                )

    def _body_box(self, body: Any) -> tuple[float, float, float, float, float, float]:
        raw = self.api._member(body, "GetBodyBox")
        values = tuple(float(value) for value in (raw or ()))
        if len(values) != 6 or any(not math.isfinite(value) for value in values):
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "body_geometry_readback",
                "SOLIDWORKS did not return a finite six-value body bounding box.",
            )
        return values  # type: ignore[return-value]

    @staticmethod
    def _translated_box(
        box: tuple[float, float, float, float, float, float],
        delta: tuple[float, float, float],
    ) -> tuple[float, float, float, float, float, float]:
        dx, dy, dz = delta
        return (
            box[0] + dx,
            box[1] + dy,
            box[2] + dz,
            box[3] + dx,
            box[4] + dy,
            box[5] + dz,
        )

    @staticmethod
    def _boxes_close(
        left: tuple[float, float, float, float, float, float],
        right: tuple[float, float, float, float, float, float],
        *,
        tolerance_m: float = 1e-7,
    ) -> bool:
        return all(abs(a - b) <= tolerance_m for a, b in zip(left, right))

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
