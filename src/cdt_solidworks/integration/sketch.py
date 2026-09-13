"""Production binding for the native Mechanical-90 sketch geometry surface."""

from __future__ import annotations

import math
from pathlib import Path
import uuid
from typing import Any, Mapping, Sequence

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.errors import NativeRuntimeError, failure_from_exception
from cdt_solidworks.native.models import NativeCallResult, NativeCallState, NativeFailure
from cdt_solidworks.native.rebuild import rebuild_document
from cdt_solidworks.part.runtime import DocumentTarget, RebuildResult as PartRebuildResult
from cdt_solidworks.sketch.models import (
    Arc,
    ArcDirection,
    CenterLine,
    Circle,
    Ellipse,
    LineSegment,
    PlaneKind,
    Point2D,
    SketchDefinition,
    SketchPlane,
    SketchPoint,
    Spline,
)
from cdt_solidworks.sketch.native import NativeSketchBinding, SketchNativeRuntime
from cdt_solidworks.sketch.service import (
    SketchContextError,
    SketchMutationError,
    SketchService,
    SketchValidationError,
)

_PART_EXT = ".sldprt"
_STANDARD_PLANE_INDEX = {
    PlaneKind.FRONT: 0,
    PlaneKind.TOP: 1,
    PlaneKind.RIGHT: 2,
}
_LENGTH_UNITS = {
    0: "mm",
    1: "cm",
    2: "m",
    3: "in",
    4: "ft",
    5: "ft-in",
    6: "angstrom",
    7: "nm",
    8: "micron",
    9: "mil",
    10: "uin",
}
_MAX_ENTITIES = 256
_MAX_FEATURES = 100_000


class _NativeResultInterrupt(RuntimeError):
    def __init__(self, result: NativeCallResult[Any]) -> None:
        self.result = result
        super().__init__(result.state.value)


