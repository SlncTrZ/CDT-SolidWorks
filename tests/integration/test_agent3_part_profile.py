from __future__ import annotations

from pathlib import Path

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.integration.part import IntegratedPartFeatureService
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.part.models import BodySnapshot, Bounds3D, FeatureKind, FeatureSnapshot, PartMutationResult


class ProfilePort:
    def __init__(self, *, closed: bool = True, ambiguous: bool = False) -> None:
        self.closed = closed
        self.ambiguous = ambiguous
        self.calls = []

    def inspect_profile(self, *, path: str, expected_revision: int, sketch_id: str):
        self.calls.append((path, expected_revision, sketch_id))
        return {"closed": self.closed, "ambiguous": self.ambiguous, "contour_count": 1 if self.closed else 0}


class Domain:
    def __init__(self) -> None:
        self.calls = []

    def extrude(self, target, spec, *, postconditions=None):
        self.calls.append(("extrude", target, spec))
        return PartMutationResult(
            FeatureSnapshot("Boss1", spec.name, FeatureKind.EXTRUDE, {"depth_mm": spec.depth_mm}),
            (BodySnapshot("Body1", Bounds3D(0, 0, 0, 20, 20, spec.depth_mm)),),
        )

    def cut(self, target, spec, *, postconditions=None):
        self.calls.append(("cut", target, spec))
        params = {"through_all": spec.through_all}
        if spec.depth_mm is not None:
            params["depth_mm"] = spec.depth_mm
        return PartMutationResult(
            FeatureSnapshot("Cut1", spec.name, FeatureKind.CUT, params),
            (BodySnapshot("Body1", Bounds3D(0, 0, 0, 20, 20, 5)),),
        )

    def get_feature_parameters(self, target, feature_id):
        self.calls.append(("get_parameters", target, feature_id))
        return {"depth_mm": 5.0}

    def set_feature_parameter(self, target, feature_id, parameter, value):
        self.calls.append(("set_parameter", target, feature_id, parameter, value))
        return FeatureSnapshot(feature_id, feature_id, FeatureKind.EXTRUDE, {parameter: value})


def _service(tmp_path: Path, port: ProfilePort | None):
    part = tmp_path / "part.SLDPRT"
    part.write_bytes(b"part")
    domain = Domain()
    service = IntegratedPartFeatureService(
        path_policy=DocumentPathPolicy((tmp_path,)),
        service=domain,
        profile_port=port,
    )
    return part, domain, service


def test_profile_extrude_and_cut_require_closed_unambiguous_profile(tmp_path: Path) -> None:
    part, domain, service = _service(tmp_path, ProfilePort())

    boss = service.profile_extrude(
        path=str(part), expected_revision=4, sketch_id="Profile1", name="Boss1", depth_mm=8.0
    )
    cut = service.profile_cut(
        path=str(part), expected_revision=5, sketch_id="Profile2", name="Cut1", through_all=True
    )

    assert boss.state is NativeCallState.SUCCESS
    assert cut.state is NativeCallState.SUCCESS
    assert [call[0] for call in domain.calls] == ["extrude", "cut"]


def test_open_or_ambiguous_profile_refuses_before_domain_dispatch(tmp_path: Path) -> None:
    for port in (ProfilePort(closed=False), ProfilePort(ambiguous=True)):
        part, domain, service = _service(tmp_path, port)
        result = service.profile_extrude(
            path=str(part), expected_revision=4, sketch_id="BadProfile", name="Boss1", depth_mm=8.0
        )
        assert result.state is NativeCallState.FAILURE
        assert result.dispatched is False
        assert domain.calls == []


def test_missing_profile_port_fails_closed(tmp_path: Path) -> None:
    part, domain, service = _service(tmp_path, None)
    result = service.profile_cut(
        path=str(part), expected_revision=4, sketch_id="Profile", name="Cut1", through_all=True
    )
    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert domain.calls == []


def test_parameter_wrappers_preserve_configuration_identity(tmp_path: Path) -> None:
    part, domain, service = _service(tmp_path, ProfilePort())
    got = service.feature_parameters_get(
        path=str(part), expected_revision=7, feature_id="Boss1", configuration="Config-A"
    )
    changed = service.feature_parameter_set(
        path=str(part), expected_revision=7, feature_id="Boss1", parameter="depth_mm", value=9.0, configuration="Config-A"
    )
    assert got.state is NativeCallState.SUCCESS
    assert changed.state is NativeCallState.SUCCESS
    assert domain.calls[0][1].expected_configuration == "Config-A"
    assert domain.calls[1][1].expected_configuration == "Config-A"
