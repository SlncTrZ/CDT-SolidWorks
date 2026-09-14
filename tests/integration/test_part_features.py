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


def test_simple_hole_wrapper_is_bounded_to_single_bbox_plus_z_center(tmp_path: Path) -> None:
    from cdt_solidworks.part.models import FeatureKind, FeatureSnapshot, PartMutationResult

    part = tmp_path / "part.SLDPRT"
    part.write_bytes(b"part")

    class Domain(_PartDomain):
        def hole(self, target, spec, *, postconditions=None):
            self.calls.append(("hole", target, spec))
            parameters = {
                "diameter_mm": spec.diameter_mm,
                "face_ref": spec.face_ref,
                "center_count": len(spec.centers_mm),
                "through_all": spec.through_all,
            }
            if spec.depth_mm is not None:
                parameters["depth_mm"] = spec.depth_mm
            parameters["center_x_mm"] = spec.centers_mm[0][0]
            parameters["center_y_mm"] = spec.centers_mm[0][1]
            return PartMutationResult(
                FeatureSnapshot("Hole1", spec.name, FeatureKind.HOLE, parameters),
                (BodySnapshot("Body1", Bounds3D(-50, -40, 0, 50, 40, 20)),),
            )

    domain = Domain()
    service = IntegratedPartFeatureService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=domain
    )

    blind = service.simple_hole(
        path=str(part), expected_revision=7, name="Hole1",
        diameter_mm=6.0, face_ref="bbox:+z", center_mm=(10.0, -5.0),
        through_all=False, depth_mm=12.0,
    )
    through = service.simple_hole(
        path=str(part), expected_revision=8, name="Hole2",
        diameter_mm=5.0, face_ref="bbox:+z", center_mm=(0.0, 0.0),
        through_all=True,
    )
    assert blind.state is NativeCallState.SUCCESS
    assert through.state is NativeCallState.SUCCESS

    before = len(domain.calls)
    bad_face = service.simple_hole(
        path=str(part), expected_revision=9, name="Bad",
        diameter_mm=5.0, face_ref="Face1", center_mm=(0.0, 0.0),
        through_all=True,
    )
    bad_depth = service.simple_hole(
        path=str(part), expected_revision=9, name="Bad2",
        diameter_mm=5.0, face_ref="bbox:+z", center_mm=(0.0, 0.0),
        through_all=False,
    )
    assert bad_face.state is NativeCallState.FAILURE and bad_face.dispatched is False
    assert bad_depth.state is NativeCallState.FAILURE and bad_depth.dispatched is False
    assert len(domain.calls) == before

    class WrongCenterDomain(Domain):
        def hole(self, target, spec, *, postconditions=None):
            result = super().hole(target, spec, postconditions=postconditions)
            params = dict(result.feature.parameters)
            params["center_x_mm"] = params["center_x_mm"] + 1.0
            return PartMutationResult(
                FeatureSnapshot(
                    result.feature.feature_id,
                    result.feature.name,
                    result.feature.kind,
                    params,
                ),
                result.bodies,
            )

    wrong = IntegratedPartFeatureService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=WrongCenterDomain()
    ).simple_hole(
        path=str(part), expected_revision=9, name="WrongCenter",
        diameter_mm=5.0, face_ref="bbox:+z", center_mm=(0.0, 0.0),
        through_all=True,
    )
    assert wrong.state is NativeCallState.FAILURE
    assert wrong.dispatched is True


class _ExpandedPartDomain:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def _mutation(self, method: str, target, spec, kind: FeatureKind) -> PartMutationResult:
        self.calls.append((method, target, spec))
        return PartMutationResult(
            FeatureSnapshot(spec.name, spec.name, kind, {}),
            (BodySnapshot("Body1", Bounds3D(0, 0, 0, 100, 60, 20)),),
        )

    def hole_wizard(self, target, spec):
        return self._mutation("hole_wizard", target, spec, FeatureKind.HOLE)

    def fillet(self, target, spec):
        return self._mutation("fillet", target, spec, FeatureKind.FILLET)

    def chamfer(self, target, spec):
        return self._mutation("chamfer", target, spec, FeatureKind.CHAMFER)

    def shell(self, target, spec):
        return self._mutation("shell", target, spec, FeatureKind.SHELL)

    def draft(self, target, spec):
        return self._mutation("draft", target, spec, FeatureKind.DRAFT)

    def rib(self, target, spec):
        return self._mutation("rib", target, spec, FeatureKind.RIB)

    def linear_pattern(self, target, spec):
        return self._mutation("linear_pattern", target, spec, FeatureKind.LINEAR_PATTERN)

    def circular_pattern(self, target, spec):
        return self._mutation("circular_pattern", target, spec, FeatureKind.CIRCULAR_PATTERN)

    def mirror(self, target, spec):
        return self._mutation("mirror", target, spec, FeatureKind.MIRROR)

    def reference_plane(self, target, spec):
        return self._mutation("reference_plane", target, spec, FeatureKind.REFERENCE_PLANE)

    def reference_axis(self, target, spec):
        return self._mutation("reference_axis", target, spec, FeatureKind.REFERENCE_AXIS)

    def reference_point(self, target, spec):
        return self._mutation("reference_point", target, spec, FeatureKind.REFERENCE_POINT)

    def get_feature(self, target, feature_id):
        self.calls.append(("get_feature", target, feature_id))
        return FeatureSnapshot(feature_id, feature_id, FeatureKind.FILLET, {"radius_mm": 3.0})

    def rename_feature(self, target, feature_id, new_name):
        self.calls.append(("rename_feature", target, feature_id, new_name))
        return FeatureSnapshot(new_name, new_name, FeatureKind.FILLET, {"radius_mm": 3.0})

    def set_feature_suppressed(self, target, feature_id, suppressed):
        self.calls.append(("set_feature_suppressed", target, feature_id, suppressed))
        return FeatureSnapshot(feature_id, feature_id, FeatureKind.FILLET, {}, suppressed=suppressed)

    def set_feature_parameter(self, target, feature_id, parameter, value):
        self.calls.append(("set_feature_parameter", target, feature_id, parameter, value))
        return FeatureSnapshot(feature_id, feature_id, FeatureKind.FILLET, {parameter: value})


