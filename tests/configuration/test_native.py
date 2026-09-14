import unittest

from cdt_solidworks.configuration.domain import MaterialSnapshot
from cdt_solidworks.configuration.native import ConfigurationNativeAdapter
from cdt_solidworks.native.models import NativeCallResult


class FakeDimension:
    def __init__(self):
        self.values = {"Default": 0.01, "Alternate": 0.02}
        self.set_calls = []

    def GetSystemValue3(self, option, config_names):
        name = tuple(config_names)[0]
        return (self.values[name],)

    def SetSystemValue3(self, value, option, config_names):
        name = tuple(config_names)[0]
        self.values[name] = value
        self.set_calls.append((value, option, tuple(config_names)))
        return 0


class FakeConfiguration:
    def __init__(self, name, *, parent=None):
        self.Name = name
        self.parent = parent
        self.display_states = ["Display State-1"]

    def IsDerived(self):
        return self.parent is not None

    def GetParent(self):
        return self.parent

    def GetDisplayStates(self):
        return tuple(self.display_states)

    def CreateDisplayState(self, name):
        self.display_states.append(name)
        return True

    def RenameDisplayState(self, old_name, new_name):
        self.display_states[self.display_states.index(old_name)] = new_name
        return True

    def DeleteDisplayState(self, name):
        self.display_states.remove(name)
        return True


class FakeConfigurationManager:
    def __init__(self, model):
        self.model = model
        self.ActiveConfiguration = model.configurations["Default"]
        self.add_calls = []

    def AddConfiguration2(self, name, comment, alternate_name, options, parent, description, rebuild):
        self.add_calls.append((name, parent, rebuild))
        self.model.config_names.append(name)
        parent_config = self.model.configurations.get(parent) if parent else None
        created = FakeConfiguration(name, parent=parent_config)
        self.model.configurations[name] = created
        self.ActiveConfiguration = created
        return created


class FakeEquationManager:
    def __init__(self):
        self.items = ['"WIDTH" = 0.01']
        self.set_calls = []

    def GetCount(self):
        return len(self.items)

    def Equation(self, index):
        return self.items[index]

    def Value(self, index):
        return 0.01

    def GlobalVariable(self, index):
        return True

    def Disabled(self, index):
        return False

    def Add2(self, index, expression, solve):
        insertion = len(self.items) if index < 0 else index
        self.items.insert(insertion, expression)
        return insertion

    def Delete(self, index):
        self.items.pop(index)
        return True

    def SetEquationAndConfigurationOption(self, index, expression, option, config_names):
        self.items[index] = expression
        self.set_calls.append((index, expression, option, config_names))
        return index

    def EvaluateAll(self):
        return -1


class FakeModel:
    def __init__(self):
        self.config_names = ["Default", "Alternate"]
        self.configurations = {
            "Default": FakeConfiguration("Default"),
            "Alternate": FakeConfiguration("Alternate"),
        }
        self.ConfigurationManager = FakeConfigurationManager(self)
        self.dimension = FakeDimension()
        self.equations = FakeEquationManager()
        self.materials = {
            "Default": ("solidworks materials.sldmat", "Plain Carbon Steel"),
            "Alternate": ("", ""),
        }
        self.show_configuration_calls = []

    def ShowConfiguration2(self, name):
        self.show_configuration_calls.append(name)
        self.ConfigurationManager.ActiveConfiguration = self.configurations[name]
        return True

    def GetType(self):
        return 1

    def GetConfigurationNames(self):
        return tuple(self.config_names)

    def GetConfigurationByName(self, name):
        return self.configurations.get(name)

    def SetMaterialPropertyName2(self, configuration, database, material_name):
        self.materials[configuration] = (database, material_name)

    def GetMaterialPropertyName2(self, configuration):
        material_database, material_name = self.materials[configuration]
        return material_name, material_database

    def Parameter(self, name):
        return self.dimension if name == "D1@Sketch1" else None

    def GetEquationMgr(self):
        return self.equations


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
        return self.model if identity == r"C:\fixtures\fixture.SLDPRT" else None

    @staticmethod
    def string_array(values):
        return tuple(values)


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


class ConfigurationNativeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession()
        self.adapter = ConfigurationNativeAdapter(self.session)
        self.document_id = r"C:\fixtures\fixture.SLDPRT"

    def test_list_configuration_uses_explicit_document_identity(self):
        names = self.adapter.list_configurations(self.document_id)
        self.assertEqual(("Default", "Alternate"), names)
        self.assertEqual([self.document_id], self.session.api.requested_documents)

    def test_create_configuration_uses_parent_and_rebuild_flag(self):
        self.adapter.create_configuration(self.document_id, "Machined", "Default")
        self.assertEqual(
            [("Machined", "Default", True)],
            self.session.model.ConfigurationManager.add_calls,
        )
        self.assertEqual(("configuration_create", True), self.session.calls[-1])

    def test_create_configuration_restores_previous_active_configuration(self):
        self.adapter.create_configuration(self.document_id, "Machined", "Default")
        self.assertEqual("Default", self.session.model.ConfigurationManager.ActiveConfiguration.Name)
        self.assertEqual(["Default"], self.session.model.show_configuration_calls)

    def test_dimension_set_is_configuration_specific_and_system_units(self):
        self.adapter.set_dimension(self.document_id, "Alternate", "D1@Sketch1", 0.025)
        self.assertEqual(
            [(0.025, 3, ("Alternate",))],
            self.session.model.dimension.set_calls,
        )
        self.assertEqual(
            0.025,
            self.adapter.read_dimension(self.document_id, "Alternate", "D1@Sketch1"),
        )

    def test_equation_list_uses_stable_left_hand_identity(self):
        self.session.model.equations.items[0] = '"WIDTH"= 0.01'
        equations = self.adapter.list_equations(self.document_id)
        self.assertEqual('"WIDTH"', equations[0].identity)
        self.assertEqual('"WIDTH" = 0.01', equations[0].expression)
        self.assertTrue(equations[0].is_global_variable)

    def test_equation_evaluate_all_minus_one_is_not_treated_as_failure(self):
        identity = self.adapter.add_equation(self.document_id, '"HEIGHT" = 0.02')
        self.assertEqual('"HEIGHT"', identity)
        self.assertEqual('"HEIGHT" = 0.02', self.session.model.equations.items[-1])

    def test_equation_edit_is_in_place_and_does_not_delete_first(self):
        self.adapter.set_equation(self.document_id, '"WIDTH"', '"WIDTH" = 0.02')
        self.assertEqual(['"WIDTH" = 0.02'], self.session.model.equations.items)
        self.assertEqual(
            [(0, '"WIDTH" = 0.02', 2, None)],
            self.session.model.equations.set_calls,
        )

    def test_derived_configuration_parent_is_read_back(self):
        self.adapter.create_configuration(self.document_id, "Machined", "Default")
        self.assertEqual("Default", self.adapter.read_configuration_parent(self.document_id, "Machined"))

    def test_material_assignment_and_readback_are_configuration_specific(self):
        self.adapter.set_material(
            self.document_id, "Alternate", "solidworks materials.sldmat", "Alloy Steel"
        )
        self.assertEqual(
            MaterialSnapshot("solidworks materials.sldmat", "Alloy Steel"),
            self.adapter.read_material(self.document_id, "Alternate"),
        )
        self.assertEqual(
            MaterialSnapshot("solidworks materials.sldmat", "Plain Carbon Steel"),
            self.adapter.read_material(self.document_id, "Default"),
        )

    def test_display_state_lifecycle_uses_configuration_object(self):
        self.assertEqual(
            ("Display State-1",),
            self.adapter.list_display_states(self.document_id, "Default"),
        )
        self.adapter.create_display_state(self.document_id, "Default", "Inspection")
        self.adapter.rename_display_state(
            self.document_id, "Default", "Inspection", "Review"
        )
        self.assertIn(
            "Review", self.adapter.list_display_states(self.document_id, "Default")
        )
        self.adapter.delete_display_state(self.document_id, "Default", "Review")
        self.assertNotIn(
            "Review", self.adapter.list_display_states(self.document_id, "Default")
        )

    def test_activate_current_configuration_is_idempotent(self):
        self.adapter.activate_configuration(self.document_id, "Default")
        self.assertEqual([], self.session.model.show_configuration_calls)

    def test_activate_other_configuration_uses_show_configuration(self):
        self.adapter.activate_configuration(self.document_id, "Alternate")
        self.assertEqual(["Alternate"], self.session.model.show_configuration_calls)


if __name__ == "__main__":
    unittest.main()
