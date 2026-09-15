from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.integration.part import (
    IntegratedPartFeatureService,
    _require_standard_reference_plane,
)
from cdt_solidworks.native.models import NativeCallResult, NativeCallState, NativeFailure


class _Transform:
    def __init__(self, values):
        self.ArrayData = tuple(values)


class _Plane:
    def __init__(self, values):
        self.Transform = _Transform(values)


class _Feature:
    def __init__(self, feature_type, *, specific=None, next_feature=None):
        self.feature_type = feature_type
        self.specific = specific
        self.next_feature = next_feature

    def GetSpecificFeature2(self):
        return self.specific

    def GetNextFeature(self):
        return self.next_feature


class _Sketch:
    def __init__(self, reference, entity_type):
        self.reference = reference
        self.entity_type = entity_type


class _Profile:
    def __init__(self, sketch):
        self.sketch = sketch

    def GetSpecificFeature2(self):
        return self.sketch


class _ContextApi:
    @staticmethod
    def _member(obj, name, *args):
        value = getattr(obj, name)
        return value(*args) if callable(value) else value

    @staticmethod
    def feature_type(feature):
        return feature.feature_type

    @staticmethod
    def first_feature(model):
        return model.first

    @staticmethod
    def next_feature(feature):
        return feature.GetNextFeature()

    @staticmethod
    def sketch_reference_entity(sketch):
        return sketch.reference, sketch.entity_type


def _plane_transform(seed: float):
    values = [0.0] * 16
    values[0] = seed
    values[5] = seed + 1.0
    values[10] = seed + 2.0
    values[15] = 1.0
    return tuple(values)


def _model_with_refplanes(*transforms):
    features = [_Feature("RefPlane", specific=_Plane(values)) for values in transforms]
    for current, next_feature in zip(features, features[1:]):
        current.next_feature = next_feature
    return SimpleNamespace(first=features[0] if features else None)


def test_cut_context_accepts_only_first_three_standard_reference_planes() -> None:
    transforms = tuple(_plane_transform(seed) for seed in (1.0, 10.0, 20.0, 30.0))
    model = _model_with_refplanes(*transforms)
    api = _ContextApi()

    accepted = _Profile(_Sketch(_Plane(transforms[1]), 4))
    _require_standard_reference_plane(api, model, accepted, "TopSketch")

    custom = _Profile(_Sketch(_Plane(transforms[3]), 4))
    with pytest.raises(RuntimeError, match="standard reference plane"):
        _require_standard_reference_plane(api, model, custom, "CustomPlaneSketch")


def test_cut_context_rejects_face_backed_sketch_before_feature_dispatch() -> None:
    model = _model_with_refplanes(*tuple(_plane_transform(seed) for seed in (1.0, 10.0, 20.0)))
    profile = _Profile(_Sketch(object(), 2))  # swSelFACES
    with pytest.raises(RuntimeError, match="face-backed"):
        _require_standard_reference_plane(_ContextApi(), model, profile, "FaceSketch")


def test_revolve_context_reports_revolve_stage() -> None:
    model = _model_with_refplanes(*tuple(_plane_transform(seed) for seed in (1.0, 10.0, 20.0)))
    profile = _Profile(_Sketch(object(), 2))
    with pytest.raises(Exception) as exc_info:
        _require_standard_reference_plane(
            _ContextApi(),
            model,
            profile,
            "RevolveSketch",
            "Revolve",
            "part_revolve_context",
        )
    assert getattr(exc_info.value, "stage", None) == "part_revolve_context"


class _NativeModel:
    LengthUnit = 0

    def __init__(self, path: str):
        self.path = path


