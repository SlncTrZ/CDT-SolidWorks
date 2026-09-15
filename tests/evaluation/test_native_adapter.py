import unittest
from pathlib import Path

from cdt_solidworks.evaluation.domain import EvaluationPostconditionError
from cdt_solidworks.evaluation.native import SolidWorksEvaluationAdapter
from cdt_solidworks.native.models import NativeCallResult


class FakePathPolicy:
    def validate_open(self, value):
        return str(value)


class FakeMassProperty:
    Mass = 2.5
    Volume = 0.001
    SurfaceArea = 0.06
    CenterOfMass = (0.01, 0.02, 0.03)

    def GetMomentOfInertia(self, where_taken):
        assert where_taken == 0
        return (0.1, 0.01, 0.02, 0.01, 0.2, 0.03, 0.02, 0.03, 0.3)


class FakeMeasure:
    def __init__(self):
        self.Distance = 0.025
        self.Angle = 1.5707963267948966
        self.Radius = -1.0
        self.Diameter = -1.0
        self.last_entities = None

    def Calculate(self):
        self.last_entities = None
        return True


class FakeExtension:
    def __init__(self):
        self.measure = FakeMeasure()

    def CreateMassProperty2(self):
        return FakeMassProperty()

    def CreateMeasure(self):
        return self.measure


class FakeBody:
    def GetBodyBox(self):
        return (0.0, 0.0, 0.0, 0.1, 0.2, 0.3)


class FakeFeature:
    def __init__(self, name, code=0, warning=False, next_feature=None):
        self.Name = name
        self.code = code
        self.warning = warning
        self.next_feature = next_feature

    def GetNextFeature(self):
        return self.next_feature


class FakeModel:
    def __init__(self):
        self.Extension = FakeExtension()
        self.title = "fixture.SLDPRT"
        self.path = r"C:\fixtures\fixture.SLDPRT"
        self.features = FakeFeature("Boss-Extrude1")
        self.clear_selection_calls = 0

    def GetType(self):
        return 1

    def GetPathName(self):
        return self.path

    def GetTitle(self):
        return self.title

    def GetBodies2(self, body_type, visible_only):
        if body_type == 0:
            return (FakeBody(),)
        return ()

    def FirstFeature(self):
        return self.features

    def ClearSelection2(self, clear_all):
        self.clear_selection_calls += 1
        return True


class FakeComponent:
    def __init__(self, name, *, suppressed=False, box=None):
        self.Name2 = name
        self.suppressed = suppressed
        self.box = box or (0.0, 0.0, 0.0, 0.1, 0.2, 0.3)
        self.get_box_calls = []

    def GetBox(self, include_ref_planes, include_sketches):
        self.get_box_calls.append((include_ref_planes, include_sketches))
        return self.box


class FakeInterference:
    def __init__(self, first, second, volume):
        self.Components = (first, second)
        self.Volume = volume


class FakeInterferenceManager:
    def __init__(self, rows):
        self.rows = tuple(rows)
        self.done = False
        self.TreatCoincidenceAsInterference = True
        self.IgnoreHiddenBodies = False
        self.TreatSubAssembliesAsComponents = True
        self.UseTransform = False

    def GetInterferences(self):
        return self.rows

    def Done(self):
        self.done = True


class FakeAssemblyModel(FakeModel):
    def __init__(self, rows=()):
        super().__init__()
        self.path = r"C:\\fixtures\\fixture.SLDASM"
        self.components = (
            FakeComponent("Plate-1"),
            FakeComponent("Bolt-1"),
        )
        self.InterferenceDetectionManager = FakeInterferenceManager(rows)

    def GetType(self):
        return 2

    def GetComponents(self, top_level_only):
        return self.components


class FakeApp:
    pass


class FakeApi:
    def __init__(self, model):
        self.model = model

    @staticmethod
    def _member(obj, name, *args):
        member = getattr(obj, name)
        if args:
            return member(*args)
        return member() if callable(member) else member

    def get_open_document(self, app, path):
        return self.model

    def open_document(self, *args, **kwargs):
        raise AssertionError("already-open fixture should not be reopened")

    def close_document(self, app, title):
        raise AssertionError("adapter must not close a caller-owned open document")

    def document_type(self, model):
        return model.GetType()

    def active_configuration(self, model):
        return "Default"

    def bodies(self, model, body_type, visible_only):
        value = model.GetBodies2(body_type, visible_only)
        return tuple(value or ())

    def components(self, model, top_level_only):
        value = model.GetComponents(top_level_only)
        return tuple(value or ())

    @staticmethod
    def component_name(component):
        return component.Name2

    @staticmethod
    def component_suppressed(component):
        return component.suppressed

    @staticmethod
    def dispatch_array(values):
        return tuple(values)

    def first_feature(self, model):
        return model.FirstFeature()

    def next_feature(self, feature):
        return feature.GetNextFeature()

    def feature_error(self, feature):
        return feature.code, feature.warning


class FakeSession:
    def __init__(self, api):
        self.api = api
        self.calls = []

    def execute(self, operation, *, stage, timeout, mutation):
        self.calls.append((stage, timeout, mutation))
        return NativeCallResult.success(operation(FakeApp()), call_id="call-1")


class SolidWorksEvaluationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.model = FakeModel()
        self.api = FakeApi(self.model)
        self.session = FakeSession(self.api)
        self.adapter = SolidWorksEvaluationAdapter(
            self.session,
            path_policy=FakePathPolicy(),
            default_timeout=5.0,
        )

    def test_mass_properties_use_imassproperty2_and_full_inertia_tensor(self):
        result = self.adapter.mass_properties(self.model.path, "Default")
        self.assertEqual(2.5, result.mass_kg)
        self.assertEqual((0.1, 0.2, 0.3, 0.01, 0.02, 0.03), result.inertia_kg_m2)
        self.assertEqual(("evaluation_mass_properties", 5.0, False), self.session.calls[-1])

    def test_bounding_box_is_explicitly_marked_approximate(self):
        result = self.adapter.bounding_box(self.model.path, "Default")
        self.assertEqual((0.0, 0.0, 0.0), result.min_m)
        self.assertEqual((0.1, 0.2, 0.3), result.max_m)
        self.assertTrue(result.approximate)

    def test_geometry_sanity_counts_feature_errors(self):
        self.model.features.code = 3
        result = self.adapter.geometry_sanity(self.model.path, "Default")
        self.assertEqual(1, result.solid_body_count)
        self.assertEqual(1, result.feature_error_count)

    def test_assembly_bounding_box_calls_component_get_box_with_explicit_flags(self):
        assembly = FakeAssemblyModel()
        assembly.components = (
            FakeComponent("Plate-1", box=(-0.1, -0.2, 0.0, 0.1, 0.2, 0.01)),
            FakeComponent("Bolt-1", box=(-0.01, -0.01, 0.0, 0.01, 0.01, 0.05)),
        )
        api = FakeApi(assembly)
        adapter = SolidWorksEvaluationAdapter(
            FakeSession(api), path_policy=FakePathPolicy(), default_timeout=5.0
        )

        result = adapter.bounding_box(assembly.path, "Default")

        self.assertEqual((-0.1, -0.2, 0.0), result.min_m)
        self.assertEqual((0.1, 0.2, 0.05), result.max_m)
        self.assertEqual([(False, False)], assembly.components[0].get_box_calls)
        self.assertEqual([(False, False)], assembly.components[1].get_box_calls)

    def test_measure_uses_explicit_bounded_selection_and_clears_it(self):
        selected = []
        self.adapter._select_measure_reference = (
            lambda model, reference, append: selected.append((reference, append))
        )
        result = self.adapter.measure(
            self.model.path, ("plane:front", "plane:right")
        )
        self.assertAlmostEqual(0.025, result.distance_m)
        self.assertAlmostEqual(1.5707963267948966, result.angle_rad)
        self.assertIsNone(result.radius_m)
        self.assertIsNone(result.diameter_m)
        self.assertIsNone(self.model.Extension.measure.last_entities)
        self.assertEqual(
            [("plane:front", False), ("plane:right", True)], selected
        )
        self.assertEqual(2, self.model.clear_selection_calls)

    def test_single_reference_measure_normalizes_radius_from_diameter(self):
        self.adapter._select_measure_reference = lambda model, reference, append: None
        self.model.Extension.measure.Distance = -1.0
        self.model.Extension.measure.Angle = -1.0
        self.model.Extension.measure.Radius = -1.0
        self.model.Extension.measure.Diameter = 0.010
        result = self.adapter.measure(
            self.model.path, ("sketch:Sketch1:segment:0",)
        )
        self.assertAlmostEqual(0.005, result.radius_m)
        self.assertAlmostEqual(0.010, result.diameter_m)

    def test_single_reference_measure_normalizes_diameter_from_radius(self):
        self.adapter._select_measure_reference = lambda model, reference, append: None
        self.model.Extension.measure.Distance = -1.0
        self.model.Extension.measure.Angle = -1.0
        self.model.Extension.measure.Radius = 0.005
        self.model.Extension.measure.Diameter = -1.0
        result = self.adapter.measure(
            self.model.path, ("sketch:Sketch1:segment:0",)
        )
        self.assertAlmostEqual(0.005, result.radius_m)
        self.assertAlmostEqual(0.010, result.diameter_m)

    def test_interference_manager_returns_component_pair_and_volume(self):
        plate = FakeComponent("Plate-1")
        bolt = FakeComponent("Bolt-1")
        assembly = FakeAssemblyModel((FakeInterference(plate, bolt, 1e-7),))
        api = FakeApi(assembly)
        adapter = SolidWorksEvaluationAdapter(
            FakeSession(api), path_policy=FakePathPolicy(), default_timeout=5.0
        )
        rows = adapter.interferences(assembly.path, "Default")
        self.assertEqual(1, len(rows))
        self.assertEqual(("Bolt-1", "Plate-1"), (rows[0].component_a, rows[0].component_b))
        self.assertAlmostEqual(1e-7, rows[0].volume_m3)
        self.assertTrue(assembly.InterferenceDetectionManager.done)

    def test_no_interference_is_clean_empty_result(self):
        assembly = FakeAssemblyModel(())
        api = FakeApi(assembly)
        adapter = SolidWorksEvaluationAdapter(
            FakeSession(api), path_policy=FakePathPolicy(), default_timeout=5.0
        )
        self.assertEqual((), adapter.interferences(assembly.path, "Default"))
        self.assertTrue(assembly.InterferenceDetectionManager.done)

    def test_configuration_mismatch_fails_closed(self):
        with self.assertRaisesRegex(EvaluationPostconditionError, "configuration_mismatch"):
            self.adapter.mass_properties(self.model.path, "Alt")

    def test_non_native_extension_is_refused_before_dispatch(self):
        with self.assertRaisesRegex(Exception, "unsupported_document_extension"):
            self.adapter.mass_properties(str(Path("C:/fixtures/fixture.step")))
        self.assertEqual([], self.session.calls)


if __name__ == "__main__":
    unittest.main()
