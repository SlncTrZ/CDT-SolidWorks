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
    CutSpec,
    FeatureKind,
    FeatureSnapshot,
    HoleSpec,
    HoleWizardSize,
    HoleWizardSpec,
    RevolveCutSpec,
    RevolveSpec,
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
_SW_SEL_FACES = 2
_SW_SELECT_DEFAULT = 0
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
            return None

        return self._execute(operation, stage="part_feature_readback", mutation=False)

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
