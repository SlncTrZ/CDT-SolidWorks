from __future__ import annotations

from types import SimpleNamespace

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.integration.plugins import agent3_part_mechanical as plugin
from cdt_solidworks.native.models import NativeCallResult


class Server:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self, *, name: str, description: str):
        def decorate(func):
            self.tools[name] = func
            return func
        return decorate


class PartWrapper:
    def __init__(self) -> None:
        self.profile_port = object()
        self.service = object()

    def feature_parameters_get(self, **kwargs):
        return NativeCallResult.success({"depth_mm": 5.0}, call_id="part-read", dispatched=False)

    def profile_extrude(self, **kwargs):
        return NativeCallResult.success({"feature_id": "Boss1"}, call_id="boss", dispatched=True)

    def profile_cut(self, **kwargs):
        return NativeCallResult.success({"feature_id": "Cut1"}, call_id="cut", dispatched=True)

    def feature_parameter_set(self, **kwargs):
        return NativeCallResult.success({"feature_id": kwargs["feature_id"]}, call_id="set", dispatched=True)


def test_plugin_contract_and_public_tool_names(tmp_path) -> None:
    runtime = SimpleNamespace(
        path_policy=DocumentPathPolicy((tmp_path,)),
        part_feature_service=PartWrapper(),
        sketch_service=SimpleNamespace(service=object()),
    )
    server = Server()

    plugin.register_tools(server, runtime)

    assert plugin.PLUGIN_CONTRACT_VERSION == 1
    assert plugin.PLUGIN_ID == "agent3_part_mechanical"
    assert plugin.PLUGIN_ORDER == 3
    assert set(server.tools) == {
        "part_profile_extrude",
        "part_profile_cut",
        "part_feature_parameters_get",
        "part_feature_parameter_set",
        "part_gear_create_spur",
        "part_gear_parameters_get",
        "part_gear_parameters_set",
    }

    payload = server.tools["part_feature_parameters_get"](
        path=str(tmp_path / "part.SLDPRT"),
        expected_revision=1,
        feature_id="Boss1",
    )
    assert payload == {
        "state": "success",
        "call_id": "part-read",
        "dispatched": False,
        "value": {"depth_mm": 5.0},
        "error": None,
    }


def test_capabilities_are_dependency_honest(tmp_path) -> None:
    unavailable = SimpleNamespace(path_policy=DocumentPathPolicy((tmp_path,)), part_feature_service=None, sketch_service=None)
    missing = {item["name"]: item for item in plugin.capability_descriptors(unavailable)}
    assert missing["part.profile_features"]["implemented"] is True
    assert missing["part.profile_features"]["available"] is False
    assert missing["mechanical.spur_gear"]["available"] is False

    part = PartWrapper()
    available = SimpleNamespace(
        path_policy=DocumentPathPolicy((tmp_path,)),
        part_feature_service=part,
        sketch_service=SimpleNamespace(service=object()),
    )
    ready = {item["name"]: item for item in plugin.capability_descriptors(available)}
    assert ready["part.profile_features"]["available"] is True
    assert ready["part.feature_parameters"]["available"] is True
    assert ready["mechanical.spur_gear"]["available"] is True
