import unittest

from cdt_solidworks.native.rebuild import rebuild_document


class FakeFeature:
    def __init__(self, name: str, code: int = 0, warning: bool = False, next_feature=None) -> None:
        self.name = name
        self.code = code
        self.warning = warning
        self.next_feature = next_feature


class FakeModel:
    def __init__(self, rebuild_ok: bool, first_feature=None) -> None:
        self.rebuild_ok = rebuild_ok
        self.first_feature = first_feature
        self.rebuild_calls = []


class FakeApi:
    def force_rebuild(self, model, top_only: bool) -> bool:
        model.rebuild_calls.append(top_only)
        return model.rebuild_ok

    def first_feature(self, model):
        return model.first_feature

    def next_feature(self, feature):
        return feature.next_feature

    def feature_name(self, feature) -> str:
        return feature.name

    def feature_error(self, feature):
        return feature.code, feature.warning


class RebuildTests(unittest.TestCase):
    def test_false_force_rebuild_is_failure(self) -> None:
        model = FakeModel(False)
        result = rebuild_document(model, FakeApi())
        self.assertFalse(result.success)
        self.assertFalse(result.native_rebuild_ok)
        self.assertEqual([False], model.rebuild_calls)

    def test_feature_error_makes_rebuild_non_success_even_when_native_return_is_true(self) -> None:
        bad = FakeFeature("Boss-Extrude1", code=7)
        model = FakeModel(True, first_feature=bad)
        result = rebuild_document(model, FakeApi())
        self.assertFalse(result.success)
        self.assertTrue(result.native_rebuild_ok)
        self.assertEqual(1, len(result.feature_issues))
        self.assertEqual("Boss-Extrude1", result.feature_issues[0].feature_name)
        self.assertEqual(7, result.feature_issues[0].error_code)

    def test_warning_is_reported_but_does_not_fake_an_error(self) -> None:
        warning = FakeFeature("Sketch1", code=3, warning=True)
        model = FakeModel(True, first_feature=warning)
        result = rebuild_document(model, FakeApi())
        self.assertTrue(result.success)
        self.assertEqual(1, len(result.feature_issues))
        self.assertTrue(result.feature_issues[0].is_warning)


if __name__ == "__main__":
    unittest.main()
