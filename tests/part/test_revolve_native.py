from __future__ import annotations

import math

import pytest

from cdt_solidworks.part.models import FeatureKind, ProfileRef, RevolveCutSpec, RevolveSpec
from cdt_solidworks.part.native import NativePartBinding, PartNativeRuntime
from cdt_solidworks.part.runtime import DocumentTarget, RebuildResult


class FakeAxis:
    ConstructionGeometry = True

    def __init__(self, segment_type: int = 0):
        self.segment_type = segment_type
        self.select_data = None

    def GetType(self):
        return self.segment_type

    def Select4(self, append, data):
        assert append is True
        self.select_data = data
        return True


class FakeProfileSketch:
    def __init__(self, segments):
        self.segments = tuple(segments)

    def GetSketchSegments(self):
        return self.segments


class FakeProfileFeature:
    Name = "RevolveProfile"

    def __init__(self, sketch):
        self.sketch = sketch
        self.selected = None

    def GetTypeName2(self):
        return "ProfileFeature"

    def GetSpecificFeature2(self):
        return self.sketch

    def Select2(self, append, mark):
        self.selected = (append, mark)
        return True


class FakeSelectData:
    Mark = 0


class FakeSelectionManager:
    def __init__(self):
        self.created = []

    def CreateSelectData(self):
        value = FakeSelectData()
        self.created.append(value)
        return value


class FakeRevolveDefinition:
    def __init__(self, *, axis, angle_rad, boss):
        self.Axis = axis
        self.angle_rad = angle_rad
        self.boss = boss
        self.accessed = False
        self.released = False
        self.component = object()

    def AccessSelections(self, model, component):
        self.accessed = True
        self.component = component
        return True

    def ReleaseSelectionAccess(self):
        self.released = True

    def GetRevolutionAngle(self, forward):
        assert forward is True
        return self.angle_rad

    def IsBossFeature(self):
        return self.boss


class FakeRevolveFeature:
    def __init__(self, name, type_name, definition, *, underlying=None):
        self.Name = name
        self.type_name = type_name
        self.underlying = underlying or type_name
        self.definition = definition

    def GetTypeName2(self):
        return self.type_name

    def GetTypeName(self):
        return self.underlying

    def GetDefinition(self):
        return self.definition


class FakeFeatureManager:
    def __init__(self, model):
        self.model = model
        self.args = None

    def FeatureRevolve2(self, *args):
        self.args = args
        is_cut = bool(args[3])
        feature = FakeRevolveFeature(
            "Cut-Revolve1" if is_cut else "Revolve1",
            "ICE",
            FakeRevolveDefinition(
                axis=self.model.axis,
                angle_rad=float(args[8]),
                boss=not is_cut,
            ),
            underlying="RevCut" if is_cut else "Revolution",
        )
        self.model.revolve = feature
        return feature


class FakeBody:
    Name = "Body1"

    def GetBodyBox(self):
        return (-0.01, -0.01, -0.01, 0.01, 0.01, 0.01)


class FakeModel:
    def __init__(self, axes):
        self.profile = FakeProfileFeature(FakeProfileSketch(axes))
        self.axis = axes[0] if axes else None
        self.SelectionManager = FakeSelectionManager()
        self.FeatureManager = FakeFeatureManager(self)
        self.revolve = None

    def FeatureByName(self, name):
        if name == self.profile.Name:
            return self.profile
        if self.revolve is not None and name == self.revolve.Name:
            return self.revolve
        return None

    def ClearSelection2(self, clear_all):
        return True

    def GetBodies2(self, body_type, visible_only):
        return (FakeBody(),)


class FakeApi:
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
        return tuple(model.GetBodies2(body_type, visible_only))

    @staticmethod
    def body_name(body):
        return body.Name


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def __call__(self, operation, *, stage, mutation):
        self.calls.append((stage, mutation))
        return operation(object())


def _runtime(axes):
    model = FakeModel(axes)
    executor = FakeExecutor()

    def resolver(app, target):
        return NativePartBinding(
            model=model,
            document_id=target.document_id,
            revision=target.expected_revision,
            units=target.expected_units,
            configuration="Default",
        )

    return (
        PartNativeRuntime(
            executor=executor,
            binding_resolver=resolver,
            member=FakeApi._member,
            feature_name=FakeApi.feature_name,
            feature_type=FakeApi.feature_type,
            bodies=FakeApi.bodies,
            body_name=FakeApi.body_name,
            rebuild_verifier=lambda model: RebuildResult(ok=True),
            null_dispatch=lambda: "NULL_DISPATCH",
        ),
        model,
        executor,
    )


def test_boss_revolve_uses_unique_construction_line_and_reads_feature_data():
    axis = FakeAxis()
    runtime, model, executor = _runtime((axis,))
    document = runtime.resolve_document(DocumentTarget("part.SLDPRT", 4, "mm"))
    receipt = runtime.create_revolve(
        document,
        RevolveSpec("Knob", ProfileRef("RevolveProfile"), "profile_centerline", 180.0),
    )
    feature = runtime.get_feature(document, receipt.object_id)

    assert receipt.object_id == "Knob"
    assert feature is not None and feature.kind is FeatureKind.REVOLVE
    assert feature.parameters["axis_ref"] == "profile_centerline"
    assert feature.parameters["angle_deg"] == pytest.approx(180.0)
    assert model.profile.selected == (False, 0)
    assert axis.select_data.Mark == 16
    args = model.FeatureManager.args
    assert args is not None
    assert args[0:6] == (True, True, False, False, False, False)
    assert args[6:8] == (0, 0)
    assert args[8] == pytest.approx(math.pi)
    assert args[17:20] == (True, False, True)
    assert model.revolve.definition.accessed is True
    assert model.revolve.definition.component == "NULL_DISPATCH"
    assert model.revolve.definition.released is True
    assert executor.calls[1] == ("part_revolve_native", True)


def test_revolve_cut_sets_cut_flag_and_reads_revcuts_kind():
    axis = FakeAxis()
    runtime, model, _executor = _runtime((axis,))
    document = runtime.resolve_document(DocumentTarget("part.SLDPRT", 4, "mm"))
    receipt = runtime.create_revolve_cut(
        document,
        RevolveCutSpec("Groove", ProfileRef("RevolveProfile"), "profile_centerline", 90.0),
    )
    feature = runtime.get_feature(document, receipt.object_id)

    assert feature is not None and feature.kind is FeatureKind.REVOLVE_CUT
    assert feature.parameters["angle_deg"] == pytest.approx(90.0)
    assert model.FeatureManager.args[3] is True
    assert model.FeatureManager.args[8] == pytest.approx(math.pi / 2.0)


def test_revolve_rejects_missing_multiple_or_non_line_construction_axis_before_native_dispatch():
    for axes, message in (
        ((), "exactly one construction centerline"),
        ((FakeAxis(), FakeAxis()), "exactly one construction centerline"),
        ((FakeAxis(segment_type=1),), "construction line"),
    ):
        runtime, model, _executor = _runtime(axes)
        document = runtime.resolve_document(DocumentTarget("part.SLDPRT", 4, "mm"))
        with pytest.raises(RuntimeError, match=message):
            runtime.create_revolve(
                document,
                RevolveSpec("Bad", ProfileRef("RevolveProfile"), "profile_centerline", 180.0),
            )
        assert model.FeatureManager.args is None
