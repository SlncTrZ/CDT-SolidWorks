from __future__ import annotations

import pytest

from cdt_solidworks.part.models import FeatureKind, HoleSpec, HoleWizardSize, HoleWizardSpec
from cdt_solidworks.part.native import NativePartBinding, PartNativeRuntime
from cdt_solidworks.part.runtime import DocumentTarget, RebuildResult


class _Surface:
    def IsPlane(self):
        return True


class _Face:
    def __init__(self, normal=(0.0, 0.0, 1.0)):
        self.Normal = normal

    def GetSurface(self):
        return _Surface()


class _SelectionManager:
    def __init__(self):
        self.selected = None

    def GetSelectedObject6(self, index, mark):
        assert index == 1
        assert mark == -1
        return self.selected


class _Extension:
    def __init__(self, selection_manager):
        self.selection_manager = selection_manager
        self.rays = []
        self.face = _Face()

    def SelectByRay(self, *args):
        self.rays.append(args)
        self.selection_manager.selected = self.face
        return True


class _Body:
    Name = "Body1"

    def GetBodyBox(self):
        return (-0.05, -0.04, 0.0, 0.05, 0.04, 0.02)


class _SketchPoint:
    def __init__(self, x, y, z=0.0):
        self.X = x
        self.Y = y
        self.Z = z


class _Sketch:
    def __init__(self, x, y):
        self.point = _SketchPoint(x, y)

    def GetSketchPoints2(self):
        return (self.point,)


class _ProfileFeature:
    Name = "HoleSketch"

    def __init__(self, x, y):
        self.sketch = _Sketch(x, y)

    def GetTypeName2(self):
        return "ProfileFeature"

    def GetSpecificFeature2(self):
        return self.sketch

    def GetNextSubFeature(self):
        return None


class _SimpleHoleDefinition:
    def __init__(self, *, diameter_m: float, depth_m: float, end_type: int):
        self.Diameter = diameter_m
        self.Depth = depth_m
        self.Type = end_type
        self.accessed = False
        self.released = False

    def AccessSelections(self, model, component):
        self.accessed = True
        return True

    def ReleaseSelectionAccess(self):
        self.released = True


class _Feature:
    def __init__(self, definition, x, y, *, type_name="Hole"):
        self.Name = "Hole1"
        self.definition = definition
        self.subfeature = _ProfileFeature(x, y)
        self.type_name = type_name

    def GetTypeName2(self):
        return self.type_name

    def GetDefinition(self):
        return self.definition

    def GetFirstSubFeature(self):
        return self.subfeature

    def IsSuppressed2(self, config_opt, config_names):
        assert config_opt == 1
        return (False,)


class _WizardDefinition:
    _GEOMETRY = {
        "M2": (0.0044, 90.0, 0.0024),
        "M3": (0.0063, 90.0, 0.0034),
        "M4": (0.0094, 90.0, 0.0045),
        "M5": (0.0104, 90.0, 0.0055),
        "M6": (0.0126, 90.0, 0.0066),
    }

    def __init__(self):
        self.Standard = ""
        self.Standard2 = -1
        self.FastenerType = ""
        self.FastenerSize = ""
        self.EndCondition = -1
        self.CounterSinkDiameter = 0.0
        self.CounterSinkAngle = 0.0
        self.ThruHoleDiameter = 0.0
        self.HoleFit = -1
        self.points = ()
        self.initialized = None

    def InitializeHole(self, standard, hole_type, fastener, size, fit):
        self.initialized = (standard, hole_type, fastener, size, fit)
        self.Standard = "ANSI Metric"
        self.Standard2 = standard
        self.FastenerType = "Flat Head Screw - ANSI B18.6.7M"
        self.FastenerSize = size
        self.EndCondition = 1
        self.HoleFit = fit
        geometry = self._GEOMETRY[size]
        self.CounterSinkDiameter, self.CounterSinkAngle, self.ThruHoleDiameter = geometry

    def AccessSelections(self, model, component):
        return True

    def ReleaseSelectionAccess(self):
        return None

    def GetSketchPointCount(self):
        return len(self.points)

    def GetSketchPoints(self):
        return self.points


