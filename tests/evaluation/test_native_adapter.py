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


class FakeExtension:
    def CreateMassProperty2(self):
        return FakeMassProperty()


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
    Extension = FakeExtension()

    def __init__(self):
        self.title = "fixture.SLDPRT"
        self.path = r"C:\fixtures\fixture.SLDPRT"
        self.features = FakeFeature("Boss-Extrude1")

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

    def test_configuration_mismatch_fails_closed(self):
        with self.assertRaisesRegex(EvaluationPostconditionError, "configuration_mismatch"):
            self.adapter.mass_properties(self.model.path, "Alt")

    def test_non_native_extension_is_refused_before_dispatch(self):
        with self.assertRaisesRegex(Exception, "unsupported_document_extension"):
            self.adapter.mass_properties(str(Path("C:/fixtures/fixture.step")))
        self.assertEqual([], self.session.calls)


if __name__ == "__main__":
    unittest.main()
