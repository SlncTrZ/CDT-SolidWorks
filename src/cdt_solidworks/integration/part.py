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
from cdt_solidworks.part.models import CutSpec, FeatureKind, FeatureSnapshot, ProfileRef
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
_SW_SEL_DATUM_PLANES = 4
_STANDARD_REFERENCE_PLANE_COUNT = 3
_TRANSFORM_TOLERANCE = 1e-9


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (tuple, list)):
        return tuple(value)
    return (value,)


def _reference_transform(api: Any, reference: Any) -> tuple[float, ...]:
    transform = api._member(reference, "Transform")
    if transform is None:
        return ()
    values = _as_tuple(api._member(transform, "ArrayData"))
    return tuple(float(value) for value in values)


def _transforms_close(first: tuple[float, ...], second: tuple[float, ...]) -> bool:
    if len(first) != 16 or len(second) != 16:
        return False
    return all(
        abs(a - b) <= _TRANSFORM_TOLERANCE
        for a, b in zip(first, second, strict=True)
    )


def _require_standard_reference_plane(
    api: Any, model: Any, profile: Any, sketch_id: str
) -> None:
    """Fail closed unless the cut profile is on Front/Top/Right reference planes."""
    sketch = api._member(profile, "GetSpecificFeature2")
    if sketch is None:
        raise NativeRuntimeError(
            "cad_precondition_failed",
            "part_cut_context",
            f"Sketch {sketch_id!r} did not expose a native sketch object.",
        )
    reference, entity_type = api.sketch_reference_entity(sketch)
    if reference is None:
        raise NativeRuntimeError(
            "cad_precondition_failed",
            "part_cut_context",
            f"Sketch {sketch_id!r} has no stable native reference entity.",
        )
    if int(entity_type) != _SW_SEL_DATUM_PLANES:
        raise NativeRuntimeError(
            "cad_precondition_failed",
            "part_cut_context",
            "Cut Extrude currently rejects face-backed sketches; use a standard reference plane sketch.",
        )

    reference_transform = _reference_transform(api, reference)
    if len(reference_transform) != 16:
        raise NativeRuntimeError(
            "cad_precondition_failed",
            "part_cut_context",
            "Cut Extrude could not read the sketch reference-plane transform.",
        )

    feature = api.first_feature(model)
    ref_index = 0
    while feature is not None:
        try:
            feature_type = api.feature_type(feature)
        except Exception:
            feature_type = ""
        if feature_type == "RefPlane":
            specific = api._member(feature, "GetSpecificFeature2")
            if ref_index < _STANDARD_REFERENCE_PLANE_COUNT and specific is not None:
                try:
                    candidate = _reference_transform(api, specific)
                except Exception:
                    candidate = ()
                if _transforms_close(reference_transform, candidate):
                    return
            ref_index += 1
        feature = api.next_feature(feature)

    raise NativeRuntimeError(
        "cad_precondition_failed",
        "part_cut_context",
        "Cut Extrude currently accepts only sketches on a standard reference plane.",
    )


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
                cut_profile_validator=lambda model, profile, sketch_id: (
                    _require_standard_reference_plane(
                        self.api, model, profile, sketch_id
                    )
                ),
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

    def reconcile_cut(
        self,
        *,
        call_id: str,
        path: str,
        name: str,
        through_all: bool,
        depth_mm: float | None = None,
        timeout: float | None = None,
    ) -> NativeCallResult[FeatureSnapshot]:
        """Verify an uncertain Cut mutation and clear dispatcher quarantine on success."""
        local_call_id = uuid.uuid4().hex
        try:
            if self.session is None or self.api is None:
                raise PartValidationError(
                    "cut reconciliation requires a bound native session"
                )
            if not isinstance(call_id, str) or not call_id.strip():
                raise PartValidationError("call_id must be a non-empty string")
            source = self.path_policy.validate_open(path)
            if Path(source).suffix.lower() != _PART_EXT:
                raise PartValidationError(
                    "cut reconciliation requires a native .SLDPRT document"
                )
            if not isinstance(name, str) or not name.strip():
                raise PartValidationError("name must be a non-empty string")
            if not isinstance(through_all, bool):
                raise PartValidationError("through_all must be boolean")
            if through_all:
                if depth_mm is not None:
                    raise PartValidationError(
                        "through-all cut reconciliation must not specify depth_mm"
                    )
            else:
                if isinstance(depth_mm, bool) or not isinstance(depth_mm, (int, float)):
                    raise PartValidationError(
                        "blind cut reconciliation requires numeric depth_mm"
                    )
                depth_mm = float(depth_mm)
                if not math.isfinite(depth_mm) or depth_mm <= 0:
                    raise PartValidationError(
                        "blind cut reconciliation depth_mm must be positive and finite"
                    )
        except Exception as exc:
            return NativeCallResult.failed(
                self._semantic_failure(exc, "part_cut_reconcile"),
                call_id=local_call_id,
                dispatched=False,
            )

        def verifier(app: Any) -> FeatureSnapshot:
            model = self.api.get_open_document(app, source)
            if model is None:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_cut_reconcile",
                    "Target part is not open while reconciling the uncertain Cut mutation.",
                )
            if int(self.api.document_type(model)) != 1:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_cut_reconcile",
                    "Resolved document is not a part during Cut reconciliation.",
                )
            actual_path = self.path_policy.canonical(
                self.api.document_path(model) or source
            )
            if actual_path != source:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_cut_reconcile",
                    "Resolved part identity changed during Cut reconciliation.",
                )
            feature = self.api._member(model, "FeatureByName", name)
            if feature is None:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_cut_reconcile",
                    "Expected Cut feature does not exist after the uncertain native call.",
                )
            feature_type = self.api.feature_type(feature)
            if feature_type == "ICE":
                feature_type = str(
                    self.api._member(feature, "GetTypeName") or ""
                ).strip()
            if feature_type != "Cut":
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_cut_reconcile",
                    "Feature identity exists but is not the expected Cut feature type.",
                )
            definition = self.api._member(feature, "GetDefinition")
            if definition is None:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_cut_reconcile",
                    "Cut feature definition is unavailable during reconciliation.",
                )
            end_condition = int(
                self.api._member(definition, "GetEndCondition", True)
            )
            expected_end = 1 if through_all else 0
            if end_condition != expected_end:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_cut_reconcile",
                    "Cut end condition does not match the uncertain request.",
                    details={
                        "expected_end_condition": expected_end,
                        "actual_end_condition": end_condition,
                    },
                )
            parameters: dict[str, float | str | bool | int] = {
                "through_all": through_all
            }
            if not through_all:
                actual_depth_mm = float(
                    self.api._member(definition, "GetDepth", True)
                ) * 1000.0
                if not math.isclose(
                    actual_depth_mm,
                    float(depth_mm),
                    rel_tol=0.0,
                    abs_tol=1e-9,
                ):
                    raise NativeRuntimeError(
                        "reconciliation_mismatch",
                        "part_cut_reconcile",
                        "Blind Cut depth does not match the uncertain request.",
                        details={
                            "expected_depth_mm": float(depth_mm),
                            "actual_depth_mm": actual_depth_mm,
                        },
                    )
                parameters["depth_mm"] = actual_depth_mm

            rebuild = rebuild_document(
                model, self.api, max_features=_MAX_FEATURES
            )
            if not rebuild.success:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_cut_reconcile",
                    "Part does not rebuild cleanly after the uncertain Cut mutation.",
                )
            if not self.api.bodies(model, 0, False):
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_cut_reconcile",
                    "Part has no solid body after the uncertain Cut mutation.",
                )
            return FeatureSnapshot(
                feature_id=self.api.feature_name(feature),
                name=self.api.feature_name(feature),
                kind=FeatureKind.CUT,
                parameters=parameters,
                suppressed=False,
            )

        return self.session.reconcile(
            call_id,
            verifier,
            stage="part_cut_reconcile",
            timeout=self.default_timeout if timeout is None else max(0.0, float(timeout)),
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