class _FeatureManager:
    def __init__(self, model):
        self.model = model
        self.args = None
        self.wizard_definition = None

    def SimpleHole2(self, *args):
        self.args = args
        definition = _SimpleHoleDefinition(
            diameter_m=float(args[0]),
            depth_m=float(args[6]),
            end_type=int(args[4]),
        )
        x, y = self.model.Extension.rays[-1][0:2]
        feature = _Feature(definition, x, y)
        self.model.feature = feature
        return feature

    def CreateDefinition(self, definition_type):
        assert definition_type == 25
        self.wizard_definition = _WizardDefinition()
        return self.wizard_definition

    def CreateFeature(self, definition):
        assert definition is self.wizard_definition
        x, y = self.model.Extension.rays[-1][0:2]
        definition.points = (_SketchPoint(x, y),)
        feature = _Feature(definition, x, y, type_name="HoleWzd")
        self.model.feature = feature
        return feature


class _Model:
    def __init__(self):
        self.SelectionManager = _SelectionManager()
        self.Extension = _Extension(self.SelectionManager)
        self.FeatureManager = _FeatureManager(self)
        self.feature = None
        self.body_count = 1

    def ClearSelection2(self, clear_all):
        return True

    def FeatureByName(self, name):
        if self.feature is not None and self.feature.Name == name:
            return self.feature
        return None

    def GetBodies2(self, body_type, visible_only):
        return tuple(_Body() for _ in range(self.body_count))


class _Api:
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


class _Executor:
    def __init__(self):
        self.calls = []

    def __call__(self, operation, *, stage, mutation):
        self.calls.append((stage, mutation))
        return operation(object())


def _runtime():
    model = _Model()
    executor = _Executor()

    def resolve(app, target):
        return NativePartBinding(
            model=model,
            document_id=target.document_id,
            revision=target.expected_revision,
            units=target.expected_units,
            configuration="Default",
        )

    runtime = PartNativeRuntime(
        executor=executor,
        binding_resolver=resolve,
        member=_Api._member,
        feature_name=_Api.feature_name,
        feature_type=_Api.feature_type,
        bodies=_Api.bodies,
        body_name=_Api.body_name,
        rebuild_verifier=lambda model: RebuildResult(ok=True),
        null_dispatch=lambda: None,
    )
    document = runtime.resolve_document(DocumentTarget("part.SLDPRT", 7, "mm"))
    return runtime, model, executor, document


def test_simple_hole_blind_ray_selects_plus_z_face_and_reads_definition():
    runtime, model, executor, document = _runtime()
    receipt = runtime.create_hole(
        document,
        HoleSpec(
            "AcceptedHole",
            diameter_mm=6.0,
            face_ref="bbox:+z",
            centers_mm=((10.0, -5.0),),
            through_all=False,
            depth_mm=12.0,
        ),
    )
    feature = runtime.get_feature(document, receipt.object_id)

    assert receipt.object_id == "AcceptedHole"
    assert feature is not None and feature.kind is FeatureKind.HOLE
    assert feature.parameters == {
        "diameter_mm": pytest.approx(6.0),
        "face_ref": "bbox:+z",
        "center_count": 1,
        "through_all": False,
        "depth_mm": pytest.approx(12.0),
        "center_x_mm": pytest.approx(10.0),
        "center_y_mm": pytest.approx(-5.0),
    }
    ray = model.Extension.rays[-1]
    assert ray[0:3] == pytest.approx((0.010, -0.005, 0.04))
    assert ray[3:6] == (0.0, 0.0, -1.0)
    assert ray[7:] == (2, False, 0, 0)
    args = model.FeatureManager.args
    assert args is not None and len(args) == 23
    assert args[0] == pytest.approx(0.006)
    assert args[1:4] == (True, False, False)
    assert args[4:8] == pytest.approx((0, 0, 0.012, 0.0))
    assert args[18:23] == (False, True, False, False, False)
    assert executor.calls[1] == ("part_hole_native", True)


