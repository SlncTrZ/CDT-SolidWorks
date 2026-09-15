from __future__ import annotations

import pkgutil
from types import ModuleType, SimpleNamespace

import pytest
from mcp.server import MCPServer

import cdt_solidworks.integration.plugins.loader as plugin_loader
from cdt_solidworks.integration.plugins.loader import (
    PluginCompositionError,
    discover_plugins,
    register_plugins,
    validate_plugin_modules,
)
from cdt_solidworks.integration.runtime import IntegratedProviderRuntime
from cdt_solidworks.integration.server import build_integrated_server
from cdt_solidworks.native.models import ApplicationProbe, NativeCallResult
from cdt_solidworks.platform.observability import SafeObserver
from cdt_solidworks.server.factory import ServerConfig


class _FakeSession:
    session_id = "agent6-session"

    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def probe(self, *, version=None, timeout=3.0):
        return NativeCallResult.success(
            ApplicationProbe(
                prog_id="SldWorks.Application",
                registered=self.available,
                running=self.available,
                revision="34.0" if self.available else None,
                version_year=2026 if self.available else None,
            ),
            call_id="probe-agent6",
            dispatched=True,
        )


class _DocumentService:
    pass


def _plugin(
    name: str,
    *,
    plugin_id: str,
    order: int,
    tool_name: str,
    capability_name: str,
    contract_version: int = 1,
) -> ModuleType:
    module = ModuleType(name)
    module.PLUGIN_CONTRACT_VERSION = contract_version
    module.PLUGIN_ID = plugin_id
    module.PLUGIN_ORDER = order

    def register_tools(server, runtime) -> None:
        @server.tool(name=tool_name, description=f"Synthetic {plugin_id} tool")
        def synthetic_tool(value: str) -> dict[str, object]:
            service = runtime.get_service("core.document_service")
            return {
                "state": "success",
                "call_id": f"{plugin_id}-call",
                "dispatched": False,
                "value": {"value": value, "service": service.__class__.__name__},
                "error": None,
            }

    def capability_descriptors(runtime):
        return (
            {
                "name": capability_name,
                "implemented": True,
                "available": True,
                "reason": None,
                "backend": "solidworks_com",
                "dependencies": ("solidworks",),
            },
        )

    module.register_tools = register_tools
    module.capability_descriptors = capability_descriptors
    return module


def _runtime(*, available: bool = True, observer: SafeObserver | None = None):
    return IntegratedProviderRuntime(
        allowed_roots=("/tmp",),
        session=_FakeSession(available=available),
        document_service=_DocumentService(),
        cad_service=None,
        observer=observer,
    )


def test_discovery_is_bounded_to_agent_modules_and_sorted(monkeypatch) -> None:
    first = _plugin(
        "cdt_solidworks.integration.plugins.agent1_alpha",
        plugin_id="alpha",
        order=1,
        tool_name="alpha_tool",
        capability_name="solidworks.alpha",
    )
    second = _plugin(
        "cdt_solidworks.integration.plugins.agent2_beta",
        plugin_id="beta",
        order=2,
        tool_name="beta_tool",
        capability_name="solidworks.beta",
    )
    package = SimpleNamespace(
        __path__=("synthetic",),
        __name__="cdt_solidworks.integration.plugins",
    )
    modules = {first.__name__: first, second.__name__: second}
    imported: list[str] = []

    monkeypatch.setattr(
        plugin_loader.pkgutil,
        "iter_modules",
        lambda path, prefix: (
            pkgutil.ModuleInfo(None, f"{prefix}loader", False),
            pkgutil.ModuleInfo(None, second.__name__, False),
            pkgutil.ModuleInfo(None, f"{prefix}not_an_agent", False),
            pkgutil.ModuleInfo(None, first.__name__, False),
        ),
    )

    def fake_import(name: str):
        imported.append(name)
        if name == "cdt_solidworks.integration.plugins":
            return package
        return modules[name]

    monkeypatch.setattr(plugin_loader.importlib, "import_module", fake_import)

    specs = discover_plugins()

    assert [item.plugin_id for item in specs] == ["alpha", "beta"]
    assert imported == [
        "cdt_solidworks.integration.plugins",
        first.__name__,
        second.__name__,
    ]


