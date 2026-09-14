import unittest

from cdt_solidworks.assembly.domain import (
    AssemblyRefusal,
    ComponentLoadState,
    MateKind,
    MateRequest,
    MateState,
)
from cdt_solidworks.assembly.native import AssemblyNativeAdapter
from cdt_solidworks.native.models import NativeCallResult


class FakeTransform:
    def __init__(self, values):
        self.ArrayData = values


class FakeComponent:
    def __init__(self, name="Bracket-1", path=r"C:\fixtures\bracket.SLDPRT", parent=None):
        self.Name2 = name
        self._path = path
        self.ReferencedConfiguration = "Default"
        self.Transform2 = FakeTransform((1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0))
        self._suppression = 3
        self._fixed = False
        self.last_suppression = None
        self.last_select_data = None
        self.parent = parent

    def GetPathName(self):
        return self._path

    def GetSuppression(self):
        return self._suppression

    def IsFixed(self):
        return self._fixed

    def GetParent(self):
        return self.parent

    def SetSuppression2(self, state):
        self.last_suppression = state
        self._suppression = state
        return 2

    def Select4(self, append, data, show_popup):
        self.last_select_data = data
        return data is not None


class FakeSelectData:
    pass


class FakePythonCom:
    VT_ARRAY = 0x2000
    VT_R8 = 5


class FakeClient:
    @staticmethod
    def VARIANT(kind, values):
        return (kind, tuple(values))


class FakeMathUtility:
    def __init__(self):
        self.last_values = None

    def CreateTransform(self, values):
        self.last_values = values
        return FakeTransform(tuple(values[1]))


class FakeApp:
    def __init__(self):
        self.math_utility = FakeMathUtility()

    def GetMathUtility(self):
        return self.math_utility


class FakeSelectionManager:
    def CreateSelectData(self):
        return FakeSelectData()


class FakeModel:
    def __init__(self):
        self.component = FakeComponent()
        self.SelectionManager = FakeSelectionManager()
        self.fixed_calls = 0
        self.unfixed_calls = 0
        self.delete_options = None

    def ClearSelection2(self, clear_all):
        return True

    def FixComponent(self):
        self.fixed_calls += 1
        self.component._fixed = True

    def UnfixComponent(self):
        self.unfixed_calls += 1
        self.component._fixed = False

    def DeleteSelections(self, options):
        self.delete_options = options
        return True

    def GetType(self):
        return 2

    def GetComponents(self, top_level_only):
        return (self.component,)


class FakeApi:
    def __init__(self, model):
        self.model = model
        self.requested_documents = []
        self._pythoncom = FakePythonCom()
        self._client = FakeClient()

    @staticmethod
    def _member(obj, name, *args):
        member = getattr(obj, name)
        if args:
            return member(*args)
        return member() if callable(member) else member

    def get_open_document(self, app, identity):
        self.requested_documents.append(identity)
        return self.model if identity == r"C:\fixtures\fixture.SLDASM" else None

    def document_type(self, model):
        return model.GetType()

    def components(self, model, top_level_only):
        return tuple(model.GetComponents(top_level_only))

    def component_name(self, component):
        return component.Name2

    def component_path(self, component):
        return component.GetPathName()

    @staticmethod
    def null_dispatch():
        return _NULL_DISPATCH

    @staticmethod
    def dispatch_array(values):
        return tuple(values)


_NULL_DISPATCH = object()


class FakeDistanceDefinition:
    def __init__(self):
        self.Distance = 0.02
        self.ErrorStatus = 1


class FakeMateSpecific:
    Type = 5

    def GetMateEntityCount(self):
        return 0


class FakeDistanceMateFeature:
    def __init__(self):
        self.definition = FakeDistanceDefinition()
        self.specific = FakeMateSpecific()
        self.modify_component = None
        self.Name = "Distance1"

    def GetDefinition(self):
        return self.definition

    def GetSpecificFeature2(self):
        return self.specific

    def ModifyDefinition(self, definition, top_doc, component):
        self.modify_component = component
        return True

    def IsSuppressed2(self, option, config_names):
        return False


class FakePatternData:
    def __init__(self):
        self.SeedComponentArray = ()
        self.D1Axis = None
        self.D1EndCondition = None
        self.D1Spacing = 0.0
        self.D1TotalInstances = 0
        self.D1ReverseDirection = False
        self.D2PatternSeedOnly = False


