import unittest

from cdt_solidworks.configuration.domain import (
    ConfigurationPostconditionError,
    ConfigurationService,
    RebuildReport,
)


class FakeConfigurationAdapter:
    def __init__(self):
        self.active = "Default"
        self.configs = {
            "Default": {
                "dimensions": {"D1@Sketch1": 0.01},
                "properties": {"Description": "Default part"},
                "components": {"Bracket-1": "resolved"},
            },
            "Alternate": {
                "dimensions": {"D1@Sketch1": 0.02},
                "properties": {"Description": "Alternate part"},
                "components": {"Bracket-1": "suppressed"},
            },
        }
        self.parents = {"Default": None, "Alternate": None}
        self.activation_readback_override = None

    def list_configurations(self, document_id):
        return tuple(self.configs)

    def create_configuration(self, document_id, name, parent):
        self.configs[name] = {
            "dimensions": {},
            "properties": {},
            "components": {},
        }
        self.parents[name] = parent

    def read_configuration_parent(self, document_id, name):
        return self.parents.get(name)

    def activate_configuration(self, document_id, name):
        self.active = name

    def read_active_configuration(self, document_id):
        return self.activation_readback_override or self.active

    def read_dimension(self, document_id, configuration, dimension_name):
        return self.configs[configuration]["dimensions"].get(dimension_name)

    def read_property(self, document_id, configuration, property_name):
        return self.configs[configuration]["properties"].get(property_name)

    def read_component_state(self, document_id, configuration, component_id):
        return self.configs[configuration]["components"].get(component_id)

    def rebuild_document(self, document_id):
        return RebuildReport(ok=True)


class ConfigurationServiceTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FakeConfigurationAdapter()
        self.service = ConfigurationService(self.adapter)

    def test_create_requires_list_readback(self):
        self.service.create("doc-1", "Machined", parent="Default")
        self.assertIn("Machined", self.service.list("doc-1"))

    def test_activate_requires_exact_readback(self):
        self.adapter.activation_readback_override = "Default"
        with self.assertRaisesRegex(
            ConfigurationPostconditionError, "activation_readback_mismatch"
        ):
            self.service.activate("doc-1", "Alternate")

    def test_query_reads_configuration_specific_state(self):
        default = self.service.query_state(
            "doc-1",
            "Default",
            dimensions=("D1@Sketch1",),
            properties=("Description",),
            component_ids=("Bracket-1",),
        )
        alternate = self.service.query_state(
            "doc-1",
            "Alternate",
            dimensions=("D1@Sketch1",),
            properties=("Description",),
            component_ids=("Bracket-1",),
        )
        self.assertNotEqual(default.dimensions, alternate.dimensions)
        self.assertNotEqual(default.component_states, alternate.component_states)

    def test_isolation_revisit_must_be_stable(self):
        first, second = self.service.verify_isolation(
            "doc-1",
            "Default",
            "Alternate",
            dimensions=("D1@Sketch1",),
            properties=("Description",),
            component_ids=("Bracket-1",),
        )
        self.assertEqual("Default", first.name)
        self.assertEqual("Alternate", second.name)


if __name__ == "__main__":
    unittest.main()
