from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cdt_solidworks.part.models import (  # noqa: E402
    BodySnapshot,
    Bounds3D,
    ChamferSpec,
    CircularPatternSpec,
    DraftSpec,
    FeatureKind,
    FeatureSnapshot,
    FilletSpec,
    HoleSpec,
    HoleWizardSize,
    HoleWizardSpec,
    LinearPatternSpec,
    LoftSpec,
    MirrorSpec,
    PartPostconditions,
    ProfileRef,
    ReferenceAxisSpec,
    ReferencePlaneSpec,
    ReferencePointSpec,
    RevolveCutSpec,
    RibSpec,
    ShellSpec,
    SweepSpec,
)
from cdt_solidworks.part.runtime import (  # noqa: E402
    DocumentTarget,
    MutationReceipt,
    RebuildResult,
    ResolvedDocument,
)
from cdt_solidworks.part.service import PartMutationError, PartService, PartValidationError  # noqa: E402


class BreadthPartRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.resolved = ResolvedDocument(
            document_id="part-breadth",
            revision=9,
            document_type="part",
            units="mm",
            configuration="Default",
        )
        self.current_feature: FeatureSnapshot | None = None
        self.bodies: tuple[BodySnapshot, ...] = (
            BodySnapshot("Body1", Bounds3D(0.0, 0.0, 0.0, 100.0, 60.0, 10.0)),
        )

    def resolve_document(self, target: DocumentTarget) -> ResolvedDocument:
        self.calls.append("resolve_document")
        return self.resolved

    def _create(self, method: str, spec: object, kind: FeatureKind, parameters: dict[str, float | str | bool]) -> MutationReceipt:
        self.calls.append(method)
        feature_id = f"{kind.value}-1"
        self.current_feature = FeatureSnapshot(feature_id, getattr(spec, "name"), kind, parameters)
        return MutationReceipt(feature_id)

    def create_revolve_cut(self, document: ResolvedDocument, spec: RevolveCutSpec) -> MutationReceipt:
        return self._create(
            "create_revolve_cut",
            spec,
            FeatureKind.REVOLVE_CUT,
            {"angle_deg": spec.angle_deg, "axis_ref": spec.axis_ref},
        )

    def create_hole(self, document: ResolvedDocument, spec: HoleSpec) -> MutationReceipt:
        params: dict[str, float | str | bool | int] = {
            "diameter_mm": spec.diameter_mm,
            "face_ref": spec.face_ref,
            "center_count": len(spec.centers_mm),
            "through_all": spec.through_all,
        }
        if spec.depth_mm is not None:
            params["depth_mm"] = spec.depth_mm
        return self._create("create_hole", spec, FeatureKind.HOLE, params)

    def create_hole_wizard(self, document: ResolvedDocument, spec: HoleWizardSpec) -> MutationReceipt:
        return self._create(
            "create_hole_wizard",
            spec,
            FeatureKind.HOLE,
            {
                "wizard_standard": "ANSI Metric",
                "wizard_fastener": "Flat Head Screw - ANSI B18.6.7M",
                "wizard_size": spec.size.value,
                "face_ref": spec.face_ref,
                "center_count": 1,
                "center_x_mm": spec.center_mm[0],
                "center_y_mm": spec.center_mm[1],
                "through_all": True,
            },
        )

    def create_fillet(self, document: ResolvedDocument, spec: FilletSpec) -> MutationReceipt:
        return self._create(
            "create_fillet",
            spec,
            FeatureKind.FILLET,
            {"radius_mm": spec.radius_mm, "tangent_propagation": spec.tangent_propagation},
        )

    def create_chamfer(self, document: ResolvedDocument, spec: ChamferSpec) -> MutationReceipt:
        return self._create(
            "create_chamfer",
            spec,
            FeatureKind.CHAMFER,
            {"distance_mm": spec.distance_mm, "angle_deg": spec.angle_deg},
        )

    def create_shell(self, document: ResolvedDocument, spec: ShellSpec) -> MutationReceipt:
        return self._create(
            "create_shell",
            spec,
            FeatureKind.SHELL,
            {"thickness_mm": spec.thickness_mm, "outward": spec.outward},
        )

    def create_draft(self, document: ResolvedDocument, spec: DraftSpec) -> MutationReceipt:
        return self._create(
            "create_draft",
            spec,
            FeatureKind.DRAFT,
            {
                "angle_deg": spec.angle_deg,
                "neutral_plane_ref": spec.neutral_plane_ref,
                "reverse_direction": spec.reverse_direction,
            },
        )

    def create_rib(self, document: ResolvedDocument, spec: RibSpec) -> MutationReceipt:
        return self._create(
            "create_rib",
            spec,
            FeatureKind.RIB,
            {"thickness_mm": spec.thickness_mm, "both_sides": spec.both_sides},
        )

    def create_linear_pattern(self, document: ResolvedDocument, spec: LinearPatternSpec) -> MutationReceipt:
        return self._create(
            "create_linear_pattern",
            spec,
            FeatureKind.LINEAR_PATTERN,
            {
                "count": spec.count,
                "spacing_mm": spec.spacing_mm,
                "direction_ref": spec.direction_ref,
                "geometry_pattern": spec.geometry_pattern,
            },
        )

    def create_circular_pattern(self, document: ResolvedDocument, spec: CircularPatternSpec) -> MutationReceipt:
        return self._create(
            "create_circular_pattern",
            spec,
            FeatureKind.CIRCULAR_PATTERN,
            {
                "count": spec.count,
                "angle_deg": spec.angle_deg,
                "axis_ref": spec.axis_ref,
                "geometry_pattern": spec.geometry_pattern,
            },
        )

    def create_mirror(self, document: ResolvedDocument, spec: MirrorSpec) -> MutationReceipt:
        return self._create(
            "create_mirror",
            spec,
            FeatureKind.MIRROR,
            {"mirror_ref": spec.mirror_ref, "geometry_pattern": spec.geometry_pattern},
        )

    def create_sweep(self, document: ResolvedDocument, spec: SweepSpec) -> MutationReceipt:
        return self._create(
            "create_sweep",
            spec,
            FeatureKind.SWEEP,
            {"path_sketch_id": spec.path.sketch_id},
        )

    def create_loft(self, document: ResolvedDocument, spec: LoftSpec) -> MutationReceipt:
        return self._create(
            "create_loft",
            spec,
            FeatureKind.LOFT,
            {"profile_count": len(spec.profiles), "closed": spec.closed},
        )

    def create_reference_plane(self, document: ResolvedDocument, spec: ReferencePlaneSpec) -> MutationReceipt:
        return self._create(
            "create_reference_plane",
            spec,
            FeatureKind.REFERENCE_PLANE,
            {"reference": spec.reference, "offset_mm": spec.offset_mm, "reverse_direction": spec.reverse_direction},
        )

    def create_reference_axis(self, document: ResolvedDocument, spec: ReferenceAxisSpec) -> MutationReceipt:
        return self._create(
            "create_reference_axis",
            spec,
            FeatureKind.REFERENCE_AXIS,
            {"first_ref": spec.first_ref, "second_ref": spec.second_ref},
        )

    def create_reference_point(self, document: ResolvedDocument, spec: ReferencePointSpec) -> MutationReceipt:
        return self._create(
            "create_reference_point",
            spec,
            FeatureKind.REFERENCE_POINT,
            {"reference": spec.reference},
        )

    def rebuild(self, document: ResolvedDocument) -> RebuildResult:
        self.calls.append("rebuild")
        return RebuildResult(ok=True)

    def get_feature(self, document: ResolvedDocument, feature_id: str) -> FeatureSnapshot | None:
        self.calls.append("get_feature")
        return self.current_feature

    def list_features(self, document: ResolvedDocument) -> tuple[FeatureSnapshot, ...]:
        self.calls.append("list_features")
        return () if self.current_feature is None else (self.current_feature,)

    def list_bodies(self, document: ResolvedDocument) -> tuple[BodySnapshot, ...]:
        self.calls.append("list_bodies")
        return self.bodies

    def rename_feature(
        self,
        document: ResolvedDocument,
        feature_id: str,
        new_name: str,
    ) -> MutationReceipt:
        self.calls.append("rename_feature")
        if self.current_feature is None:
            self.current_feature = FeatureSnapshot(feature_id, feature_id, FeatureKind.FILLET, {})
        self.current_feature = FeatureSnapshot(
            new_name,
            new_name,
            self.current_feature.kind,
            self.current_feature.parameters,
            suppressed=self.current_feature.suppressed,
        )
        return MutationReceipt(new_name)

    def set_feature_suppressed(
        self,
        document: ResolvedDocument,
        feature_id: str,
        suppressed: bool,
    ) -> MutationReceipt:
        self.calls.append("set_feature_suppressed")
        if self.current_feature is None:
            self.current_feature = FeatureSnapshot(feature_id, feature_id, FeatureKind.FILLET, {})
        self.current_feature = FeatureSnapshot(
            self.current_feature.feature_id,
            self.current_feature.name,
            self.current_feature.kind,
            self.current_feature.parameters,
            suppressed=suppressed,
        )
        return MutationReceipt(feature_id)

    def set_feature_parameter(
        self,
        document: ResolvedDocument,
        feature_id: str,
        parameter: str,
        value: float,
    ) -> MutationReceipt:
        self.calls.append("set_feature_parameter")
        if self.current_feature is None:
            self.current_feature = FeatureSnapshot(feature_id, feature_id, FeatureKind.FILLET, {"radius_mm": 2.0})
        params = dict(self.current_feature.parameters)
        params[parameter] = value
        self.current_feature = FeatureSnapshot(
            self.current_feature.feature_id,
            self.current_feature.name,
            self.current_feature.kind,
            params,
            suppressed=self.current_feature.suppressed,
        )
        return MutationReceipt(feature_id)


class PartBreadthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = DocumentTarget("part-breadth", expected_revision=9, expected_units="mm")
        self.runtime = BreadthPartRuntime()
        self.service = PartService(self.runtime)
        self.post = PartPostconditions(body_count=1)

    def test_revolve_cut_uses_normal_mutation_gate(self) -> None:
        result = self.service.revolve_cut(
            self.target,
            RevolveCutSpec("Groove", ProfileRef("Sketch2"), axis_ref="Axis1", angle_deg=360.0),
            postconditions=self.post,
        )
        self.assertEqual(FeatureKind.REVOLVE_CUT, result.feature.kind)
        self.assertEqual(
            ["resolve_document", "create_revolve_cut", "rebuild", "get_feature", "list_bodies"],
            self.runtime.calls,
        )

    def test_simple_hole_requires_depth_only_for_blind_hole(self) -> None:
        with self.assertRaisesRegex(PartValidationError, "blind hole"):
            self.service.hole(
                self.target,
                HoleSpec("Hole1", 6.0, face_ref="FaceTop", centers_mm=((10.0, 10.0),), through_all=False),
            )
        self.assertEqual([], self.runtime.calls)

        result = self.service.hole(
            self.target,
            HoleSpec("Hole1", 6.0, face_ref="FaceTop", centers_mm=((10.0, 10.0),), through_all=True),
            postconditions=self.post,
        )
        self.assertEqual(FeatureKind.HOLE, result.feature.kind)

    def test_hole_wizard_uses_typed_size_and_common_mutation_gate(self) -> None:
        result = self.service.hole_wizard(
            self.target,
            HoleWizardSpec("CSK_M4", HoleWizardSize.M4, center_mm=(10.0, 5.0)),
            postconditions=self.post,
        )

        self.assertEqual(FeatureKind.HOLE, result.feature.kind)
        self.assertEqual("M4", result.feature.parameters["wizard_size"])
        self.assertEqual(
            ["resolve_document", "create_hole_wizard", "rebuild", "get_feature", "list_bodies"],
            self.runtime.calls,
        )

    def test_hole_wizard_rejects_mismatched_native_center_readback(self) -> None:
        original = self.runtime.create_hole_wizard

        def mismatched_center(document: ResolvedDocument, spec: HoleWizardSpec) -> MutationReceipt:
            receipt = original(document, spec)
            assert self.runtime.current_feature is not None
            params = dict(self.runtime.current_feature.parameters)
            params["center_x_mm"] = spec.center_mm[0] + 1.0
            self.runtime.current_feature = FeatureSnapshot(
                self.runtime.current_feature.feature_id,
                self.runtime.current_feature.name,
                self.runtime.current_feature.kind,
                params,
            )
            return receipt

        self.runtime.create_hole_wizard = mismatched_center  # type: ignore[method-assign]

        with self.assertRaisesRegex(PartMutationError, "center_x_mm"):
            self.service.hole_wizard(
                self.target,
                HoleWizardSpec("CSK_M4", HoleWizardSize.M4, center_mm=(10.0, 5.0)),
                postconditions=self.post,
            )

    def test_fillet_chamfer_shell_have_strict_ranges(self) -> None:
        with self.assertRaisesRegex(PartValidationError, "fillet radius"):
            self.service.fillet(self.target, FilletSpec("F", edge_refs=("E1",), radius_mm=0.0))
        with self.assertRaisesRegex(PartValidationError, "chamfer angle"):
            self.service.chamfer(self.target, ChamferSpec("C", ("E1",), distance_mm=1.0, angle_deg=90.0))
        with self.assertRaisesRegex(PartValidationError, "shell thickness"):
            self.service.shell(self.target, ShellSpec("S", face_refs=("F1",), thickness_mm=-1.0))
        self.assertEqual([], self.runtime.calls)

    def test_draft_and_rib_require_explicit_references(self) -> None:
        with self.assertRaisesRegex(PartValidationError, "neutral plane"):
            self.service.draft(self.target, DraftSpec("D", face_refs=("F1",), neutral_plane_ref="", angle_deg=3.0))
        with self.assertRaisesRegex(PartValidationError, "profile"):
            self.service.rib(self.target, RibSpec("R", ProfileRef(""), thickness_mm=2.0))

    def test_patterns_are_bounded_and_require_seed_identity(self) -> None:
        with self.assertRaisesRegex(PartValidationError, "count"):
            self.service.linear_pattern(
                self.target,
                LinearPatternSpec("LP", seed_feature_ids=("Cut1",), direction_ref="Edge1", count=1001, spacing_mm=10.0),
            )
        with self.assertRaisesRegex(PartValidationError, "seed"):
            self.service.circular_pattern(
                self.target,
                CircularPatternSpec("CP", seed_feature_ids=(), axis_ref="Axis1", count=4, angle_deg=360.0),
            )

    def test_mirror_sweep_and_loft_validate_identity_before_dispatch(self) -> None:
        with self.assertRaisesRegex(PartValidationError, "mirror reference"):
            self.service.mirror(self.target, MirrorSpec("M", seed_feature_ids=("F1",), mirror_ref=""))
        with self.assertRaisesRegex(PartValidationError, "path"):
            self.service.sweep(self.target, SweepSpec("SW", ProfileRef("Profile"), ProfileRef("")))
        with self.assertRaisesRegex(PartValidationError, "at least two"):
            self.service.loft(self.target, LoftSpec("L", profiles=(ProfileRef("P1"),)))

    def test_reference_geometry_has_finite_bounded_parameters(self) -> None:
        plane = self.service.reference_plane(
            self.target,
            ReferencePlaneSpec("PlaneA", reference="Front Plane", offset_mm=15.0),
            postconditions=self.post,
        )
        self.assertEqual(FeatureKind.REFERENCE_PLANE, plane.feature.kind)

        with self.assertRaisesRegex(PartValidationError, "distinct references"):
            self.service.reference_axis(self.target, ReferenceAxisSpec("AxisA", "Face1", "Face1"))
        with self.assertRaisesRegex(PartValidationError, "reference"):
            self.service.reference_point(self.target, ReferencePointSpec("PointA", reference=""))

    def test_positive_feature_breadth_uses_common_mutation_gate(self) -> None:
        cases = (
            (
                FeatureKind.FILLET,
                "create_fillet",
                lambda: self.service.fillet(
                    self.target,
                    FilletSpec("F", edge_refs=("Edge1",), radius_mm=2.0, tangent_propagation=True),
                    postconditions=self.post,
                ),
            ),
            (
                FeatureKind.CHAMFER,
                "create_chamfer",
                lambda: self.service.chamfer(
                    self.target,
                    ChamferSpec("C", edge_refs=("Edge1",), distance_mm=1.0, angle_deg=45.0),
                    postconditions=self.post,
                ),
            ),
            (
                FeatureKind.SHELL,
                "create_shell",
                lambda: self.service.shell(
                    self.target,
                    ShellSpec("S", face_refs=("FaceTop",), thickness_mm=1.5),
                    postconditions=self.post,
                ),
            ),
            (
                FeatureKind.DRAFT,
                "create_draft",
                lambda: self.service.draft(
                    self.target,
                    DraftSpec("D", face_refs=("FaceSide",), neutral_plane_ref="Front Plane", angle_deg=3.0),
                    postconditions=self.post,
                ),
            ),
            (
                FeatureKind.RIB,
                "create_rib",
                lambda: self.service.rib(
                    self.target,
                    RibSpec("R", ProfileRef("RibSketch"), thickness_mm=2.0),
                    postconditions=self.post,
                ),
            ),
            (
                FeatureKind.LINEAR_PATTERN,
                "create_linear_pattern",
                lambda: self.service.linear_pattern(
                    self.target,
                    LinearPatternSpec("LP", ("Cut1",), "Edge1", count=4, spacing_mm=20.0),
                    postconditions=self.post,
                ),
            ),
            (
                FeatureKind.CIRCULAR_PATTERN,
                "create_circular_pattern",
                lambda: self.service.circular_pattern(
                    self.target,
                    CircularPatternSpec("CP", ("Cut1",), "Axis1", count=6, angle_deg=360.0),
                    postconditions=self.post,
                ),
            ),
            (
                FeatureKind.MIRROR,
                "create_mirror",
                lambda: self.service.mirror(
                    self.target,
                    MirrorSpec("M", ("Fillet1",), "Right Plane"),
                    postconditions=self.post,
                ),
            ),
            (
                FeatureKind.SWEEP,
                "create_sweep",
                lambda: self.service.sweep(
                    self.target,
                    SweepSpec("SW", ProfileRef("SweepProfile"), ProfileRef("SweepPath")),
                    postconditions=self.post,
                ),
            ),
            (
                FeatureKind.LOFT,
                "create_loft",
                lambda: self.service.loft(
                    self.target,
                    LoftSpec("L", (ProfileRef("P1"), ProfileRef("P2"))),
                    postconditions=self.post,
                ),
            ),
            (
                FeatureKind.REFERENCE_AXIS,
                "create_reference_axis",
                lambda: self.service.reference_axis(
                    self.target,
                    ReferenceAxisSpec("AxisA", "Face1", "Face2"),
                    postconditions=self.post,
                ),
            ),
            (
                FeatureKind.REFERENCE_POINT,
                "create_reference_point",
                lambda: self.service.reference_point(
                    self.target,
                    ReferencePointSpec("PointA", "Vertex1"),
                    postconditions=self.post,
                ),
            ),
        )

        for expected_kind, dispatch_name, invoke in cases:
            with self.subTest(kind=expected_kind.value):
                self.runtime.calls.clear()
                result = invoke()
                self.assertEqual(expected_kind, result.feature.kind)
                self.assertEqual(
                    ["resolve_document", dispatch_name, "rebuild", "get_feature", "list_bodies"],
                    self.runtime.calls,
                )

    def test_feature_rename_requires_rebuild_and_readback(self) -> None:
        self.runtime.current_feature = FeatureSnapshot("Fillet1", "Fillet1", FeatureKind.FILLET, {"radius_mm": 2.0})

        snapshot = self.service.rename_feature(self.target, "Fillet1", "EdgeRound")

        self.assertEqual("EdgeRound", snapshot.feature_id)
        self.assertEqual("EdgeRound", snapshot.name)
        self.assertEqual(
            ["resolve_document", "rename_feature", "rebuild", "get_feature"],
            self.runtime.calls,
        )

    def test_selected_feature_parameter_edit_requires_rebuild_and_readback(self) -> None:
        self.runtime.current_feature = FeatureSnapshot("Fillet1", "Fillet1", FeatureKind.FILLET, {"radius_mm": 2.0})

        snapshot = self.service.set_feature_parameter(self.target, "Fillet1", "radius_mm", 3.5)

        self.assertEqual(3.5, snapshot.parameters["radius_mm"])
        self.assertEqual(
            ["resolve_document", "set_feature_parameter", "rebuild", "get_feature"],
            self.runtime.calls,
        )

        self.runtime.calls.clear()
        with self.assertRaisesRegex(PartValidationError, "radius_mm"):
            self.service.set_feature_parameter(self.target, "Fillet1", "radius_mm", 0.0)
        self.assertEqual([], self.runtime.calls)

        with self.assertRaisesRegex(PartValidationError, "unsupported"):
            self.service.set_feature_parameter(self.target, "Fillet1", "arbitrary", 1.0)

    def test_suppress_unsuppress_requires_rebuild_and_readback(self) -> None:
        self.runtime.current_feature = FeatureSnapshot("Fillet1", "Fillet1", FeatureKind.FILLET, {"radius_mm": 2.0})

        snapshot = self.service.set_feature_suppressed(self.target, "Fillet1", True)

        self.assertTrue(snapshot.suppressed)
        self.assertEqual(
            ["resolve_document", "set_feature_suppressed", "rebuild", "get_feature"],
            self.runtime.calls,
        )

    def test_suppression_readback_mismatch_is_failure(self) -> None:
        self.runtime.current_feature = FeatureSnapshot("Fillet1", "Fillet1", FeatureKind.FILLET, {})
        original = self.runtime.set_feature_suppressed

        def mismatch(document: ResolvedDocument, feature_id: str, suppressed: bool) -> MutationReceipt:
            receipt = original(document, feature_id, suppressed)
            assert self.runtime.current_feature is not None
            self.runtime.current_feature = FeatureSnapshot(
                self.runtime.current_feature.feature_id,
                self.runtime.current_feature.name,
                self.runtime.current_feature.kind,
                self.runtime.current_feature.parameters,
                suppressed=False,
            )
            return receipt

        self.runtime.set_feature_suppressed = mismatch  # type: ignore[method-assign]

        with self.assertRaisesRegex(PartMutationError, "suppression"):
            self.service.set_feature_suppressed(self.target, "Fillet1", True)


if __name__ == "__main__":
    unittest.main()