def test_integrated_server_auto_composes_synthetic_plugins(monkeypatch, tmp_path) -> None:
    first = _plugin(
        "cdt_solidworks.integration.plugins.agent1_alpha",
        plugin_id="alpha",
        order=1,
        tool_name="alpha_tool",
        capability_name="solidworks.alpha",
    )
    second = _plugin(
        "cdt_solidworks.integration.plugins.agent2_beta",
        plugin_id="beta",
        order=2,
        tool_name="beta_tool",
        capability_name="solidworks.beta",
    )
    specs = validate_plugin_modules((second, first))
    monkeypatch.setattr(plugin_loader, "discover_plugins", lambda: specs)
    runtime = IntegratedProviderRuntime(
        allowed_roots=(tmp_path,),
        session=_FakeSession(available=True),
        document_service=_DocumentService(),
        cad_service=None,
    )

    server = build_integrated_server(ServerConfig.in_process(), runtime=runtime)
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
    states = {item.name: item for item in runtime.runtime_context().capabilities}

    assert "alpha_tool" in tools
    assert "beta_tool" in tools
    assert tools["alpha_tool"].parameters["additionalProperties"] is False
    assert set(tools["alpha_tool"].parameters["properties"]) == {"value"}
    assert states["solidworks.alpha"].available is True
    assert states["solidworks.beta"].available is True


def test_discovery_import_failure_is_typed_and_redacted(monkeypatch) -> None:
    package = SimpleNamespace(
        __path__=("synthetic",),
        __name__="cdt_solidworks.integration.plugins",
    )
    module_name = "cdt_solidworks.integration.plugins.agent1_broken"
    monkeypatch.setattr(
        plugin_loader.pkgutil,
        "iter_modules",
        lambda path, prefix: (pkgutil.ModuleInfo(None, module_name, False),),
    )

    def fake_import(name: str):
        if name == "cdt_solidworks.integration.plugins":
            return package
        raise RuntimeError("Bearer private-internal-detail")

    monkeypatch.setattr(plugin_loader.importlib, "import_module", fake_import)

    with pytest.raises(PluginCompositionError) as exc_info:
        discover_plugins()

    assert exc_info.value.code == "plugin_import_failed"
    assert "private-internal-detail" not in str(exc_info.value)


def test_plugin_contract_validation_is_deterministic() -> None:
    second = _plugin(
        "cdt_solidworks.integration.plugins.agent2_beta",
        plugin_id="beta",
        order=2,
        tool_name="beta_tool",
        capability_name="solidworks.beta",
    )
    first = _plugin(
        "cdt_solidworks.integration.plugins.agent1_alpha",
        plugin_id="alpha",
        order=1,
        tool_name="alpha_tool",
        capability_name="solidworks.alpha",
    )

    specs = validate_plugin_modules((second, first))

    assert [item.plugin_id for item in specs] == ["alpha", "beta"]
    assert [item.order for item in specs] == [1, 2]


@pytest.mark.parametrize(
    "modules, code",
    [
        (
            (
                _plugin("agent1_alpha", plugin_id="same", order=1, tool_name="a", capability_name="cap.a"),
                _plugin("agent2_beta", plugin_id="same", order=2, tool_name="b", capability_name="cap.b"),
            ),
            "duplicate_plugin_id",
        ),
        (
            (
                _plugin("agent1_alpha", plugin_id="alpha", order=1, tool_name="a", capability_name="cap.a"),
                _plugin("agent2_beta", plugin_id="beta", order=1, tool_name="b", capability_name="cap.b"),
            ),
            "duplicate_plugin_order",
        ),
        (
            (
                _plugin(
                    "agent1_alpha",
                    plugin_id="alpha",
                    order=1,
                    tool_name="a",
                    capability_name="cap.a",
                    contract_version=2,
                ),
            ),
            "unsupported_plugin_contract",
        ),
    ],
)
def test_plugin_contract_validation_fails_typed(modules, code: str) -> None:
    with pytest.raises(PluginCompositionError) as exc_info:
        validate_plugin_modules(modules)

    assert exc_info.value.code == code


def test_unavailable_capability_without_registered_tool_is_allowed() -> None:
    module = ModuleType("cdt_solidworks.integration.plugins.agent2_unavailable")
    module.PLUGIN_CONTRACT_VERSION = 1
    module.PLUGIN_ID = "unavailable"
    module.PLUGIN_ORDER = 2
    module.register_tools = lambda server, runtime: None
    module.capability_descriptors = lambda runtime: (
        {
            "name": "solidworks.test.unavailable",
            "implemented": True,
            "available": False,
            "reason": "dependency_unavailable",
            "backend": "solidworks_com",
            "dependencies": ("solidworks",),
        },
    )

    server = MCPServer("agent6-test")
    register_plugins(server, _runtime(), plugins=validate_plugin_modules((module,)))


