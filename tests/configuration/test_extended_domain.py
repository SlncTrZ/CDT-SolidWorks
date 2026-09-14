import unittest

from cdt_solidworks.configuration.domain import (
    ConfigurationPostconditionError,
    ConfigurationRefusal,
    ConfigurationService,
    EquationSnapshot,
    MaterialSnapshot,
    RebuildReport,
)


class ExtendedConfigurationAdapter:
    def __init__(self):
        self.active = "Default"
        self.configs = {
            "Default": {
                "dimensions": {"D1@Sketch1": 0.01},
                "properties": {"Description": "Default part"},
                "features": {"Boss-Extrude1": "resolved"},
                "components": {"Bracket-1": "resolved"},
                "component_configs": {"Bracket-1": "Default"},
            },
            "Alternate": {
                "dimensions": {"D1@Sketch1": 0.02},
                "properties": {"Description": "Alternate part"},
                "features": {"Boss-Extrude1": "suppressed"},
                "components": {"Bracket-1": "suppressed"},
                "component_configs": {"Bracket-1": "Machined"},
            },
        }
        self.document_properties = {"Project": "CDT"}
        self.parents = {"Default": None, "Alternate": None}
        self.materials = {
            ("doc-1", "Default"): MaterialSnapshot("SOLIDWORKS Materials", "Plain Carbon Steel"),
            ("doc-1", "Alternate"): None,
        }
        self.display_states = {
            "Default": ["Display State-1"],
            "Alternate": ["Display State-1"],
        }
        self.equations = [
            EquationSnapshot(
                identity='"WIDTH"',
                expression='"WIDTH" = 0.01',
                value=0.01,
                is_global_variable=True,
            )
        ]
        self.rebuild = RebuildReport(ok=True)

    def list_configurations(self, document_id):
        return tuple(self.configs)

    def create_configuration(self, document_id, name, parent):
        source = self.configs[parent] if parent is not None else {
            "dimensions": {},
            "properties": {},
            "features": {},
            "components": {},
            "component_configs": {},
        }
        self.configs[name] = {key: dict(value) for key, value in source.items()}
        self.parents[name] = parent
        self.display_states[name] = ["Display State-1"]

    def read_configuration_parent(self, document_id, name):
        return self.parents.get(name)

    def delete_configuration(self, document_id, name):
        del self.configs[name]
        self.parents.pop(name, None)
        self.display_states.pop(name, None)

    def rename_configuration(self, document_id, old_name, new_name):
        self.configs[new_name] = self.configs.pop(old_name)
        self.parents[new_name] = self.parents.pop(old_name, None)
        self.display_states[new_name] = self.display_states.pop(old_name, ["Display State-1"])
        for child, parent in tuple(self.parents.items()):
            if parent == old_name:
                self.parents[child] = new_name
        if self.active == old_name:
            self.active = new_name

    def activate_configuration(self, document_id, name):
        self.active = name

    def read_active_configuration(self, document_id):
        return self.active

    def read_dimension(self, document_id, configuration, dimension_name):
        return self.configs[configuration]["dimensions"].get(dimension_name)

    def set_dimension(self, document_id, configuration, dimension_name, value):
        self.configs[configuration]["dimensions"][dimension_name] = value

    def read_property(self, document_id, configuration, property_name):
        if configuration is None:
            return self.document_properties.get(property_name)
        return self.configs[configuration]["properties"].get(property_name)

    def set_property(self, document_id, configuration, property_name, value):
        target = self.document_properties if configuration is None else self.configs[configuration]["properties"]
        target[property_name] = value

    def delete_property(self, document_id, configuration, property_name):
        target = self.document_properties if configuration is None else self.configs[configuration]["properties"]
        target.pop(property_name, None)

    def read_feature_state(self, document_id, configuration, feature_id):
        return self.configs[configuration]["features"].get(feature_id)

    def set_feature_suppressed(self, document_id, configuration, feature_id, suppressed):
        self.configs[configuration]["features"][feature_id] = "suppressed" if suppressed else "resolved"

    def read_component_state(self, document_id, configuration, component_id):
        return self.configs[configuration]["components"].get(component_id)

    def set_component_suppressed(self, document_id, configuration, component_id, suppressed):
        self.configs[configuration]["components"][component_id] = "suppressed" if suppressed else "resolved"

    def read_component_configuration(self, document_id, configuration, component_id):
        return self.configs[configuration]["component_configs"].get(component_id)

    def set_component_configuration(self, document_id, configuration, component_id, referenced_configuration):
        self.configs[configuration]["component_configs"][component_id] = referenced_configuration

    def read_material(self, document_id, configuration):
        return self.materials.get((document_id, configuration))

    def set_material(self, document_id, configuration, database, material_name):
        self.materials[(document_id, configuration)] = MaterialSnapshot(database, material_name)

    def list_display_states(self, document_id, configuration):
        return tuple(self.display_states[configuration])

    def create_display_state(self, document_id, configuration, name):
        self.display_states[configuration].append(name)

    def rename_display_state(self, document_id, configuration, old_name, new_name):
        states = self.display_states[configuration]
        states[states.index(old_name)] = new_name

    def delete_display_state(self, document_id, configuration, name):
        self.display_states[configuration].remove(name)

    def list_equations(self, document_id):
        return tuple(self.equations)

    def add_equation(self, document_id, expression):
        identity = expression.split("=", 1)[0].strip()
        self.equations.append(
            EquationSnapshot(identity=identity, expression=expression, value=None, is_global_variable=identity.startswith('"'))
        )
        return identity

    def set_equation(self, document_id, identity, expression):
        for index, current in enumerate(self.equations):
            if current.identity == identity:
                self.equations[index] = EquationSnapshot(
                    identity=identity,
                    expression=expression,
                    value=current.value,
                    is_global_variable=current.is_global_variable,
                )
                return
        raise ConfigurationRefusal("missing_equation", identity)

    def delete_equation(self, document_id, identity):
        self.equations = [item for item in self.equations if item.identity != identity]

    def rebuild_document(self, document_id):
        return self.rebuild


