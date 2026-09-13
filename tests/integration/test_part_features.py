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
