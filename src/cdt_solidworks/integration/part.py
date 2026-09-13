"""Part Integration — Evidence-gated provider binding for parametric features.
Wing: Mechanical 90 | Topic: parametric-part | Updated: 2026-09-13
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any
import uuid

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.errors import NativeRuntimeError, failure_from_exception
from cdt_solidworks.native.models import NativeCallResult, NativeCallState, NativeFailure
from cdt_solidworks.native.rebuild import rebuild_document
from cdt_solidworks.part.models import CutSpec, ProfileRef
from cdt_solidworks.part.native import NativePartBinding, PartNativeRuntime
from cdt_solidworks.part.runtime import DocumentTarget, RebuildResult
from cdt_solidworks.part.service import (
    PartContextError,
    PartMutationError,
    PartService,
    PartValidationError,
)

_PART_EXT = ".sldprt"
_LENGTH_UNITS = {0: "mm", 1: "cm", 2: "m", 3: "in", 4: "ft", 5: "ft-in", 6: "angstrom", 7: "nm", 8: "micron", 9: "mil", 10: "uin"}
_MAX_FEATURES = 100_000


class _NativeResultInterrupt(RuntimeError):
    def __init__(self, result: NativeCallResult[Any]) -> None:
        self.result = result
        super().__init__(result.state.value)


class IntegratedPartFeatureService:
    """Bind only native-accepted parametric feature operations to PartService."""

    def __init__(
        self,
        session: Any | None = None,
        *,
        path_policy: DocumentPathPolicy,
        service: Any | None = None,
        default_timeout: float = 60.0,
    ) -> None:
        self.session = session
        self.path_policy = path_policy
        self.default_timeout = float(default_timeout)
        self.api = None if session is None else session.api
        if service is None:
            if session is None:
                raise ValueError("session is required when part service is not injected")
            runtime = PartNativeRuntime(
                executor=self._execute,
                binding_resolver=self._resolve_binding,
                member=self.api._member,
                feature_name=self.api.feature_name,
                feature_type=self.api.feature_type,
                bodies=self.api.bodies,
                body_name=self.api.body_name,
                rebuild_verifier=self._verify_rebuild,
            )
            service = PartService(runtime)
        self.service = service

    def cut_extrude(
        self,
        *,
        path: str,
        expected_revision: int,
        sketch_id: str,
        name: str,
        through_all: bool,
        depth_mm: float | None = None,
    ) -> NativeCallResult[Any]:
        call_id = uuid.uuid4().hex
        try:
            target = self._target(path, expected_revision)
            if not isinstance(through_all, bool):
                raise PartValidationError("through_all must be boolean")
            if not isinstance(sketch_id, str) or not sketch_id.strip():
                raise PartValidationError("sketch_id must be a non-empty string")
            if not isinstance(name, str) or not name.strip():
                raise PartValidationError("name must be a non-empty string")
            if through_all:
                if depth_mm is not None:
                    raise PartValidationError("through-all cut must not specify depth_mm")
            else:
                if isinstance(depth_mm, bool) or not isinstance(depth_mm, (int, float)):
                    raise PartValidationError("blind cut requires numeric depth_mm")
                depth_mm = float(depth_mm)
                if not math.isfinite(depth_mm) or depth_mm <= 0:
                    raise PartValidationError("blind cut depth_mm must be positive and finite")
            value = self.service.cut(
                target,
                CutSpec(
                    name=name,
                    profile=ProfileRef(sketch_id),
                    through_all=through_all,
                    depth_mm=depth_mm,
                ),
            )
            return NativeCallResult.success(value, call_id=call_id, dispatched=True)
        except _NativeResultInterrupt as exc:
            return exc.result
        except Exception as exc:
            return NativeCallResult.failed(
                self._semantic_failure(exc, "part_cut_extrude"),
                call_id=call_id,
                dispatched=isinstance(exc, PartMutationError),
            )

    def _target(self, path: str, expected_revision: int) -> DocumentTarget:
        source = self.path_policy.validate_open(path)
        if Path(source).suffix.lower() != _PART_EXT:
            raise PartValidationError("parametric part feature requires a native .SLDPRT document")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise PartValidationError("expected_revision must be a non-negative integer")
        return DocumentTarget(source, expected_revision, "mm")

    def _execute(self, operation: Any, *, stage: str, mutation: bool) -> Any:
        assert self.session is not None
        result = self.session.execute(
            operation,
            stage=stage,
            timeout=self.default_timeout,
            mutation=mutation,
        )
        if result.state is NativeCallState.SUCCESS:
            return result.value
        raise _NativeResultInterrupt(result)

    def _resolve_binding(self, app: Any, target: DocumentTarget) -> NativePartBinding:
        assert self.api is not None
        source = self.path_policy.validate_open(target.document_id)
        model = self.api.get_open_document(app, source)
        if model is None:
            raise NativeRuntimeError(
                "document_not_open",
                "part_resolve_document",
                "Parametric feature operations require the target part to be opened explicitly first.",
            )
        if int(self.api.document_type(model)) != 1:
            raise NativeRuntimeError("document_type_mismatch", "part_resolve_document", "Parametric feature operation requires a part document.")
        actual_path = self.path_policy.canonical(self.api.document_path(model) or source)
        if actual_path != source:
            raise NativeRuntimeError("document_context_mismatch", "part_resolve_document", "Open document identity does not match the requested part path.")
        revision = self.api.update_stamp(model)
        if revision is None:
            raise NativeRuntimeError("document_context_mismatch", "part_resolve_document", "SOLIDWORKS did not provide a document revision stamp.")
        units = _LENGTH_UNITS.get(int(self.api._member(model, "LengthUnit")), "unknown")
        return NativePartBinding(
            model=model,
            document_id=source,
            revision=int(revision),
            units=units,
            configuration=self.api.active_configuration(model),
        )

    def _verify_rebuild(self, model: Any) -> RebuildResult:
        assert self.api is not None
        result = rebuild_document(model, self.api, max_features=_MAX_FEATURES)
        if result.success:
            return RebuildResult(ok=True)
        return RebuildResult(
            ok=False,
            error_code="rebuild_failed",
            message=f"native_rebuild_ok={result.native_rebuild_ok}; feature_errors={sum(not issue.is_warning for issue in result.feature_issues)}",
        )

    @staticmethod
    def _semantic_failure(exc: Exception, stage: str) -> NativeFailure:
        if isinstance(exc, PartValidationError):
            return NativeFailure("cad_validation_error", stage, str(exc), retryable=False)
        if isinstance(exc, PartContextError):
            return NativeFailure("document_context_mismatch", stage, str(exc), retryable=False)
        if isinstance(exc, PartMutationError):
            return NativeFailure("cad_postcondition_failed", stage, str(exc), retryable=False)
        return failure_from_exception(exc, stage)
