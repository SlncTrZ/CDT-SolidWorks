from pathlib import Path

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.integration.assembly_config import IntegratedConfigurationService
from cdt_solidworks.native.models import NativeCallState


class _ConfigurationDomain:
    def __init__(self) -> None:
        self.calls = []

    def query_state(self, path, name, *, dimensions, properties, component_ids):
        self.calls.append(("query", path, name, dimensions, properties, component_ids))
        return {
            "name": name,
            "dimensions": tuple((key, 1.0) for key in dimensions),
            "properties": tuple((key, "value") for key in properties),
            "component_states": tuple((key, "resolved") for key in component_ids),
        }

    def set_component_suppressed(self, path, configuration, component_id, suppressed):
        self.calls.append(("suppress", path, configuration, component_id, suppressed))
        return "suppressed" if suppressed else "resolved"

    def set_component_configuration(
        self, path, configuration, component_id, referenced_configuration
    ):
        self.calls.append(
            (
                "component_configuration",
                path,
                configuration,
                component_id,
                referenced_configuration,
            )
        )
        return referenced_configuration


def test_configuration_component_context_wrappers_preserve_explicit_scope(tmp_path: Path) -> None:
    assembly = tmp_path / "fixture.SLDASM"
    assembly.write_bytes(b"asm")
    domain = _ConfigurationDomain()
    service = IntegratedConfigurationService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=domain
    )

    query = service.query_state(
        str(assembly),
        "Alternate",
        dimensions=("D1@Sketch1",),
        properties=("PartNo",),
        component_ids=("Bolt-1",),
    )
    assert query.state is NativeCallState.SUCCESS
    assert domain.calls[-1] == (
        "query",
        str(assembly.resolve()),
        "Alternate",
        ("D1@Sketch1",),
        ("PartNo",),
        ("Bolt-1",),
    )

    suppressed = service.set_component_suppressed(
        str(assembly), "Alternate", "Bolt-1", True
    )
    assert suppressed.state is NativeCallState.SUCCESS
    assert domain.calls[-1] == (
        "suppress",
        str(assembly.resolve()),
        "Alternate",
        "Bolt-1",
        True,
    )

    referenced = service.set_component_configuration(
        str(assembly), "Alternate", "Bolt-1", "Machined"
    )
    assert referenced.state is NativeCallState.SUCCESS
    assert domain.calls[-1] == (
        "component_configuration",
        str(assembly.resolve()),
        "Alternate",
        "Bolt-1",
        "Machined",
    )