class IntegratedSketchService:
    """Bind native sketch geometry to the shared serialized session and path policy."""

    def __init__(
        self,
        session: Any,
        *,
        path_policy: DocumentPathPolicy,
        default_timeout: float = 60.0,
    ) -> None:
        self.session = session
        self.api = session.api
        self.path_policy = path_policy
        self.default_timeout = default_timeout
        self.runtime = SketchNativeRuntime(
            executor=self._execute,
            binding_resolver=self._resolve_binding,
            plane_selector=self._select_plane,
            sketch_feature_resolver=self._resolve_created_sketch_feature,
            member=self.api._member,
            rebuild_verifier=self._verify_rebuild,
        )
        self.service = SketchService(self.runtime)

    def create_geometry(
        self,
        *,
        path: str,
        expected_revision: int,
        name: str,
        plane: str,
        entities: Sequence[Mapping[str, Any]],
    ) -> NativeCallResult[Any]:
        call_id = uuid.uuid4().hex
        try:
            target = self._target(path, expected_revision)
            definition = self._definition(name=name, plane=plane, entities=entities)
            value = self.service.create(target, definition)
            return NativeCallResult.success(value, call_id=call_id, dispatched=True)
        except _NativeResultInterrupt as exc:
            return exc.result
        except Exception as exc:
            return NativeCallResult.failed(
                self._semantic_failure(exc, "sketch_create_geometry"),
                call_id=call_id,
                dispatched=isinstance(exc, SketchMutationError),
            )

    def get(
        self,
        *,
        path: str,
        expected_revision: int,
        sketch_id: str,
    ) -> NativeCallResult[Any]:
        call_id = uuid.uuid4().hex
        try:
            target = self._target(path, expected_revision)
            value = self.service.get(target, sketch_id)
            return NativeCallResult.success(value, call_id=call_id, dispatched=True)
        except _NativeResultInterrupt as exc:
            return exc.result
        except Exception as exc:
            return NativeCallResult.failed(
                self._semantic_failure(exc, "sketch_get"),
                call_id=call_id,
                dispatched=False,
            )

    def _target(self, path: str, expected_revision: int) -> DocumentTarget:
        source = self.path_policy.validate_open(path)
        if Path(source).suffix.lower() != _PART_EXT:
            raise SketchValidationError("sketch geometry requires a native .SLDPRT document")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise SketchValidationError("expected_revision must be a non-negative integer")
        return DocumentTarget(source, expected_revision=expected_revision, expected_units="mm")

    def _definition(
        self,
        *,
        name: str,
        plane: str,
        entities: Sequence[Mapping[str, Any]],
    ) -> SketchDefinition:
        if isinstance(entities, (str, bytes)) or not isinstance(entities, Sequence):
            raise SketchValidationError("entities must be an array of geometry objects")
        if len(entities) > _MAX_ENTITIES:
            raise SketchValidationError(f"sketch supports at most {_MAX_ENTITIES} entities per call")
        if not isinstance(name, str):
            raise SketchValidationError("sketch name must be a string")
        if not isinstance(plane, str):
            raise SketchValidationError("plane must be front, top, or right")
        normalized_plane = plane.strip().lower()
        try:
            plane_kind = PlaneKind(normalized_plane)
        except ValueError as exc:
            raise SketchValidationError("plane must be front, top, or right") from exc
        if plane_kind not in _STANDARD_PLANE_INDEX:
            raise SketchValidationError("plane must be front, top, or right")
        parsed = tuple(self._entity(item) for item in entities)
        return SketchDefinition(
            name=name,
            plane=SketchPlane(plane_kind),
            entities=parsed,
        )

    def _entity(self, raw: Mapping[str, Any]) -> Any:
        if not isinstance(raw, Mapping):
            raise SketchValidationError("each sketch entity must be an object")
        kind = str(raw.get("type", "")).strip().lower()
        if kind == "line":
            self._strict_keys(raw, {"type", "start_mm", "end_mm", "construction"}, {"type", "start_mm", "end_mm"})
            return LineSegment(
                self._point(raw["start_mm"]),
                self._point(raw["end_mm"]),
                construction=self._bool(raw.get("construction", False), "construction"),
            )
        if kind == "centerline":
            self._strict_keys(raw, {"type", "start_mm", "end_mm"}, {"type", "start_mm", "end_mm"})
            return CenterLine(self._point(raw["start_mm"]), self._point(raw["end_mm"]))
        if kind == "circle":
            self._strict_keys(raw, {"type", "center_mm", "radius_mm", "construction"}, {"type", "center_mm", "radius_mm"})
            return Circle(
                self._point(raw["center_mm"]),
                self._number(raw["radius_mm"], "radius_mm"),
                construction=self._bool(raw.get("construction", False), "construction"),
            )
        if kind == "arc":
            self._strict_keys(
                raw,
                {"type", "center_mm", "start_mm", "end_mm", "direction", "construction"},
                {"type", "center_mm", "start_mm", "end_mm"},
            )
            direction_value = str(raw.get("direction", "counter_clockwise")).strip().lower()
            direction_map = {
                "clockwise": ArcDirection.CLOCKWISE,
                "counter_clockwise": ArcDirection.COUNTER_CLOCKWISE,
            }
            if direction_value not in direction_map:
                raise SketchValidationError("arc direction must be clockwise or counter_clockwise")
            return Arc(
                self._point(raw["center_mm"]),
                self._point(raw["start_mm"]),
                self._point(raw["end_mm"]),
                direction=direction_map[direction_value],
                construction=self._bool(raw.get("construction", False), "construction"),
            )
        if kind == "ellipse":
            self._strict_keys(
                raw,
                {"type", "center_mm", "major_axis_point_mm", "minor_axis_point_mm", "construction"},
                {"type", "center_mm", "major_axis_point_mm", "minor_axis_point_mm"},
            )
            return Ellipse(
                self._point(raw["center_mm"]),
                self._point(raw["major_axis_point_mm"]),
                self._point(raw["minor_axis_point_mm"]),
                construction=self._bool(raw.get("construction", False), "construction"),
            )
        if kind == "point":
            self._strict_keys(raw, {"type", "point_mm"}, {"type", "point_mm"})
            return SketchPoint(self._point(raw["point_mm"]))
        if kind == "spline":
            self._strict_keys(
                raw,
                {"type", "points_mm", "degree", "construction", "simulate_natural_ends"},
                {"type", "points_mm"},
            )
            points = raw["points_mm"]
            if isinstance(points, (str, bytes)) or not isinstance(points, Sequence):
                raise SketchValidationError("spline points_mm must be an array")
            degree = raw.get("degree", 3)
            if isinstance(degree, bool) or not isinstance(degree, int):
                raise SketchValidationError("spline degree must be an integer")
            return Spline(
                tuple(self._point(point) for point in points),
                degree=degree,
                construction=self._bool(raw.get("construction", False), "construction"),
                simulate_natural_ends=self._bool(
                    raw.get("simulate_natural_ends", False), "simulate_natural_ends"
                ),
            )
        raise SketchValidationError(
            "entity type must be line, centerline, circle, arc, ellipse, point, or spline"
        )

    def _point(self, raw: Any) -> Point2D:
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or len(raw) != 2:
            raise SketchValidationError("point coordinates must be [x_mm, y_mm]")
        return Point2D(self._number(raw[0], "x_mm"), self._number(raw[1], "y_mm"))

    @staticmethod
    def _number(raw: Any, label: str) -> float:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise SketchValidationError(f"{label} must be numeric")
        value = float(raw)
        if not math.isfinite(value):
            raise SketchValidationError(f"{label} must be finite")
        return value

    @staticmethod
    def _bool(raw: Any, label: str) -> bool:
        if not isinstance(raw, bool):
            raise SketchValidationError(f"{label} must be boolean")
        return raw

    @staticmethod
    def _strict_keys(raw: Mapping[str, Any], allowed: set[str], required: set[str]) -> None:
        keys = {str(key) for key in raw}
        missing = sorted(required - keys)
        unexpected = sorted(keys - allowed)
        if missing:
            raise SketchValidationError(f"missing entity field(s): {', '.join(missing)}")
        if unexpected:
            raise SketchValidationError(f"unexpected entity field(s): {', '.join(unexpected)}")

    def _execute(self, operation: Any, *, stage: str, mutation: bool) -> Any:
        result = self.session.execute(
            operation,
            stage=stage,
            timeout=self.default_timeout,
            mutation=mutation,
        )
        if result.state is NativeCallState.SUCCESS:
            return result.value
        raise _NativeResultInterrupt(result)

    def _resolve_binding(self, app: Any, target: DocumentTarget) -> NativeSketchBinding:
        source = self.path_policy.validate_open(target.document_id)
        model = self.api.get_open_document(app, source)
        if model is None:
            raise NativeRuntimeError(
                "document_not_open",
                "sketch_resolve_document",
                "Sketch operations require the target part to be opened explicitly first.",
            )
        if int(self.api.document_type(model)) != 1:
            raise NativeRuntimeError(
                "document_type_mismatch",
                "sketch_resolve_document",
                "Sketch geometry operation requires a part document.",
            )
        actual_path = self.path_policy.canonical(self.api.document_path(model) or source)
        if actual_path != source:
            raise NativeRuntimeError(
                "document_context_mismatch",
                "sketch_resolve_document",
                "Open document identity does not match the requested part path.",
            )
        revision = self.api.update_stamp(model)
        if revision is None:
            raise NativeRuntimeError(
                "document_context_mismatch",
                "sketch_resolve_document",
                "SOLIDWORKS did not provide a document revision stamp.",
            )
        unit_code = int(self.api._member(model, "LengthUnit"))
        units = _LENGTH_UNITS.get(unit_code, f"sw_length_unit_{unit_code}")
        return NativeSketchBinding(
            model=model,
            document_id=source,
            revision=int(revision),
            units=units,
            configuration=self.api.active_configuration(model),
        )

    def _select_plane(self, model: Any, plane: SketchPlane) -> None:
        if plane.kind not in _STANDARD_PLANE_INDEX or plane.reference_id is not None:
            raise NativeRuntimeError(
                "cad_precondition_failed",
                "sketch_plane_select",
                "Only front, top, and right reference planes are native-accepted for this tool.",
            )
        target_index = _STANDARD_PLANE_INDEX[plane.kind]
        current = self.api.first_feature(model)
        index = 0
        while current is not None:
            if self.api.feature_type(current) == "RefPlane":
                if index == target_index:
                    self.api._member(model, "ClearSelection2", True)
                    if not bool(self.api._member(current, "Select2", False, 0)):
                        raise NativeRuntimeError(
                            "cad_selection_failed",
                            "sketch_plane_select",
                            "SOLIDWORKS reference plane could not be selected.",
                        )
                    return
                index += 1
            current = self.api.next_feature(current)
        raise NativeRuntimeError(
            "cad_selection_failed",
            "sketch_plane_select",
            "SOLIDWORKS standard reference plane could not be resolved.",
        )

    def _resolve_created_sketch_feature(self, model: Any, active_sketch: Any, requested_name: str) -> Any:
        found = None
        current = self.api.first_feature(model)
        visited = 0
        while current is not None:
            visited += 1
            if visited > _MAX_FEATURES:
                raise NativeRuntimeError(
                    "feature_traversal_limit",
                    "sketch_feature_resolve",
                    "Feature traversal exceeded its bounded verification limit.",
                )
            if self.api.feature_type(current) == "ProfileFeature":
                found = current
            current = self.api.next_feature(current)
        if found is None:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "sketch_feature_resolve",
                "Created sketch feature was not found after mutation.",
            )
        return found

    def _verify_rebuild(self, model: Any) -> PartRebuildResult:
        result = rebuild_document(model, self.api, max_features=_MAX_FEATURES)
        if result.success:
            return PartRebuildResult(ok=True)
        errors = sum(not issue.is_warning for issue in result.feature_issues)
        return PartRebuildResult(
            ok=False,
            error_code="rebuild_failed",
            message=f"native_rebuild_ok={result.native_rebuild_ok}; feature_errors={errors}",
        )

    @staticmethod
    def _semantic_failure(exc: Exception, stage: str) -> NativeFailure:
        if isinstance(exc, SketchValidationError):
            return NativeFailure("cad_validation_error", stage, str(exc), retryable=False)
        if isinstance(exc, SketchContextError):
            return NativeFailure("document_context_mismatch", stage, str(exc), retryable=False)
        if isinstance(exc, SketchMutationError):
            return NativeFailure("cad_postcondition_failed", stage, str(exc), retryable=False)
        return failure_from_exception(exc, stage)
