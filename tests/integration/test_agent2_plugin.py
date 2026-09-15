from types import SimpleNamespace

from cdt_solidworks.integration.plugins import agent2_assembly_toolbox as plugin
from cdt_solidworks.native.models import NativeCallResult
from cdt_solidworks.toolbox.domain import ToolboxProbeSnapshot


class _Server:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self, *, name, description):
        assert description

        def decorator(function):
            self.tools[name] = function
            return function

        return decorator


class _Assembly:
    def __init__(self) -> None:
        self.bound = None

    def bind_topology_service(self, service, *, required=True):
        self.bound = (service, required)

    def insert_component(self, path, source_path, configuration=None, transform=None):
        return NativeCallResult.success(
            {"identity": "Bolt-1"}, call_id="assembly-insert", dispatched=True
        )

    def read_mate(self, path, mate_id):
        return NativeCallResult.success(
            {"identity": mate_id}, call_id="mate-get", dispatched=True
        )

    def delete_mate(self, path, mate_id):
        return NativeCallResult.success(None, call_id="mate-delete", dispatched=True)


class _Configuration:
    def query_state(self, *args, **kwargs):
        return NativeCallResult.success(
            {"name": args[1]}, call_id="config-query", dispatched=True
        )

    def set_component_suppressed(self, *args):
        return NativeCallResult.success(
            "suppressed", call_id="config-suppress", dispatched=True
        )

    def set_component_configuration(self, *args):
        return NativeCallResult.success(
            args[-1], call_id="config-component", dispatched=True
        )


class _Toolbox:
    assembly_service = None

    def probe(self):
        return NativeCallResult.success(
            ToolboxProbeSnapshot(
                available=True,
                addin_loaded=True,
                root_path="C:/Toolbox",
                database_path="C:/Toolbox/lang/english/swbrowser.sldedb",
                version="2026",
                license_state="available",
            ),
            call_id="toolbox-probe",
            dispatched=True,
        )

    def catalog_query(self, **kwargs):
        return NativeCallResult.success((), call_id="toolbox-query", dispatched=False)

    def resolve_component(self, **kwargs):
        return NativeCallResult.success(
            {"project_path": "C:/project/bolt.SLDPRT"},
            call_id="toolbox-resolve",
            dispatched=True,
        )

    def component_properties(self, path, configuration):
        return NativeCallResult.success(
            (("Part Number", "BOLT"),),
            call_id="toolbox-properties",
            dispatched=False,
        )

    def insert_component(self, *args, **kwargs):
        return NativeCallResult.success(
            {"identity": "Bolt-1"}, call_id="toolbox-insert", dispatched=True
        )


class _Topology:
    def resolve(self, document_id, reference):
        return object()


def _runtime():
    dependency = SimpleNamespace(name="solidworks", available=True, reason=None)
    return SimpleNamespace(
        assembly_service=_Assembly(),
        configuration_service=_Configuration(),
        toolbox_service=_Toolbox(),
        topology_service=_Topology(),
        runtime_context=lambda: SimpleNamespace(dependencies=(dependency,)),
    )


def test_plugin_contract_and_unique_agent2_tool_surface() -> None:
    assert plugin.PLUGIN_CONTRACT_VERSION == 1
    assert plugin.PLUGIN_ID == "agent2.assembly_toolbox"
    assert plugin.PLUGIN_ORDER == 2

    runtime = _runtime()
    server = _Server()
    plugin.register_tools(server, runtime)
    assert set(server.tools) == {
        "assembly_component_insert",
        "assembly_mate_get",
        "assembly_mate_delete",
        "configuration_state_query",
        "configuration_component_set_suppressed",
        "configuration_component_set_configuration",
        "toolbox_probe",
        "toolbox_catalog_query",
        "toolbox_component_resolve",
        "toolbox_component_properties",
        "toolbox_component_insert",
    }
    assert "assembly_mate_create" not in server.tools
    assert runtime.assembly_service.bound == (runtime.topology_service, True)
    assert runtime.toolbox_service.assembly_service is runtime.assembly_service


def test_plugin_tools_preserve_native_call_envelope() -> None:
    server = _Server()
    plugin.register_tools(server, _runtime())
    payload = server.tools["assembly_component_insert"](
        "C:/project/a.SLDASM", "C:/project/b.SLDPRT", "Default", None
    )
    assert payload["state"] == "success"
    assert payload["call_id"] == "assembly-insert"
    assert payload["dispatched"] is True
    assert payload["value"]["identity"] == "Bolt-1"


def test_capability_descriptors_are_strict_and_truthful() -> None:
    descriptors = plugin.capability_descriptors(_runtime())
    required = {"name", "implemented", "available", "reason", "backend", "dependencies"}
    assert descriptors
    assert all(set(item) == required for item in descriptors)
    by_name = {item["name"]: item for item in descriptors}
    assert by_name["solidworks.assembly.topology_mates"]["available"] is True
    assert by_name["solidworks.toolbox.catalog"]["available"] is True
    assert by_name["solidworks.toolbox.component"]["available"] is True
