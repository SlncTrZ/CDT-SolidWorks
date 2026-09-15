from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from types import SimpleNamespace

from cdt_solidworks.native.models import NativeCallResult
from cdt_solidworks.topology.models import TopologyQueryResult, TopologyResolution


_PLUGIN_PATH = Path(__file__).parents[2] / "src/cdt_solidworks/integration/plugins/agent1_topology_sketch.py"
_SPEC = importlib.util.spec_from_file_location("agent1_topology_sketch", _PLUGIN_PATH)
assert _SPEC is not None and _SPEC.loader is not None
plugin = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(plugin)


class FakeServer:
    def __init__(self):
        self.tools = {}

    def tool(self, *, name, description):
        def decorate(func):
            self.tools[name] = func
            return func
        return decorate


class FakeTopology:
    def query(self, context, *, kinds, component_id, timeout):
        return NativeCallResult.success(
            TopologyQueryResult(items=(), counts={"body": 0, "face": 0, "edge": 0, "vertex": 0}),
            call_id="query-1", dispatched=True,
        )

    def resolve(self, context, reference, *, expected_kind, component_id, timeout):
        return NativeCallResult.success(
            TopologyResolution("face", reference, None, component_id),
            call_id="resolve-1", dispatched=True,
        )

    def inspect(self, context, reference, *, expected_kind, component_id, timeout):
        return self.resolve(
            context, reference, expected_kind=expected_kind,
            component_id=component_id, timeout=timeout,
        )


def test_agent1_plugin_registers_only_three_explicit_schema_tools():
    server = FakeServer()
    runtime = SimpleNamespace(topology_service=FakeTopology())
    plugin.register_tools(server, runtime)
    assert set(server.tools) == {"topology_query", "topology_resolve", "topology_inspect"}
    for handler in server.tools.values():
        assert all(
            parameter.kind not in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
            for parameter in inspect.signature(handler).parameters.values()
        )

    payload = server.tools["topology_query"](
        "session", "/tmp/a.SLDPRT", "a.SLDPRT", "part", "Default", 1,
        ["face"], None, 5.0,
    )
    assert payload["state"] == "success"
    assert payload["call_id"] == "query-1"
    assert payload["error"] is None


def test_agent1_capability_descriptors_reflect_dependency_state():
    unavailable = plugin.capability_descriptors(SimpleNamespace())
    assert {item["name"] for item in unavailable} == {
        "topology.query", "topology.resolve", "topology.inspect"
    }
    assert all(item["implemented"] is True and item["available"] is False for item in unavailable)

    available = plugin.capability_descriptors(SimpleNamespace(topology_service=FakeTopology()))
    assert all(item["available"] is True for item in available)
    assert all(item["backend"] == "solidworks_native" for item in available)
