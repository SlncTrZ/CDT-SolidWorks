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
    CutSpec,
    ExtrudeSpec,
    FeatureKind,
    FeatureSnapshot,
    HoleFact,
    PartPostconditions,
    ProfileRef,
    RevolveSpec,
)
from cdt_solidworks.part.runtime import (  # noqa: E402
    DocumentTarget,
    MutationReceipt,
    RebuildResult,
    ResolvedDocument,
)
from cdt_solidworks.part.service import (  # noqa: E402
    PartContextError,
    PartMutationError,
    PartService,
    PartValidationError,
)


class FakePartRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.resolved = ResolvedDocument(
            document_id="part-1",
            revision=12,
            document_type="part",
            units="mm",
            configuration="Default",
        )
        self.receipt = MutationReceipt(object_id="Boss-Extrude1")
        self.rebuild_result = RebuildResult(ok=True)
        self.feature: FeatureSnapshot | None = FeatureSnapshot(
            feature_id="Boss-Extrude1",
            name="BaseExtrude",
            kind=FeatureKind.EXTRUDE,
            parameters={"depth_mm": 10.0},
        )
        self.features: tuple[FeatureSnapshot, ...] = (self.feature,)
        self.bodies: tuple[BodySnapshot, ...] = (
            BodySnapshot(
                body_id="Body1",
                bounds=Bounds3D(0.0, 0.0, 0.0, 120.0, 80.0, 10.0),
            ),
        )

    def resolve_document(self, target: DocumentTarget) -> ResolvedDocument:
        self.calls.append("resolve_document")
        return self.resolved

    def create_extrude(self, document: ResolvedDocument, spec: ExtrudeSpec) -> MutationReceipt:
        self.calls.append("create_extrude")
        return self.receipt

    def create_cut(self, document: ResolvedDocument, spec: CutSpec) -> MutationReceipt:
        self.calls.append("create_cut")
        return self.receipt

    def create_revolve(self, document: ResolvedDocument, spec: RevolveSpec) -> MutationReceipt:
        self.calls.append("create_revolve")
        return self.receipt

    def rebuild(self, document: ResolvedDocument) -> RebuildResult:
        self.calls.append("rebuild")
        return self.rebuild_result

    def get_feature(self, document: ResolvedDocument, feature_id: str) -> FeatureSnapshot | None:
        self.calls.append("get_feature")
        return self.feature

    def list_features(self, document: ResolvedDocument) -> tuple[FeatureSnapshot, ...]:
        self.calls.append("list_features")
        return self.features

    def list_bodies(self, document: ResolvedDocument) -> tuple[BodySnapshot, ...]:
        self.calls.append("list_bodies")
        return self.bodies


class PartServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = DocumentTarget(document_id="part-1", expected_revision=12, expected_units="mm")
        self.runtime = FakePartRuntime()
        self.service = PartService(self.runtime)

    def test_extrude_requires_rebuild_feature_readback_and_body_readback(self) -> None:
        spec = ExtrudeSpec(name="BaseExtrude", profile=ProfileRef("PlateProfile"), depth_mm=10.0)
        postconditions = PartPostconditions(
            body_count=1,
            bounds=(Bounds3D(0.0, 0.0, 0.0, 120.0, 80.0, 10.0),),
        )

        result = self.service.extrude(self.target, spec, postconditions=postconditions)

        self.assertEqual("Boss-Extrude1", result.feature.feature_id)
        self.assertEqual(1, len(result.bodies))
        self.assertEqual(
            ["resolve_document", "create_extrude", "rebuild", "get_feature", "list_bodies"],
            self.runtime.calls,
        )

    def test_cut_through_all_verifies_feature_and_hole_facts(self) -> None:
        self.runtime.receipt = MutationReceipt(object_id="Cut-Extrude1")
        self.runtime.feature = FeatureSnapshot(
            feature_id="Cut-Extrude1",
            name="MountHoles",
            kind=FeatureKind.CUT,
            parameters={"through_all": True},
        )
        expected_holes = (
            HoleFact(15.0, 15.0, 3.0, through=True),
            HoleFact(105.0, 15.0, 3.0, through=True),
            HoleFact(15.0, 65.0, 3.0, through=True),
            HoleFact(105.0, 65.0, 3.0, through=True),
        )
        self.runtime.bodies = (
            BodySnapshot(
                body_id="Body1",
                bounds=Bounds3D(0.0, 0.0, 0.0, 120.0, 80.0, 10.0),
                holes=expected_holes,
            ),
        )
        spec = CutSpec(name="MountHoles", profile=ProfileRef("HoleSketch"), through_all=True)

        result = self.service.cut(
            self.target,
            spec,
            postconditions=PartPostconditions(body_count=1, holes=expected_holes),
        )

        self.assertEqual(FeatureKind.CUT, result.feature.kind)
        self.assertEqual(4, len(result.bodies[0].holes))
        self.assertEqual(
            ["resolve_document", "create_cut", "rebuild", "get_feature", "list_bodies"],
            self.runtime.calls,
        )

    def test_revolve_baseline_verifies_angle_and_axis(self) -> None:
        self.runtime.receipt = MutationReceipt(object_id="Revolve1")
        self.runtime.feature = FeatureSnapshot(
            feature_id="Revolve1",
            name="Knob",
            kind=FeatureKind.REVOLVE,
            parameters={"angle_deg": 360.0, "axis_ref": "Axis1"},
        )
        spec = RevolveSpec(
            name="Knob",
            profile=ProfileRef("RevolveProfile"),
            axis_ref="Axis1",
            angle_deg=360.0,
        )

        result = self.service.revolve(self.target, spec, postconditions=PartPostconditions(body_count=1))

        self.assertEqual(FeatureKind.REVOLVE, result.feature.kind)
        self.assertEqual(
            ["resolve_document", "create_revolve", "rebuild", "get_feature", "list_bodies"],
            self.runtime.calls,
        )

    def test_negative_extrude_depth_is_rejected_before_runtime_dispatch(self) -> None:
        spec = ExtrudeSpec(name="Bad", profile=ProfileRef("PlateProfile"), depth_mm=-1.0)

        with self.assertRaisesRegex(PartValidationError, "positive"):
            self.service.extrude(self.target, spec)

        self.assertEqual([], self.runtime.calls)

    def test_empty_profile_reference_is_rejected_before_runtime_dispatch(self) -> None:
        spec = ExtrudeSpec(name="Bad", profile=ProfileRef(""), depth_mm=10.0)

        with self.assertRaisesRegex(PartValidationError, "profile"):
            self.service.extrude(self.target, spec)

        self.assertEqual([], self.runtime.calls)

    def test_stale_document_is_rejected_before_mutation(self) -> None:
        self.runtime.resolved = ResolvedDocument(
            document_id="part-1",
            revision=13,
            document_type="part",
            units="mm",
            configuration="Default",
        )
        spec = ExtrudeSpec(name="BaseExtrude", profile=ProfileRef("PlateProfile"), depth_mm=10.0)

        with self.assertRaisesRegex(PartContextError, "stale document"):
            self.service.extrude(self.target, spec)

        self.assertEqual(["resolve_document"], self.runtime.calls)

    def test_wrong_document_identity_is_rejected_before_mutation(self) -> None:
        self.runtime.resolved = ResolvedDocument(
            document_id="part-2",
            revision=12,
            document_type="part",
            units="mm",
            configuration="Default",
        )
        spec = ExtrudeSpec(name="BaseExtrude", profile=ProfileRef("PlateProfile"), depth_mm=10.0)

        with self.assertRaisesRegex(PartContextError, "document identity"):
            self.service.extrude(self.target, spec)

        self.assertEqual(["resolve_document"], self.runtime.calls)

    def test_rebuild_failure_is_never_reported_as_success(self) -> None:
        self.runtime.rebuild_result = RebuildResult(ok=False, error_code="feature_error", message="zero thickness")
        spec = ExtrudeSpec(name="BaseExtrude", profile=ProfileRef("PlateProfile"), depth_mm=10.0)

        with self.assertRaisesRegex(PartMutationError, "rebuild failed"):
            self.service.extrude(self.target, spec)

        self.assertEqual(["resolve_document", "create_extrude", "rebuild"], self.runtime.calls)

    def test_feature_readback_mismatch_is_failure(self) -> None:
        self.runtime.feature = FeatureSnapshot(
            feature_id="Boss-Extrude1",
            name="BaseExtrude",
            kind=FeatureKind.EXTRUDE,
            parameters={"depth_mm": 9.0},
        )
        spec = ExtrudeSpec(name="BaseExtrude", profile=ProfileRef("PlateProfile"), depth_mm=10.0)

        with self.assertRaisesRegex(PartMutationError, "depth_mm"):
            self.service.extrude(self.target, spec)

    def test_body_postcondition_mismatch_is_failure(self) -> None:
        spec = ExtrudeSpec(name="BaseExtrude", profile=ProfileRef("PlateProfile"), depth_mm=10.0)

        with self.assertRaisesRegex(PartMutationError, "body count"):
            self.service.extrude(self.target, spec, postconditions=PartPostconditions(body_count=2))

    def test_invalid_postcondition_tolerance_is_rejected_before_runtime_dispatch(self) -> None:
        spec = ExtrudeSpec(name="BaseExtrude", profile=ProfileRef("PlateProfile"), depth_mm=10.0)

        with self.assertRaisesRegex(PartValidationError, "tolerance"):
            self.service.extrude(
                self.target,
                spec,
                postconditions=PartPostconditions(body_count=1, tolerance_mm=-1.0),
            )

        self.assertEqual([], self.runtime.calls)

    def test_unsupported_document_type_is_rejected_before_mutation(self) -> None:
        self.runtime.resolved = ResolvedDocument(
            document_id="part-1",
            revision=12,
            document_type="assembly",
            units="mm",
            configuration="Default",
        )
        spec = ExtrudeSpec(name="BaseExtrude", profile=ProfileRef("PlateProfile"), depth_mm=10.0)

        with self.assertRaisesRegex(PartContextError, "part document"):
            self.service.extrude(self.target, spec)

        self.assertEqual(["resolve_document"], self.runtime.calls)

    def test_inspect_returns_feature_and_body_query_without_mutation(self) -> None:
        snapshot = self.service.inspect(self.target)

        self.assertEqual(1, len(snapshot.features))
        self.assertEqual(1, len(snapshot.bodies))
        self.assertEqual(["resolve_document", "list_features", "list_bodies"], self.runtime.calls)


if __name__ == "__main__":
    unittest.main()