class _TimeoutApi:
    def __init__(self, model):
        self.model = model

    def get_open_document(self, app, path):
        return self.model if path == self.model.path else None

    @staticmethod
    def document_type(model):
        return 1

    @staticmethod
    def document_path(model):
        return model.path

    @staticmethod
    def update_stamp(model):
        return 7

    @staticmethod
    def active_configuration(model):
        return "Default"

    @staticmethod
    def _member(obj, name, *args):
        value = getattr(obj, name)
        return value(*args) if callable(value) else value

    @staticmethod
    def feature_name(feature):
        return feature.Name

    @staticmethod
    def feature_type(feature):
        return feature.GetTypeName2()

    @staticmethod
    def bodies(model, body_type, visible_only):
        return ()

    @staticmethod
    def body_name(body):
        return ""

    @staticmethod
    def sketch_reference_entity(sketch):
        raise AssertionError("mutation operation must not run after injected timeout")


class _TimeoutSession:
    def __init__(self, model):
        self.api = _TimeoutApi(model)
        self.stages = []

    def execute(self, operation, *, stage, timeout, mutation, **recovery):
        self.stages.append((stage, mutation))
        if stage == "part_resolve_document":
            return NativeCallResult.success(operation(object()), call_id="resolve-1", dispatched=True)
        if stage == "part_cut_native":
            return NativeCallResult(
                state=NativeCallState.UNCERTAIN_AFTER_DISPATCH,
                call_id="cut-native-timeout-42",
                failure=NativeFailure(
                    code="uncertain_state",
                    stage=stage,
                    message="Native call timed out after dispatch.",
                    retryable=False,
                ),
                dispatched=True,
            )
        raise AssertionError(f"unexpected follow-up stage after uncertain mutation: {stage}")


def test_cut_timeout_preserves_uncertain_state_call_id_and_stops_follow_up(tmp_path: Path) -> None:
    part = tmp_path / "part.SLDPRT"
    part.write_bytes(b"fixture")
    model = _NativeModel(str(part.resolve()))
    session = _TimeoutSession(model)
    service = IntegratedPartFeatureService(
        session,
        path_policy=DocumentPathPolicy((tmp_path,)),
        default_timeout=1.0,
    )

    result = service.cut_extrude(
        path=str(part),
        expected_revision=7,
        sketch_id="CutSketch",
        name="Cut1",
        through_all=True,
    )

    assert result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
    assert result.call_id == "cut-native-timeout-42"
    assert result.dispatched is True
    assert result.failure is not None and result.failure.retryable is False
    assert session.stages == [
        ("part_resolve_document", False),
        ("part_cut_native", True),
    ]