class ExtendedConfigurationServiceTests(unittest.TestCase):
    def setUp(self):
        self.adapter = ExtendedConfigurationAdapter()
        self.service = ConfigurationService(self.adapter)

    def test_create_derived_configuration_requires_parent_readback(self):
        self.service.create("doc-1", "Machined", parent="Default")
        self.assertEqual("Default", self.adapter.read_configuration_parent("doc-1", "Machined"))

    def test_rename_configuration_requires_identity_readback(self):
        self.service.rename("doc-1", "Alternate", "Machined")
        self.assertIn("Machined", self.service.list("doc-1"))
        self.assertNotIn("Alternate", self.service.list("doc-1"))

    def test_delete_configuration_refuses_active_configuration(self):
        with self.assertRaisesRegex(ConfigurationRefusal, "cannot_delete_active_configuration"):
            self.service.delete("doc-1", "Default")

    def test_delete_configuration_requires_absence_readback(self):
        self.service.delete("doc-1", "Alternate")
        self.assertNotIn("Alternate", self.service.list("doc-1"))

    def test_set_configuration_dimension_rebuilds_and_reads_back(self):
        value = self.service.set_dimension("doc-1", "Alternate", "D1@Sketch1", 0.025)
        self.assertEqual(0.025, value)

    def test_nonfinite_dimension_is_refused_before_side_effect(self):
        with self.assertRaisesRegex(ConfigurationRefusal, "invalid_dimension_value"):
            self.service.set_dimension("doc-1", "Alternate", "D1@Sketch1", float("nan"))

    def test_document_and_configuration_properties_are_distinct(self):
        doc_value = self.service.set_property("doc-1", None, "Project", "M90")
        config_value = self.service.set_property("doc-1", "Alternate", "Description", "Machined")
        self.assertEqual("M90", doc_value)
        self.assertEqual("Machined", config_value)
        self.assertEqual("Default part", self.adapter.read_property("doc-1", "Default", "Description"))

    def test_delete_property_requires_absence_readback(self):
        self.service.delete_property("doc-1", "Alternate", "Description")
        self.assertIsNone(self.adapter.read_property("doc-1", "Alternate", "Description"))

    def test_feature_suppression_is_configuration_specific(self):
        state = self.service.set_feature_suppressed("doc-1", "Default", "Boss-Extrude1", True)
        self.assertEqual("suppressed", state)
        self.assertEqual("suppressed", self.adapter.read_feature_state("doc-1", "Alternate", "Boss-Extrude1"))

    def test_component_suppression_is_configuration_specific(self):
        state = self.service.set_component_suppressed("doc-1", "Default", "Bracket-1", True)
        self.assertEqual("suppressed", state)
        self.assertEqual("suppressed", self.adapter.read_component_state("doc-1", "Alternate", "Bracket-1"))

    def test_component_referenced_configuration_is_read_back(self):
        value = self.service.set_component_configuration("doc-1", "Default", "Bracket-1", "Machined")
        self.assertEqual("Machined", value)

    def test_material_assignment_is_configuration_specific_and_read_back(self):
        result = self.service.set_material(
            "doc-1", "Alternate", "SOLIDWORKS Materials", "AISI 304"
        )
        self.assertEqual(MaterialSnapshot("SOLIDWORKS Materials", "AISI 304"), result)
        self.assertEqual(
            MaterialSnapshot("SOLIDWORKS Materials", "Plain Carbon Steel"),
            self.adapter.read_material("doc-1", "Default"),
        )

    def test_material_database_path_matches_canonical_library_name(self):
        original = self.adapter.set_material

        def canonicalizing_set(document_id, configuration, database, material_name):
            original(document_id, configuration, database, material_name)
            self.adapter.materials[(document_id, configuration)] = MaterialSnapshot(
                "SOLIDWORKS Materials", material_name
            )

        self.adapter.set_material = canonicalizing_set
        result = self.service.set_material(
            "doc-1",
            "Alternate",
            r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\lang\english\sldmaterials\solidworks materials.sldmat",
            "Plain Carbon Steel",
        )
        self.assertEqual(
            MaterialSnapshot("SOLIDWORKS Materials", "Plain Carbon Steel"), result
        )

    def test_display_state_lifecycle_requires_exact_readback(self):
        self.assertEqual(("Display State-1",), self.service.list_display_states("doc-1", "Default"))
        self.service.create_display_state("doc-1", "Default", "Inspection")
        self.service.rename_display_state("doc-1", "Default", "Inspection", "Review")
        self.assertIn("Review", self.service.list_display_states("doc-1", "Default"))
        self.service.delete_display_state("doc-1", "Default", "Review")
        self.assertNotIn("Review", self.service.list_display_states("doc-1", "Default"))

    def test_display_state_delete_refuses_last_state(self):
        with self.assertRaisesRegex(ConfigurationRefusal, "cannot_delete_last_display_state"):
            self.service.delete_display_state("doc-1", "Default", "Display State-1")

    def test_equation_readback_tolerates_solidworks_assignment_spacing_normalization(self):
        original = self.adapter.set_equation

        def canonicalizing_set(document_id, identity, expression):
            original(document_id, identity, expression)
            for index, current in enumerate(self.adapter.equations):
                if current.identity == identity:
                    self.adapter.equations[index] = EquationSnapshot(
                        identity=current.identity,
                        expression=current.expression.replace(" = ", "= ", 1),
                        value=current.value,
                        is_global_variable=current.is_global_variable,
                    )
                    return

        self.adapter.set_equation = canonicalizing_set
        updated = self.service.set_equation(
            "doc-1", '"WIDTH"', '"WIDTH" = 0.02'
        )
        self.assertEqual('"WIDTH"= 0.02', updated.expression)

    def test_equation_add_edit_delete_has_stable_identity(self):
        added = self.service.add_equation("doc-1", '"HEIGHT" = "WIDTH" * 2')
        self.assertEqual('"HEIGHT"', added.identity)
        edited = self.service.set_equation("doc-1", '"HEIGHT"', '"HEIGHT" = "WIDTH" * 3')
        self.assertIn("* 3", edited.expression)
        self.service.delete_equation("doc-1", '"HEIGHT"')
        self.assertNotIn('"HEIGHT"', {item.identity for item in self.service.list_equations("doc-1")})

    def test_rebuild_failure_prevents_mutation_success(self):
        self.adapter.rebuild = RebuildReport(ok=False, errors=("equation failed",))
        with self.assertRaisesRegex(ConfigurationPostconditionError, "rebuild_failed"):
            self.service.set_property("doc-1", "Alternate", "Description", "Bad")


if __name__ == "__main__":
    unittest.main()
