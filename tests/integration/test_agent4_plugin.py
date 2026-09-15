from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from cdt_solidworks.native.models import NativeCallResult


_PLUGIN = (
    Path(__file__).parents[2]
    / "src"
    / "cdt_solidworks"
    / "integration"
    / "plugins"
    / "agent4_drawing_interop.py"
)


def _load_plugin():
    spec = importlib.util.spec_from_file_location("agent4_drawing_interop_test", _PLUGIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Server:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self, *, name: str, description: str):
        def decorator(func):
            self.tools[name] = (description, func)
            return func

        return decorator


class _Drawing:
    dimension_available = True
    bom_available = False

    def _success(self, value):
        return NativeCallResult.success(value, call_id="agent4-call", dispatched=True)

    def create_dimension(self, *args):
        return self._success({"kind": "dimension", "args": args})

    def list_dimensions(self, *args):
        return self._success(({"kind": "dimension"},))

    def create_bom(self, *args):
        return self._success({"kind": "bom", "args": args})

    def read_bom(self, *args):
        return self._success({"kind": "bom", "args": args})

    def update(self, *args):
        return self._success({"kind": "update", "args": args})


def test_agent4_plugin_is_importable_by_agent6_discovery_path() -> None:
    module = importlib.import_module(
        "cdt_solidworks.integration.plugins.agent4_drawing_interop"
    )
    assert module.PLUGIN_CONTRACT_VERSION == 1
    assert module.PLUGIN_ORDER == 4


def test_agent4_plugin_registers_only_lane_public_additions_with_provider_envelope() -> None:
    plugin = _load_plugin()
    server = _Server()
    runtime = SimpleNamespace(drawing_service=_Drawing())

    plugin.register_tools(server, runtime)

    assert plugin.PLUGIN_CONTRACT_VERSION == 1
    assert plugin.PLUGIN_ID == "agent4.drawing_interop"
    assert plugin.PLUGIN_ORDER == 4
    assert set(server.tools) == {
        "drawing_dimension_create",
        "drawing_dimensions_list",
        "drawing_bom_create",
        "drawing_bom_read",
        "drawing_update",
    }

    payload = server.tools["drawing_update"][1]("drawing.SLDDRW", "part.SLDPRT", "Default")
    assert payload == {
        "state": "success",
        "call_id": "agent4-call",
        "dispatched": True,
        "value": {"kind": "update", "args": ["drawing.SLDDRW", "part.SLDPRT", "Default"]},
        "error": None,
    }


def test_agent4_capabilities_are_dependency_honest() -> None:
    plugin = _load_plugin()
    descriptors = plugin.capability_descriptors(
        SimpleNamespace(drawing_service=_Drawing())
    )
    by_name = {item["name"]: item for item in descriptors}

    assert by_name["solidworks.drawing.dimension"]["implemented"] is True
    assert by_name["solidworks.drawing.dimension"]["available"] is True
    assert by_name["solidworks.drawing.bom"]["implemented"] is True
    assert by_name["solidworks.drawing.bom"]["available"] is False
    assert by_name["solidworks.drawing.update_propagation"]["available"] is True
    assert all(
        set(item) == {"name", "implemented", "available", "reason", "backend", "dependencies"}
        for item in descriptors
    )
