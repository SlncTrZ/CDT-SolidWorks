"""Parametric part service with rebuild and geometric read-back gates."""

from __future__ import annotations

import math
from collections.abc import Callable

from cdt_solidworks.part.models import (
    BodySnapshot,
    Bounds3D,
    CutSpec,
    ExtrudeSpec,
    FeatureKind,
    FeatureSnapshot,
    HoleFact,
    PartMutationResult,
    PartPostconditions,
    PartSnapshot,
    RevolveSpec,
)
from cdt_solidworks.part.runtime import (
    DocumentTarget,
    MutationReceipt,
    PartSketchRuntime,
    ResolvedDocument,
)

_PARAMETER_TOLERANCE = 1e-9


class PartError(RuntimeError):
    """Base error for lane-C part behavior."""


class PartValidationError(PartError):
    """Input is invalid before any native side effect."""


class PartContextError(PartError):
    """Explicit document/context identity is unsafe for mutation."""


class PartMutationError(PartError):
    """Mutation dispatched but rebuild/read-back acceptance failed."""


class PartService:
    """Coordinates bounded parametric feature operations against an injected runtime."""

    def __init__(self, runtime: PartSketchRuntime) -> None:
        self._runtime = runtime

    def extrude(
        self,
        target: DocumentTarget,
        spec: ExtrudeSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name_and_profile(spec.name, spec.profile.sketch_id)
        self._require_positive_finite(spec.depth_mm, "extrude depth")
        if postconditions is not None:
            self._validate_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.EXTRUDE,
            self._runtime.create_extrude,
            expected_parameters={"depth_mm": spec.depth_mm},
            postconditions=postconditions,
        )

    def cut(
        self,
        target: DocumentTarget,
        spec: CutSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name_and_profile(spec.name, spec.profile.sketch_id)
        if spec.through_all:
            if spec.depth_mm is not None:
                raise PartValidationError("through-all cut must not also specify a finite depth")
            expected_parameters: dict[str, float | str | bool] = {"through_all": True}
        else:
            if spec.depth_mm is None:
                raise PartValidationError("blind cut requires a positive depth")
            self._require_positive_finite(spec.depth_mm, "cut depth")
            expected_parameters = {"through_all": False, "depth_mm": spec.depth_mm}
        if postconditions is not None:
            self._validate_postconditions(postconditions)

        return self._mutate_feature(
            target,
            spec,
            FeatureKind.CUT,
            self._runtime.create_cut,
            expected_parameters=expected_parameters,
            postconditions=postconditions,
        )

    def revolve(
        self,
        target: DocumentTarget,
        spec: RevolveSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name_and_profile(spec.name, spec.profile.sketch_id)
        if not spec.axis_ref.strip():
            raise PartValidationError("revolve axis reference must not be empty")
        if not math.isfinite(spec.angle_deg) or spec.angle_deg <= 0 or spec.angle_deg > 360:
            raise PartValidationError("revolve angle must be finite and in the range (0, 360]")
        if postconditions is not None:
            self._validate_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.REVOLVE,
            self._runtime.create_revolve,
            expected_parameters={"angle_deg": spec.angle_deg, "axis_ref": spec.axis_ref},
            postconditions=postconditions,
        )

    def inspect(self, target: DocumentTarget) -> PartSnapshot:
        self._validate_target(target)
        document = self._resolve_part_document(target)
        return PartSnapshot(
            features=self._runtime.list_features(document),
            bodies=self._runtime.list_bodies(document),
        )

    def _mutate_feature(
        self,
        target: DocumentTarget,
        spec: object,
        expected_kind: FeatureKind,
        dispatch: Callable[[ResolvedDocument, object], MutationReceipt],
        *,
        expected_parameters: dict[str, float | str | bool],
        postconditions: PartPostconditions | None,
    ) -> PartMutationResult:
        document = self._resolve_part_document(target)
        receipt = dispatch(document, spec)
        if not receipt.object_id.strip():
            raise PartMutationError("native feature mutation returned an empty identity")

        rebuild = self._runtime.rebuild(document)
        if not rebuild.ok:
            detail = rebuild.error_code or rebuild.message or "unknown rebuild error"
            raise PartMutationError(f"rebuild failed after feature mutation: {detail}")

        feature = self._runtime.get_feature(document, receipt.object_id)
        if feature is None:
            raise PartMutationError("feature read-back failed: created feature was not found")
        self._verify_feature(feature, receipt.object_id, expected_kind, spec, expected_parameters)

        bodies = self._runtime.list_bodies(document)
        if not bodies:
            raise PartMutationError("body read-back failed: mutation left the part without a body")
        if postconditions is not None:
            self._verify_postconditions(bodies, postconditions)

        return PartMutationResult(feature=feature, bodies=bodies)

    @staticmethod
    def _validate_target(target: DocumentTarget) -> None:
        if not target.document_id.strip():
            raise PartValidationError("document identity must not be empty")
        if target.expected_revision < 0:
            raise PartValidationError("document revision must be non-negative")
        if target.expected_units != "mm":
            raise PartValidationError("W1 part dimensions currently require millimeter document units")

    @staticmethod
    def _validate_name_and_profile(name: str, sketch_id: str) -> None:
        if not name.strip():
            raise PartValidationError("feature name must not be empty")
        if not sketch_id.strip():
            raise PartValidationError("feature profile reference must not be empty")

    @staticmethod
    def _require_positive_finite(value: float, label: str) -> None:
        if not math.isfinite(value) or value <= 0:
            raise PartValidationError(f"{label} must be positive and finite")

    def _resolve_part_document(self, target: DocumentTarget) -> ResolvedDocument:
        document = self._runtime.resolve_document(target)
        if document.document_id != target.document_id:
            raise PartContextError("resolved document identity does not match requested document identity")
        if document.revision != target.expected_revision:
            raise PartContextError(
                f"stale document context: expected revision {target.expected_revision}, got {document.revision}"
            )
        if document.document_type.lower() != "part":
            raise PartContextError("parametric feature mutation requires a part document")
        if document.units != target.expected_units:
            raise PartContextError(
                f"document units changed from {target.expected_units!r} to {document.units!r}"
            )
        return document

    @classmethod
    def _verify_feature(
        cls,
        feature: FeatureSnapshot,
        expected_id: str,
        expected_kind: FeatureKind,
        spec: object,
        expected_parameters: dict[str, float | str | bool],
    ) -> None:
        if feature.feature_id != expected_id:
            raise PartMutationError("feature read-back identity mismatch")
        expected_name = getattr(spec, "name", None)
        if feature.name != expected_name:
            raise PartMutationError(
                f"feature read-back name mismatch: expected {expected_name!r}, got {feature.name!r}"
            )
        if feature.kind is not expected_kind:
            raise PartMutationError(
                f"feature read-back kind mismatch: expected {expected_kind.value}, got {feature.kind.value}"
            )

        for key, expected in expected_parameters.items():
            if key not in feature.parameters:
                raise PartMutationError(f"feature read-back missing parameter {key!r}")
            actual = feature.parameters[key]
            if isinstance(expected, float):
                if not isinstance(actual, (float, int)) or not math.isclose(
                    float(actual), expected, rel_tol=0.0, abs_tol=_PARAMETER_TOLERANCE
                ):
                    raise PartMutationError(
                        f"feature read-back parameter {key} mismatch: expected {expected}, got {actual}"
                    )
            elif actual != expected:
                raise PartMutationError(
                    f"feature read-back parameter {key} mismatch: expected {expected!r}, got {actual!r}"
                )

    @staticmethod
    def _validate_postconditions(postconditions: PartPostconditions) -> None:
        tolerance = postconditions.tolerance_mm
        if not math.isfinite(tolerance) or tolerance < 0:
            raise PartValidationError("postcondition tolerance must be finite and non-negative")
        if postconditions.body_count is not None and postconditions.body_count < 0:
            raise PartValidationError("expected body count must be non-negative")
        if (
            postconditions.body_count is not None
            and postconditions.bounds is not None
            and len(postconditions.bounds) != postconditions.body_count
        ):
            raise PartValidationError("expected bounds count must match expected body count")
        if postconditions.bounds is not None:
            for bounds in postconditions.bounds:
                values = (
                    bounds.min_x_mm,
                    bounds.min_y_mm,
                    bounds.min_z_mm,
                    bounds.max_x_mm,
                    bounds.max_y_mm,
                    bounds.max_z_mm,
                )
                if not all(math.isfinite(value) for value in values):
                    raise PartValidationError("expected body bounds must be finite")
                if (
                    bounds.min_x_mm > bounds.max_x_mm
                    or bounds.min_y_mm > bounds.max_y_mm
                    or bounds.min_z_mm > bounds.max_z_mm
                ):
                    raise PartValidationError("expected body bounds min values must not exceed max values")
        if postconditions.holes is not None:
            for hole in postconditions.holes:
                if not all(
                    math.isfinite(value)
                    for value in (hole.center_x_mm, hole.center_y_mm, hole.radius_mm)
                ):
                    raise PartValidationError("expected hole geometry must be finite")
                if hole.radius_mm <= 0:
                    raise PartValidationError("expected hole radius must be positive")

    @classmethod
    def _verify_postconditions(
        cls,
        bodies: tuple[BodySnapshot, ...],
        postconditions: PartPostconditions,
    ) -> None:
        tolerance = postconditions.tolerance_mm
        if postconditions.body_count is not None:
            if len(bodies) != postconditions.body_count:
                raise PartMutationError(
                    f"body count postcondition mismatch: expected {postconditions.body_count}, got {len(bodies)}"
                )

        if postconditions.bounds is not None:
            if len(postconditions.bounds) != len(bodies):
                raise PartMutationError(
                    "bounds postcondition count does not match body read-back count"
                )
            for index, (actual_body, expected_bounds) in enumerate(zip(bodies, postconditions.bounds, strict=True)):
                if not cls._bounds_close(actual_body.bounds, expected_bounds, tolerance):
                    raise PartMutationError(f"body {index} bounds postcondition mismatch")

        if postconditions.holes is not None:
            actual_holes = tuple(hole for body in bodies for hole in body.holes)
            if len(actual_holes) != len(postconditions.holes):
                raise PartMutationError(
                    f"hole count postcondition mismatch: expected {len(postconditions.holes)}, got {len(actual_holes)}"
                )
            unmatched = list(actual_holes)
            for expected in postconditions.holes:
                match_index = next(
                    (
                        index
                        for index, actual in enumerate(unmatched)
                        if cls._hole_close(actual, expected, tolerance)
                    ),
                    None,
                )
                if match_index is None:
                    raise PartMutationError(
                        "hole geometry postcondition mismatch for "
                        f"center=({expected.center_x_mm}, {expected.center_y_mm}), radius={expected.radius_mm}"
                    )
                unmatched.pop(match_index)

    @staticmethod
    def _bounds_close(actual: Bounds3D, expected: Bounds3D, tolerance: float) -> bool:
        return all(
            math.isclose(a, e, rel_tol=0.0, abs_tol=tolerance)
            for a, e in zip(
                (
                    actual.min_x_mm,
                    actual.min_y_mm,
                    actual.min_z_mm,
                    actual.max_x_mm,
                    actual.max_y_mm,
                    actual.max_z_mm,
                ),
                (
                    expected.min_x_mm,
                    expected.min_y_mm,
                    expected.min_z_mm,
                    expected.max_x_mm,
                    expected.max_y_mm,
                    expected.max_z_mm,
                ),
                strict=True,
            )
        )

    @staticmethod
    def _hole_close(actual: HoleFact, expected: HoleFact, tolerance: float) -> bool:
        return (
            actual.through is expected.through
            and math.isclose(actual.center_x_mm, expected.center_x_mm, rel_tol=0.0, abs_tol=tolerance)
            and math.isclose(actual.center_y_mm, expected.center_y_mm, rel_tol=0.0, abs_tol=tolerance)
            and math.isclose(actual.radius_mm, expected.radius_mm, rel_tol=0.0, abs_tol=tolerance)
        )