def test_uncertain_cut_requires_feature_specific_reconcile_before_next_mutation(tmp_path: Path) -> None:
    import threading

    from cdt_solidworks.native.session import SolidWorksSession

    part = tmp_path / "part.SLDPRT"
    part.write_bytes(b"fixture")
    release_cut = threading.Event()
    cut_finished = threading.Event()
    standard = tuple(_plane_transform(seed) for seed in (1.0, 10.0, 20.0))

    class FeatureDefinition:
        def GetEndCondition(self, forward):
            return 1

        def GetDepth(self, forward):
            return 0.0

    class ProfileFeature:
        Name = "CutSketch"

        def __init__(self):
            self.sketch = _Sketch(_Plane(standard[0]), 4)

        def GetTypeName2(self):
            return "ProfileFeature"

        def GetSpecificFeature2(self):
            return self.sketch

        def Select2(self, append, mark):
            return True

    class CutFeature:
        Name = "AcceptedCut"

        def GetTypeName2(self):
            return "ICE"

        def GetTypeName(self):
            return "Cut"

        def GetDefinition(self):
            return FeatureDefinition()

        def GetNextFeature(self):
            return None

        def GetErrorCode2(self, *args):
            return 0

    class RefFeature(_Feature):
        Name = "Ref"

        def GetTypeName2(self):
            return "RefPlane"

        def GetErrorCode2(self, *args):
            return 0

    class Body:
        Name = "Body1"

        def GetBodyBox(self):
            return (0.0, 0.0, 0.0, 0.1, 0.06, 0.02)

    class FeatureManager:
        def __init__(self, model):
            self.model = model

        def FeatureCut3(self, *args):
            assert release_cut.wait(timeout=2.0)
            feature = CutFeature()
            self.model.cut = feature
            self.model.update_stamp = 8
            self.model.last_ref.next_feature = feature
            cut_finished.set()
            return feature

    class Model:
        LengthUnit = 0

        def __init__(self):
            self.path = str(part.resolve())
            self.update_stamp = 7
            refs = [RefFeature("RefPlane", specific=_Plane(values)) for values in standard]
            for current, next_feature in zip(refs, refs[1:]):
                current.next_feature = next_feature
            self.first = refs[0]
            self.last_ref = refs[-1]
            self.profile = ProfileFeature()
            self.cut = None
            self.FeatureManager = FeatureManager(self)

        def FeatureByName(self, name):
            if name == "CutSketch":
                return self.profile
            if self.cut is not None and name == self.cut.Name:
                return self.cut
            return None

        def ClearSelection2(self, clear_all):
            return True

        def ForceRebuild3(self, top_only):
            return True

        def GetBodies2(self, body_type, visible_only):
            return (Body(),)

    model = Model()

    class Api:
        def initialize_thread(self):
            return None

        def uninitialize_thread(self):
            return None

        def get_open_document(self, app, path):
            return model if path == model.path else None

        @staticmethod
        def document_type(doc):
            return 1

        @staticmethod
        def document_path(doc):
            return doc.path

        @staticmethod
        def document_title(doc):
            return "part.SLDPRT"

        @staticmethod
        def update_stamp(doc):
            return doc.update_stamp

        @staticmethod
        def active_configuration(doc):
            return "Default"

        @staticmethod
        def _member(obj, name, *args):
            value = getattr(obj, name)
            return value(*args) if callable(value) else value

        @staticmethod
        def feature_name(feature):
            return feature.Name

        @staticmethod
        def feature_type(feature):
            return feature.GetTypeName2()

        @staticmethod
        def feature_error(feature):
            return (0, False)

        @staticmethod
        def force_rebuild(doc, top_only):
            return bool(doc.ForceRebuild3(top_only))

        @staticmethod
        def first_feature(doc):
            return doc.first

        @staticmethod
        def next_feature(feature):
            return feature.GetNextFeature()

        @staticmethod
        def bodies(doc, body_type, visible_only):
            return tuple(doc.GetBodies2(body_type, visible_only))

        @staticmethod
        def body_name(body):
            return body.Name

        @staticmethod
        def sketch_reference_entity(sketch):
            return sketch.reference, sketch.entity_type

    session = SolidWorksSession(api=Api())
    session._application = model
    service = IntegratedPartFeatureService(
        session,
        path_policy=DocumentPathPolicy((tmp_path,)),
        default_timeout=0.05,
    )
    try:
        uncertain = service.cut_extrude(
            path=str(part),
            expected_revision=7,
            sketch_id="CutSketch",
            name="AcceptedCut",
            through_all=True,
        )
        assert uncertain.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
        assert uncertain.dispatched is True
        assert session.uncertain_call_id == uncertain.call_id

        blocked = session.execute(
            lambda app: True,
            stage="blocked_follow_up",
            timeout=0.2,
            mutation=True,
        )
        assert blocked.state is NativeCallState.FAILURE
        assert blocked.dispatched is False
        assert blocked.failure is not None and blocked.failure.code == "uncertain_state"

        release_cut.set()
        assert cut_finished.wait(timeout=1.0)

        mismatch = service.reconcile_cut(
            call_id=uncertain.call_id,
            path=str(part),
            name="WrongCut",
            through_all=True,
            timeout=1.0,
        )
        assert mismatch.state is NativeCallState.FAILURE
        assert mismatch.failure is not None
        assert mismatch.failure.code == "reconciliation_mismatch"
        assert session.uncertain_call_id == uncertain.call_id

        reconciled = service.reconcile_cut(
            call_id=uncertain.call_id,
            path=str(part),
            name="AcceptedCut",
            through_all=True,
            timeout=1.0,
        )
        assert reconciled.state is NativeCallState.SUCCESS
        assert reconciled.value is not None
        assert reconciled.value.kind.value == "cut"
        assert session.uncertain_call_id is None

        allowed = session.execute(
            lambda app: True,
            stage="post_reconcile_mutation",
            timeout=0.2,
            mutation=True,
        )
        assert allowed.state is NativeCallState.SUCCESS
    finally:
        release_cut.set()
        session.close_dispatcher(timeout=1.0)