def test_available_capability_without_registered_tool_is_rejected() -> None:
    module = ModuleType("cdt_solidworks.integration.plugins.agent2_invalid_available")
    module.PLUGIN_CONTRACT_VERSION = 1
    module.PLUGIN_ID = "invalid-available"
    module.PLUGIN_ORDER = 2
    module.register_tools = lambda server, runtime: None
    module.capability_descriptors = lambda runtime: (
        {
            "name": "solidworks.test.invalid_available",
            "implemented": True,
            "available": True,
            "reason": None,
            "backend": "solidworks_com",
            "dependencies": ("solidworks",),
        },
    )

    server = MCPServer("agent6-test")
    with pytest.raises(PluginCompositionError) as exc_info:
        register_plugins(server, _runtime(), plugins=validate_plugin_modules((module,)))
    assert exc_info.value.code == "plugin_capability_without_tools"


def test_plugin_registration_failure_is_typed_and_fail_closed() -> None:
    module = _plugin(
        "cdt_solidworks.integration.plugins.agent1_broken",
        plugin_id="broken",
        order=1,
        tool_name="broken_tool",
        capability_name="solidworks.broken",
    )

    def explode(server, runtime) -> None:
        raise RuntimeError("internal detail")

    module.register_tools = explode
    server = MCPServer("agent6-test")

    with pytest.raises(PluginCompositionError) as exc_info:
        register_plugins(server, _runtime(), plugins=validate_plugin_modules((module,)))

    assert exc_info.value.code == "plugin_registration_failed"
    assert "internal detail" not in str(exc_info.value)


def test_plugin_registration_rejects_duplicate_public_tool() -> None:
    server = MCPServer("agent6-test")
    runtime = _runtime()

    @server.tool(name="collision", description="Existing tool")
    def collision(value: str) -> str:
        return value

    plugin = _plugin(
        "cdt_solidworks.integration.plugins.agent1_collision",
        plugin_id="collision-plugin",
        order=1,
        tool_name="collision",
        capability_name="solidworks.collision",
    )

    with pytest.raises(PluginCompositionError) as exc_info:
        register_plugins(server, runtime, plugins=validate_plugin_modules((plugin,)))

    assert exc_info.value.code == "duplicate_public_tool"


def test_runtime_service_and_capability_registry_fail_on_duplicates() -> None:
    runtime = _runtime()
    assert runtime.get_service("core.session") is runtime.session
    assert runtime.get_service("core.path_policy") is runtime.path_policy
    assert runtime.get_service("core.document_service") is runtime.document_service
    assert runtime.get_service("core.cad_service") is None

    runtime.register_service("topology.query", object())
    with pytest.raises(ValueError, match="duplicate service"):
        runtime.register_service("topology.query", object())

    descriptor = {
        "name": "solidworks.topology.query",
        "implemented": True,
        "available": True,
        "reason": None,
        "backend": "solidworks_com",
        "dependencies": ("solidworks",),
    }
    runtime.register_capability_descriptors((descriptor,), source="agent1")
    with pytest.raises(ValueError, match="duplicate capability"):
        runtime.register_capability_descriptors((descriptor,), source="agent2")


def test_plugin_capability_cannot_collide_with_legacy_runtime_capability() -> None:
    runtime = _runtime()
    runtime.register_capability_descriptors(
        (
            {
                "name": "solidworks.application",
                "implemented": True,
                "available": True,
                "reason": None,
                "backend": "solidworks_com",
                "dependencies": ("solidworks",),
            },
        ),
        source="agent1",
    )

    with pytest.raises(ValueError, match="duplicate capability"):
        runtime.runtime_context()


def test_capability_cannot_claim_available_when_unimplemented() -> None:
    runtime = _runtime()

    with pytest.raises(ValueError, match="cannot be available when unimplemented"):
        runtime.register_capability_descriptors(
            (
                {
                    "name": "solidworks.invalid.claim",
                    "implemented": False,
                    "available": True,
                    "reason": None,
                    "backend": "solidworks_com",
                    "dependencies": ("solidworks",),
                },
            ),
            source="agent1",
        )


def test_unavailable_dependency_forces_plugin_capability_unavailable() -> None:
    runtime = _runtime(available=False)
    runtime.register_capability_descriptors(
        (
            {
                "name": "solidworks.topology.query",
                "implemented": True,
                "available": True,
                "reason": None,
                "backend": "solidworks_com",
                "dependencies": ("solidworks",),
            },
        ),
        source="agent1",
    )

    states = {item.name: item for item in runtime.runtime_context().capabilities}
    plugin_state = states["solidworks.topology.query"]
    assert plugin_state.implemented is True
    assert plugin_state.available is False
    assert plugin_state.reason == "solidworks_not_registered"