@pytest.mark.parametrize(
    ("size", "countersink_mm", "through_mm"),
    (
        (HoleWizardSize.M2, 4.4, 2.4),
        (HoleWizardSize.M3, 6.3, 3.4),
        (HoleWizardSize.M4, 9.4, 4.5),
        (HoleWizardSize.M5, 10.4, 5.5),
        (HoleWizardSize.M6, 12.6, 6.6),
    ),
)
def test_hole_wizard_ansi_metric_countersink_uses_database_strings_and_geometry_readback(
    size,
    countersink_mm,
    through_mm,
):
    runtime, model, executor, document = _runtime()
    receipt = runtime.create_hole_wizard(
        document,
        HoleWizardSpec(
            f"CSK_{size.value}",
            size,
            face_ref="bbox:+z",
            center_mm=(10.0, -5.0),
        ),
    )
    feature = runtime.get_feature(document, receipt.object_id)

    assert receipt.object_id == f"CSK_{size.value}"
    assert feature is not None and feature.kind is FeatureKind.HOLE
    assert feature.parameters["wizard_standard"] == "ANSI Metric"
    assert feature.parameters["wizard_fastener"] == "Flat Head Screw - ANSI B18.6.7M"
    assert feature.parameters["wizard_size"] == size.value
    assert feature.parameters["through_all"] is True
    assert feature.parameters["counter_sink_diameter_mm"] == pytest.approx(countersink_mm)
    assert feature.parameters["counter_sink_angle_deg"] == pytest.approx(90.0)
    assert feature.parameters["thru_hole_diameter_mm"] == pytest.approx(through_mm)
    assert feature.parameters["center_x_mm"] == pytest.approx(10.0)
    assert feature.parameters["center_y_mm"] == pytest.approx(-5.0)
    assert model.FeatureManager.wizard_definition.initialized == (1, 1, 36, size.value, 1)
    assert executor.calls[1] == ("part_hole_wizard_native", True)


def test_simple_hole_through_all_uses_through_all_end_condition():
    runtime, model, _executor, document = _runtime()
    receipt = runtime.create_hole(
        document,
        HoleSpec(
            "ThroughHole",
            diameter_mm=5.0,
            face_ref="bbox:+z",
            centers_mm=((0.0, 0.0),),
            through_all=True,
        ),
    )
    feature = runtime.get_feature(document, receipt.object_id)
    assert feature is not None
    assert feature.parameters["through_all"] is True
    assert "depth_mm" not in feature.parameters
    assert model.FeatureManager.args[4] == 1


@pytest.mark.parametrize(
    ("spec", "message"),
    (
        (HoleSpec("Bad", 5.0, face_ref="Face1", centers_mm=((0.0, 0.0),), through_all=True), "bbox:\\+z"),
        (HoleSpec("Bad", 5.0, face_ref="bbox:+z", centers_mm=((0.0, 0.0), (1.0, 1.0)), through_all=True), "exactly one"),
        (HoleSpec("Bad", 5.0, face_ref="bbox:+z", centers_mm=((100.0, 0.0),), through_all=True), "outside"),
    ),
)
def test_simple_hole_rejects_unbounded_face_multi_center_or_outside_point_before_ray(spec, message):
    runtime, model, _executor, document = _runtime()
    with pytest.raises(RuntimeError, match=message):
        runtime.create_hole(document, spec)
    assert model.Extension.rays == []
    assert model.FeatureManager.args is None


def test_simple_hole_rejects_non_plus_z_face_before_dispatch():
    runtime, model, _executor, document = _runtime()
    model.Extension.face = _Face(normal=(0.0, 1.0, 0.0))
    with pytest.raises(RuntimeError, match=r"normal \+Z"):
        runtime.create_hole(
            document,
            HoleSpec(
                "BadNormal",
                diameter_mm=5.0,
                face_ref="bbox:+z",
                centers_mm=((0.0, 0.0),),
                through_all=True,
            ),
        )
    assert model.FeatureManager.args is None


def test_simple_hole_rejects_multibody_part_before_selection():
    runtime, model, _executor, document = _runtime()
    model.body_count = 2
    with pytest.raises(RuntimeError, match="exactly one solid body"):
        runtime.create_hole(
            document,
            HoleSpec(
                "BadMultiBody",
                diameter_mm=5.0,
                face_ref="bbox:+z",
                centers_mm=((0.0, 0.0),),
                through_all=True,
            ),
        )
    assert model.Extension.rays == []
    assert model.FeatureManager.args is None
