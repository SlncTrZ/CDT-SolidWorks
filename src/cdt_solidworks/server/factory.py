"""MCP server construction and lane integration seam."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse

from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from cdt_solidworks.auth.config import NetworkAuthConfig
from cdt_solidworks.auth.verifier import StaticBearerTokenVerifier
from cdt_solidworks.platform.capabilities import (
    build_system_capabilities,
    build_system_status,
    default_runtime_context,
)
from cdt_solidworks.platform.errors import StartupConfigError
from cdt_solidworks.platform.help import HelpService
from cdt_solidworks.platform.identity import PROVIDER_ID, PROVIDER_VERSION
from cdt_solidworks.platform.log_safety import install_mcp_tool_log_safety
from cdt_solidworks.platform.models import (
    HelpResponse,
    ObservabilitySnapshot,
    RuntimeContext,
    SystemCapabilities,
    SystemStatus,
)
from cdt_solidworks.platform.observability import SafeObserver
from cdt_solidworks.server.validation import (
    _TOOL_ARGUMENTS,
    seal_tool_input_schemas,
    strict_tool_input_middleware,
)


RuntimeContextProvider = Callable[[], RuntimeContext]
Registrar = Callable[[MCPServer], None]


@dataclass(frozen=True)
class ServerConfig:
    """Construction inputs with an explicit network/auth boundary."""

    network_mode: bool
    guide_path: Path | None = None
    network_auth: NetworkAuthConfig | None = None
    runtime_context_provider: RuntimeContextProvider | None = None
    observer: SafeObserver | None = None

    @classmethod
    def in_process(
        cls,
        *,
        guide_path: Path | None = None,
        runtime_context_provider: RuntimeContextProvider | None = None,
        observer: SafeObserver | None = None,
    ) -> "ServerConfig":
        return cls(
            network_mode=False,
            guide_path=guide_path,
            network_auth=None,
            runtime_context_provider=runtime_context_provider,
            observer=observer,
        )


def build_server(
    config: ServerConfig,
    *,
    registrars: Sequence[Registrar] = (),
) -> MCPServer:
    """Build the provider server and deterministically apply external lane registrars."""

    if config.network_mode and config.network_auth is None:
        raise StartupConfigError("Network mode requires configured Bearer authentication.")

    install_mcp_tool_log_safety(
        bearer_token=config.network_auth.bearer_token if config.network_auth is not None else None
    )

    auth_settings: AuthSettings | None = None
    token_verifier: StaticBearerTokenVerifier | None = None
    if config.network_auth is not None:
        token_verifier = StaticBearerTokenVerifier(config.network_auth)
        auth_settings = AuthSettings(
            issuer_url=config.network_auth.issuer_url,
            resource_server_url=config.network_auth.resource_url,
            required_scopes=list(config.network_auth.required_scopes),
            validate_token_resource=True,
        )

    tool_arguments: dict[str, frozenset[str]] = dict(_TOOL_ARGUMENTS)
    server = MCPServer(
        PROVIDER_ID,
        version=PROVIDER_VERSION,
        auth=auth_settings,
        token_verifier=token_verifier,
        middleware=(strict_tool_input_middleware(tool_arguments),),
    )
    observer = config.observer or SafeObserver()
    help_service = HelpService(config.guide_path)
    runtime_context_provider = config.runtime_context_provider or default_runtime_context

    @server.tool(
        name="help",
        description="Read the current provider contract and deterministic fingerprint.",
    )
    def help_tool() -> HelpResponse:
        started = perf_counter()
        try:
            result = help_service.read()
        except Exception:
            observer.record(
                tool_name="help",
                outcome="failure",
                provider_latency_ms=(perf_counter() - started) * 1000,
                dependency_latency_ms=None,
            )
            raise
        observer.record(
            tool_name="help",
            outcome="success",
            provider_latency_ms=(perf_counter() - started) * 1000,
            dependency_latency_ms=None,
        )
        return result

    def current_context() -> RuntimeContext:
        try:
            return runtime_context_provider()
        except Exception:
            return RuntimeContext(
                backend="provider_platform",
                dependencies=(
                    default_runtime_context().dependencies[0].model_copy(
                        update={"reason": "dependency_probe_failed"}
                    ),
                ),
            )

    @server.tool(
        name="system_status",
        description="Report provider and dependency availability without inferring native readiness.",
    )
    def system_status_tool() -> SystemStatus:
        started = perf_counter()
        result = build_system_status(current_context())
        observer.record(
            tool_name="system_status",
            outcome="success",
            provider_latency_ms=(perf_counter() - started) * 1000,
            dependency_latency_ms=None,
        )
        return result

    @server.tool(
        name="system_observability",
        description="Report bounded provider telemetry without request payloads or credentials.",
    )
    def system_observability_tool() -> ObservabilitySnapshot:
        return observer.snapshot()

    @server.tool(
        name="system_capabilities",
        description="Report implemented and currently available capabilities as separate facts.",
    )
    def system_capabilities_tool() -> SystemCapabilities:
        started = perf_counter()
        result = build_system_capabilities(current_context())
        observer.record(
            tool_name="system_capabilities",
            outcome="success",
            provider_latency_ms=(perf_counter() - started) * 1000,
            dependency_latency_ms=None,
        )
        return result

    for registrar in registrars:
        registrar(server)

    tool_arguments.update(seal_tool_input_schemas(server))
    return server


def build_network_app(
    config: ServerConfig,
    *,
    registrars: Sequence[Registrar] = (),
) -> Starlette:
    """Build the authenticated Streamable HTTP application at `/mcp`."""

    if not config.network_mode:
        raise StartupConfigError("Network app construction requires network_mode=True.")
    if config.network_auth is None:
        raise StartupConfigError("Network mode requires configured Bearer authentication.")
    resource = urlparse(config.network_auth.resource_url)
    resource_host = resource.hostname
    if resource_host is None:
        raise StartupConfigError("Network resource URL must include a hostname.")
    header_host = f"[{resource_host}]" if ":" in resource_host else resource_host
    allowed_hosts = [header_host]
    allowed_origins = [f"{resource.scheme}://{header_host}"]
    if resource.port is not None:
        allowed_hosts.append(f"{header_host}:{resource.port}")
        allowed_origins.append(f"{resource.scheme}://{header_host}:{resource.port}")
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
    return build_server(config, registrars=registrars).streamable_http_app(
        host=resource_host,
        transport_security=transport_security,
    )