class _RevolveAxis:
    ConstructionGeometry = True

    @staticmethod
    def GetType():
        return 0


class _RevolveDefinition:
    def __init__(self, *, angle_deg: float, is_boss: bool):
        self.Axis = _RevolveAxis()
        self._angle_rad = __import__('math').radians(angle_deg)
        self._is_boss = is_boss
        self.accessed = False
        self.released = False

    def AccessSelections(self, model, component):
        self.accessed = True
        return True

    def ReleaseSelectionAccess(self):
        self.released = True

    def GetRevolutionAngle(self, forward):
        assert forward is True
        return self._angle_rad

    def IsBossFeature(self):
        return self._is_boss


class _RevolveFeature:
    def __init__(self, name: str, feature_type: str, definition: _RevolveDefinition):
        self.Name = name
        self._feature_type = feature_type
        self._definition = definition

    def GetTypeName2(self):
        return self._feature_type

    def GetDefinition(self):
        return self._definition

    def GetNextFeature(self):
        return None

    def GetErrorCode2(self, *args):
        return 0


class _RevolveBody:
    Name = "Body1"


class _RevolveReconcileModel:
    LengthUnit = 0

    def __init__(self, path: str, feature: _RevolveFeature):
        self.path = path
        self.feature = feature

    def FeatureByName(self, name):
        return self.feature if name == self.feature.Name else None

    def ForceRebuild3(self, top_only):
        return True


class _RevolveReconcileApi:
    def __init__(self, model: _RevolveReconcileModel):
        self.model = model

    def get_open_document(self, app, path):
        return self.model if path == self.model.path else None

    @staticmethod
    def document_type(model):
        return 1

    @staticmethod
    def document_path(model):
        return model.path

    @staticmethod
    def feature_type(feature):
        return feature.GetTypeName2()

    @staticmethod
    def feature_name(feature):
        return feature.Name

    @staticmethod
    def bodies(model, body_type, visible_only):
        return (_RevolveBody(),)

    @staticmethod
    def force_rebuild(model, top_only):
        return model.ForceRebuild3(top_only)

    @staticmethod
    def first_feature(model):
        return model.feature

    @staticmethod
    def next_feature(feature):
        return feature.GetNextFeature()

    @staticmethod
    def feature_error(feature):
        return (0, False)

    @staticmethod
    def null_dispatch():
        return None

    @staticmethod
    def _member(obj, name, *args):
        value = getattr(obj, name)
        return value(*args) if callable(value) else value


class _RevolveReconcileSession:
    session_id = "revolve-reconcile-session"

    def __init__(self, api: _RevolveReconcileApi):
        self.api = api
        self.calls = []

    def reconcile(self, call_id, verifier, *, stage, timeout, identity):
        # This fake exercises verifier semantics only; real dispatcher identity gates
        # are covered in test_r0_dispatcher and test_r0_recovery.
        assert identity[0] == self.api.model.path
        from cdt_solidworks.native.errors import failure_from_exception

        self.calls.append((call_id, stage, timeout))
        try:
            value = verifier(object())
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=call_id,
                dispatched=True,
            )
        return NativeCallResult.success(value, call_id=call_id, dispatched=True)


