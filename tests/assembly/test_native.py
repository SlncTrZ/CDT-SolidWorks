import unittest

from cdt_solidworks.assembly.domain import AssemblyRefusal, ComponentLoadState, MateKind
from cdt_solidworks.assembly.native import AssemblyNativeAdapter
from cdt_solidworks.native.models import NativeCallResult


class FakeTransform:
    def __init__(self, values):
        self.ArrayData = values


class FakeComponent:
    def __init__(self, name="Bracket-1", path=r"C:\fixtures\bracket.SLDPRT"):
        self.Name2 = name
        self._path = path
        self.ReferencedConfiguration = "Default"
        self.Transform2 = FakeTransform((1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0))
        self._suppression = 3
        self._fixed = False
        self.last_suppression = None
        self.last_select_data = None

    def GetPathName(self):
        return self._path

    def GetSuppression(self):
        return self._suppression

    def IsFixed(self):
        return self._fixed

    def SetSuppression2(self, state):
        self.last_suppression = state
        self._suppression = state
        return 2

    def Select4(self, append, data, show_popup):
        self.last_select_data = data
        return data is not None


class FakeSelectData:
    pass


class FakeSelectionManager:
    def CreateSelectData(self):
        return FakeSelectData()


class FakeModel:
    def __init__(self):
        self.component = FakeComponent()
        self.SelectionManager = FakeSelectionManager()
        self.fixed_calls = 0
        self.unfixed_calls = 0

    def ClearSelection2(self, clear_all):
        return True

    def FixComponent(self):
        self.fixed_calls += 1
        self.component._fixed = True

    def UnfixComponent(self):
        self.unfixed_calls += 1
        self.component._fixed = False

    def GetType(self):
        return 2

    def GetComponents(self, top_level_only):
        return (self.component,)


class FakeApi:
    def __init__(self, model):
        self.model = model
        self.requested_documents = []

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


_NULL_DISPATCH = object()


class FakeDistanceDefinition:
    def __init__(self):
        self.Distance = 0.02


class FakeMateSpecific:
    Type = 5


class FakeDistanceMateFeature:
    def __init__(self):
        self.definition = FakeDistanceDefinition()
        self.specific = FakeMateSpecific()
        self.modify_component = None

    def GetDefinition(self):
        return self.definition

    def GetSpecificFeature2(self):
        return self.specific

    def ModifyDefinition(self, definition, top_doc, component):
        self.modify_component = component
        return True


class FakeSession:
    def __init__(self):
        self.model = FakeModel()
        self.api = FakeApi(self.model)
        self.calls = []

    def execute(self, operation, *, stage, timeout, mutation=False):
        self.calls.append((stage, mutation))
        return NativeCallResult.success(
            operation(object()), call_id=f"call-{len(self.calls)}", dispatched=True
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

    def test_selection_reference_parser_is_bounded_to_standard_planes(self):
        self.assertEqual((None, "front"), self.adapter._parse_selection_ref("assembly:plane:front"))
        self.assertEqual(("Bracket-1", "right"), self.adapter._parse_selection_ref("Bracket-1:plane:right"))
        with self.assertRaisesRegex(AssemblyRefusal, "unsupported_selection_reference"):
            self.adapter._parse_selection_ref("Bracket-1:face:Face1")

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
