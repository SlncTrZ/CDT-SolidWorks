from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from cdt_solidworks.part.models import CutSpec, FeatureKind, ProfileRef
from cdt_solidworks.part.native import NativePartBinding, PartNativeRuntime
from cdt_solidworks.part.runtime import DocumentTarget, RebuildResult


class FakeFeatureDefinition:
    def __init__(self, end_condition: int, depth_m: float) -> None:
        self.end_condition = end_condition
        self.depth_m = depth_m

    def GetEndCondition(self, forward: bool):
        assert forward is True
        return self.end_condition

    def GetDepth(self, forward: bool):
        assert forward is True
        return self.depth_m


class FakeFeature:
    def __init__(self, name: str, type_name: str, definition=None, underlying_type: str | None = None) -> None:
        self.Name = name
        self.type_name = type_name
        self.underlying_type = underlying_type or type_name
        self.definition = definition
        self.selected = False
        self.next_feature = None

    def Select2(self, append: bool, mark: int):
        assert append is False
        assert mark == 0
        self.selected = True
        return True

    def GetDefinition(self):
        return self.definition

    def GetTypeName2(self):
        return self.type_name

    def GetTypeName(self):
        return self.underlying_type

    def GetNextFeature(self):
        return self.next_feature


class FakeBody:
    def __init__(self, name: str, box_m: tuple[float, ...]) -> None:
        self.Name = name
        self.box_m = box_m

    def GetBodyBox(self):
        return self.box_m


class FakeFeatureManager:
    def __init__(self, model) -> None:
        self.model = model
        self.args = None

    def FeatureCut3(self, *args):
        self.args = args
        end_condition = int(args[3])
        depth_m = float(args[5])
        feature = FakeFeature(
            "Cut-Extrude1",
            "ICE",
            FakeFeatureDefinition(end_condition, depth_m),
            underlying_type="Cut",
        )
        self.model.features[feature.Name] = feature
        return feature


class FakeModel:
    def __init__(self) -> None:
        self.features = {"HoleSketch": FakeFeature("HoleSketch", "ProfileFeature")}
        self.FeatureManager = FakeFeatureManager(self)
        self.bodies = (FakeBody("Body1", (0.0, 0.0, 0.0, 0.1, 0.06, 0.02)),)
        self.cleared = False

    def FeatureByName(self, name: str):
        direct = self.features.get(name)
        if direct is not None:
            return direct
        return next((feature for feature in self.features.values() if feature.Name == name), None)

    def ClearSelection2(self, clear_all: bool):
        self.cleared = clear_all
        return True

    def GetBodies2(self, body_type: int, visible_only: bool):
        assert body_type == 0
        assert visible_only is False
        return self.bodies


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
    def __init__(self, app=object()):
        self.app = app
        self.calls = []

    def __call__(self, operation, *, stage, mutation):
        self.calls.append((stage, mutation))
        return operation(self.app)


@pytest.fixture
def native_runtime():
    model = FakeModel()
    api = FakeApi()
    executor = FakeExecutor()

    def binding_resolver(app, target):
        return NativePartBinding(
            model=model,
            document_id=target.document_id,
            revision=target.expected_revision,
            units=target.expected_units,
            configuration="Default",
        )

    runtime = PartNativeRuntime(
        executor=executor,
        binding_resolver=binding_resolver,
        member=api._member,
        feature_name=api.feature_name,
        feature_type=api.feature_type,
        bodies=api.bodies,
        body_name=api.body_name,
        rebuild_verifier=lambda model: RebuildResult(ok=True),
    )
    return runtime, model, executor


def test_cut_through_all_uses_featurecut3_and_native_readback(native_runtime):
    runtime, model, executor = native_runtime
    target = DocumentTarget("part.SLDPRT", expected_revision=7, expected_units="mm")
    document = runtime.resolve_document(target)
    receipt = runtime.create_cut(document, CutSpec("MountHoles", ProfileRef("HoleSketch"), through_all=True))
    feature = runtime.get_feature(document, receipt.object_id)
    bodies = runtime.list_bodies(document)

    assert receipt.object_id == "MountHoles"
    assert feature is not None
    assert feature.kind is FeatureKind.CUT
    assert feature.parameters == {"through_all": True}
    assert len(bodies) == 1
    assert bodies[0].bounds.max_x_mm == pytest.approx(100.0)
    args = model.FeatureManager.args
    assert args is not None
    assert args[2] is True  # cut follows the sketch normal for accepted standard-plane profiles
    assert args[3] == 1  # swEndCondThroughAll
    assert args[23] == 0  # swStartSketchPlane
    assert executor.calls[1] == ("part_cut_native", True)


def test_blind_cut_converts_depth_mm_to_meters_and_reads_it_back(native_runtime):
    runtime, model, _executor = native_runtime
    document = runtime.resolve_document(DocumentTarget("part.SLDPRT", 8, "mm"))
    receipt = runtime.create_cut(
        document,
        CutSpec("Pocket", ProfileRef("HoleSketch"), through_all=False, depth_mm=6.5),
    )
    feature = runtime.get_feature(document, receipt.object_id)
    assert feature is not None
    assert feature.parameters["through_all"] is False
    assert feature.parameters["depth_mm"] == pytest.approx(6.5)
    assert model.FeatureManager.args[3] == 0  # swEndCondBlind
    assert model.FeatureManager.args[5] == pytest.approx(0.0065)


def test_missing_profile_fails_before_featurecut_dispatch(native_runtime):
    runtime, model, _executor = native_runtime
    document = runtime.resolve_document(DocumentTarget("part.SLDPRT", 8, "mm"))
    with pytest.raises(RuntimeError, match="profile sketch"):
        runtime.create_cut(
            document,
            CutSpec("Pocket", ProfileRef("MissingSketch"), through_all=True),
        )
    assert model.FeatureManager.args is None