@pytest.mark.parametrize(
    ("feature_type", "is_boss", "expected_kind", "expected_is_cut"),
    (
        ("Revolution", True, "revolve", False),
        ("RevCut", False, "revolve_cut", True),
    ),
)
def test_revolve_reconcile_verifies_exact_native_type_axis_angle_and_body(
    tmp_path: Path,
    feature_type: str,
    is_boss: bool,
    expected_kind: str,
    expected_is_cut: bool,
) -> None:
    part = tmp_path / "part.SLDPRT"
    part.write_bytes(b"fixture")
    definition = _RevolveDefinition(angle_deg=135.0, is_boss=is_boss)
    feature = _RevolveFeature("AcceptedRevolve", feature_type, definition)
    model = _RevolveReconcileModel(str(part.resolve()), feature)
    session = _RevolveReconcileSession(_RevolveReconcileApi(model))
    service = IntegratedPartFeatureService(
        session,
        path_policy=DocumentPathPolicy((tmp_path,)),
        service=object(),
        default_timeout=2.0,
    )

    result = service.reconcile_revolve(
        call_id="revolve-call-42",
        path=str(part),
        name="AcceptedRevolve",
        axis_ref="profile_centerline",
        angle_deg=135.0,
        is_cut=expected_is_cut,
    )

    assert result.state is NativeCallState.SUCCESS
    assert result.call_id == "revolve-call-42"
    assert result.value is not None
    assert result.value.kind.value == expected_kind
    assert result.value.parameters["axis_ref"] == "profile_centerline"
    assert result.value.parameters["angle_deg"] == pytest.approx(135.0)
    assert definition.accessed is True and definition.released is True
    assert session.calls == [("revolve-call-42", "part_revolve_reconcile", 2.0)]


@pytest.mark.parametrize(
    ("requested_angle", "requested_is_cut", "failure_fragment"),
    (
        (90.0, False, "angle"),
        (135.0, True, "boss/cut"),
    ),
)
def test_revolve_reconcile_rejects_mismatched_postcondition(
    tmp_path: Path,
    requested_angle: float,
    requested_is_cut: bool,
    failure_fragment: str,
) -> None:
    part = tmp_path / "part.SLDPRT"
    part.write_bytes(b"fixture")
    definition = _RevolveDefinition(angle_deg=135.0, is_boss=True)
    feature = _RevolveFeature("AcceptedRevolve", "Revolution", definition)
    model = _RevolveReconcileModel(str(part.resolve()), feature)
    session = _RevolveReconcileSession(_RevolveReconcileApi(model))
    service = IntegratedPartFeatureService(
        session,
        path_policy=DocumentPathPolicy((tmp_path,)),
        service=object(),
        default_timeout=2.0,
    )

    result = service.reconcile_revolve(
        call_id="revolve-call-43",
        path=str(part),
        name="AcceptedRevolve",
        axis_ref="profile_centerline",
        angle_deg=requested_angle,
        is_cut=requested_is_cut,
    )

    assert result.state is NativeCallState.FAILURE
    assert result.call_id == "revolve-call-43"
    assert result.failure is not None
    assert result.failure.code == "reconciliation_mismatch"
    assert failure_fragment.lower() in result.failure.message.lower()


class _HolePoint:
    def __init__(self, x_m: float, y_m: float):
        self.X = x_m
        self.Y = y_m
        self.Z = 0.0


class _HoleSketch:
    def __init__(self, x_m: float, y_m: float):
        self.point = _HolePoint(x_m, y_m)

    def GetSketchPoints2(self):
        return (self.point,)


class _HoleProfileFeature:
    Name = "HoleSketch"

    def __init__(self, x_m: float, y_m: float):
        self.sketch = _HoleSketch(x_m, y_m)

    def GetTypeName2(self):
        return "ProfileFeature"

    def GetSpecificFeature2(self):
        return self.sketch

    def GetNextSubFeature(self):
        return None


class _HoleDefinition:
    def __init__(self, *, diameter_mm: float, through_all: bool, depth_mm: float | None):
        self.Diameter = diameter_mm / 1000.0
        self.Type = 1 if through_all else 0
        self.Depth = 0.0 if depth_mm is None else depth_mm / 1000.0
        self.accessed = False
        self.released = False

    def AccessSelections(self, model, component):
        self.accessed = True
        return True

    def ReleaseSelectionAccess(self):
        self.released = True


class _HoleReconcileFeature:
    def __init__(
        self,
        name: str,
        *,
        diameter_mm: float,
        center_mm: tuple[float, float],
        through_all: bool,
        depth_mm: float | None,
        feature_type: str = "SketchHole",
    ):
        self.Name = name
        self.definition = _HoleDefinition(
            diameter_mm=diameter_mm,
            through_all=through_all,
            depth_mm=depth_mm,
        )
        self.profile = _HoleProfileFeature(
            center_mm[0] / 1000.0, center_mm[1] / 1000.0
        )
        self.feature_type = feature_type

    def GetTypeName2(self):
        return self.feature_type

    def GetDefinition(self):
        return self.definition

    def GetFirstSubFeature(self):
        return self.profile

    def GetNextFeature(self):
        return None