class FakePatternFeature:
    def __init__(self, data):
        self.Name = "LocalLPattern1"
        self.data = data

    def GetDefinition(self):
        return self.data


class FakeFeatureManager:
    def __init__(self):
        self.data = None
        self.feature = None

    def CreateDefinition(self, feature_id):
        assert feature_id == 108
        self.data = FakePatternData()
        return self.data

    def CreateFeature(self, data):
        self.feature = FakePatternFeature(data)
        return self.feature


class FakeSession:
    def __init__(self):
        self.model = FakeModel()
        self.api = FakeApi(self.model)
        self.app = FakeApp()
        self.calls = []

    def execute(self, operation, *, stage, timeout, mutation=False):
        self.calls.append((stage, mutation))
        return NativeCallResult.success(
            operation(self.app), call_id=f"call-{len(self.calls)}", dispatched=True
        )


class AssemblyNativeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession()
        self.adapter = AssemblyNativeAdapter(self.session)
        self.assembly_id = r"C:\fixtures\fixture.SLDASM"

    def test_component_query_resolves_explicit_document_identity(self):
        components = self.adapter.list_components(self.assembly_id, recursive=True)
        self.assertEqual(("Bracket-1",), tuple(item.identity for item in components))
        self.assertEqual([self.assembly_id], self.session.api.requested_documents)
        self.assertEqual(ComponentLoadState.RESOLVED, components[0].load_state)
        self.assertFalse(components[0].fixed)

    def test_component_fix_selection_uses_real_select_data(self):
        self.adapter.set_component_fixed(self.assembly_id, "Bracket-1", True)
        self.assertIsNotNone(self.session.model.component.last_select_data)
        self.assertEqual(1, self.session.model.fixed_calls)

    def test_component_delete_uses_explicit_zero_delete_options(self):
        self.adapter.delete_component(self.assembly_id, "Bracket-1")
        self.assertEqual(0, self.session.model.delete_options)

    def test_component_transform_marshals_explicit_double_safearray(self):
        values = (
            1.0, 0.0, 0.0, 0.0,
            1.0, 0.0, 0.0, 0.0,
            1.0, 0.1, 0.2, 0.3,
            1.0, 0.0, 0.0, 0.0,
        )
        self.adapter.set_component_transform(self.assembly_id, "Bracket-1", values)
        self.assertEqual(
            (FakePythonCom.VT_ARRAY | FakePythonCom.VT_R8, values),
            self.session.app.math_utility.last_values,
        )

    def test_component_suppression_maps_to_native_enum_and_checks_status(self):
        self.adapter.set_component_load_state(
            self.assembly_id, "Bracket-1", ComponentLoadState.SUPPRESSED
        )
        self.assertEqual(0, self.session.model.component.last_suppression)
        self.assertEqual(("assembly_component_state", True), self.session.calls[-1])

    def test_lightweight_and_resolved_native_enum_mapping(self):
        self.assertEqual(1, self.adapter._native_suppression(ComponentLoadState.LIGHTWEIGHT))
        self.assertEqual(3, self.adapter._native_suppression(ComponentLoadState.RESOLVED))
        self.assertEqual(ComponentLoadState.LIGHTWEIGHT, self.adapter._domain_suppression(4))
        self.assertEqual(ComponentLoadState.RESOLVED, self.adapter._domain_suppression(2))

    def test_selection_reference_parser_accepts_bounded_stable_identity_forms(self):
        self.assertEqual(
            (None, "plane", "front"),
            self.adapter._parse_selection_ref("assembly:plane:front"),
        )
        self.assertEqual(
            ("Bracket-1", "plane", "right"),
            self.adapter._parse_selection_ref("Bracket-1:plane:right"),
        )
        self.assertEqual(
            ("Bracket-1", "face", "bore"),
            self.adapter._parse_selection_ref("Bracket-1:face:bore"),
        )
        self.assertEqual(
            ("Bracket-1", "component", ""),
            self.adapter._parse_selection_ref("component:Bracket-1"),
        )
        with self.assertRaisesRegex(AssemblyRefusal, "unsupported_selection_reference"):
            self.adapter._parse_selection_ref("Bracket-1:face:")

    def test_component_snapshot_reports_parent_identity(self):
        parent = FakeComponent(name="SubAsm-1", path=r"C:\fixtures\sub.SLDASM")
        child = FakeComponent(name="Nested-1@SubAsm-1", parent=parent)
        snapshot = self.adapter._component_snapshot(child)
        self.assertEqual("SubAsm-1", snapshot.parent_identity)

    def test_mate_specific_data_is_configured_explicitly(self):
        class MateData:
            pass

        width = MateData()
        self.adapter._configure_mate_data(
            MateKind.WIDTH,
            width,
            ("w1", "w2", "t1", "t2"),
            MateRequest(
                MateKind.WIDTH,
                ("a", "b", "c", "d"),
                constraint="centered",
            ),
        )
        self.assertEqual(("w1", "w2"), width.WidthSelection)
        self.assertEqual(("t1", "t2"), width.TabSelection)
        self.assertEqual(0, width.ConstraintType)

        slot = MateData()
        self.adapter._configure_mate_data(
            MateKind.SLOT,
            slot,
            ("slot", "pin"),
            MateRequest(MateKind.SLOT, ("a", "b"), constraint="centered"),
        )
        self.assertEqual(("slot", "pin"), slot.EntitiesToMate)
        self.assertEqual(1, slot.Constraint)

        concentric = MateData()
        self.adapter._configure_mate_data(
            MateKind.CONCENTRIC,
            concentric,
            ("bore", "shaft"),
            MateRequest(MateKind.CONCENTRIC, ("a", "b")),
        )
        self.assertFalse(concentric.LockRotation)

    def test_mate_error_status_maps_overdefined_instead_of_dangling(self):
        feature = FakeDistanceMateFeature()
        feature.definition.ErrorStatus = 5
        self.session.api.feature_name = lambda value: value.Name
        self.session.api.feature_error = lambda value: (0, False)
        snapshot = self.adapter._mate_snapshot(feature)
        self.assertEqual(MateState.OVER_DEFINED, snapshot.state)
        self.assertEqual(5, snapshot.error_status)

    def test_linear_component_pattern_uses_native_feature_data_and_readback(self):
        manager = FakeFeatureManager()
        self.session.model.FeatureManager = manager
        seed = self.session.model.component
        self.adapter._assembly = lambda app, assembly_id: self.session.model
        self.adapter._component = lambda assembly, component_id: seed
        self.adapter._resolve_selection_reference = lambda assembly, ref: "named-edge"
        self.session.api.feature_name = lambda feature: feature.Name
        self.adapter._pattern_feature = lambda assembly, pattern_id: manager.feature

        pattern_id = self.adapter.create_linear_component_pattern(
            self.assembly_id,
            ("Bracket-1",),
            "Bracket-1:edge:pattern-axis",
            0.025,
            3,
        )
        self.assertEqual("LocalLPattern1", pattern_id)
        self.assertEqual((seed,), manager.data.SeedComponentArray)
        self.assertEqual("named-edge", manager.data.D1Axis)
        self.assertEqual(0, manager.data.D1EndCondition)
        self.assertEqual(0.025, manager.data.D1Spacing)
        self.assertEqual(3, manager.data.D1TotalInstances)
        self.assertTrue(manager.data.D2PatternSeedOnly)

        snapshot = self.adapter.read_component_pattern(
            self.assembly_id, "LocalLPattern1"
        )
        self.assertEqual(("Bracket-1",), snapshot.seed_component_ids)
        self.assertEqual(0.025, snapshot.spacing_m)
        self.assertEqual(3, snapshot.total_instances)
        self.assertTrue(snapshot.direction_resolved)

    def test_distance_mate_edit_uses_typed_null_dispatch_for_component(self):
        feature = FakeDistanceMateFeature()
        self.adapter._assembly = lambda app, assembly_id: self.session.model
        self.adapter._mate_feature = lambda assembly, mate_id: feature

        self.adapter.set_mate_value(self.assembly_id, "Distance1", 0.03)

        self.assertEqual(0.03, feature.definition.Distance)
        self.assertIs(_NULL_DISPATCH, feature.modify_component)

    def test_mate_type_mapping_is_explicit_and_no_dynamic_method_name(self):
        self.assertEqual(0, self.adapter._mate_type(MateKind.COINCIDENT))
        self.assertEqual(5, self.adapter._mate_type(MateKind.DISTANCE))
        self.assertEqual(21, self.adapter._mate_type(MateKind.SLOT))


if __name__ == "__main__":
    unittest.main()
