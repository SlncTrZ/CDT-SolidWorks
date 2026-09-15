"""Parametric part service with rebuild and geometric read-back gates."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable

from cdt_solidworks.part.models import (
    BodySnapshot,
    Bounds3D,
    ChamferSpec,
    CircularPatternSpec,
    CutSpec,
    DraftSpec,
    ExtrudeSpec,
    FeatureKind,
    FeatureSnapshot,
    FilletSpec,
    HoleFact,
    HoleSpec,
    HoleWizardSpec,
    LinearPatternSpec,
    LoftSpec,
    MirrorSpec,
    PartMutationResult,
    PartPostconditions,
    PartSnapshot,
    ReferenceAxisSpec,
    ReferencePlaneSpec,
    ReferencePointSpec,
    RevolveCutSpec,
    RevolveSpec,
    RibSpec,
    ShellSpec,
    SweepSpec,
)
from cdt_solidworks.part.runtime import (
    DocumentTarget,
    MutationReceipt,
    PartSketchRuntime,
    ResolvedDocument,
)

_PARAMETER_TOLERANCE = 1e-9
_MAX_PATTERN_INSTANCES = 1000
_MAX_SELECTION_REFS = 512
_MAX_HOLE_CENTERS = 1000
_MAX_LOFT_PROFILES = 32


class PartError(RuntimeError):
    """Base error for Mechanical-90 part behavior."""


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
        self._validate_optional_postconditions(postconditions)
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
        expected_parameters: dict[str, float | str | bool | int]
        if spec.through_all:
            if spec.depth_mm is not None:
                raise PartValidationError("through-all cut must not also specify a finite depth")
            expected_parameters = {"through_all": True}
        else:
            if spec.depth_mm is None:
                raise PartValidationError("blind cut requires a positive depth")
            self._require_positive_finite(spec.depth_mm, "cut depth")
            expected_parameters = {"through_all": False, "depth_mm": spec.depth_mm}
        self._validate_optional_postconditions(postconditions)
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
        self._validate_revolve_like(target, spec.name, spec.profile.sketch_id, spec.axis_ref, spec.angle_deg)
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.REVOLVE,
            self._runtime.create_revolve,
            expected_parameters={"angle_deg": spec.angle_deg, "axis_ref": spec.axis_ref},
            postconditions=postconditions,
        )

    def revolve_cut(
        self,
        target: DocumentTarget,
        spec: RevolveCutSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_revolve_like(target, spec.name, spec.profile.sketch_id, spec.axis_ref, spec.angle_deg)
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.REVOLVE_CUT,
            self._runtime.create_revolve_cut,
            expected_parameters={"angle_deg": spec.angle_deg, "axis_ref": spec.axis_ref},
            postconditions=postconditions,
        )

    def hole(
        self,
        target: DocumentTarget,
        spec: HoleSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name(spec.name)
        self._require_positive_finite(spec.diameter_mm, "hole diameter")
        self._validate_identity(spec.face_ref, "hole face reference")
        if not spec.centers_mm:
            raise PartValidationError("hole requires at least one center")
        if len(spec.centers_mm) > _MAX_HOLE_CENTERS:
            raise PartValidationError(f"hole supports at most {_MAX_HOLE_CENTERS} centers")
        for center in spec.centers_mm:
            if len(center) != 2 or not all(math.isfinite(value) for value in center):
                raise PartValidationError("hole center coordinates must be finite x/y pairs")
        expected: dict[str, float | str | bool | int] = {
            "diameter_mm": spec.diameter_mm,
            "face_ref": spec.face_ref,
            "center_count": len(spec.centers_mm),
            "through_all": spec.through_all,
        }
        if spec.through_all:
            if spec.depth_mm is not None:
                raise PartValidationError("through-all hole must not also specify a finite depth")
        else:
            if spec.depth_mm is None:
                raise PartValidationError("blind hole requires a positive depth")
            self._require_positive_finite(spec.depth_mm, "hole depth")
            expected["depth_mm"] = spec.depth_mm
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.HOLE,
            self._runtime.create_hole,
            expected_parameters=expected,
            postconditions=postconditions,
        )

    def hole_wizard(
        self,
        target: DocumentTarget,
        spec: HoleWizardSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name(spec.name)
        self._validate_identity(spec.face_ref, "Hole Wizard face reference")
        if len(spec.center_mm) != 2 or not all(math.isfinite(value) for value in spec.center_mm):
            raise PartValidationError("Hole Wizard center must be a finite x/y pair")
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.HOLE,
            self._runtime.create_hole_wizard,
            expected_parameters={
                "wizard_standard": "ANSI Metric",
                "wizard_fastener": "Flat Head Screw - ANSI B18.6.7M",
                "wizard_size": spec.size.value,
                "face_ref": spec.face_ref,
                "center_count": 1,
                "center_x_mm": float(spec.center_mm[0]),
                "center_y_mm": float(spec.center_mm[1]),
                "through_all": True,
            },
            postconditions=postconditions,
        )

    def fillet(
        self,
        target: DocumentTarget,
        spec: FilletSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name(spec.name)
        self._validate_refs(spec.edge_refs, "fillet edge")
        self._require_positive_finite(spec.radius_mm, "fillet radius")
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.FILLET,
            self._runtime.create_fillet,
            expected_parameters={
                "radius_mm": spec.radius_mm,
                "tangent_propagation": spec.tangent_propagation,
            },
            postconditions=postconditions,
        )

    def chamfer(
        self,
        target: DocumentTarget,
        spec: ChamferSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name(spec.name)
        self._validate_refs(spec.edge_refs, "chamfer edge")
        self._require_positive_finite(spec.distance_mm, "chamfer distance")
        if not math.isfinite(spec.angle_deg) or spec.angle_deg <= 0 or spec.angle_deg >= 90:
            raise PartValidationError("chamfer angle must be finite and in the range (0, 90)")
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.CHAMFER,
            self._runtime.create_chamfer,
            expected_parameters={"distance_mm": spec.distance_mm, "angle_deg": spec.angle_deg},
            postconditions=postconditions,
        )

    def shell(
        self,
        target: DocumentTarget,
        spec: ShellSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name(spec.name)
        self._validate_refs(spec.face_refs, "shell face")
        self._require_positive_finite(spec.thickness_mm, "shell thickness")
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.SHELL,
            self._runtime.create_shell,
            expected_parameters={"thickness_mm": spec.thickness_mm, "outward": spec.outward},
            postconditions=postconditions,
        )

    def draft(
        self,
        target: DocumentTarget,
        spec: DraftSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name(spec.name)
        self._validate_refs(spec.face_refs, "draft face")
        self._validate_identity(spec.neutral_plane_ref, "draft neutral plane reference")
        if not math.isfinite(spec.angle_deg) or spec.angle_deg <= 0 or spec.angle_deg >= 90:
            raise PartValidationError("draft angle must be finite and in the range (0, 90)")
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.DRAFT,
            self._runtime.create_draft,
            expected_parameters={
                "angle_deg": spec.angle_deg,
                "reverse_direction": spec.reverse_direction,
            },
            postconditions=postconditions,
        )

    def rib(
        self,
        target: DocumentTarget,
        spec: RibSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name_and_profile(spec.name, spec.profile.sketch_id)
        self._require_positive_finite(spec.thickness_mm, "rib thickness")
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.RIB,
            self._runtime.create_rib,
            expected_parameters={"thickness_mm": spec.thickness_mm, "both_sides": spec.both_sides},
            postconditions=postconditions,
        )

    def linear_pattern(
        self,
        target: DocumentTarget,
        spec: LinearPatternSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name(spec.name)
        self._validate_refs(spec.seed_feature_ids, "linear pattern seed")
        self._validate_identity(spec.direction_ref, "linear pattern direction reference")
        self._validate_pattern_count(spec.count)
        self._require_positive_finite(spec.spacing_mm, "linear pattern spacing")
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.LINEAR_PATTERN,
            self._runtime.create_linear_pattern,
            expected_parameters={
                "count": spec.count,
                "spacing_mm": spec.spacing_mm,
                "geometry_pattern": spec.geometry_pattern,
            },
            postconditions=postconditions,
        )

    def circular_pattern(
        self,
        target: DocumentTarget,
        spec: CircularPatternSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name(spec.name)
        self._validate_refs(spec.seed_feature_ids, "circular pattern seed")
        self._validate_identity(spec.axis_ref, "circular pattern axis reference")
        self._validate_pattern_count(spec.count)
        if not math.isfinite(spec.angle_deg) or spec.angle_deg <= 0 or spec.angle_deg > 360:
            raise PartValidationError("circular pattern angle must be finite and in the range (0, 360]")
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.CIRCULAR_PATTERN,
            self._runtime.create_circular_pattern,
            expected_parameters={
                "count": spec.count,
                "angle_deg": spec.angle_deg,
                "geometry_pattern": spec.geometry_pattern,
            },
            postconditions=postconditions,
        )

    def mirror(
        self,
        target: DocumentTarget,
        spec: MirrorSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name(spec.name)
        self._validate_refs(spec.seed_feature_ids, "mirror seed")
        self._validate_identity(spec.mirror_ref, "mirror reference")
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.MIRROR,
            self._runtime.create_mirror,
            expected_parameters={"geometry_pattern": spec.geometry_pattern},
            postconditions=postconditions,
        )

    def sweep(
        self,
        target: DocumentTarget,
        spec: SweepSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name_and_profile(spec.name, spec.profile.sketch_id)
        self._validate_identity(spec.path.sketch_id, "sweep path profile reference")
        if spec.profile.sketch_id == spec.path.sketch_id:
            raise PartValidationError("sweep profile and path references must be distinct")
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.SWEEP,
            self._runtime.create_sweep,
            expected_parameters={"path_sketch_id": spec.path.sketch_id},
            postconditions=postconditions,
        )

    def loft(
        self,
        target: DocumentTarget,
        spec: LoftSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name(spec.name)
        if len(spec.profiles) < 2:
            raise PartValidationError("loft requires at least two profiles")
        if len(spec.profiles) > _MAX_LOFT_PROFILES:
            raise PartValidationError(f"loft supports at most {_MAX_LOFT_PROFILES} profiles")
        profile_ids = tuple(profile.sketch_id for profile in spec.profiles)
        self._validate_refs(profile_ids, "loft profile")
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.LOFT,
            self._runtime.create_loft,
            expected_parameters={"profile_count": len(spec.profiles), "closed": spec.closed},
            postconditions=postconditions,
        )

    def reference_plane(
        self,
        target: DocumentTarget,
        spec: ReferencePlaneSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name(spec.name)
        self._validate_identity(spec.reference, "reference plane source reference")
        if not math.isfinite(spec.offset_mm):
            raise PartValidationError("reference plane offset must be finite")
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.REFERENCE_PLANE,
            self._runtime.create_reference_plane,
            expected_parameters={"offset_mm": abs(spec.offset_mm)},
            postconditions=postconditions,
            require_body=False,
        )

    def reference_axis(
        self,
        target: DocumentTarget,
        spec: ReferenceAxisSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name(spec.name)
        self._validate_identity(spec.first_ref, "reference axis first reference")
        self._validate_identity(spec.second_ref, "reference axis second reference")
        if spec.first_ref == spec.second_ref:
            raise PartValidationError("reference axis requires distinct references")
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.REFERENCE_AXIS,
            self._runtime.create_reference_axis,
            expected_parameters={},
            postconditions=postconditions,
            require_body=False,
        )

    def reference_point(
        self,
        target: DocumentTarget,
        spec: ReferencePointSpec,
        *,
        postconditions: PartPostconditions | None = None,
    ) -> PartMutationResult:
        self._validate_target(target)
        self._validate_name(spec.name)
        self._validate_identity(spec.reference, "reference point source reference")
        self._validate_optional_postconditions(postconditions)
        return self._mutate_feature(
            target,
            spec,
            FeatureKind.REFERENCE_POINT,
            self._runtime.create_reference_point,
            expected_parameters={},
            postconditions=postconditions,
            require_body=False,
        )

    def rename_feature(
        self,
        target: DocumentTarget,
        feature_id: str,
        new_name: str,
    ) -> FeatureSnapshot:
        self._validate_target(target)
        self._validate_identity(feature_id, "feature identity")
        self._validate_name(new_name)
        document = self._resolve_part_document(target)
        receipt = self._runtime.rename_feature(document, feature_id, new_name)
        if receipt.object_id != new_name:
            raise PartMutationError("feature rename mutation returned the wrong identity")
        self._require_clean_rebuild(document, "feature rename mutation")
        feature = self._runtime.get_feature(document, new_name)
        if feature is None:
            raise PartMutationError("feature rename read-back failed: renamed feature was not found")
        if feature.feature_id != new_name or feature.name != new_name:
            raise PartMutationError("feature rename read-back identity mismatch")
        return feature

    def set_feature_suppressed(
        self,
        target: DocumentTarget,
        feature_id: str,
        suppressed: bool,
    ) -> FeatureSnapshot:
        self._validate_target(target)
        self._validate_identity(feature_id, "feature identity")
        document = self._resolve_part_document(target)
        receipt = self._runtime.set_feature_suppressed(document, feature_id, bool(suppressed))
        if receipt.object_id != feature_id:
            raise PartMutationError("feature suppression mutation returned the wrong identity")
        self._require_clean_rebuild(document, "feature suppression mutation")
        feature = self._runtime.get_feature(document, feature_id)
        if feature is None:
            raise PartMutationError("feature suppression read-back failed: feature was not found")
        if feature.feature_id != feature_id:
            raise PartMutationError("feature suppression read-back identity mismatch")
        if feature.suppressed is not bool(suppressed):
            raise PartMutationError(
                f"feature suppression read-back mismatch: expected {suppressed}, got {feature.suppressed}"
            )
        return feature

    def set_feature_parameter(
        self,
        target: DocumentTarget,
        feature_id: str,
        parameter: str,
        value: float,
    ) -> FeatureSnapshot:
        self._validate_target(target)
        self._validate_identity(feature_id, "feature identity")
        if parameter not in {"depth_mm", "radius_mm"}:
            raise PartValidationError(f"unsupported feature parameter edit: {parameter!r}")
        self._require_positive_finite(value, parameter)
        document = self._resolve_part_document(target)
        expected_kind: FeatureKind
        if parameter == "radius_mm":
            # Preserve the historical fillet call contract; the native runtime itself
            # verifies that the target is a promoted constant-radius fillet before write.
            expected_kind = FeatureKind.FILLET
        else:
            current = self._runtime.get_feature(document, feature_id)
            if current is None:
                raise PartMutationError(f"feature read-back failed: {feature_id!r} was not found")
            if current.kind not in {FeatureKind.EXTRUDE, FeatureKind.CUT}:
                raise PartValidationError(
                    f"unsupported feature parameter edit: {current.kind.value}.{parameter}"
                )
            if current.kind is FeatureKind.CUT and bool(current.parameters.get("through_all")):
                raise PartValidationError("through-all cut does not expose a finite depth_mm parameter")
            expected_kind = current.kind
        receipt = self._runtime.set_feature_parameter(document, feature_id, parameter, float(value))
        if receipt.object_id != feature_id:
            raise PartMutationError("feature parameter mutation returned the wrong identity")
        self._require_clean_rebuild(document, "feature parameter mutation")
        feature = self._runtime.get_feature(document, feature_id)
        if feature is None:
            raise PartMutationError("feature parameter read-back failed: feature was not found")
        if feature.feature_id != feature_id or feature.kind is not expected_kind:
            raise PartMutationError("feature parameter read-back identity/type mismatch")
        actual = feature.parameters.get(parameter)
        if not isinstance(actual, (float, int)) or isinstance(actual, bool) or not math.isclose(
            float(actual), float(value), rel_tol=0.0, abs_tol=_PARAMETER_TOLERANCE
        ):
            raise PartMutationError(
                f"feature parameter read-back mismatch for {parameter!r}: expected {value}, got {actual}"
            )
        return feature

    def get_feature_parameters(
        self, target: DocumentTarget, feature_id: str
    ) -> dict[str, float | str | bool | int]:
        """Return the bounded parameter map from an identity-validated feature snapshot."""
        feature = self.get_feature(target, feature_id)
        return dict(feature.parameters)

    def get_feature(self, target: DocumentTarget, feature_id: str) -> FeatureSnapshot:
        self._validate_target(target)
        self._validate_identity(feature_id, "feature identity")
        document = self._resolve_part_document(target)
        feature = self._runtime.get_feature(document, feature_id)
        if feature is None:
            raise PartMutationError(f"feature read-back failed: {feature_id!r} was not found")
        return feature

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
        expected_parameters: dict[str, float | str | bool | int],
        postconditions: PartPostconditions | None,
        require_body: bool = True,
    ) -> PartMutationResult:
        document = self._resolve_part_document(target)
        receipt = dispatch(document, spec)
        if not receipt.object_id.strip():
            raise PartMutationError("native feature mutation returned an empty identity")

        self._require_clean_rebuild(document, "feature mutation")
        feature = self._runtime.get_feature(document, receipt.object_id)
        if feature is None:
            raise PartMutationError("feature read-back failed: created feature was not found")
        self._verify_feature(feature, receipt.object_id, expected_kind, spec, expected_parameters)

        bodies = self._runtime.list_bodies(document)
        if require_body and not bodies:
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
            raise PartValidationError("Mechanical-90 part dimensions currently require millimeter document units")
        if target.expected_configuration is not None and not target.expected_configuration.strip():
            raise PartValidationError("expected configuration must not be empty when supplied")

    @classmethod
    def _validate_revolve_like(
        cls,
        target: DocumentTarget,
        name: str,
        profile_id: str,
        axis_ref: str,
        angle_deg: float,
    ) -> None:
        cls._validate_target(target)
        cls._validate_name_and_profile(name, profile_id)
        cls._validate_identity(axis_ref, "revolve axis reference")
        if not math.isfinite(angle_deg) or angle_deg <= 0 or angle_deg > 360:
            raise PartValidationError("revolve angle must be finite and in the range (0, 360]")

    @classmethod
    def _validate_name_and_profile(cls, name: str, sketch_id: str) -> None:
        cls._validate_name(name)
        cls._validate_identity(sketch_id, "feature profile reference")

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name.strip():
            raise PartValidationError("feature name must not be empty")

    @staticmethod
    def _validate_identity(value: str, label: str) -> None:
        if not value.strip():
            raise PartValidationError(f"{label} must not be empty")

    @classmethod
    def _validate_refs(cls, refs: Iterable[str], label: str) -> None:
        normalized = tuple(refs)
        if not normalized:
            raise PartValidationError(f"{label} list must not be empty")
        if len(normalized) > _MAX_SELECTION_REFS:
            raise PartValidationError(f"{label} list exceeds {_MAX_SELECTION_REFS} bounded references")
        for value in normalized:
            cls._validate_identity(value, f"{label} identity")
        if len(set(normalized)) != len(normalized):
            raise PartValidationError(f"{label} identities must be unique")

    @staticmethod
    def _validate_pattern_count(count: int) -> None:
        if isinstance(count, bool) or count < 2 or count > _MAX_PATTERN_INSTANCES:
            raise PartValidationError(f"pattern count must be in [2, {_MAX_PATTERN_INSTANCES}]")

    @staticmethod
    def _require_positive_finite(value: float, label: str) -> None:
        if not math.isfinite(value) or value <= 0:
            raise PartValidationError(f"{label} must be positive and finite")

    @classmethod
    def _validate_optional_postconditions(cls, postconditions: PartPostconditions | None) -> None:
        if postconditions is not None:
            cls._validate_postconditions(postconditions)

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
        if (
            target.expected_configuration is not None
            and document.configuration != target.expected_configuration
        ):
            raise PartContextError(
                "document configuration changed from "
                f"{target.expected_configuration!r} to {document.configuration!r}"
            )
        return document

    def _require_clean_rebuild(self, document: ResolvedDocument, label: str) -> None:
        rebuild = self._runtime.rebuild(document)
        if not rebuild.ok:
            detail = rebuild.error_code or rebuild.message or "unknown rebuild error"
            raise PartMutationError(f"rebuild failed after {label}: {detail}")

    @classmethod
    def _verify_feature(
        cls,
        feature: FeatureSnapshot,
        expected_id: str,
        expected_kind: FeatureKind,
        spec: object,
        expected_parameters: dict[str, float | str | bool | int],
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
                if not isinstance(actual, (float, int)) or isinstance(actual, bool) or not math.isclose(
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
        if postconditions.body_count is not None and len(bodies) != postconditions.body_count:
            raise PartMutationError(
                f"body count postcondition mismatch: expected {postconditions.body_count}, got {len(bodies)}"
            )

        if postconditions.bounds is not None:
            if len(postconditions.bounds) != len(bodies):
                raise PartMutationError("bounds postcondition count does not match body read-back count")
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