def test_registered_plugin_capability_recovers_when_dependency_recovers() -> None:
    runtime = _runtime(available=False)
    module = _plugin(
        "cdt_solidworks.integration.plugins.agent2_recovering",
        plugin_id="recovering",
        order=2,
        tool_name="recovering_tool",
        capability_name="solidworks.test.recovering",
    )

    def capability_descriptors(current_runtime):
        available = bool(current_runtime.session.available)
        return (
            {
                "name": "solidworks.test.recovering",
                "implemented": True,
                "available": available,
                "reason": None if available else "solidworks_temporarily_unavailable",
                "backend": "solidworks_com",
                "dependencies": ("solidworks",),
            },
        )

    module.capability_descriptors = capability_descriptors
    server = MCPServer("agent6-recovery-test")
    register_plugins(server, runtime, plugins=validate_plugin_modules((module,)))

    initial = {item.name: item for item in runtime.runtime_context().capabilities}
    assert initial["solidworks.test.recovering"].available is False

    runtime.session.available = True
    recovered = {item.name: item for item in runtime.runtime_context().capabilities}

    assert recovered["solidworks.test.recovering"].available is True
    assert recovered["solidworks.test.recovering"].reason is None


def test_internal_service_dependency_can_satisfy_plugin_capability() -> None:
    runtime = _runtime(available=True)
    runtime.topology_service = SimpleNamespace(resolve_native_for_document=lambda *args: None)
    runtime.register_capability_descriptors(
        (
            {
                "name": "solidworks.test.topology_consumer",
                "implemented": True,
                "available": True,
                "reason": None,
                "backend": "solidworks_com",
                "dependencies": ("solidworks", "topology.resolve"),
            },
        ),
        source="integration-test",
    )

    context = runtime.runtime_context()
    dependencies = {item.name: item for item in context.dependencies}
    states = {item.name: item for item in context.capabilities}

    assert dependencies["topology.resolve"].available is True
    assert states["solidworks.test.topology_consumer"].available is True


def test_plugin_mutation_preserves_uncertain_call_id_without_composition_retry() -> None:
    class MutationService:
        calls = 0

        def mutate(self):
            self.calls += 1
            return {
                "state": "uncertain",
                "call_id": "original-native-call",
                "dispatched": True,
                "value": None,
                "error": {
                    "code": "timeout",
                    "native_code": "timeout_after_dispatch",
                    "message": "Operation requires reconciliation.",
                    "retryable": False,
                },
            }

    observer = SafeObserver()
    runtime = _runtime(observer=observer)
    service = MutationService()
    runtime.register_service("test.mutation", service)
    module = ModuleType("cdt_solidworks.integration.plugins.agent3_mutation")
    module.PLUGIN_CONTRACT_VERSION = 1
    module.PLUGIN_ID = "mutation"
    module.PLUGIN_ORDER = 3

    def register_tools(server, runtime) -> None:
        @server.tool(name="mutation_tool", description="Synthetic uncertain mutation")
        def mutation_tool() -> dict[str, object]:
            return runtime.get_service("test.mutation").mutate()

    def capability_descriptors(runtime):
        return (
            {
                "name": "solidworks.test.mutation",
                "implemented": True,
                "available": True,
                "reason": None,
                "backend": "solidworks_com",
                "dependencies": ("solidworks",),
            },
        )

    module.register_tools = register_tools
    module.capability_descriptors = capability_descriptors
    server = MCPServer("agent6-test")
    register_plugins(server, runtime, plugins=validate_plugin_modules((module,)))

    tool = next(item for item in server._tool_manager.list_tools() if item.name == "mutation_tool")
    result = tool.fn()

    assert service.calls == 1
    assert result["state"] == "uncertain"
    assert result["call_id"] == "original-native-call"
    snapshot = observer.snapshot()
    assert snapshot.uncertain_count == 1
    assert snapshot.timeout_count == 1
    assert snapshot.last_operation_id == "original-native-call"


def test_plugin_tool_observability_records_call_id_and_uncertainty() -> None:
    observer = SafeObserver()
    runtime = _runtime(observer=observer)
    server = MCPServer("agent6-test")
    plugin = _plugin(
        "cdt_solidworks.integration.plugins.agent1_alpha",
        plugin_id="alpha",
        order=1,
        tool_name="alpha_tool",
        capability_name="solidworks.alpha",
    )
    register_plugins(server, runtime, plugins=validate_plugin_modules((plugin,)))

    tool = next(item for item in server._tool_manager.list_tools() if item.name == "alpha_tool")
    result = tool.fn(value="x")

    assert result["state"] == "success"
    snapshot = observer.snapshot()
    assert snapshot.request_count == 1
    assert snapshot.last_tool == "alpha_tool"
    assert snapshot.last_operation_id == "alpha-call"
    assert snapshot.last_outcome == "success"