def test_part_integration_promotes_agent_a_native_passed_p0_surface(tmp_path: Path) -> None:
    part = tmp_path / "part.SLDPRT"
    part.write_bytes(b"part")
    domain = _ExpandedPartDomain()
    service = IntegratedPartFeatureService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=domain
    )

    results = (
        service.hole_wizard(
            path=str(part), expected_revision=10, name="HW1", size="M4",
            face_ref="bbox:+z", center_mm=(0.0, 0.0),
        ),
        service.fillet(
            path=str(part), expected_revision=10, name="Fillet1",
            edge_refs=("bbox:edge:+x:+z",), radius_mm=3.0,
        ),
        service.chamfer(
            path=str(part), expected_revision=10, name="Chamfer1",
            edge_refs=("bbox:edge:-x:+z",), distance_mm=2.0, angle_deg=45.0,
        ),
        service.shell(
            path=str(part), expected_revision=10, name="Shell1",
            face_refs=("bbox:+z",), thickness_mm=2.0,
        ),
        service.draft(
            path=str(part), expected_revision=10, name="Draft1",
            face_refs=("bbox:+x",), neutral_plane_ref="bbox:+z", angle_deg=3.0,
        ),
        service.rib(
            path=str(part), expected_revision=10, name="Rib1",
            sketch_id="RibSketch", thickness_mm=2.5,
        ),
        service.linear_pattern(
            path=str(part), expected_revision=10, name="Linear1",
            seed_feature_ids=("Fillet1",), direction_ref="bbox:edge:+y:+z",
            count=3, spacing_mm=12.0,
        ),
        service.circular_pattern(
            path=str(part), expected_revision=10, name="Circular1",
            seed_feature_ids=("Fillet1",), axis_ref="feature:AxisZ",
            count=4, angle_deg=360.0,
        ),
        service.mirror(
            path=str(part), expected_revision=10, name="Mirror1",
            seed_feature_ids=("Fillet1",), mirror_ref="plane:right",
        ),
        service.reference_plane(
            path=str(part), expected_revision=10, name="OffsetPlane",
            reference="plane:front", offset_mm=10.0,
        ),
        service.reference_axis(
            path=str(part), expected_revision=10, name="AxisZ",
            first_ref="plane:top", second_ref="plane:right",
        ),
        service.reference_point(
            path=str(part), expected_revision=10, name="TopCenter", reference="bbox:+z",
        ),
        service.get_feature(path=str(part), expected_revision=10, feature_id="Fillet1"),
        service.rename_feature(
            path=str(part), expected_revision=10, feature_id="Fillet1", new_name="FilletManaged"
        ),
        service.set_feature_suppressed(
            path=str(part), expected_revision=10, feature_id="FilletManaged", suppressed=True
        ),
        service.set_feature_parameter(
            path=str(part), expected_revision=10, feature_id="FilletManaged",
            parameter="radius_mm", value=4.0,
        ),
    )

    assert all(result.state is NativeCallState.SUCCESS for result in results)
    assert [call[0] for call in domain.calls] == [
        "hole_wizard", "fillet", "chamfer", "shell", "draft", "rib",
        "linear_pattern", "circular_pattern", "mirror", "reference_plane",
        "reference_axis", "reference_point", "get_feature", "rename_feature",
        "set_feature_suppressed", "set_feature_parameter",
    ]


def test_part_integration_rejects_unbounded_a_surface_inputs_before_domain_call(tmp_path: Path) -> None:
    part = tmp_path / "part.SLDPRT"
    part.write_bytes(b"part")
    domain = _ExpandedPartDomain()
    service = IntegratedPartFeatureService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=domain
    )

    bad_edge = service.fillet(
        path=str(part), expected_revision=1, name="BadFillet",
        edge_refs=("Edge1",), radius_mm=3.0,
    )
    bad_size = service.hole_wizard(
        path=str(part), expected_revision=1, name="BadHole", size="M12",
        face_ref="bbox:+z", center_mm=(0.0, 0.0),
    )
    unproven_size = service.hole_wizard(
        path=str(part), expected_revision=1, name="UnprovenHole", size="M3",
        face_ref="bbox:+z", center_mm=(0.0, 0.0),
    )
    unproven_option = service.fillet(
        path=str(part), expected_revision=1, name="UnprovenFillet",
        edge_refs=("bbox:edge:+x:+z",), radius_mm=3.0, tangent_propagation=True,
    )
    bad_parameter = service.set_feature_parameter(
        path=str(part), expected_revision=1, feature_id="Fillet1",
        parameter="raw_definition", value=4.0,
    )

    for result in (bad_edge, bad_size, unproven_size, unproven_option, bad_parameter):
        assert result.state is NativeCallState.FAILURE
        assert result.dispatched is False
        assert result.failure is not None
        assert result.failure.code == "cad_validation_error"
    assert domain.calls == []
