from __future__ import annotations

from pathlib import Path

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.integration.part import IntegratedPartFeatureService
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.part.models import BodySnapshot, Bounds3D, FeatureKind, FeatureSnapshot, PartMutationResult


class _PartDomain:
    def __init__(self):
        self.calls = []

    def cut(self, target, spec, *, postconditions=None):
        self.calls.append((target, spec, postconditions))
        params = {"through_all": spec.through_all}
        if spec.depth_mm is not None:
            params["depth_mm"] = spec.depth_mm
        return PartMutationResult(
            FeatureSnapshot("Cut1", spec.name, FeatureKind.CUT, params),
            (BodySnapshot("Body1", Bounds3D(0, 0, 0, 100, 60, 20)),),
        )


def test_cut_wrapper_requires_open_part_path_revision_and_strict_cut_semantics(tmp_path: Path):
    part = tmp_path / "part.SLDPRT"; part.write_bytes(b"part")
    drawing = tmp_path / "drawing.SLDDRW"; drawing.write_bytes(b"drawing")
    domain = _PartDomain()
    service = IntegratedPartFeatureService(
        path_policy=DocumentPathPolicy((tmp_path,)),
        service=domain,
    )

    through = service.cut_extrude(
        path=str(part), expected_revision=4, sketch_id="HoleSketch", name="MountHoles", through_all=True
    )
    assert through.state is NativeCallState.SUCCESS
    assert domain.calls[-1][1].through_all is True

    blind = service.cut_extrude(
        path=str(part), expected_revision=5, sketch_id="PocketSketch", name="Pocket", through_all=False, depth_mm=6.0
    )
    assert blind.state is NativeCallState.SUCCESS
    assert domain.calls[-1][1].depth_mm == 6.0

    before = len(domain.calls)
    bad = service.cut_extrude(
        path=str(drawing), expected_revision=5, sketch_id="S", name="Bad", through_all=True
    )
    assert bad.state is NativeCallState.FAILURE and bad.dispatched is False
    ambiguous = service.cut_extrude(
        path=str(part), expected_revision=5, sketch_id="S", name="Bad", through_all=True, depth_mm=1.0
    )
    assert ambiguous.state is NativeCallState.FAILURE and ambiguous.dispatched is False
    assert len(domain.calls) == before


def test_revolve_wrappers_accept_only_profile_centerline_alias(tmp_path: Path) -> None:
    from cdt_solidworks.part.models import FeatureKind, FeatureSnapshot, PartMutationResult

    part = tmp_path / "part.SLDPRT"
    part.write_bytes(b"part")

    class Domain(_PartDomain):
        def revolve(self, target, spec, *, postconditions=None):
            self.calls.append(("revolve", target, spec))
            return PartMutationResult(
                FeatureSnapshot(
                    "Revolve1",
                    spec.name,
                    FeatureKind.REVOLVE,
                    {"angle_deg": spec.angle_deg, "axis_ref": spec.axis_ref},
                ),
                (BodySnapshot("Body1", Bounds3D(-10, -10, -10, 10, 10, 10)),),
            )

        def revolve_cut(self, target, spec, *, postconditions=None):
            self.calls.append(("revolve_cut", target, spec))
            return PartMutationResult(
                FeatureSnapshot(
                    "RevCut1",
                    spec.name,
                    FeatureKind.REVOLVE_CUT,
                    {"angle_deg": spec.angle_deg, "axis_ref": spec.axis_ref},
                ),
                (BodySnapshot("Body1", Bounds3D(-10, -10, -10, 10, 10, 10)),),
            )

    domain = Domain()
    service = IntegratedPartFeatureService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=domain
    )

    boss = service.revolve(
        path=str(part), expected_revision=4, sketch_id="Profile", name="Knob",
        axis_ref="profile_centerline", angle_deg=180.0,
    )
    cut = service.revolve_cut(
        path=str(part), expected_revision=5, sketch_id="Groove", name="Groove1",
        axis_ref="profile_centerline", angle_deg=90.0,
    )
    assert boss.state is NativeCallState.SUCCESS
    assert cut.state is NativeCallState.SUCCESS

    before = len(domain.calls)
    bad_axis = service.revolve(
        path=str(part), expected_revision=6, sketch_id="Profile", name="Bad",
        axis_ref="Axis1", angle_deg=180.0,
    )
    bad_angle = service.revolve_cut(
        path=str(part), expected_revision=6, sketch_id="Profile", name="Bad",
        axis_ref="profile_centerline", angle_deg=0.0,
    )
    assert bad_axis.state is NativeCallState.FAILURE and bad_axis.dispatched is False
    assert bad_angle.state is NativeCallState.FAILURE and bad_angle.dispatched is False
    assert len(domain.calls) == before