def _hole_reconcile_service(tmp_path: Path, feature: _HoleReconcileFeature):
    part = tmp_path / "part.SLDPRT"
    part.write_bytes(b"fixture")
    model = _RevolveReconcileModel(str(part.resolve()), feature)
    session = _RevolveReconcileSession(_RevolveReconcileApi(model))
    service = IntegratedPartFeatureService(
        session,
        path_policy=DocumentPathPolicy((tmp_path,)),
        service=object(),
        default_timeout=2.0,
    )
    return part, session, service


@pytest.mark.parametrize(
    ("through_all", "depth_mm"),
    ((False, 12.0), (True, None)),
)
def test_simple_hole_reconcile_verifies_type_diameter_center_end_condition_and_body(
    tmp_path: Path, through_all: bool, depth_mm: float | None
) -> None:
    feature = _HoleReconcileFeature(
        "AcceptedHole",
        diameter_mm=6.0,
        center_mm=(10.0, -5.0),
        through_all=through_all,
        depth_mm=depth_mm,
    )
    part, session, service = _hole_reconcile_service(tmp_path, feature)

    result = service.reconcile_simple_hole(
        call_id="hole-call-42",
        path=str(part),
        name="AcceptedHole",
        diameter_mm=6.0,
        face_ref="bbox:+z",
        center_mm=(10.0, -5.0),
        through_all=through_all,
        depth_mm=depth_mm,
    )

    assert result.state is NativeCallState.SUCCESS
    assert result.call_id == "hole-call-42"
    assert result.value is not None
    assert result.value.kind.value == "hole"
    assert result.value.parameters["center_x_mm"] == pytest.approx(10.0)
    assert result.value.parameters["center_y_mm"] == pytest.approx(-5.0)
    assert result.value.parameters["diameter_mm"] == pytest.approx(6.0)
    assert result.value.parameters["through_all"] is through_all
    if depth_mm is None:
        assert "depth_mm" not in result.value.parameters
    else:
        assert result.value.parameters["depth_mm"] == pytest.approx(depth_mm)
    assert feature.definition.accessed is True
    assert feature.definition.released is True
    assert session.calls == [("hole-call-42", "part_simple_hole_reconcile", 2.0)]


@pytest.mark.parametrize(
    ("requested_diameter", "requested_center", "requested_through", "requested_depth", "fragment"),
    (
        (7.0, (10.0, -5.0), False, 12.0, "diameter"),
        (6.0, (11.0, -5.0), False, 12.0, "center"),
        (6.0, (10.0, -5.0), True, None, "end condition"),
        (6.0, (10.0, -5.0), False, 10.0, "depth"),
    ),
)
def test_simple_hole_reconcile_rejects_mismatched_postcondition(
    tmp_path: Path,
    requested_diameter: float,
    requested_center: tuple[float, float],
    requested_through: bool,
    requested_depth: float | None,
    fragment: str,
) -> None:
    feature = _HoleReconcileFeature(
        "AcceptedHole",
        diameter_mm=6.0,
        center_mm=(10.0, -5.0),
        through_all=False,
        depth_mm=12.0,
    )
    part, _session, service = _hole_reconcile_service(tmp_path, feature)

    result = service.reconcile_simple_hole(
        call_id="hole-call-43",
        path=str(part),
        name="AcceptedHole",
        diameter_mm=requested_diameter,
        face_ref="bbox:+z",
        center_mm=requested_center,
        through_all=requested_through,
        depth_mm=requested_depth,
    )

    assert result.state is NativeCallState.FAILURE
    assert result.call_id == "hole-call-43"
    assert result.failure is not None
    assert result.failure.code == "reconciliation_mismatch"
    assert fragment in result.failure.message.lower()
