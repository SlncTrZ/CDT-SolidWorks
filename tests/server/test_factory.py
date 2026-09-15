from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cdt_solidworks.auth.config import NetworkAuthConfig
from cdt_solidworks.platform.errors import StartupConfigError
from cdt_solidworks.server.factory import ServerConfig, build_network_app, build_server


AUTH_ENV = {
    "CDT_SOLIDWORKS_BEARER_TOKEN": "test-only-secret",
    "CDT_SOLIDWORKS_AUTH_ISSUER_URL": "https://auth.example.test/",
    "CDT_SOLIDWORKS_RESOURCE_URL": "https://solidworks.example.test/mcp",
}


def _guide(tmp_path: Path) -> Path:
    path = tmp_path / "guide.md"
    path.write_text("# Current runtime\n", encoding="utf-8")
    return path


def test_server_constructs_with_zero_external_registrars(tmp_path: Path) -> None:
    server = build_server(ServerConfig.in_process(guide_path=_guide(tmp_path)))

    assert server.name == "solidworks"


def test_server_invokes_multiple_external_registrars_in_order(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def first(server) -> None:
        calls.append(("first", server.name))

    def second(server) -> None:
        calls.append(("second", server.name))

    build_server(
        ServerConfig.in_process(guide_path=_guide(tmp_path)),
        registrars=(first, second),
    )

    assert calls == [("first", "solidworks"), ("second", "solidworks")]


def test_external_tool_schema_is_closed_without_static_name_hardcoding(tmp_path: Path) -> None:
    def register_echo(server) -> None:
        @server.tool(name="agent6_echo", description="Synthetic plugin-shaped tool")
        def agent6_echo(value: str, count: int = 1) -> str:
            return value * count

    server = build_server(
        ServerConfig.in_process(guide_path=_guide(tmp_path)),
        registrars=(register_echo,),
    )
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    schema = tools["agent6_echo"].input_schema
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"value", "count"}


def test_external_tool_kwargs_escape_hatch_is_rejected(tmp_path: Path) -> None:
    def register_open(server) -> None:
        @server.tool(name="agent6_open", description="Invalid open schema")
        def agent6_open(**kwargs) -> dict:
            return kwargs

    with pytest.raises(RuntimeError, match=r"may not expose \*\*kwargs"):
        build_server(
            ServerConfig.in_process(guide_path=_guide(tmp_path)),
            registrars=(register_open,),
        )


def test_network_app_fails_closed_without_auth_config(tmp_path: Path) -> None:
    config = ServerConfig(network_mode=True, guide_path=_guide(tmp_path), network_auth=None)

    with pytest.raises(StartupConfigError):
        build_network_app(config)


def test_network_app_exposes_mcp_route_with_auth_config(tmp_path: Path) -> None:
    config = ServerConfig(
        network_mode=True,
        guide_path=_guide(tmp_path),
        network_auth=NetworkAuthConfig.from_environ(AUTH_ENV),
    )

    app = build_network_app(config)
    route_paths = {getattr(route, "path", None) for route in app.routes}

    assert "/mcp" in route_paths
