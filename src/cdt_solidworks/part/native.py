"""Part Native — Finite runtime for evidence-promoted parametric features.
Wing: Mechanical 90 | Topic: parametric-part | Updated: 2026-09-13
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Protocol, TypeVar

from cdt_solidworks.native.errors import NativeRuntimeError
from cdt_solidworks.part.models import (
    BodySnapshot,
    Bounds3D,
    ChamferSpec,
    CircularPatternSpec,
    CutSpec,
    DraftSpec,
    FeatureKind,
    FeatureSnapshot,
    FilletSpec,
    HoleSpec,
    HoleWizardSize,
    HoleWizardSpec,
    LinearPatternSpec,
    MirrorSpec,
    ReferenceAxisSpec,
    ReferencePlaneSpec,
    ReferencePointSpec,
    RevolveCutSpec,
    RevolveSpec,
    RibSpec,
    ShellSpec,
)
from cdt_solidworks.part.runtime import DocumentTarget, MutationReceipt, RebuildResult, ResolvedDocument

T = TypeVar("T")
_SW_END_BLIND = 0
_SW_END_THROUGH_ALL = 1
_SW_START_SKETCH_PLANE = 0
_SW_SEL_REVOLVE_AXIS = 16
_SW_SKETCH_LINE = 0
_PROFILE_CENTERLINE_AXIS = "profile_centerline"
_BBOX_PLUS_Z_FACE = "bbox:+z"
_SW_SEL_EDGES = 1
_SW_SEL_FACES = 2
_SW_SEL_VERTICES = 3
_SW_SELECT_DEFAULT = 0
_SW_FM_FILLET = 1
_SW_FM_CIRCULAR_PATTERN = 5
_SW_FM_LINEAR_PATTERN = 6
_SW_CONST_RADIUS_FILLET = 0
_SW_FEATURE_FILLET_CIRCULAR = 0
_SW_FILLET_OVERFLOW_DEFAULT = 0
_SW_CHAMFER_ANGLE_DISTANCE = 1
_SW_NEUTRAL_PLANE_DRAFT = 0
_SW_PATTERN_SPACING_AND_INSTANCES = 0
_SW_REF_PLANE_DISTANCE = 8
_SW_REF_PLANE_FLIP = 256
_SW_REF_POINT_FACE_CENTER = 4
_SW_SUPPRESS_FEATURE = 0
_SW_UNSUPPRESS_FEATURE = 1
_SW_THIS_CONFIGURATION = 1
_MAX_FEATURE_TRAVERSAL = 4096
_SW_HOLE_WIZARD_DEFINITION_TYPE = 25
_SW_HOLE_WIZARD_STANDARD_ANSI_METRIC = 1
_SW_HOLE_WIZARD_TYPE_COUNTERSINK = 1
_SW_HOLE_WIZARD_FASTENER_FLAT_HEAD_ANSI = 36
_SW_HOLE_WIZARD_FIT_NORMAL = 1
_HOLE_WIZARD_STANDARD = "ANSI Metric"
_HOLE_WIZARD_FASTENER = "Flat Head Screw - ANSI B18.6.7M"
_HOLE_WIZARD_SIZES = frozenset(item.value for item in HoleWizardSize)


@dataclass(frozen=True, slots=True)
class NativePartBinding:
    model: Any
    document_id: str
    revision: int
    units: str
    document_type: str = "part"
    configuration: str | None = None


class SerializedPartExecutor(Protocol):
    def __call__(self, operation: Callable[[Any], T], *, stage: str, mutation: bool) -> T: ...


class PartNativeRuntime:
    """Native implementation for the currently promoted part-feature subset."""

    def __init__(
        self,
        *,
        executor: SerializedPartExecutor,
        binding_resolver: Callable[[Any, DocumentTarget], NativePartBinding],
        member: Callable[..., Any],
        feature_name: Callable[[Any], str],
        feature_type: Callable[[Any], str],
        bodies: Callable[[Any, int, bool], tuple[Any, ...]],
        body_name: Callable[[Any], str],
        rebuild_verifier: Callable[[Any], RebuildResult],
        cut_profile_validator: Callable[[Any, Any, str], None] | None = None,
        revolve_profile_validator: Callable[[Any, Any, str], None] | None = None,
        null_dispatch: Callable[[], Any] | None = None,
    ) -> None:
        self._execute = executor
        self._resolve_binding = binding_resolver
        self._member = member
        self._feature_name = feature_name
        self._feature_type = feature_type
        self._bodies = bodies
        self._body_name = body_name
        self._rebuild = rebuild_verifier
        self._cut_profile_validator = cut_profile_validator
        self._revolve_profile_validator = revolve_profile_validator
        self._null_dispatch = null_dispatch or (lambda: None)

    def resolve_document(self, target: DocumentTarget) -> ResolvedDocument:
        def operation(app: Any) -> ResolvedDocument:
            binding = self._resolve_binding(app, target)
            return ResolvedDocument(
                document_id=binding.document_id,
                revision=binding.revision,
                document_type=binding.document_type,
                units=binding.units,
                configuration=binding.configuration,
            )

        return self._execute(operation, stage="part_resolve_document", mutation=False)

    def create_cut(self, document: ResolvedDocument, spec: CutSpec) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            binding = self._binding_from_document(app, document)
            model = binding.model
            profile = self._member(model, "FeatureByName", spec.profile.sketch_id)
            if profile is None or self._native_feature_type(profile) != "ProfileFeature":
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_cut_native",
                    "The requested profile sketch could not be resolved as a native sketch feature.",
                )
            if self._cut_profile_validator is not None:
                self._cut_profile_validator(model, profile, spec.profile.sketch_id)
            self._member(model, "ClearSelection2", True)
            if not bool(self._member(profile, "Select2", False, 0)):
                raise NativeRuntimeError(
                    "cad_selection_failed",
                    "part_cut_native",
                    "The requested profile sketch could not be selected.",
                )
            manager = self._member(model, "FeatureManager")
            end_condition = _SW_END_THROUGH_ALL if spec.through_all else _SW_END_BLIND
            depth_m = 0.0 if spec.through_all else float(spec.depth_mm or 0.0) / 1000.0
            feature = self._member(
                manager,
                "FeatureCut3",
                True,   # single direction
                False,  # do not flip side to cut
                True,   # reverse default cut direction so it follows the sketch normal
                end_condition,
                _SW_END_BLIND,
                depth_m,
                0.0,
                False,
                False,
                False,
                False,
                0.0,
                0.0,
                False,
                False,
                False,
                False,
                False,  # normal cut: non-sheet-metal feature
                False,  # feature scope off
                True,   # auto-select affected bodies
                False,
                True,
                False,
                _SW_START_SKETCH_PLANE,
                0.0,
                False,
            )
            if feature is None:
                raise NativeRuntimeError(
                    "cad_mutation_failed",
                    "part_cut_native",
                    "SOLIDWORKS did not create the requested cut-extrude feature.",
                )
            try:
                feature.Name = spec.name
            except Exception:
                pass
            identity = self._feature_name(feature).strip()
            if not identity:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_cut_native",
                    "Created cut-extrude returned an empty feature identity.",
                )
            return MutationReceipt(identity)

        return self._execute(operation, stage="part_cut_native", mutation=True)

    def create_hole(
        self, document: ResolvedDocument, spec: HoleSpec
    ) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            binding = self._binding_from_document(app, document)
            model = binding.model
            if spec.face_ref != _BBOX_PLUS_Z_FACE:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_native",
                    "Native simple hole currently accepts only face_ref='bbox:+z'.",
                )
            if len(spec.centers_mm) != 1:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_native",
                    "Native simple hole currently accepts exactly one center.",
                    details={"center_count": len(spec.centers_mm)},
                )
            center_x_mm, center_y_mm = spec.centers_mm[0]
            bodies = self._as_tuple(self._bodies(model, 0, False))
            if len(bodies) != 1:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_native",
                    "Native simple hole currently requires exactly one solid body.",
                    details={"solid_body_count": len(bodies)},
                )
            bounds = self._solid_bounds_m(bodies)
            min_x, min_y, min_z, max_x, max_y, max_z = bounds
            x = float(center_x_mm) / 1000.0
            y = float(center_y_mm) / 1000.0
            tolerance = 1e-9
            if not (min_x - tolerance <= x <= max_x + tolerance) or not (
                min_y - tolerance <= y <= max_y + tolerance
            ):
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_native",
                    "Simple hole center lies outside the selected body bounding box.",
                    details={
                        "center_mm": [float(center_x_mm), float(center_y_mm)],
                        "bbox_xy_mm": [
                            min_x * 1000.0,
                            min_y * 1000.0,
                            max_x * 1000.0,
                            max_y * 1000.0,
                        ],
                    },
                )

            span_z = max_z - min_z
            ray_z = max_z + max(span_z, 0.01)
            self._member(model, "ClearSelection2", True)
            extension = self._member(model, "Extension")
            selected = bool(
                self._member(
                    extension,
                    "SelectByRay",
                    x,
                    y,
                    ray_z,
                    0.0,
                    0.0,
                    -1.0,
                    1e-6,
                    _SW_SEL_FACES,
                    False,
                    0,
                    _SW_SELECT_DEFAULT,
                )
            )
            if not selected:
                raise NativeRuntimeError(
                    "cad_selection_failed",
                    "part_hole_native",
                    "No face was intersected by the bounded +Z hole-selection ray.",
                )
            selection_manager = self._member(model, "SelectionManager")
            face = self._member(selection_manager, "GetSelectedObject6", 1, -1)
            if face is None:
                raise NativeRuntimeError(
                    "cad_selection_failed",
                    "part_hole_native",
                    "The bounded hole-selection ray did not resolve a native face.",
                )
            surface = self._member(face, "GetSurface")
            if surface is None or not bool(self._member(surface, "IsPlane")):
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_native",
                    "Native simple hole currently requires the selected +Z outer face to be planar.",
                )
            normal = self._as_tuple(self._member(face, "Normal"))
            if len(normal) != 3:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_native",
                    "Selected simple-hole face did not expose a three-value normal.",
                )
            nx, ny, nz = (float(value) for value in normal)
            if not all(math.isfinite(value) for value in (nx, ny, nz)) or (
                abs(nx) > 1e-9 or abs(ny) > 1e-9 or nz < 1.0 - 1e-9
            ):
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_native",
                    "face_ref='bbox:+z' requires a planar face with outward normal +Z.",
                    details={"face_normal": [nx, ny, nz]},
                )

            manager = self._member(model, "FeatureManager")
            end_type = _SW_END_THROUGH_ALL if spec.through_all else _SW_END_BLIND
            depth_m = 0.0 if spec.through_all else float(spec.depth_mm or 0.0) / 1000.0
            feature = self._member(
                manager,
                "SimpleHole2",
                float(spec.diameter_mm) / 1000.0,
                True,
                False,
                False,
                end_type,
                _SW_END_BLIND,
                depth_m,
                0.0,
                False,
                False,
                False,
                False,
                0.0,
                0.0,
                False,
                False,
                False,
                False,
                False,
                True,
                False,
                False,
                False,
            )
            if feature is None:
                raise NativeRuntimeError(
                    "cad_mutation_failed",
                    "part_hole_native",
                    "SOLIDWORKS did not create the requested simple hole feature.",
                )
            try:
                feature.Name = spec.name
            except Exception:
                pass
            identity = self._feature_name(feature).strip()
            if not identity:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_hole_native",
                    "Created simple hole returned an empty feature identity.",
                )
            return MutationReceipt(identity)

        return self._execute(operation, stage="part_hole_native", mutation=True)

    def create_hole_wizard(
        self,
        document: ResolvedDocument,
        spec: HoleWizardSpec,
    ) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            binding = self._binding_from_document(app, document)
            model = binding.model
            if spec.face_ref != _BBOX_PLUS_Z_FACE:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_wizard_native",
                    "Native Hole Wizard accepts only face_ref='bbox:+z'.",
                )
            if spec.size.value not in _HOLE_WIZARD_SIZES:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_wizard_native",
                    "Hole Wizard size is outside the evidence-backed M2-M6 subset.",
                )
            center_x_mm, center_y_mm = (float(value) for value in spec.center_mm)
            bodies = self._as_tuple(self._bodies(model, 0, False))
            if len(bodies) != 1:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_wizard_native",
                    "Native Hole Wizard requires exactly one solid body.",
                    details={"solid_body_count": len(bodies)},
                )
            min_x, min_y, min_z, max_x, max_y, max_z = self._solid_bounds_m(bodies)
            x = center_x_mm / 1000.0
            y = center_y_mm / 1000.0
            tolerance = 1e-9
            if not (min_x - tolerance <= x <= max_x + tolerance) or not (
                min_y - tolerance <= y <= max_y + tolerance
            ):
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_wizard_native",
                    "Hole Wizard center lies outside the selected body bounding box.",
                    details={"center_mm": [center_x_mm, center_y_mm]},
                )
            ray_z = max_z + max(max_z - min_z, 0.01)
            self._member(model, "ClearSelection2", True)
            extension = self._member(model, "Extension")
            if not bool(
                self._member(
                    extension,
                    "SelectByRay",
                    x,
                    y,
                    ray_z,
                    0.0,
                    0.0,
                    -1.0,
                    1e-6,
                    _SW_SEL_FACES,
                    False,
                    0,
                    _SW_SELECT_DEFAULT,
                )
            ):
                raise NativeRuntimeError(
                    "cad_selection_failed",
                    "part_hole_wizard_native",
                    "No face was intersected by the bounded +Z Hole Wizard selection ray.",
                )
            selection_manager = self._member(model, "SelectionManager")
            face = self._member(selection_manager, "GetSelectedObject6", 1, -1)
            if face is None:
                raise NativeRuntimeError(
                    "cad_selection_failed",
                    "part_hole_wizard_native",
                    "The bounded Hole Wizard ray did not resolve a native face.",
                )
            surface = self._member(face, "GetSurface")
            if surface is None or not bool(self._member(surface, "IsPlane")):
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_wizard_native",
                    "Hole Wizard requires the bounded +Z face to be planar.",
                )
            normal = self._as_tuple(self._member(face, "Normal"))
            if len(normal) != 3:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_wizard_native",
                    "Selected Hole Wizard face did not expose a three-value normal.",
                )
            nx, ny, nz = (float(value) for value in normal)
            if not all(math.isfinite(value) for value in (nx, ny, nz)) or (
                abs(nx) > 1e-9 or abs(ny) > 1e-9 or nz < 1.0 - 1e-9
            ):
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_wizard_native",
                    "face_ref='bbox:+z' requires a planar face with outward normal +Z.",
                    details={"face_normal": [nx, ny, nz]},
                )

            manager = self._member(model, "FeatureManager")
            definition = self._member(manager, "CreateDefinition", _SW_HOLE_WIZARD_DEFINITION_TYPE)
            if definition is None:
                raise NativeRuntimeError(
                    "cad_mutation_failed",
                    "part_hole_wizard_native",
                    "SOLIDWORKS did not create a Hole Wizard feature definition.",
                )
            self._member(
                definition,
                "InitializeHole",
                _SW_HOLE_WIZARD_STANDARD_ANSI_METRIC,
                _SW_HOLE_WIZARD_TYPE_COUNTERSINK,
                _SW_HOLE_WIZARD_FASTENER_FLAT_HEAD_ANSI,
                spec.size.value,
                _SW_HOLE_WIZARD_FIT_NORMAL,
            )
            feature = self._member(manager, "CreateFeature", definition)
            if feature is None:
                raise NativeRuntimeError(
                    "cad_mutation_failed",
                    "part_hole_wizard_native",
                    "SOLIDWORKS did not create the requested Hole Wizard feature.",
                )
            try:
                feature.Name = spec.name
            except Exception:
                pass
            identity = self._feature_name(feature).strip()
            if not identity:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_hole_wizard_native",
                    "Created Hole Wizard returned an empty feature identity.",
                )
            return MutationReceipt(identity)

        return self._execute(operation, stage="part_hole_wizard_native", mutation=True)

    def create_fillet(self, document: ResolvedDocument, spec: FilletSpec) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            model = self._binding_from_document(app, document).model
            edges = tuple(self._resolve_bounded_edge(model, ref) for ref in spec.edge_refs)
            manager = self._member(model, "FeatureManager")
            definition = self._member(manager, "CreateDefinition", _SW_FM_FILLET)
            if definition is None or not bool(self._member(definition, "Initialize", _SW_CONST_RADIUS_FILLET)):
                raise NativeRuntimeError("cad_mutation_failed", "part_fillet_native", "SOLIDWORKS did not initialize constant-radius fillet data.")
            definition.ConicTypeForCrossSectionProfile = _SW_FEATURE_FILLET_CIRCULAR
            definition.DefaultRadius = float(spec.radius_mm) / 1000.0
            definition.OverflowType = _SW_FILLET_OVERFLOW_DEFAULT
            definition.PropagateToTangentFaces = bool(spec.tangent_propagation)
            definition.Edges = self._dispatch_array(edges)
            feature = self._member(manager, "CreateFeature", definition)
            return self._finish_created_feature(feature, spec.name, "part_fillet_native", "fillet")

        return self._execute(operation, stage="part_fillet_native", mutation=True)

    def create_chamfer(self, document: ResolvedDocument, spec: ChamferSpec) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            model = self._binding_from_document(app, document).model
            edges = tuple(self._resolve_bounded_edge(model, ref) for ref in spec.edge_refs)
            self._member(model, "ClearSelection2", True)
            for index, edge in enumerate(edges):
                self._select_native_entity(
                    model,
                    edge,
                    append=index > 0,
                    mark=0,
                    stage="part_chamfer_native",
                )
            manager = self._member(model, "FeatureManager")
            feature = self._member(
                manager,
                "InsertFeatureChamfer",
                0,
                _SW_CHAMFER_ANGLE_DISTANCE,
                float(spec.distance_mm) / 1000.0,
                math.radians(float(spec.angle_deg)),
                0.0,
                0.0,
                0.0,
                0.0,
            )
            return self._finish_created_feature(feature, spec.name, "part_chamfer_native", "chamfer")

        return self._execute(operation, stage="part_chamfer_native", mutation=True)

    def create_shell(self, document: ResolvedDocument, spec: ShellSpec) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            model = self._binding_from_document(app, document).model
            faces = tuple(self._resolve_bounded_face(model, ref) for ref in spec.face_refs)
            self._member(model, "ClearSelection2", True)
            for index, face in enumerate(faces):
                self._select_native_entity(
                    model,
                    face,
                    append=index > 0,
                    mark=1,
                    stage="part_shell_native",
                )
            self._member(
                model,
                "InsertFeatureShell",
                float(spec.thickness_mm) / 1000.0,
                bool(spec.outward),
            )
            selection_manager = self._member(model, "SelectionManager")
            feature = self._member(selection_manager, "GetSelectedObject6", 1, -1)
            if feature is None or self._native_feature_type(feature) != "Shell":
                raise NativeRuntimeError(
                    "cad_mutation_failed",
                    "part_shell_native",
                    "SOLIDWORKS did not leave the created shell feature selected for reconciliation.",
                )
            return self._finish_created_feature(feature, spec.name, "part_shell_native", "shell")

        return self._execute(operation, stage="part_shell_native", mutation=True)

    def create_draft(self, document: ResolvedDocument, spec: DraftSpec) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            model = self._binding_from_document(app, document).model
            faces = tuple(self._resolve_bounded_face(model, ref) for ref in spec.face_refs)
            neutral = self._resolve_bounded_reference(
                model,
                spec.neutral_plane_ref,
                allow_face=True,
                allow_edge=False,
            )
            self._member(model, "ClearSelection2", True)
            self._select_native_entity(
                model,
                neutral,
                append=False,
                mark=1,
                stage="part_draft_native",
            )
            for face in faces:
                self._select_native_entity(
                    model,
                    face,
                    append=True,
                    mark=2,
                    stage="part_draft_native",
                )
            manager = self._member(model, "FeatureManager")
            feature = self._member(
                manager,
                "InsertMultiFaceDraft",
                math.radians(float(spec.angle_deg)),
                bool(spec.reverse_direction),
                False,
                0,
                False,
                False,
            )
            return self._finish_created_feature(feature, spec.name, "part_draft_native", "draft")

        return self._execute(operation, stage="part_draft_native", mutation=True)

    def create_rib(self, document: ResolvedDocument, spec: RibSpec) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            model = self._binding_from_document(app, document).model
            profile = self._member(model, "FeatureByName", spec.profile.sketch_id)
            if profile is None or self._native_feature_type(profile) != "ProfileFeature":
                raise NativeRuntimeError("cad_precondition_failed", "part_rib_native", "Rib profile must resolve to an explicit native sketch feature.")
            existing = self._feature_names_by_native_type(model, "Rib")
            self._member(model, "ClearSelection2", True)
            if not bool(self._member(profile, "Select2", False, 0)):
                raise NativeRuntimeError("cad_selection_failed", "part_rib_native", "Rib profile sketch could not be selected.")
            manager = self._member(model, "FeatureManager")
            self._member(
                manager,
                "InsertRib",
                bool(spec.both_sides),
                False,
                float(spec.thickness_mm) / 1000.0,
                0,
                False,
                False,
                False,
                0.0,
                True,
                False,
            )
            feature = self._single_new_feature_by_native_type(model, "Rib", existing)
            return self._finish_created_feature(feature, spec.name, "part_rib_native", "rib")

        return self._execute(operation, stage="part_rib_native", mutation=True)

    def create_linear_pattern(self, document: ResolvedDocument, spec: LinearPatternSpec) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            model = self._binding_from_document(app, document).model
            seeds = self._resolve_seed_features(model, spec.seed_feature_ids)
            direction = self._resolve_bounded_reference(model, spec.direction_ref, allow_face=False, allow_edge=True)
            self._member(model, "ClearSelection2", True)
            self._select_native_entity(
                model,
                direction,
                append=False,
                mark=1,
                stage="part_linear_pattern_native",
            )
            for seed in seeds:
                self._select_native_entity(
                    model,
                    seed,
                    append=True,
                    mark=4,
                    stage="part_linear_pattern_native",
                )
            manager = self._member(model, "FeatureManager")
            definition = self._member(manager, "CreateDefinition", _SW_FM_LINEAR_PATTERN)
            if definition is None:
                raise NativeRuntimeError("cad_mutation_failed", "part_linear_pattern_native", "SOLIDWORKS did not create linear-pattern feature data.")
            definition.BodyPattern = False
            definition.D1Axis = direction
            definition.D1EndCondition = _SW_PATTERN_SPACING_AND_INSTANCES
            definition.D1ReverseDirection = False
            definition.D1Spacing = float(spec.spacing_mm) / 1000.0
            definition.D1TotalInstances = int(spec.count)
            definition.GeometryPattern = bool(spec.geometry_pattern)
            definition.VarySketch = False
            definition.PatternFeatureArray = self._dispatch_array(seeds)
            feature = self._member(manager, "CreateFeature", definition)
            return self._finish_created_feature(feature, spec.name, "part_linear_pattern_native", "linear pattern")

        return self._execute(operation, stage="part_linear_pattern_native", mutation=True)

    def create_circular_pattern(self, document: ResolvedDocument, spec: CircularPatternSpec) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            model = self._binding_from_document(app, document).model
            seeds = self._resolve_seed_features(model, spec.seed_feature_ids)
            axis = self._resolve_bounded_reference(model, spec.axis_ref, allow_face=False, allow_edge=True, allow_axis=True)
            self._member(model, "ClearSelection2", True)
            self._select_native_entity(
                model,
                axis,
                append=False,
                mark=1,
                stage="part_circular_pattern_native",
            )
            for seed in seeds:
                self._select_native_entity(
                    model,
                    seed,
                    append=True,
                    mark=4,
                    stage="part_circular_pattern_native",
                )
            manager = self._member(model, "FeatureManager")
            definition = self._member(manager, "CreateDefinition", _SW_FM_CIRCULAR_PATTERN)
            if definition is None:
                raise NativeRuntimeError("cad_mutation_failed", "part_circular_pattern_native", "SOLIDWORKS did not create circular-pattern feature data.")
            definition.BodyPattern = False
            definition.Spacing = math.radians(float(spec.angle_deg))
            definition.TotalInstances = int(spec.count)
            definition.EqualSpacing = True
            definition.GeometryPattern = bool(spec.geometry_pattern)
            definition.VarySketch = False
            definition.PatternFeatureArray = self._dispatch_array(seeds)
            feature = self._member(manager, "CreateFeature", definition)
            return self._finish_created_feature(feature, spec.name, "part_circular_pattern_native", "circular pattern")

        return self._execute(operation, stage="part_circular_pattern_native", mutation=True)

    def create_mirror(self, document: ResolvedDocument, spec: MirrorSpec) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            model = self._binding_from_document(app, document).model
            seeds = self._resolve_seed_features(model, spec.seed_feature_ids)
            plane = self._resolve_bounded_reference(model, spec.mirror_ref, allow_face=True, allow_edge=False)
            self._member(model, "ClearSelection2", True)
            for index, seed in enumerate(seeds):
                self._select_native_entity(
                    model,
                    seed,
                    append=index > 0,
                    mark=1,
                    stage="part_mirror_native",
                )
            self._select_native_entity(
                model,
                plane,
                append=True,
                mark=2,
                stage="part_mirror_native",
            )
            manager = self._member(model, "FeatureManager")
            feature = self._member(
                manager,
                "InsertMirrorFeature2",
                False,
                bool(spec.geometry_pattern),
                False,
                False,
                0,
            )
            return self._finish_created_feature(feature, spec.name, "part_mirror_native", "mirror pattern")

        return self._execute(operation, stage="part_mirror_native", mutation=True)

    def create_reference_plane(self, document: ResolvedDocument, spec: ReferencePlaneSpec) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            model = self._binding_from_document(app, document).model
            reference = self._resolve_bounded_reference(model, spec.reference, allow_face=True, allow_edge=False)
            self._member(model, "ClearSelection2", True)
            self._select_native_entity(model, reference, append=False, mark=0, stage="part_reference_plane_native")
            flip = bool(spec.reverse_direction) ^ (float(spec.offset_mm) < 0.0)
            constraint = _SW_REF_PLANE_DISTANCE | (_SW_REF_PLANE_FLIP if flip else 0)
            manager = self._member(model, "FeatureManager")
            feature = self._member(
                manager,
                "InsertRefPlane",
                constraint,
                abs(float(spec.offset_mm)) / 1000.0,
                0,
                0.0,
                0,
                0.0,
            )
            return self._finish_created_feature(feature, spec.name, "part_reference_plane_native", "reference plane")

        return self._execute(operation, stage="part_reference_plane_native", mutation=True)

    def create_reference_axis(self, document: ResolvedDocument, spec: ReferenceAxisSpec) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            model = self._binding_from_document(app, document).model
            first = self._resolve_bounded_reference(model, spec.first_ref, allow_face=True, allow_edge=False)
            second = self._resolve_bounded_reference(model, spec.second_ref, allow_face=True, allow_edge=False)
            existing = self._feature_names_by_native_type(model, "RefAxis")
            self._member(model, "ClearSelection2", True)
            self._select_native_entity(model, first, append=False, mark=0, stage="part_reference_axis_native")
            self._select_native_entity(model, second, append=True, mark=0, stage="part_reference_axis_native")
            created = bool(self._member(model, "InsertAxis2", True))
            if not created:
                raise NativeRuntimeError(
                    "cad_mutation_failed",
                    "part_reference_axis_native",
                    "SOLIDWORKS refused to create the reference axis.",
                )
            feature = self._single_new_feature_by_native_type(model, "RefAxis", existing)
            return self._finish_created_feature(feature, spec.name, "part_reference_axis_native", "reference axis")

        return self._execute(operation, stage="part_reference_axis_native", mutation=True)

    def create_reference_point(self, document: ResolvedDocument, spec: ReferencePointSpec) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            model = self._binding_from_document(app, document).model
            face = self._resolve_bounded_face(model, spec.reference)
            self._member(model, "ClearSelection2", True)
            self._select_native_entity(model, face, append=False, mark=0, stage="part_reference_point_native")
            manager = self._member(model, "FeatureManager")
            raw_features = self._member(
                manager,
                "InsertReferencePoint",
                _SW_REF_POINT_FACE_CENTER,
                0,
                0.0,
                1,
            )
            features = self._as_tuple(raw_features)
            if len(features) != 1 or self._native_feature_type(features[0]) not in {"RefPoint", "PointRef"}:
                raise NativeRuntimeError(
                    "cad_mutation_failed",
                    "part_reference_point_native",
                    "SOLIDWORKS did not create exactly one reference-point feature.",
                    details={"returned_feature_count": len(features)},
                )
            return self._finish_created_feature(
                features[0],
                spec.name,
                "part_reference_point_native",
                "reference point",
            )

        return self._execute(operation, stage="part_reference_point_native", mutation=True)

    def create_revolve(
        self, document: ResolvedDocument, spec: RevolveSpec
    ) -> MutationReceipt:
        return self._create_revolve_feature(document, spec, is_cut=False)

    def create_revolve_cut(
        self, document: ResolvedDocument, spec: RevolveCutSpec
    ) -> MutationReceipt:
        return self._create_revolve_feature(document, spec, is_cut=True)

    def _create_revolve_feature(
        self,
        document: ResolvedDocument,
        spec: RevolveSpec | RevolveCutSpec,
        *,
        is_cut: bool,
    ) -> MutationReceipt:
        stage = "part_revolve_cut_native" if is_cut else "part_revolve_native"

        def operation(app: Any) -> MutationReceipt:
            binding = self._binding_from_document(app, document)
            model = binding.model
            profile = self._member(model, "FeatureByName", spec.profile.sketch_id)
            if profile is None or self._native_feature_type(profile) != "ProfileFeature":
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    stage,
                    "The requested revolve profile could not be resolved as a native sketch feature.",
                )
            if spec.axis_ref != _PROFILE_CENTERLINE_AXIS:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    stage,
                    "Native revolve currently accepts only axis_ref='profile_centerline'.",
                )
            if self._revolve_profile_validator is not None:
                self._revolve_profile_validator(model, profile, spec.profile.sketch_id)

            sketch = self._member(profile, "GetSpecificFeature2")
            if sketch is None:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    stage,
                    "The revolve profile did not expose a native sketch object.",
                )
            segments = self._as_tuple(self._member(sketch, "GetSketchSegments"))
            construction = tuple(
                segment
                for segment in segments
                if bool(self._member(segment, "ConstructionGeometry"))
            )
            if len(construction) != 1:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    stage,
                    "Revolve profile must contain exactly one construction centerline.",
                    details={"construction_segment_count": len(construction)},
                )
            axis = construction[0]
            if int(self._member(axis, "GetType")) != _SW_SKETCH_LINE:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    stage,
                    "Revolve profile centerline must be a construction line.",
                )

            self._member(model, "ClearSelection2", True)
            if not bool(self._member(profile, "Select2", False, 0)):
                raise NativeRuntimeError(
                    "cad_selection_failed",
                    stage,
                    "The requested revolve profile could not be selected.",
                )
            selection_manager = self._member(model, "SelectionManager")
            select_data = self._member(selection_manager, "CreateSelectData")
            select_data.Mark = _SW_SEL_REVOLVE_AXIS
            if not bool(self._member(axis, "Select4", True, select_data)):
                raise NativeRuntimeError(
                    "cad_selection_failed",
                    stage,
                    "The profile construction centerline could not be selected as the revolve axis.",
                )

            angle_rad = math.radians(float(spec.angle_deg))
            manager = self._member(model, "FeatureManager")
            feature = self._member(
                manager,
                "FeatureRevolve2",
                True,   # single direction
                True,   # solid revolve
                False,  # non-thin feature
                bool(is_cut),
                False,  # do not reverse direction
                False,  # no shared up-to entity
                _SW_END_BLIND,
                _SW_END_BLIND,
                angle_rad,
                0.0,
                False,
                False,
                0.0,
                0.0,
                0,
                0.0,
                0.0,
                True,   # merge boss result / accepted cut behavior
                False,  # feature scope disabled
                True,   # auto-select affected bodies
            )
            if feature is None:
                raise NativeRuntimeError(
                    "cad_mutation_failed",
                    stage,
                    "SOLIDWORKS did not create the requested revolve feature.",
                )
            try:
                feature.Name = spec.name
            except Exception:
                pass
            identity = self._feature_name(feature).strip()
            if not identity:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    stage,
                    "Created revolve returned an empty feature identity.",
                )
            return MutationReceipt(identity)

        return self._execute(operation, stage=stage, mutation=True)

    def rebuild(self, document: ResolvedDocument) -> RebuildResult:
        def operation(app: Any) -> RebuildResult:
            return self._rebuild(self._binding_from_document(app, document).model)

        return self._execute(operation, stage="part_rebuild_native", mutation=True)

    def get_feature(self, document: ResolvedDocument, feature_id: str) -> FeatureSnapshot | None:
        def operation(app: Any) -> FeatureSnapshot | None:
            model = self._binding_from_document(app, document).model
            feature = self._member(model, "FeatureByName", feature_id)
            if feature is None:
                return None
            native_type = self._native_feature_type(feature)
            if native_type == "Cut":
                definition = self._member(feature, "GetDefinition")
                if definition is None:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "part_feature_readback",
                        "Cut feature definition was unavailable during native read-back.",
                    )
                end_condition = int(self._member(definition, "GetEndCondition", True))
                parameters: dict[str, float | str | bool | int] = {
                    "through_all": end_condition == _SW_END_THROUGH_ALL,
                }
                if end_condition == _SW_END_BLIND:
                    depth_m = float(self._member(definition, "GetDepth", True))
                    if not math.isfinite(depth_m) or depth_m <= 0.0:
                        raise NativeRuntimeError(
                            "cad_postcondition_failed",
                            "part_feature_readback",
                            "Blind cut returned an invalid native depth.",
                        )
                    parameters["depth_mm"] = depth_m * 1000.0
                elif end_condition != _SW_END_THROUGH_ALL:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "part_feature_readback",
                        "Cut feature returned an unaccepted end condition.",
                        details={"end_condition": end_condition},
                    )
                return FeatureSnapshot(
                    feature_id=self._feature_name(feature),
                    name=self._feature_name(feature),
                    kind=FeatureKind.CUT,
                    parameters=parameters,
                    suppressed=self._is_feature_suppressed(feature),
                )
            if native_type == "HoleWzd":
                return self._read_hole_wizard_feature(model, feature)
            if native_type in {"Hole", "SketchHole", "SimpleHole"}:
                return self._read_simple_hole_feature(model, feature)
            if native_type in {"Revolution", "Revolve", "RevCut", "RevolveCut"}:
                return self._read_revolve_feature(model, feature, native_type)
            if native_type in {"Fillet", "Fillet2", "Fillet3"}:
                return self._read_fillet_feature(model, feature)
            if native_type in {"Chamfer"}:
                return self._read_chamfer_feature(model, feature)
            if native_type in {"Shell"}:
                return self._read_shell_feature(model, feature)
            if native_type in {"Draft"}:
                return self._read_draft_feature(model, feature)
            if native_type in {"Rib"}:
                return self._read_rib_feature(model, feature)
            if native_type in {"LPattern", "LinearPattern"}:
                return self._read_linear_pattern_feature(model, feature)
            if native_type in {"CirPattern", "CircularPattern"}:
                return self._read_circular_pattern_feature(model, feature)
            if native_type in {"MirrorPattern", "MirrorSolid"}:
                return self._read_mirror_feature(model, feature)
            if native_type == "RefPlane":
                return self._read_reference_plane_feature(model, feature)
            if native_type == "RefAxis":
                return self._read_reference_feature(feature, FeatureKind.REFERENCE_AXIS)
            if native_type in {"RefPoint", "PointRef"}:
                return self._read_reference_feature(feature, FeatureKind.REFERENCE_POINT)
            return None

        return self._execute(operation, stage="part_feature_readback", mutation=False)

    def _read_definition(self, model: Any, feature: Any, stage: str) -> tuple[Any, bool]:
        definition = self._member(feature, "GetDefinition")
        if definition is None:
            raise NativeRuntimeError("cad_postcondition_failed", stage, "Native feature definition was unavailable during read-back.")
        accessed = False
        try:
            accessed = bool(self._member(definition, "AccessSelections", model, self._null_dispatch()))
        except Exception:
            accessed = False
        return definition, accessed

    def _release_definition(self, definition: Any, accessed: bool) -> None:
        if accessed:
            self._member(definition, "ReleaseSelectionAccess")

    def _read_fillet_feature(self, model: Any, feature: Any) -> FeatureSnapshot:
        definition, accessed = self._read_definition(model, feature, "part_fillet_readback")
        try:
            radius_mm = float(self._member(definition, "DefaultRadius")) * 1000.0
            tangent = bool(self._member(definition, "PropagateToTangentFaces"))
        finally:
            self._release_definition(definition, accessed)
        return self._snapshot(feature, FeatureKind.FILLET, {"radius_mm": radius_mm, "tangent_propagation": tangent})

    def _read_chamfer_feature(self, model: Any, feature: Any) -> FeatureSnapshot:
        definition, accessed = self._read_definition(model, feature, "part_chamfer_readback")
        try:
            distance_mm = float(self._member(definition, "GetEdgeChamferDistance", 0)) * 1000.0
            angle_deg = math.degrees(float(self._member(definition, "EdgeChamferAngle")))
        finally:
            self._release_definition(definition, accessed)
        return self._snapshot(feature, FeatureKind.CHAMFER, {"distance_mm": distance_mm, "angle_deg": angle_deg})

    def _read_shell_feature(self, model: Any, feature: Any) -> FeatureSnapshot:
        definition, accessed = self._read_definition(model, feature, "part_shell_readback")
        try:
            thickness_mm = float(self._member(definition, "Thickness")) * 1000.0
            outward = bool(self._member(definition, "Direction"))
        finally:
            self._release_definition(definition, accessed)
        return self._snapshot(feature, FeatureKind.SHELL, {"thickness_mm": thickness_mm, "outward": outward})

    def _read_draft_feature(self, model: Any, feature: Any) -> FeatureSnapshot:
        definition, accessed = self._read_definition(model, feature, "part_draft_readback")
        try:
            angle_deg = math.degrees(float(self._member(definition, "Angle")))
            reverse = bool(self._member(definition, "ReverseDirection"))
        finally:
            self._release_definition(definition, accessed)
        return self._snapshot(feature, FeatureKind.DRAFT, {"angle_deg": angle_deg, "reverse_direction": reverse})

    def _read_rib_feature(self, model: Any, feature: Any) -> FeatureSnapshot:
        definition, accessed = self._read_definition(model, feature, "part_rib_readback")
        try:
            thickness_mm = float(self._member(definition, "Thickness")) * 1000.0
            both_sides = bool(self._member(definition, "IsTwoSided"))
        finally:
            self._release_definition(definition, accessed)
        return self._snapshot(feature, FeatureKind.RIB, {"thickness_mm": thickness_mm, "both_sides": both_sides})

    def _read_linear_pattern_feature(self, model: Any, feature: Any) -> FeatureSnapshot:
        definition, accessed = self._read_definition(model, feature, "part_linear_pattern_readback")
        try:
            count = int(self._member(definition, "D1TotalInstances"))
            spacing_mm = float(self._member(definition, "D1Spacing")) * 1000.0
            geometry = bool(self._member(definition, "GeometryPattern"))
        finally:
            self._release_definition(definition, accessed)
        return self._snapshot(feature, FeatureKind.LINEAR_PATTERN, {"count": count, "spacing_mm": spacing_mm, "geometry_pattern": geometry})

    def _read_circular_pattern_feature(self, model: Any, feature: Any) -> FeatureSnapshot:
        definition, accessed = self._read_definition(model, feature, "part_circular_pattern_readback")
        try:
            count = int(self._member(definition, "TotalInstances"))
            angle_deg = math.degrees(float(self._member(definition, "Spacing")))
            geometry = bool(self._member(definition, "GeometryPattern"))
        finally:
            self._release_definition(definition, accessed)
        return self._snapshot(feature, FeatureKind.CIRCULAR_PATTERN, {"count": count, "angle_deg": angle_deg, "geometry_pattern": geometry})

    def _read_mirror_feature(self, model: Any, feature: Any) -> FeatureSnapshot:
        definition, accessed = self._read_definition(model, feature, "part_mirror_readback")
        try:
            geometry = bool(self._member(definition, "GeometryPattern"))
        finally:
            self._release_definition(definition, accessed)
        return self._snapshot(feature, FeatureKind.MIRROR, {"geometry_pattern": geometry})

    def _read_reference_plane_feature(self, model: Any, feature: Any) -> FeatureSnapshot:
        definition, accessed = self._read_definition(model, feature, "part_reference_plane_readback")
        try:
            distance_mm = abs(float(self._member(definition, "Distance"))) * 1000.0
        finally:
            self._release_definition(definition, accessed)
        return self._snapshot(feature, FeatureKind.REFERENCE_PLANE, {"offset_mm": distance_mm})

    def _read_reference_feature(self, feature: Any, kind: FeatureKind) -> FeatureSnapshot:
        return self._snapshot(feature, kind, {})

    def _snapshot(
        self,
        feature: Any,
        kind: FeatureKind,
        parameters: dict[str, float | str | bool | int],
    ) -> FeatureSnapshot:
        name = self._feature_name(feature)
        return FeatureSnapshot(
            feature_id=name,
            name=name,
            kind=kind,
            parameters=parameters,
            suppressed=self._is_feature_suppressed(feature),
        )

    def _read_simple_hole_feature(
        self, model: Any, feature: Any
    ) -> FeatureSnapshot:
        definition = self._member(feature, "GetDefinition")
        if definition is None:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "Simple hole feature definition was unavailable during native read-back.",
            )
        if not bool(
            self._member(
                definition,
                "AccessSelections",
                model,
                self._null_dispatch(),
            )
        ):
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "SOLIDWORKS did not grant access to simple-hole selections.",
            )
        try:
            diameter_m = float(self._member(definition, "Diameter"))
            end_type = int(self._member(definition, "Type"))
            if not math.isfinite(diameter_m) or diameter_m <= 0.0:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_feature_readback",
                    "Simple hole returned an invalid diameter.",
                )
            parameters: dict[str, float | str | bool | int] = {
                "diameter_mm": diameter_m * 1000.0,
                "face_ref": _BBOX_PLUS_Z_FACE,
                "center_count": 1,
                "through_all": end_type == _SW_END_THROUGH_ALL,
            }
            if end_type == _SW_END_BLIND:
                depth_m = float(self._member(definition, "Depth"))
                if not math.isfinite(depth_m) or depth_m <= 0.0:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "part_feature_readback",
                        "Blind simple hole returned an invalid depth.",
                    )
                parameters["depth_mm"] = depth_m * 1000.0
            elif end_type != _SW_END_THROUGH_ALL:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_feature_readback",
                    "Simple hole returned an unaccepted end condition.",
                    details={"end_condition": end_type},
                )
        finally:
            self._member(definition, "ReleaseSelectionAccess")
        center_x_mm, center_y_mm = self._simple_hole_center_mm(feature)
        parameters["center_x_mm"] = center_x_mm
        parameters["center_y_mm"] = center_y_mm
        return FeatureSnapshot(
            feature_id=self._feature_name(feature),
            name=self._feature_name(feature),
            kind=FeatureKind.HOLE,
            parameters=parameters,
            suppressed=self._is_feature_suppressed(feature),
        )

    def _read_hole_wizard_feature(self, model: Any, feature: Any) -> FeatureSnapshot:
        definition = self._member(feature, "GetDefinition")
        if definition is None:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "Hole Wizard definition was unavailable during native read-back.",
            )
        if not bool(
            self._member(definition, "AccessSelections", model, self._null_dispatch())
        ):
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "SOLIDWORKS did not grant access to Hole Wizard selections.",
            )
        try:
            standard = str(self._member(definition, "Standard") or "").strip()
            fastener = str(self._member(definition, "FastenerType") or "").strip()
            size = str(self._member(definition, "FastenerSize") or "").strip()
            end_condition = int(self._member(definition, "EndCondition"))
            countersink_diameter_mm = float(self._member(definition, "CounterSinkDiameter")) * 1000.0
            countersink_angle_deg = float(self._member(definition, "CounterSinkAngle"))
            thru_hole_diameter_mm = float(self._member(definition, "ThruHoleDiameter")) * 1000.0
            hole_fit = int(self._member(definition, "HoleFit"))
            point_count = int(self._member(definition, "GetSketchPointCount"))
            points = self._as_tuple(self._member(definition, "GetSketchPoints"))
        finally:
            self._member(definition, "ReleaseSelectionAccess")

        if standard != _HOLE_WIZARD_STANDARD or fastener != _HOLE_WIZARD_FASTENER:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "Hole Wizard standard/fastener read-back left the evidence-backed contract.",
                details={"standard": standard, "fastener": fastener},
            )
        if size not in _HOLE_WIZARD_SIZES:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "Hole Wizard size read-back is outside the evidence-backed M2-M6 subset.",
                details={"size": size},
            )
        if end_condition != _SW_END_THROUGH_ALL:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "Hole Wizard read-back is not through-all.",
                details={"end_condition": end_condition},
            )
        if point_count != 1 or len(points) != 1:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "Hole Wizard must persist exactly one sketch point.",
                details={"point_count": point_count, "returned_points": len(points)},
            )
        center_x_mm = float(self._member(points[0], "X")) * 1000.0
        center_y_mm = float(self._member(points[0], "Y")) * 1000.0
        numeric = (
            countersink_diameter_mm,
            countersink_angle_deg,
            thru_hole_diameter_mm,
            center_x_mm,
            center_y_mm,
        )
        if not all(math.isfinite(value) for value in numeric):
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "Hole Wizard returned non-finite geometry read-back.",
            )
        return FeatureSnapshot(
            feature_id=self._feature_name(feature),
            name=self._feature_name(feature),
            kind=FeatureKind.HOLE,
            parameters={
                "wizard_standard": standard,
                "wizard_fastener": fastener,
                "wizard_size": size,
                "face_ref": _BBOX_PLUS_Z_FACE,
                "center_count": 1,
                "through_all": True,
                "counter_sink_diameter_mm": countersink_diameter_mm,
                "counter_sink_angle_deg": countersink_angle_deg,
                "thru_hole_diameter_mm": thru_hole_diameter_mm,
                "hole_fit": hole_fit,
                "center_x_mm": center_x_mm,
                "center_y_mm": center_y_mm,
            },
            suppressed=self._is_feature_suppressed(feature),
        )

    def _simple_hole_center_mm(self, feature: Any) -> tuple[float, float]:
        subfeatures: list[Any] = []
        subfeature = self._member(feature, "GetFirstSubFeature")
        visited = 0
        while subfeature is not None:
            visited += 1
            if visited > 16:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_feature_readback",
                    "Simple-hole subfeature traversal exceeded its bounded limit.",
                )
            if self._native_feature_type(subfeature) == "ProfileFeature":
                subfeatures.append(subfeature)
            subfeature = self._member(subfeature, "GetNextSubFeature")
        if len(subfeatures) != 1:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "Simple hole must expose exactly one profile subfeature for center read-back.",
                details={"profile_subfeature_count": len(subfeatures)},
            )
        sketch = self._member(subfeatures[0], "GetSpecificFeature2")
        if sketch is None:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "Simple-hole profile subfeature did not expose a native sketch.",
            )
        points = self._as_tuple(self._member(sketch, "GetSketchPoints2"))
        if len(points) != 1:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "Simple-hole profile must contain exactly one sketch point.",
                details={"sketch_point_count": len(points)},
            )
        x = float(self._member(points[0], "X")) * 1000.0
        y = float(self._member(points[0], "Y")) * 1000.0
        if not math.isfinite(x) or not math.isfinite(y):
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "Simple-hole sketch point returned non-finite center coordinates.",
            )
        return x, y

    def _read_revolve_feature(
        self, model: Any, feature: Any, native_type: str
    ) -> FeatureSnapshot:
        definition = self._member(feature, "GetDefinition")
        if definition is None:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "Revolve feature definition was unavailable during native read-back.",
            )
        accessed = bool(
            self._member(
                definition,
                "AccessSelections",
                model,
                self._null_dispatch(),
            )
        )
        if not accessed:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "SOLIDWORKS did not grant access to revolve-defining selections.",
            )
        try:
            axis = self._member(definition, "Axis")
            if axis is None:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_feature_readback",
                    "Revolve feature returned no axis selection.",
                )
            if not bool(self._member(axis, "ConstructionGeometry")):
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_feature_readback",
                    "Revolve axis is not construction geometry.",
                )
            if int(self._member(axis, "GetType")) != _SW_SKETCH_LINE:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_feature_readback",
                    "Revolve axis is not a sketch line.",
                )
            angle_rad = float(self._member(definition, "GetRevolutionAngle", True))
            angle_deg = math.degrees(angle_rad)
            if (
                not math.isfinite(angle_deg)
                or angle_deg <= 0.0
                or angle_deg > 360.0 + 1e-9
            ):
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_feature_readback",
                    "Revolve feature returned an invalid revolution angle.",
                    details={"angle_deg": angle_deg},
                )
            is_boss = bool(self._member(definition, "IsBossFeature"))
        finally:
            self._member(definition, "ReleaseSelectionAccess")

        inferred_cut = native_type in {"RevCut", "RevolveCut"}
        if inferred_cut == is_boss:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "Revolve feature type disagrees with its boss/cut definition state.",
            )
        return FeatureSnapshot(
            feature_id=self._feature_name(feature),
            name=self._feature_name(feature),
            kind=FeatureKind.REVOLVE if is_boss else FeatureKind.REVOLVE_CUT,
            parameters={
                "axis_ref": _PROFILE_CENTERLINE_AXIS,
                "angle_deg": angle_deg,
            },
            suppressed=self._is_feature_suppressed(feature),
        )

    def list_bodies(self, document: ResolvedDocument) -> tuple[BodySnapshot, ...]:
        def operation(app: Any) -> tuple[BodySnapshot, ...]:
            model = self._binding_from_document(app, document).model
            snapshots = []
            for index, body in enumerate(self._bodies(model, 0, False)):
                raw = self._as_tuple(self._member(body, "GetBodyBox"))
                if len(raw) != 6:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "part_body_readback",
                        "Native body bounding box did not contain six values.",
                    )
                values = tuple(float(value) * 1000.0 for value in raw)
                if not all(math.isfinite(value) for value in values):
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "part_body_readback",
                        "Native body bounding box contained a non-finite value.",
                    )
                name = self._body_name(body).strip() or f"body-{index + 1}"
                snapshots.append(
                    BodySnapshot(
                        name,
                        Bounds3D(*values),
                    )
                )
            return tuple(snapshots)

        return self._execute(operation, stage="part_body_readback", mutation=False)

    def set_feature_suppressed(
        self,
        document: ResolvedDocument,
        feature_id: str,
        suppressed: bool,
    ) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            model = self._binding_from_document(app, document).model
            feature = self._member(model, "FeatureByName", feature_id)
            if feature is None:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_feature_suppression",
                    f"Feature {feature_id!r} was not found.",
                )
            if self._feature_kind(feature) is None:
                raise NativeRuntimeError(
                    "unsupported_native_operation",
                    "part_feature_suppression",
                    "Only evidence-promoted parametric features may be suppressed by this lane.",
                )
            action = _SW_SUPPRESS_FEATURE if suppressed else _SW_UNSUPPRESS_FEATURE
            changed = bool(
                self._member(
                    feature,
                    "SetSuppression2",
                    action,
                    _SW_THIS_CONFIGURATION,
                    None,
                )
            )
            if not changed:
                raise NativeRuntimeError(
                    "cad_mutation_failed",
                    "part_feature_suppression",
                    f"SOLIDWORKS refused to change suppression for {feature_id!r}.",
                )
            return MutationReceipt(feature_id)

        return self._execute(operation, stage="part_feature_suppression", mutation=True)

    def set_feature_parameter(
        self,
        document: ResolvedDocument,
        feature_id: str,
        parameter: str,
        value: float,
    ) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            model = self._binding_from_document(app, document).model
            feature = self._member(model, "FeatureByName", feature_id)
            if feature is None:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_feature_parameter",
                    f"Feature {feature_id!r} was not found.",
                )
            if self._feature_kind(feature) is not FeatureKind.FILLET or parameter != "radius_mm":
                raise NativeRuntimeError(
                    "unsupported_native_operation",
                    "part_feature_parameter",
                    "Only radius_mm editing for promoted constant-radius fillets is supported.",
                )
            dimension = self._member(feature, "Parameter", "D1")
            if dimension is None:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_feature_parameter",
                    "Promoted fillet did not expose its local D1 radius parameter.",
                )
            status = int(
                self._member(
                    dimension,
                    "SetSystemValue3",
                    float(value) / 1000.0,
                    _SW_THIS_CONFIGURATION,
                    "",
                )
            )
            if status != 0:
                raise NativeRuntimeError(
                    "cad_mutation_failed",
                    "part_feature_parameter",
                    "SOLIDWORKS rejected the fillet radius dimension update.",
                    details={"status": status},
                )
            return MutationReceipt(feature_id)

        return self._execute(operation, stage="part_feature_parameter", mutation=True)

    def rename_feature(
        self,
        document: ResolvedDocument,
        feature_id: str,
        new_name: str,
    ) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            model = self._binding_from_document(app, document).model
            feature = self._member(model, "FeatureByName", feature_id)
            if feature is None:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_feature_rename",
                    f"Feature {feature_id!r} was not found.",
                )
            if self._feature_kind(feature) is None:
                raise NativeRuntimeError(
                    "unsupported_native_operation",
                    "part_feature_rename",
                    "Only evidence-promoted parametric features may be renamed by this lane.",
                )
            existing = self._member(model, "FeatureByName", new_name)
            if existing is not None and existing is not feature:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_feature_rename",
                    f"Feature name {new_name!r} is already in use.",
                )
            feature.Name = new_name
            identity = self._feature_name(feature).strip()
            if identity != new_name:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_feature_rename",
                    "Feature rename did not persist the requested identity.",
                    details={"requested": new_name, "actual": identity},
                )
            return MutationReceipt(identity)

        return self._execute(operation, stage="part_feature_rename", mutation=True)

    def list_features(self, document: ResolvedDocument) -> tuple[FeatureSnapshot, ...]:
        def operation(app: Any) -> tuple[FeatureSnapshot, ...]:
            model = self._binding_from_document(app, document).model
            feature = self._member(model, "FirstFeature")
            snapshots: list[FeatureSnapshot] = []
            visited = 0
            while feature is not None:
                visited += 1
                if visited > _MAX_FEATURE_TRAVERSAL:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "part_features_list",
                        "Feature-tree traversal exceeded its bounded limit.",
                    )
                kind = self._feature_kind(feature)
                if kind is not None:
                    name = self._feature_name(feature).strip()
                    if not name:
                        raise NativeRuntimeError(
                            "cad_postcondition_failed",
                            "part_features_list",
                            "Native feature returned an empty identity.",
                        )
                    snapshots.append(
                        FeatureSnapshot(
                            feature_id=name,
                            name=name,
                            kind=kind,
                            parameters={},
                            suppressed=self._is_feature_suppressed(feature),
                        )
                    )
                feature = self._member(feature, "GetNextFeature")
            return tuple(snapshots)

        return self._execute(operation, stage="part_features_list", mutation=False)

    def _feature_kind(self, feature: Any) -> FeatureKind | None:
        native_type = self._native_feature_type(feature)
        if native_type == "Cut":
            return FeatureKind.CUT
        if native_type in {"Hole", "SketchHole", "SimpleHole", "HoleWzd"}:
            return FeatureKind.HOLE
        if native_type in {"Revolution", "Revolve"}:
            return FeatureKind.REVOLVE
        if native_type in {"RevCut", "RevolveCut"}:
            return FeatureKind.REVOLVE_CUT
        if native_type in {"Fillet", "Fillet2", "Fillet3"}:
            return FeatureKind.FILLET
        if native_type == "Chamfer":
            return FeatureKind.CHAMFER
        if native_type == "Shell":
            return FeatureKind.SHELL
        if native_type == "Draft":
            return FeatureKind.DRAFT
        if native_type == "Rib":
            return FeatureKind.RIB
        if native_type in {"LPattern", "LinearPattern"}:
            return FeatureKind.LINEAR_PATTERN
        if native_type in {"CirPattern", "CircularPattern"}:
            return FeatureKind.CIRCULAR_PATTERN
        if native_type in {"MirrorPattern", "MirrorSolid"}:
            return FeatureKind.MIRROR
        if native_type == "RefPlane":
            return FeatureKind.REFERENCE_PLANE
        if native_type == "RefAxis":
            return FeatureKind.REFERENCE_AXIS
        if native_type in {"RefPoint", "PointRef"}:
            return FeatureKind.REFERENCE_POINT
        return None

    def _is_feature_suppressed(self, feature: Any) -> bool:
        raw = self._as_tuple(
            self._member(feature, "IsSuppressed2", _SW_THIS_CONFIGURATION, None)
        )
        if len(raw) != 1:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_readback",
                "Feature suppression read-back did not return exactly one current-configuration state.",
            )
        return bool(raw[0])

    def _native_feature_type(self, feature: Any) -> str:
        type_name = self._feature_type(feature)
        if type_name == "ICE":
            try:
                underlying = str(self._member(feature, "GetTypeName") or "").strip()
            except Exception:
                underlying = ""
            if underlying:
                return underlying
        return type_name

    def _solid_bounds_m(self, bodies: tuple[Any, ...]) -> tuple[float, float, float, float, float, float]:
        boxes: list[tuple[float, float, float, float, float, float]] = []
        for body in bodies:
            raw = self._as_tuple(self._member(body, "GetBodyBox"))
            if len(raw) != 6:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_native",
                    "Solid body did not expose a six-value bounding box.",
                )
            values = tuple(float(value) for value in raw)
            if not all(math.isfinite(value) for value in values):
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_hole_native",
                    "Solid body bounding box contained a non-finite value.",
                )
            boxes.append(values)
        return (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            min(box[2] for box in boxes),
            max(box[3] for box in boxes),
            max(box[4] for box in boxes),
            max(box[5] for box in boxes),
        )

    def _feature_names_by_native_type(self, model: Any, native_type: str) -> frozenset[str]:
        names: set[str] = set()
        feature = self._member(model, "FirstFeature")
        visited = 0
        while feature is not None:
            visited += 1
            if visited > _MAX_FEATURE_TRAVERSAL:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_feature_reconcile",
                    "Feature-tree traversal exceeded its bounded limit.",
                )
            if self._native_feature_type(feature) == native_type:
                name = self._feature_name(feature).strip()
                if name:
                    names.add(name)
            feature = self._member(feature, "GetNextFeature")
        return frozenset(names)

    def _single_new_feature_by_native_type(
        self,
        model: Any,
        native_type: str,
        existing_names: frozenset[str],
    ) -> Any:
        matches: list[Any] = []
        feature = self._member(model, "FirstFeature")
        visited = 0
        while feature is not None:
            visited += 1
            if visited > _MAX_FEATURE_TRAVERSAL:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_feature_reconcile",
                    "Feature-tree traversal exceeded its bounded limit.",
                )
            if self._native_feature_type(feature) == native_type:
                name = self._feature_name(feature).strip()
                if name and name not in existing_names:
                    matches.append(feature)
            feature = self._member(feature, "GetNextFeature")
        if len(matches) != 1:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "part_feature_reconcile",
                "Native mutation did not add exactly one expected feature.",
                details={"native_type": native_type, "new_feature_count": len(matches)},
            )
        return matches[0]

    def _finish_created_feature(
        self,
        feature: Any,
        requested_name: str,
        stage: str,
        label: str,
    ) -> MutationReceipt:
        if feature is None:
            raise NativeRuntimeError(
                "cad_mutation_failed",
                stage,
                f"SOLIDWORKS did not create the requested {label} feature.",
            )
        try:
            feature.Name = requested_name
        except Exception:
            pass
        identity = self._feature_name(feature).strip()
        if not identity:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                stage,
                f"Created {label} returned an empty feature identity.",
            )
        return MutationReceipt(identity)

    def _single_solid_body(self, model: Any, stage: str) -> Any:
        bodies = self._as_tuple(self._bodies(model, 0, False))
        if len(bodies) != 1:
            raise NativeRuntimeError(
                "cad_precondition_failed",
                stage,
                "Bounded topology resolution requires exactly one solid body.",
                details={"solid_body_count": len(bodies)},
            )
        return bodies[0]

    def _resolve_bounded_face(self, model: Any, reference: str) -> Any:
        if not reference.startswith("bbox:") or reference.startswith("bbox:edge:"):
            raise NativeRuntimeError(
                "cad_precondition_failed",
                "part_bounded_face",
                "Face reference must use bbox:+x|-x|+y|-y|+z|-z.",
                details={"reference": reference},
            )
        token = reference.split(":", 1)[1]
        normals = {
            "+x": (1.0, 0.0, 0.0),
            "-x": (-1.0, 0.0, 0.0),
            "+y": (0.0, 1.0, 0.0),
            "-y": (0.0, -1.0, 0.0),
            "+z": (0.0, 0.0, 1.0),
            "-z": (0.0, 0.0, -1.0),
        }
        expected = normals.get(token)
        if expected is None:
            raise NativeRuntimeError(
                "cad_precondition_failed",
                "part_bounded_face",
                "Unsupported bounded face reference.",
                details={"reference": reference},
            )
        body = self._single_solid_body(model, "part_bounded_face")
        matches: list[Any] = []
        for face in self._as_tuple(self._member(body, "GetFaces")):
            surface = self._member(face, "GetSurface")
            if surface is None or not bool(self._member(surface, "IsPlane")):
                continue
            normal = self._as_tuple(self._member(face, "Normal"))
            if len(normal) != 3:
                continue
            values = tuple(float(value) for value in normal)
            if all(abs(actual - wanted) <= 1e-8 for actual, wanted in zip(values, expected, strict=True)):
                matches.append(face)
        if len(matches) != 1:
            raise NativeRuntimeError(
                "cad_precondition_failed",
                "part_bounded_face",
                "Bounded face reference did not resolve to exactly one planar outer face.",
                details={"reference": reference, "match_count": len(matches)},
            )
        return matches[0]

    def _resolve_bounded_edge(self, model: Any, reference: str) -> Any:
        parts = reference.split(":")
        if len(parts) != 4 or parts[0] != "bbox" or parts[1] != "edge":
            raise NativeRuntimeError(
                "cad_precondition_failed",
                "part_bounded_edge",
                "Edge reference must use bbox:edge:<signed-axis>:<signed-axis>.",
                details={"reference": reference},
            )
        signed_axes = parts[2:]
        parsed: dict[int, int] = {}
        axis_index = {"x": 0, "y": 1, "z": 2}
        for token in signed_axes:
            if len(token) != 2 or token[0] not in "+-" or token[1] not in axis_index:
                raise NativeRuntimeError("cad_precondition_failed", "part_bounded_edge", "Invalid bounded edge token.")
            index = axis_index[token[1]]
            if index in parsed:
                raise NativeRuntimeError("cad_precondition_failed", "part_bounded_edge", "Bounded edge axes must be distinct.")
            parsed[index] = 1 if token[0] == "+" else -1
        body = self._single_solid_body(model, "part_bounded_edge")
        bounds = self._as_tuple(self._member(body, "GetBodyBox"))
        if len(bounds) != 6:
            raise NativeRuntimeError("cad_precondition_failed", "part_bounded_edge", "Solid body has no six-value bounding box.")
        low = tuple(float(value) for value in bounds[:3])
        high = tuple(float(value) for value in bounds[3:])
        tolerance = max(max(high[i] - low[i] for i in range(3)) * 1e-7, 1e-9)
        matches: list[Any] = []
        for edge in self._as_tuple(self._member(body, "GetEdges")):
            start = self._member(edge, "GetStartVertex")
            end = self._member(edge, "GetEndVertex")
            if start is None or end is None:
                continue
            p1 = self._as_tuple(self._member(start, "GetPoint"))
            p2 = self._as_tuple(self._member(end, "GetPoint"))
            if len(p1) != 3 or len(p2) != 3:
                continue
            ok = True
            for index, sign in parsed.items():
                expected = high[index] if sign > 0 else low[index]
                if abs(float(p1[index]) - expected) > tolerance or abs(float(p2[index]) - expected) > tolerance:
                    ok = False
                    break
            if ok:
                matches.append(edge)
        if len(matches) != 1:
            raise NativeRuntimeError(
                "cad_precondition_failed",
                "part_bounded_edge",
                "Bounded edge reference did not resolve to exactly one straight box edge.",
                details={"reference": reference, "match_count": len(matches)},
            )
        return matches[0]

    def _standard_plane_feature(self, model: Any, reference: str) -> Any:
        ordinal_map = {"plane:front": 0, "plane:top": 1, "plane:right": 2}
        ordinal = ordinal_map.get(reference.lower())
        if ordinal is None:
            raise NativeRuntimeError(
                "cad_precondition_failed",
                "part_bounded_reference",
                "Datum reference must be plane:front, plane:top, or plane:right.",
                details={"reference": reference},
            )
        feature = self._member(model, "FirstFeature")
        found: list[Any] = []
        visited = 0
        while feature is not None and len(found) <= ordinal:
            visited += 1
            if visited > _MAX_FEATURE_TRAVERSAL:
                raise NativeRuntimeError("cad_postcondition_failed", "part_bounded_reference", "Datum traversal exceeded bounded limit.")
            if self._native_feature_type(feature) == "RefPlane":
                found.append(feature)
            feature = self._member(feature, "GetNextFeature")
        if len(found) <= ordinal:
            raise NativeRuntimeError("cad_precondition_failed", "part_bounded_reference", "Requested standard datum plane was not found.")
        return found[ordinal]

    def _resolve_bounded_reference(
        self,
        model: Any,
        reference: str,
        *,
        allow_face: bool,
        allow_edge: bool,
        allow_axis: bool = False,
    ) -> Any:
        lower = reference.lower()
        if lower.startswith("plane:"):
            return self._standard_plane_feature(model, lower)
        if reference.startswith("bbox:edge:"):
            if not allow_edge:
                raise NativeRuntimeError("cad_precondition_failed", "part_bounded_reference", "Edge reference is not valid for this operation.")
            return self._resolve_bounded_edge(model, reference)
        if reference.startswith("bbox:"):
            if not allow_face:
                raise NativeRuntimeError("cad_precondition_failed", "part_bounded_reference", "Face reference is not valid for this operation.")
            return self._resolve_bounded_face(model, reference)
        if reference.startswith("feature:"):
            feature = self._member(model, "FeatureByName", reference.split(":", 1)[1])
            if feature is None:
                raise NativeRuntimeError("cad_precondition_failed", "part_bounded_reference", "Explicit reference feature was not found.")
            kind = self._feature_kind(feature)
            if kind is FeatureKind.REFERENCE_AXIS and allow_axis:
                return feature
            if kind is FeatureKind.REFERENCE_PLANE and allow_face:
                return feature
            raise NativeRuntimeError("cad_precondition_failed", "part_bounded_reference", "Explicit feature type is not valid for this reference role.")
        raise NativeRuntimeError(
            "cad_precondition_failed",
            "part_bounded_reference",
            "Reference is outside the bounded native selector contract.",
            details={"reference": reference},
        )

    def _resolve_seed_features(self, model: Any, feature_ids: tuple[str, ...]) -> tuple[Any, ...]:
        seeds: list[Any] = []
        for feature_id in feature_ids:
            feature = self._member(model, "FeatureByName", feature_id)
            if feature is None or self._feature_kind(feature) is None:
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_pattern_seed",
                    "Pattern seed must resolve to an evidence-promoted feature identity.",
                    details={"feature_id": feature_id},
                )
            seeds.append(feature)
        return tuple(seeds)

    def _select_native_entity(
        self,
        model: Any,
        entity: Any,
        *,
        append: bool,
        mark: int,
        stage: str,
    ) -> None:
        selection_manager = self._member(model, "SelectionManager")
        select_data = self._member(selection_manager, "CreateSelectData")
        select_data.Mark = mark
        selected = False
        try:
            selected = bool(self._member(entity, "Select4", append, select_data))
        except Exception:
            selected = bool(self._member(entity, "Select2", append, mark))
        if not selected:
            raise NativeRuntimeError("cad_selection_failed", stage, "Native bounded reference could not be selected.")

    @staticmethod
    def _dispatch_array(values: tuple[Any, ...]) -> Any:
        try:
            import pythoncom  # type: ignore[import-not-found]
            from win32com.client import VARIANT  # type: ignore[import-not-found]
        except ImportError:
            return tuple(values)
        return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, tuple(values))

    def _binding_from_document(self, app: Any, document: ResolvedDocument) -> NativePartBinding:
        binding = self._resolve_binding(
            app,
            DocumentTarget(document.document_id, document.revision, document.units),
        )
        if binding.document_id != document.document_id or binding.units != document.units:
            raise NativeRuntimeError(
                "document_context_mismatch",
                "part_native_binding",
                "Native part identity or units changed during the operation.",
            )
        return binding

    @staticmethod
    def _as_tuple(value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, (tuple, list)):
            return tuple(value)
        return (value,)
