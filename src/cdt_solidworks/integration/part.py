"""Part Integration — Evidence-gated provider binding for parametric features.
Wing: Mechanical 90 | Topic: parametric-part | Updated: 2026-09-14
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from collections.abc import Mapping, Sequence
import math
from pathlib import Path
from typing import Any
import uuid

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.errors import NativeRuntimeError, failure_from_exception
from cdt_solidworks.native.models import NativeCallResult, NativeCallState, NativeFailure
from cdt_solidworks.native.rebuild import rebuild_document
from cdt_solidworks.part.models import (
    ChamferSpec,
    CircularPatternSpec,
    CutSpec,
    DraftSpec,
    ExtrudeSpec,
    FeatureKind,
    FeatureSnapshot,
    FilletSpec,
    HoleSpec,
    HoleWizardSize,
    HoleWizardSpec,
    LinearPatternSpec,
    MirrorSpec,
    ProfileRef,
    ReferenceAxisSpec,
    ReferencePlaneSpec,
    ReferencePointSpec,
    RevolveCutSpec,
    RevolveSpec,
    RibSpec,
    ShellSpec,
)
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
_SIMPLE_HOLE_FACE_REF = "bbox:+z"
_SIMPLE_HOLE_TYPES = frozenset({"Hole", "SketchHole", "SimpleHole"})
_STANDARD_REFERENCE_PLANE_COUNT = 3
_TRANSFORM_TOLERANCE = 1e-9
_FACE_REFS = frozenset({"bbox:+x", "bbox:-x", "bbox:+y", "bbox:-y", "bbox:+z", "bbox:-z"})
_PLANE_REFS = frozenset({"plane:front", "plane:top", "plane:right"})
_NATIVE_HOLE_WIZARD_SIZES = frozenset({HoleWizardSize.M2, HoleWizardSize.M4, HoleWizardSize.M6})
_FILLET_EDGE_REF = "bbox:edge:+x:+z"
_CHAMFER_EDGE_REF = "bbox:edge:-x:+z"
_SHELL_FACE_REF = "bbox:+z"
_DRAFT_FACE_REF = "bbox:+x"
_DRAFT_NEUTRAL_REF = "bbox:+z"
_LINEAR_DIRECTION_REF = "bbox:edge:+y:+z"
_MIRROR_REF = "plane:right"
_REFERENCE_PLANE_REF = "plane:front"
_REFERENCE_AXIS_REFS = ("plane:top", "plane:right")
_REFERENCE_POINT_REF = "bbox:+z"
_MAX_PUBLIC_REFS = 512


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
    api: Any,
    model: Any,
    profile: Any,
    sketch_id: str,
    feature_label: str = "Cut Extrude",
    context_stage: str = "part_cut_context",
) -> None:
    """Fail closed unless a profile is on Front/Top/Right reference planes."""
    sketch = api._member(profile, "GetSpecificFeature2")
    if sketch is None:
        raise NativeRuntimeError(
            "cad_precondition_failed",
            context_stage,
            f"Sketch {sketch_id!r} did not expose a native sketch object.",
        )
    reference, entity_type = api.sketch_reference_entity(sketch)
    if reference is None:
        raise NativeRuntimeError(
            "cad_precondition_failed",
            context_stage,
            f"Sketch {sketch_id!r} has no stable native reference entity.",
        )
    if int(entity_type) != _SW_SEL_DATUM_PLANES:
        raise NativeRuntimeError(
            "cad_precondition_failed",
            context_stage,
            f"{feature_label} currently rejects face-backed sketches; use a standard reference plane sketch.",
        )

    reference_transform = _reference_transform(api, reference)
    if len(reference_transform) != 16:
        raise NativeRuntimeError(
            "cad_precondition_failed",
            context_stage,
            f"{feature_label} could not read the sketch reference-plane transform.",
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
        context_stage,
        f"{feature_label} currently accepts only sketches on a standard reference plane.",
    )


def _simple_hole_center_from_feature(
    api: Any, feature: Any, *, stage: str, failure_code: str
) -> tuple[float, float]:
    profiles: list[Any] = []
    subfeature = api._member(feature, "GetFirstSubFeature")
    visited = 0
    while subfeature is not None:
        visited += 1
        if visited > 16:
            raise NativeRuntimeError(
                failure_code,
                stage,
                "Simple-hole subfeature traversal exceeded its bounded limit.",
            )
        feature_type = str(api.feature_type(subfeature) or "").strip()
        if feature_type == "ICE":
            feature_type = str(api._member(subfeature, "GetTypeName") or "").strip()
        if feature_type == "ProfileFeature":
            profiles.append(subfeature)
        subfeature = api._member(subfeature, "GetNextSubFeature")
    if len(profiles) != 1:
        raise NativeRuntimeError(
            failure_code,
            stage,
            "Simple hole must expose exactly one profile subfeature for center verification.",
            details={"profile_subfeature_count": len(profiles)},
        )
    sketch = api._member(profiles[0], "GetSpecificFeature2")
    if sketch is None:
        raise NativeRuntimeError(
            failure_code,
            stage,
            "Simple-hole profile subfeature did not expose a native sketch.",
        )
    raw_points = api._member(sketch, "GetSketchPoints2")
    if raw_points is None:
        points: tuple[Any, ...] = ()
    elif isinstance(raw_points, (tuple, list)):
        points = tuple(raw_points)
    else:
        points = (raw_points,)
    if len(points) != 1:
        raise NativeRuntimeError(
            failure_code,
            stage,
            "Simple-hole profile must contain exactly one sketch point.",
            details={"sketch_point_count": len(points)},
        )
    x_mm = float(api._member(points[0], "X")) * 1000.0
    y_mm = float(api._member(points[0], "Y")) * 1000.0
    if not math.isfinite(x_mm) or not math.isfinite(y_mm):
        raise NativeRuntimeError(
            failure_code,
            stage,
            "Simple-hole sketch point returned non-finite center coordinates.",
        )
    return x_mm, y_mm


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
        profile_port: Any | None = None,
        default_timeout: float = 60.0,
    ) -> None:
        self._recovery = ContextVar("part_recovery", default=None)
        self.session = session
        self.path_policy = path_policy
        self.profile_port = profile_port
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
                revolve_profile_validator=lambda model, profile, sketch_id: (
                    _require_standard_reference_plane(
                        self.api,
                        model,
                        profile,
                        sketch_id,
                        "Revolve",
                        "part_revolve_context",
                    )
                ),
                null_dispatch=getattr(self.api, "null_dispatch", lambda: None),
            )
            service = PartService(runtime)
        self.service = service

    def profile_extrude(
        self,
        *,
        path: str,
        expected_revision: int,
        sketch_id: str,
        name: str,
        depth_mm: float,
        configuration: str | None = None,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            target = self._target(path, expected_revision, configuration)
            profile_id = self._identity(sketch_id, "sketch_id")
            self._require_profile(target, profile_id)
            return self.service.extrude(
                target,
                ExtrudeSpec(
                    name=self._name(name, "name"),
                    profile=ProfileRef(profile_id),
                    depth_mm=self._positive_number(depth_mm, "depth_mm"),
                ),
            )

        return self._call_service("part_profile_extrude", operation, mutation=True)

    def profile_cut(
        self,
        *,
        path: str,
        expected_revision: int,
        sketch_id: str,
        name: str,
        through_all: bool,
        depth_mm: float | None = None,
        configuration: str | None = None,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            target = self._target(path, expected_revision, configuration)
            profile_id = self._identity(sketch_id, "sketch_id")
            self._require_profile(target, profile_id)
            through = self._boolean(through_all, "through_all")
            depth: float | None = None
            if through:
                if depth_mm is not None:
                    raise PartValidationError("through-all cut must not specify depth_mm")
            else:
                depth = self._positive_number(depth_mm, "depth_mm")
            return self.service.cut(
                target,
                CutSpec(
                    name=self._name(name, "name"),
                    profile=ProfileRef(profile_id),
                    through_all=through,
                    depth_mm=depth,
                ),
            )

        return self._call_service("part_profile_cut", operation, mutation=True)

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
            with self._recoverable("cut", path=path, name=name, through_all=through_all, depth_mm=depth_mm):
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

    def simple_hole(
        self,
        *,
        path: str,
        expected_revision: int,
        name: str,
        diameter_mm: float,
        face_ref: str,
        center_mm: tuple[float, float] | list[float],
        through_all: bool,
        depth_mm: float | None = None,
    ) -> NativeCallResult[Any]:
        call_id = uuid.uuid4().hex
        try:
            target = self._target(path, expected_revision)
            if not isinstance(name, str) or not name.strip():
                raise PartValidationError("name must be a non-empty string")
            if isinstance(diameter_mm, bool) or not isinstance(diameter_mm, (int, float)):
                raise PartValidationError("diameter_mm must be numeric")
            diameter = float(diameter_mm)
            if not math.isfinite(diameter) or diameter <= 0.0:
                raise PartValidationError("diameter_mm must be positive and finite")
            if not isinstance(face_ref, str) or face_ref.strip() != "bbox:+z":
                raise PartValidationError("face_ref must be exactly 'bbox:+z' for native simple hole")
            if (
                isinstance(center_mm, (str, bytes))
                or not isinstance(center_mm, (tuple, list))
                or len(center_mm) != 2
            ):
                raise PartValidationError("center_mm must be a two-value [x_mm, y_mm] coordinate")
            if any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in center_mm
            ):
                raise PartValidationError("center_mm coordinates must be numeric")
            center = (float(center_mm[0]), float(center_mm[1]))
            if not all(math.isfinite(value) for value in center):
                raise PartValidationError("center_mm coordinates must be finite")
            if not isinstance(through_all, bool):
                raise PartValidationError("through_all must be boolean")
            if through_all:
                if depth_mm is not None:
                    raise PartValidationError(
                        "through-all simple hole must not specify depth_mm"
                    )
                depth = None
            else:
                if isinstance(depth_mm, bool) or not isinstance(depth_mm, (int, float)):
                    raise PartValidationError(
                        "blind simple hole requires numeric depth_mm"
                    )
                depth = float(depth_mm)
                if not math.isfinite(depth) or depth <= 0.0:
                    raise PartValidationError(
                        "blind simple hole depth_mm must be positive and finite"
                    )
            with self._recoverable("simple_hole", path=path, name=name, diameter_mm=diameter, face_ref=face_ref, center_mm=center, through_all=through_all, depth_mm=depth):
                mutation = self.service.hole(
                    target,
                    HoleSpec(
                        name=name,
                        diameter_mm=diameter,
                        face_ref=_SIMPLE_HOLE_FACE_REF,
                        centers_mm=(center,),
                        through_all=through_all,
                        depth_mm=depth,
                    ),
                )
            actual_x = mutation.feature.parameters.get("center_x_mm")
            actual_y = mutation.feature.parameters.get("center_y_mm")
            if (
                not isinstance(actual_x, (int, float))
                or isinstance(actual_x, bool)
                or not isinstance(actual_y, (int, float))
                or isinstance(actual_y, bool)
                or not math.isclose(float(actual_x), center[0], rel_tol=0.0, abs_tol=1e-7)
                or not math.isclose(float(actual_y), center[1], rel_tol=0.0, abs_tol=1e-7)
            ):
                raise PartMutationError(
                    "simple-hole center read-back does not match the requested model X/Y coordinate"
                )
            return NativeCallResult.success(
                mutation, call_id=call_id, dispatched=True
            )
        except _NativeResultInterrupt as exc:
            return exc.result
        except Exception as exc:
            return NativeCallResult.failed(
                self._semantic_failure(exc, "part_simple_hole"),
                call_id=call_id,
                dispatched=isinstance(exc, PartMutationError),
            )

    def hole_wizard(
        self,
        *,
        path: str,
        expected_revision: int,
        name: str,
        size: str,
        face_ref: str,
        center_mm: Sequence[float],
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            target = self._target(path, expected_revision)
            spec = HoleWizardSpec(
                name=self._name(name, "name"),
                size=self._hole_wizard_size(size),
                face_ref=self._exact_face(face_ref, "face_ref", allowed={_SIMPLE_HOLE_FACE_REF}),
                center_mm=self._point(center_mm, "center_mm"),
            )
            return self.service.hole_wizard(target, spec)

        return self._call_service("part_hole_wizard", operation, mutation=True)

    def fillet(
        self,
        *,
        path: str,
        expected_revision: int,
        name: str,
        edge_refs: Sequence[str],
        radius_mm: float,
        tangent_propagation: bool = False,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            target = self._target(path, expected_revision)
            refs = self._edge_refs(edge_refs, "edge_refs")
            if refs != (_FILLET_EDGE_REF,):
                raise PartValidationError(f"edge_refs must be exactly [{_FILLET_EDGE_REF!r}] for native-passed fillet")
            tangent = self._boolean(tangent_propagation, "tangent_propagation")
            if tangent:
                raise PartValidationError("tangent_propagation must be false for native-passed fillet")
            spec = FilletSpec(
                self._name(name, "name"),
                refs,
                self._positive_number(radius_mm, "radius_mm"),
                tangent,
            )
            return self.service.fillet(target, spec)

        return self._call_service("part_fillet", operation, mutation=True)

    def chamfer(
        self,
        *,
        path: str,
        expected_revision: int,
        name: str,
        edge_refs: Sequence[str],
        distance_mm: float,
        angle_deg: float = 45.0,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            target = self._target(path, expected_revision)
            angle = self._number(angle_deg, "angle_deg")
            if angle <= 0.0 or angle >= 90.0:
                raise PartValidationError("angle_deg must be in the range (0, 90)")
            refs = self._edge_refs(edge_refs, "edge_refs")
            if refs != (_CHAMFER_EDGE_REF,):
                raise PartValidationError(f"edge_refs must be exactly [{_CHAMFER_EDGE_REF!r}] for native-passed chamfer")
            spec = ChamferSpec(
                self._name(name, "name"),
                refs,
                self._positive_number(distance_mm, "distance_mm"),
                angle,
            )
            return self.service.chamfer(target, spec)

        return self._call_service("part_chamfer", operation, mutation=True)

    def shell(
        self,
        *,
        path: str,
        expected_revision: int,
        name: str,
        face_refs: Sequence[str],
        thickness_mm: float,
        outward: bool = False,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            target = self._target(path, expected_revision)
            refs = self._face_refs(face_refs, "face_refs")
            if refs != (_SHELL_FACE_REF,):
                raise PartValidationError(f"face_refs must be exactly [{_SHELL_FACE_REF!r}] for native-passed shell")
            outward_value = self._boolean(outward, "outward")
            if outward_value:
                raise PartValidationError("outward must be false for native-passed shell")
            spec = ShellSpec(
                self._name(name, "name"),
                refs,
                self._positive_number(thickness_mm, "thickness_mm"),
                outward_value,
            )
            return self.service.shell(target, spec)

        return self._call_service("part_shell", operation, mutation=True)

    def draft(
        self,
        *,
        path: str,
        expected_revision: int,
        name: str,
        face_refs: Sequence[str],
        neutral_plane_ref: str,
        angle_deg: float,
        reverse_direction: bool = False,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            target = self._target(path, expected_revision)
            angle = self._number(angle_deg, "angle_deg")
            if angle <= 0.0 or angle >= 90.0:
                raise PartValidationError("angle_deg must be in the range (0, 90)")
            refs = self._face_refs(face_refs, "face_refs")
            if refs != (_DRAFT_FACE_REF,):
                raise PartValidationError(f"face_refs must be exactly [{_DRAFT_FACE_REF!r}] for native-passed draft")
            neutral = self._plane_or_face_ref(neutral_plane_ref, "neutral_plane_ref")
            if neutral != _DRAFT_NEUTRAL_REF:
                raise PartValidationError(f"neutral_plane_ref must be exactly {_DRAFT_NEUTRAL_REF!r} for native-passed draft")
            reverse = self._boolean(reverse_direction, "reverse_direction")
            if reverse:
                raise PartValidationError("reverse_direction must be false for native-passed draft")
            spec = DraftSpec(
                self._name(name, "name"),
                refs,
                neutral,
                angle,
                reverse,
            )
            return self.service.draft(target, spec)

        return self._call_service("part_draft", operation, mutation=True)

    def rib(
        self,
        *,
        path: str,
        expected_revision: int,
        name: str,
        sketch_id: str,
        thickness_mm: float,
        both_sides: bool = True,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            target = self._target(path, expected_revision)
            both = self._boolean(both_sides, "both_sides")
            if not both:
                raise PartValidationError("both_sides must be true for native-passed rib")
            spec = RibSpec(
                self._name(name, "name"),
                ProfileRef(self._identity(sketch_id, "sketch_id")),
                self._positive_number(thickness_mm, "thickness_mm"),
                both,
            )
            return self.service.rib(target, spec)

        return self._call_service("part_rib", operation, mutation=True)

    def linear_pattern(
        self,
        *,
        path: str,
        expected_revision: int,
        name: str,
        seed_feature_ids: Sequence[str],
        direction_ref: str,
        count: int,
        spacing_mm: float,
        geometry_pattern: bool = False,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            target = self._target(path, expected_revision)
            direction = self._edge_ref(direction_ref, "direction_ref")
            if direction != _LINEAR_DIRECTION_REF:
                raise PartValidationError(f"direction_ref must be exactly {_LINEAR_DIRECTION_REF!r} for native-passed linear pattern")
            geometry = self._boolean(geometry_pattern, "geometry_pattern")
            if geometry:
                raise PartValidationError("geometry_pattern must be false for native-passed linear pattern")
            spec = LinearPatternSpec(
                self._name(name, "name"),
                self._feature_ids(seed_feature_ids, "seed_feature_ids"),
                direction,
                self._pattern_count(count),
                self._positive_number(spacing_mm, "spacing_mm"),
                geometry,
            )
            return self.service.linear_pattern(target, spec)

        return self._call_service("part_linear_pattern", operation, mutation=True)

    def circular_pattern(
        self,
        *,
        path: str,
        expected_revision: int,
        name: str,
        seed_feature_ids: Sequence[str],
        axis_ref: str,
        count: int,
        angle_deg: float = 360.0,
        geometry_pattern: bool = False,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            target = self._target(path, expected_revision)
            angle = self._number(angle_deg, "angle_deg")
            if angle <= 0.0 or angle > 360.0:
                raise PartValidationError("angle_deg must be in the range (0, 360]")
            geometry = self._boolean(geometry_pattern, "geometry_pattern")
            if geometry:
                raise PartValidationError("geometry_pattern must be false for native-passed circular pattern")
            spec = CircularPatternSpec(
                self._name(name, "name"),
                self._feature_ids(seed_feature_ids, "seed_feature_ids"),
                self._axis_feature_ref(axis_ref, "axis_ref"),
                self._pattern_count(count),
                angle,
                geometry,
            )
            return self.service.circular_pattern(target, spec)

        return self._call_service("part_circular_pattern", operation, mutation=True)

    def mirror(
        self,
        *,
        path: str,
        expected_revision: int,
        name: str,
        seed_feature_ids: Sequence[str],
        mirror_ref: str,
        geometry_pattern: bool = False,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            target = self._target(path, expected_revision)
            mirror = self._plane_ref(mirror_ref, "mirror_ref")
            if mirror != _MIRROR_REF:
                raise PartValidationError(f"mirror_ref must be exactly {_MIRROR_REF!r} for native-passed mirror")
            geometry = self._boolean(geometry_pattern, "geometry_pattern")
            if geometry:
                raise PartValidationError("geometry_pattern must be false for native-passed mirror")
            spec = MirrorSpec(
                self._name(name, "name"),
                self._feature_ids(seed_feature_ids, "seed_feature_ids"),
                mirror,
                geometry,
            )
            return self.service.mirror(target, spec)

        return self._call_service("part_mirror", operation, mutation=True)

    def reference_plane(
        self,
        *,
        path: str,
        expected_revision: int,
        name: str,
        reference: str,
        offset_mm: float = 0.0,
        reverse_direction: bool = False,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            target = self._target(path, expected_revision)
            source = self._plane_ref(reference, "reference")
            if source != _REFERENCE_PLANE_REF:
                raise PartValidationError(f"reference must be exactly {_REFERENCE_PLANE_REF!r} for native-passed reference plane")
            reverse = self._boolean(reverse_direction, "reverse_direction")
            if reverse:
                raise PartValidationError("reverse_direction must be false for native-passed reference plane")
            spec = ReferencePlaneSpec(
                self._name(name, "name"),
                source,
                self._number(offset_mm, "offset_mm"),
                reverse,
            )
            return self.service.reference_plane(target, spec)

        return self._call_service("part_reference_plane", operation, mutation=True)

    def reference_axis(
        self,
        *,
        path: str,
        expected_revision: int,
        name: str,
        first_ref: str,
        second_ref: str,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            target = self._target(path, expected_revision)
            first = self._plane_ref(first_ref, "first_ref")
            second = self._plane_ref(second_ref, "second_ref")
            if (first, second) != _REFERENCE_AXIS_REFS:
                raise PartValidationError(
                    f"reference axis must use first_ref={_REFERENCE_AXIS_REFS[0]!r} and second_ref={_REFERENCE_AXIS_REFS[1]!r} for native-passed subset"
                )
            return self.service.reference_axis(
                target,
                ReferenceAxisSpec(self._name(name, "name"), first, second),
            )

        return self._call_service("part_reference_axis", operation, mutation=True)

    def reference_point(
        self,
        *,
        path: str,
        expected_revision: int,
        name: str,
        reference: str,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            target = self._target(path, expected_revision)
            return self.service.reference_point(
                target,
                ReferencePointSpec(
                    self._name(name, "name"),
                    self._exact_face(reference, "reference", allowed={_REFERENCE_POINT_REF}),
                ),
            )

        return self._call_service("part_reference_point", operation, mutation=True)

    def get_feature(
        self,
        *,
        path: str,
        expected_revision: int,
        feature_id: str,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            return self.service.get_feature(
                self._target(path, expected_revision),
                self._identity(feature_id, "feature_id"),
            )

        return self._call_service("part_feature_get", operation, mutation=False)

    def rename_feature(
        self,
        *,
        path: str,
        expected_revision: int,
        feature_id: str,
        new_name: str,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            return self.service.rename_feature(
                self._target(path, expected_revision),
                self._identity(feature_id, "feature_id"),
                self._name(new_name, "new_name"),
            )

        return self._call_service("part_feature_rename", operation, mutation=True)

    def set_feature_suppressed(
        self,
        *,
        path: str,
        expected_revision: int,
        feature_id: str,
        suppressed: bool,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            return self.service.set_feature_suppressed(
                self._target(path, expected_revision),
                self._identity(feature_id, "feature_id"),
                self._boolean(suppressed, "suppressed"),
            )

        return self._call_service("part_feature_set_suppressed", operation, mutation=True)

    def feature_parameters_get(
        self,
        *,
        path: str,
        expected_revision: int,
        feature_id: str,
        configuration: str | None = None,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            return self.service.get_feature_parameters(
                self._target(path, expected_revision, configuration),
                self._identity(feature_id, "feature_id"),
            )

        return self._call_service("part_feature_parameters_get", operation, mutation=False)

    def feature_parameter_set(
        self,
        *,
        path: str,
        expected_revision: int,
        feature_id: str,
        parameter: str,
        value: float,
        configuration: str | None = None,
    ) -> NativeCallResult[Any]:
        def operation() -> Any:
            if parameter not in {"depth_mm", "radius_mm"}:
                raise PartValidationError("parameter must be depth_mm or radius_mm")
            return self.service.set_feature_parameter(
                self._target(path, expected_revision, configuration),
                self._identity(feature_id, "feature_id"),
                parameter,
                self._positive_number(value, "value"),
            )

        return self._call_service("part_feature_parameter_set", operation, mutation=True)

    def set_feature_parameter(
        self,
        *,
        path: str,
        expected_revision: int,
        feature_id: str,
        parameter: str,
        value: float,
    ) -> NativeCallResult[Any]:
        return self.feature_parameter_set(
            path=path,
            expected_revision=expected_revision,
            feature_id=feature_id,
            parameter=parameter,
            value=value,
        )

    def reconcile_simple_hole(
        self,
        *,
        call_id: str,
        path: str,
        name: str,
        diameter_mm: float,
        face_ref: str,
        center_mm: tuple[float, float] | list[float],
        through_all: bool,
        depth_mm: float | None = None,
        timeout: float | None = None,
        _capture: bool = False,
    ) -> NativeCallResult[FeatureSnapshot]:
        """Verify an uncertain Simple Hole before clearing dispatcher quarantine."""
        local_call_id = call_id
        try:
            if self.session is None or self.api is None:
                raise PartValidationError(
                    "simple-hole reconciliation requires a bound native session"
                )
            if not isinstance(call_id, str) or not call_id.strip():
                raise PartValidationError("call_id must be a non-empty string")
            source = self.path_policy.validate_open(path)
            if Path(source).suffix.lower() != _PART_EXT:
                raise PartValidationError(
                    "simple-hole reconciliation requires a native .SLDPRT document"
                )
            if not isinstance(name, str) or not name.strip():
                raise PartValidationError("name must be a non-empty string")
            if isinstance(diameter_mm, bool) or not isinstance(diameter_mm, (int, float)):
                raise PartValidationError("diameter_mm must be numeric")
            expected_diameter = float(diameter_mm)
            if not math.isfinite(expected_diameter) or expected_diameter <= 0.0:
                raise PartValidationError("diameter_mm must be positive and finite")
            if not isinstance(face_ref, str) or face_ref.strip() != _SIMPLE_HOLE_FACE_REF:
                raise PartValidationError(
                    "face_ref must be exactly 'bbox:+z' for native simple-hole reconciliation"
                )
            if (
                isinstance(center_mm, (str, bytes))
                or not isinstance(center_mm, (tuple, list))
                or len(center_mm) != 2
                or any(
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    for value in center_mm
                )
            ):
                raise PartValidationError(
                    "center_mm must be a two-value numeric [x_mm, y_mm] coordinate"
                )
            expected_center = (float(center_mm[0]), float(center_mm[1]))
            if not all(math.isfinite(value) for value in expected_center):
                raise PartValidationError("center_mm coordinates must be finite")
            if not isinstance(through_all, bool):
                raise PartValidationError("through_all must be boolean")
            if through_all:
                if depth_mm is not None:
                    raise PartValidationError(
                        "through-all simple-hole reconciliation must not specify depth_mm"
                    )
                expected_depth = None
            else:
                if isinstance(depth_mm, bool) or not isinstance(depth_mm, (int, float)):
                    raise PartValidationError(
                        "blind simple-hole reconciliation requires numeric depth_mm"
                    )
                expected_depth = float(depth_mm)
                if not math.isfinite(expected_depth) or expected_depth <= 0.0:
                    raise PartValidationError(
                        "blind simple-hole reconciliation depth_mm must be positive and finite"
                    )
        except Exception as exc:
            return NativeCallResult.failed(
                self._semantic_failure(exc, "part_simple_hole_reconcile"),
                call_id=local_call_id,
                dispatched=False,
            )

        def verifier(app: Any) -> FeatureSnapshot:
            model = self.api.get_open_document(app, source)
            if model is None:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_simple_hole_reconcile",
                    "Target part is not open while reconciling the uncertain Simple Hole mutation.",
                )
            if int(self.api.document_type(model)) != 1:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_simple_hole_reconcile",
                    "Resolved document is not a part during Simple Hole reconciliation.",
                )
            actual_path = self.path_policy.canonical(
                self.api.document_path(model) or source
            )
            if actual_path != source:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_simple_hole_reconcile",
                    "Resolved part identity changed during Simple Hole reconciliation.",
                )
            feature = self.api._member(model, "FeatureByName", name)
            if feature is None:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_simple_hole_reconcile",
                    "Expected Simple Hole feature does not exist after the uncertain native call.",
                )
            feature_type = str(self.api.feature_type(feature) or "").strip()
            if feature_type == "ICE":
                feature_type = str(
                    self.api._member(feature, "GetTypeName") or ""
                ).strip()
            if feature_type not in _SIMPLE_HOLE_TYPES:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_simple_hole_reconcile",
                    "Feature identity exists but is not the expected Simple Hole feature type.",
                    details={"actual_feature_type": feature_type},
                )
            definition = self.api._member(feature, "GetDefinition")
            if definition is None:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_simple_hole_reconcile",
                    "Simple Hole feature definition is unavailable during reconciliation.",
                )
            null_dispatch = getattr(self.api, "null_dispatch", lambda: None)()
            if not bool(
                self.api._member(
                    definition, "AccessSelections", model, null_dispatch
                )
            ):
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_simple_hole_reconcile",
                    "SOLIDWORKS did not grant access to Simple Hole selections during reconciliation.",
                )
            try:
                actual_diameter = float(
                    self.api._member(definition, "Diameter")
                ) * 1000.0
                actual_end = int(self.api._member(definition, "Type"))
                expected_end = 1 if through_all else 0
                if not math.isclose(
                    actual_diameter, expected_diameter, rel_tol=0.0, abs_tol=1e-7
                ):
                    raise NativeRuntimeError(
                        "reconciliation_mismatch",
                        "part_simple_hole_reconcile",
                        "Simple Hole diameter does not match the uncertain request.",
                        details={
                            "expected_diameter_mm": expected_diameter,
                            "actual_diameter_mm": actual_diameter,
                        },
                    )
                if actual_end != expected_end:
                    raise NativeRuntimeError(
                        "reconciliation_mismatch",
                        "part_simple_hole_reconcile",
                        "Simple Hole end condition does not match the uncertain request.",
                        details={
                            "expected_end_condition": expected_end,
                            "actual_end_condition": actual_end,
                        },
                    )
                actual_depth = None
                if expected_end == 0:
                    actual_depth = float(
                        self.api._member(definition, "Depth")
                    ) * 1000.0
                    if not math.isclose(
                        actual_depth,
                        float(expected_depth),
                        rel_tol=0.0,
                        abs_tol=1e-7,
                    ):
                        raise NativeRuntimeError(
                            "reconciliation_mismatch",
                            "part_simple_hole_reconcile",
                            "Simple Hole blind depth does not match the uncertain request.",
                            details={
                                "expected_depth_mm": expected_depth,
                                "actual_depth_mm": actual_depth,
                            },
                        )
            finally:
                self.api._member(definition, "ReleaseSelectionAccess")

            actual_center = _simple_hole_center_from_feature(
                self.api,
                feature,
                stage="part_simple_hole_reconcile",
                failure_code="reconciliation_mismatch",
            )
            if not (
                math.isclose(
                    actual_center[0], expected_center[0], rel_tol=0.0, abs_tol=1e-7
                )
                and math.isclose(
                    actual_center[1], expected_center[1], rel_tol=0.0, abs_tol=1e-7
                )
            ):
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_simple_hole_reconcile",
                    "Simple Hole center does not match the uncertain request.",
                    details={
                        "expected_center_mm": list(expected_center),
                        "actual_center_mm": list(actual_center),
                    },
                )

            rebuild = rebuild_document(
                model, self.api, max_features=_MAX_FEATURES
            )
            if not rebuild.success:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_simple_hole_reconcile",
                    "Part does not rebuild cleanly after the uncertain Simple Hole mutation.",
                )
            solid_bodies = tuple(self.api.bodies(model, 0, False))
            if len(solid_bodies) != 1:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_simple_hole_reconcile",
                    "Part must contain exactly one solid body after the uncertain Simple Hole mutation.",
                    details={"solid_body_count": len(solid_bodies)},
                )
            parameters: dict[str, float | str | bool | int] = {
                "diameter_mm": actual_diameter,
                "face_ref": _SIMPLE_HOLE_FACE_REF,
                "center_count": 1,
                "center_x_mm": actual_center[0],
                "center_y_mm": actual_center[1],
                "through_all": through_all,
            }
            if actual_depth is not None:
                parameters["depth_mm"] = actual_depth
            return FeatureSnapshot(
                feature_id=self.api.feature_name(feature),
                name=self.api.feature_name(feature),
                kind=FeatureKind.HOLE,
                parameters=parameters,
                suppressed=False,
            )

        identity = (source, name, float(diameter_mm), face_ref, tuple(center_mm), through_all, depth_mm)
        if _capture:
            return identity, verifier
        return self.session.reconcile(
            call_id,
            verifier,
            identity=identity,
            stage="part_simple_hole_reconcile",
            timeout=(
                self.default_timeout
                if timeout is None
                else max(0.0, float(timeout))
            ),
        )

    def revolve(
        self,
        *,
        path: str,
        expected_revision: int,
        sketch_id: str,
        name: str,
        axis_ref: str,
        angle_deg: float,
    ) -> NativeCallResult[Any]:
        return self._revolve_like(
            path=path,
            expected_revision=expected_revision,
            sketch_id=sketch_id,
            name=name,
            axis_ref=axis_ref,
            angle_deg=angle_deg,
            is_cut=False,
        )

    def revolve_cut(
        self,
        *,
        path: str,
        expected_revision: int,
        sketch_id: str,
        name: str,
        axis_ref: str,
        angle_deg: float,
    ) -> NativeCallResult[Any]:
        return self._revolve_like(
            path=path,
            expected_revision=expected_revision,
            sketch_id=sketch_id,
            name=name,
            axis_ref=axis_ref,
            angle_deg=angle_deg,
            is_cut=True,
        )

    def _revolve_like(
        self,
        *,
        path: str,
        expected_revision: int,
        sketch_id: str,
        name: str,
        axis_ref: str,
        angle_deg: float,
        is_cut: bool,
    ) -> NativeCallResult[Any]:
        call_id = uuid.uuid4().hex
        stage = "part_revolve_cut" if is_cut else "part_revolve"
        try:
            target = self._target(path, expected_revision)
            if not isinstance(sketch_id, str) or not sketch_id.strip():
                raise PartValidationError("sketch_id must be a non-empty string")
            if not isinstance(name, str) or not name.strip():
                raise PartValidationError("name must be a non-empty string")
            if not isinstance(axis_ref, str) or axis_ref.strip() != "profile_centerline":
                raise PartValidationError(
                    "axis_ref must be exactly 'profile_centerline' for native Revolve"
                )
            if isinstance(angle_deg, bool) or not isinstance(angle_deg, (int, float)):
                raise PartValidationError("angle_deg must be numeric")
            angle = float(angle_deg)
            if not math.isfinite(angle) or angle <= 0.0 or angle > 360.0:
                raise PartValidationError("angle_deg must be finite and in the range (0, 360]")
            spec_cls = RevolveCutSpec if is_cut else RevolveSpec
            spec = spec_cls(
                name=name,
                profile=ProfileRef(sketch_id),
                axis_ref="profile_centerline",
                angle_deg=angle,
            )
            with self._recoverable("revolve", path=path, name=name, axis_ref=axis_ref, angle_deg=angle, is_cut=is_cut):
                mutation = (
                    self.service.revolve_cut(target, spec)
                    if is_cut
                    else self.service.revolve(target, spec)
                )
            return NativeCallResult.success(
                mutation, call_id=call_id, dispatched=True
            )
        except _NativeResultInterrupt as exc:
            return exc.result
        except Exception as exc:
            return NativeCallResult.failed(
                self._semantic_failure(exc, stage),
                call_id=call_id,
                dispatched=isinstance(exc, PartMutationError),
            )

    def reconcile_revolve(
        self,
        *,
        call_id: str,
        path: str,
        name: str,
        axis_ref: str,
        angle_deg: float,
        is_cut: bool,
        timeout: float | None = None,
        _capture: bool = False,
    ) -> NativeCallResult[FeatureSnapshot]:
        """Verify an uncertain boss/cut Revolve and clear quarantine on success."""
        local_call_id = call_id
        try:
            if self.session is None or self.api is None:
                raise PartValidationError(
                    "revolve reconciliation requires a bound native session"
                )
            if not isinstance(call_id, str) or not call_id.strip():
                raise PartValidationError("call_id must be a non-empty string")
            source = self.path_policy.validate_open(path)
            if Path(source).suffix.lower() != _PART_EXT:
                raise PartValidationError(
                    "revolve reconciliation requires a native .SLDPRT document"
                )
            if not isinstance(name, str) or not name.strip():
                raise PartValidationError("name must be a non-empty string")
            if not isinstance(axis_ref, str) or axis_ref.strip() != "profile_centerline":
                raise PartValidationError(
                    "axis_ref must be exactly 'profile_centerline' for native Revolve reconciliation"
                )
            if isinstance(angle_deg, bool) or not isinstance(angle_deg, (int, float)):
                raise PartValidationError("angle_deg must be numeric")
            expected_angle = float(angle_deg)
            if (
                not math.isfinite(expected_angle)
                or expected_angle <= 0.0
                or expected_angle > 360.0
            ):
                raise PartValidationError(
                    "angle_deg must be finite and in the range (0, 360]"
                )
            if not isinstance(is_cut, bool):
                raise PartValidationError("is_cut must be boolean")
        except Exception as exc:
            return NativeCallResult.failed(
                self._semantic_failure(exc, "part_revolve_reconcile"),
                call_id=local_call_id,
                dispatched=False,
            )

        def verifier(app: Any) -> FeatureSnapshot:
            model = self.api.get_open_document(app, source)
            if model is None:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_revolve_reconcile",
                    "Target part is not open while reconciling the uncertain Revolve mutation.",
                )
            if int(self.api.document_type(model)) != 1:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_revolve_reconcile",
                    "Resolved document is not a part during Revolve reconciliation.",
                )
            actual_path = self.path_policy.canonical(
                self.api.document_path(model) or source
            )
            if actual_path != source:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_revolve_reconcile",
                    "Resolved part identity changed during Revolve reconciliation.",
                )
            feature = self.api._member(model, "FeatureByName", name)
            if feature is None:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_revolve_reconcile",
                    "Expected Revolve feature does not exist after the uncertain native call.",
                )
            native_type = str(self.api.feature_type(feature) or "").strip()
            if native_type == "ICE":
                native_type = str(
                    self.api._member(feature, "GetTypeName") or ""
                ).strip()
            accepted_types = {"RevCut", "RevolveCut"} if is_cut else {"Revolution", "Revolve"}
            if native_type not in accepted_types:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_revolve_reconcile",
                    "Feature identity exists but is not the expected boss/cut Revolve type.",
                    details={"actual_feature_type": native_type},
                )
            definition = self.api._member(feature, "GetDefinition")
            if definition is None:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_revolve_reconcile",
                    "Revolve feature definition is unavailable during reconciliation.",
                )
            null_dispatch = getattr(self.api, "null_dispatch", lambda: None)()
            if not bool(
                self.api._member(
                    definition, "AccessSelections", model, null_dispatch
                )
            ):
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_revolve_reconcile",
                    "SOLIDWORKS did not grant access to Revolve selections during reconciliation.",
                )
            try:
                axis = self.api._member(definition, "Axis")
                if axis is None:
                    raise NativeRuntimeError(
                        "reconciliation_mismatch",
                        "part_revolve_reconcile",
                        "Revolve reconciliation found no axis selection.",
                    )
                if not bool(self.api._member(axis, "ConstructionGeometry")):
                    raise NativeRuntimeError(
                        "reconciliation_mismatch",
                        "part_revolve_reconcile",
                        "Revolve axis is not construction geometry.",
                    )
                if int(self.api._member(axis, "GetType")) != 0:
                    raise NativeRuntimeError(
                        "reconciliation_mismatch",
                        "part_revolve_reconcile",
                        "Revolve axis is not a sketch line.",
                    )
                actual_angle = math.degrees(
                    float(
                        self.api._member(
                            definition, "GetRevolutionAngle", True
                        )
                    )
                )
                if not math.isclose(
                    actual_angle,
                    expected_angle,
                    rel_tol=0.0,
                    abs_tol=1e-7,
                ):
                    raise NativeRuntimeError(
                        "reconciliation_mismatch",
                        "part_revolve_reconcile",
                        "Revolve angle does not match the uncertain request.",
                        details={
                            "expected_angle_deg": expected_angle,
                            "actual_angle_deg": actual_angle,
                        },
                    )
                actual_boss = bool(
                    self.api._member(definition, "IsBossFeature")
                )
                if actual_boss is is_cut:
                    raise NativeRuntimeError(
                        "reconciliation_mismatch",
                        "part_revolve_reconcile",
                        "Revolve boss/cut state does not match the uncertain request.",
                    )
            finally:
                self.api._member(definition, "ReleaseSelectionAccess")

            rebuild = rebuild_document(
                model, self.api, max_features=_MAX_FEATURES
            )
            if not rebuild.success:
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_revolve_reconcile",
                    "Part does not rebuild cleanly after the uncertain Revolve mutation.",
                )
            if not self.api.bodies(model, 0, False):
                raise NativeRuntimeError(
                    "reconciliation_mismatch",
                    "part_revolve_reconcile",
                    "Part has no solid body after the uncertain Revolve mutation.",
                )
            return FeatureSnapshot(
                feature_id=self.api.feature_name(feature),
                name=self.api.feature_name(feature),
                kind=(
                    FeatureKind.REVOLVE_CUT
                    if is_cut
                    else FeatureKind.REVOLVE
                ),
                parameters={
                    "axis_ref": "profile_centerline",
                    "angle_deg": actual_angle,
                },
                suppressed=False,
            )

        identity = (source, name, axis_ref, float(angle_deg), is_cut)
        if _capture:
            return identity, verifier
        return self.session.reconcile(
            call_id,
            verifier,
            identity=identity,
            stage="part_revolve_reconcile",
            timeout=(
                self.default_timeout
                if timeout is None
                else max(0.0, float(timeout))
            ),
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
        _capture: bool = False,
    ) -> NativeCallResult[FeatureSnapshot]:
        """Verify an uncertain Cut mutation and clear dispatcher quarantine on success."""
        local_call_id = call_id
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

        identity = (source, name, through_all, depth_mm)
        if _capture:
            return identity, verifier
        return self.session.reconcile(
            call_id,
            verifier,
            identity=identity,
            stage="part_cut_reconcile",
            timeout=self.default_timeout if timeout is None else max(0.0, float(timeout)),
        )

    def _call_service(self, stage: str, operation: Any, *, mutation: bool) -> NativeCallResult[Any]:
        call_id = uuid.uuid4().hex
        try:
            value = operation()
            return NativeCallResult.success(value, call_id=call_id, dispatched=mutation)
        except _NativeResultInterrupt as exc:
            return exc.result
        except Exception as exc:
            return NativeCallResult.failed(
                self._semantic_failure(exc, stage),
                call_id=call_id,
                dispatched=mutation and isinstance(exc, PartMutationError),
            )

    @staticmethod
    def _name(raw: Any, label: str) -> str:
        if not isinstance(raw, str) or not raw.strip():
            raise PartValidationError(f"{label} must be a non-empty string")
        return raw.strip()

    @classmethod
    def _identity(cls, raw: Any, label: str) -> str:
        return cls._name(raw, label)

    @staticmethod
    def _number(raw: Any, label: str) -> float:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise PartValidationError(f"{label} must be numeric")
        value = float(raw)
        if not math.isfinite(value):
            raise PartValidationError(f"{label} must be finite")
        return value

    @classmethod
    def _positive_number(cls, raw: Any, label: str) -> float:
        value = cls._number(raw, label)
        if value <= 0.0:
            raise PartValidationError(f"{label} must be positive")
        return value

    @staticmethod
    def _boolean(raw: Any, label: str) -> bool:
        if not isinstance(raw, bool):
            raise PartValidationError(f"{label} must be boolean")
        return raw

    @classmethod
    def _point(cls, raw: Any, label: str) -> tuple[float, float]:
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or len(raw) != 2:
            raise PartValidationError(f"{label} must be a two-value [x_mm, y_mm] coordinate")
        return (cls._number(raw[0], f"{label}[0]"), cls._number(raw[1], f"{label}[1]"))

    @classmethod
    def _string_refs(cls, raw: Any, label: str) -> tuple[str, ...]:
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise PartValidationError(f"{label} must be an array of references")
        if not raw:
            raise PartValidationError(f"{label} must not be empty")
        if len(raw) > _MAX_PUBLIC_REFS:
            raise PartValidationError(f"{label} supports at most {_MAX_PUBLIC_REFS} references")
        values = tuple(cls._identity(item, label) for item in raw)
        if len(set(values)) != len(values):
            raise PartValidationError(f"{label} references must be unique")
        return values

    @classmethod
    def _feature_ids(cls, raw: Any, label: str) -> tuple[str, ...]:
        return cls._string_refs(raw, label)

    @classmethod
    def _edge_refs(cls, raw: Any, label: str) -> tuple[str, ...]:
        return tuple(cls._edge_ref(item, label) for item in cls._string_refs(raw, label))

    @classmethod
    def _face_refs(cls, raw: Any, label: str) -> tuple[str, ...]:
        return tuple(cls._exact_face(item, label) for item in cls._string_refs(raw, label))

    @classmethod
    def _edge_ref(cls, raw: Any, label: str) -> str:
        value = cls._identity(raw, label)
        parts = value.split(":")
        if len(parts) != 4 or parts[:2] != ["bbox", "edge"]:
            raise PartValidationError(f"{label} must use bbox:edge:<signed-axis>:<signed-axis>")
        axes: set[str] = set()
        for token in parts[2:]:
            if len(token) != 2 or token[0] not in "+-" or token[1] not in "xyz":
                raise PartValidationError(f"{label} contains an invalid bounded edge token")
            if token[1] in axes:
                raise PartValidationError(f"{label} bounded edge axes must be distinct")
            axes.add(token[1])
        return value

    @classmethod
    def _exact_face(
        cls,
        raw: Any,
        label: str,
        *,
        allowed: set[str] | frozenset[str] = _FACE_REFS,
    ) -> str:
        value = cls._identity(raw, label).lower()
        if value not in allowed:
            raise PartValidationError(
                f"{label} must be one of: {', '.join(sorted(allowed))}"
            )
        return value

    @classmethod
    def _plane_ref(cls, raw: Any, label: str) -> str:
        value = cls._identity(raw, label).lower()
        if value not in _PLANE_REFS:
            raise PartValidationError(f"{label} must be plane:front, plane:top, or plane:right")
        return value

    @classmethod
    def _plane_or_face_ref(cls, raw: Any, label: str) -> str:
        value = cls._identity(raw, label).lower()
        if value in _PLANE_REFS or value in _FACE_REFS:
            return value
        raise PartValidationError(
            f"{label} must be a standard plane reference or bounded bbox face"
        )

    @classmethod
    def _axis_feature_ref(cls, raw: Any, label: str) -> str:
        value = cls._identity(raw, label)
        if not value.startswith("feature:") or not value.split(":", 1)[1].strip():
            raise PartValidationError(f"{label} must be feature:<reference-axis-name>")
        return value

    @staticmethod
    def _pattern_count(raw: Any) -> int:
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 2 or raw > 1000:
            raise PartValidationError("count must be an integer in [2, 1000]")
        return raw

    @staticmethod
    def _hole_wizard_size(raw: Any) -> HoleWizardSize:
        if not isinstance(raw, str):
            raise PartValidationError("size must be one of M2, M4, or M6")
        try:
            size = HoleWizardSize(raw.strip().upper())
        except ValueError as exc:
            raise PartValidationError("size must be one of M2, M4, or M6") from exc
        if size not in _NATIVE_HOLE_WIZARD_SIZES:
            raise PartValidationError("size must be one of M2, M4, or M6")
        return size

    def _target(
        self,
        path: str,
        expected_revision: int,
        configuration: str | None = None,
    ) -> DocumentTarget:
        source = self.path_policy.validate_open(path)
        if Path(source).suffix.lower() != _PART_EXT:
            raise PartValidationError("parametric part feature requires a native .SLDPRT document")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise PartValidationError("expected_revision must be a non-negative integer")
        if configuration is not None and (not isinstance(configuration, str) or not configuration.strip()):
            raise PartValidationError("configuration must be a non-empty string when supplied")
        return DocumentTarget(
            source,
            expected_revision,
            "mm",
            expected_configuration=configuration.strip() if configuration is not None else None,
        )

    def _require_profile(self, target: DocumentTarget, sketch_id: str) -> None:
        port = self.profile_port
        if port is None or not callable(getattr(port, "inspect_profile", None)):
            raise PartValidationError("profile inspection dependency is unavailable")
        raw = port.inspect_profile(
            path=target.document_id,
            expected_revision=target.expected_revision,
            sketch_id=sketch_id,
        )
        if isinstance(raw, NativeCallResult):
            if raw.state is not NativeCallState.SUCCESS or raw.value is None:
                raise PartValidationError("profile inspection dependency did not return usable state")
            raw = raw.value
        if isinstance(raw, Mapping):
            closed = raw.get("closed")
            ambiguous = raw.get("ambiguous")
        else:
            closed = getattr(raw, "closed", None)
            ambiguous = getattr(raw, "ambiguous", None)
        if closed is not True:
            raise PartValidationError("profile must be explicitly closed before feature mutation")
        if ambiguous is not False:
            raise PartValidationError("profile is ambiguous and requires an explicit unambiguous profile")

    @contextmanager
    def _recoverable(self, family: str, **kwargs):
        plan = None
        if self.session is not None:
            prepared = getattr(self, "reconcile_" + family)(call_id="prepare",
                                                           _capture=True, **kwargs)
            if isinstance(prepared, NativeCallResult):
                raise _NativeResultInterrupt(prepared)
            identity, verifier = prepared
            plan = (identity, "part_" + ("simple_hole" if family == "simple_hole" else family) + "_reconcile", verifier)
        token = self._recovery.set(plan)
        try:
            yield
        finally:
            self._recovery.reset(token)

    def _execute(self, operation: Any, *, stage: str, mutation: bool) -> Any:
        assert self.session is not None
        plan = self._recovery.get() if mutation else None
        options = {}
        if plan is not None:
            identity, recovery_stage, verifier = plan
            # Captured on the STA before the mutation; the operation may change revision,
            # but recovery may not silently switch configuration.
            original_configuration = []
            original_operation = operation

            def operation(app):
                model = self.api.get_open_document(app, identity[0])
                if model is None:
                    raise NativeRuntimeError("document_not_open", stage, "Recovery target is not open.")
                original_configuration.append(self.api.active_configuration(model))
                return original_operation(app)

            def verify_original(app):
                model = self.api.get_open_document(app, identity[0])
                if model is None or not original_configuration or self.api.active_configuration(model) != original_configuration[0]:
                    raise NativeRuntimeError("reconciliation_mismatch", recovery_stage,
                                             "Original document configuration is unavailable or changed.")
                return verifier(app)

            options = dict(recovery_identity=identity, recovery_stage=recovery_stage,
                           recovery_verifier=verify_original)
        result = self.session.execute(operation, stage=stage, timeout=self.default_timeout,
                                      mutation=mutation, **options)
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
