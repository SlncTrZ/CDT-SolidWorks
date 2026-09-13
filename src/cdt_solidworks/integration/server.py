"""Final Agent-E server composition for the integrated W0 native/document surface."""

from __future__ import annotations

from dataclasses import replace

from mcp.server import MCPServer
from starlette.applications import Starlette

from cdt_solidworks.server.factory import ServerConfig, build_network_app, build_server

from .runtime import IntegratedProviderRuntime


def _config(config: ServerConfig, runtime: IntegratedProviderRuntime) -> ServerConfig:
    return replace(config, runtime_context_provider=runtime.runtime_context)


def build_integrated_server(
    config: ServerConfig,
    *,
    runtime: IntegratedProviderRuntime,
) -> MCPServer:
    return build_server(_config(config, runtime), registrars=(runtime.register_tools,))


def build_integrated_network_app(
    config: ServerConfig,
    *,
    runtime: IntegratedProviderRuntime,
) -> Starlette:
    return build_network_app(_config(config, runtime), registrars=(runtime.register_tools,))
