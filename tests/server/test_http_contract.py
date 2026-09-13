from __future__ import annotations

from pathlib import Path

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from cdt_solidworks.auth.config import NetworkAuthConfig
from cdt_solidworks.integration.runtime import IntegratedProviderRuntime
from cdt_solidworks.integration.server import build_integrated_network_app
from cdt_solidworks.native.models import ApplicationProbe, NativeCallResult
from cdt_solidworks.server.factory import ServerConfig, build_network_app


RESOURCE_URL = "https://solidworks.example.test/mcp"
AUTH_ENV = {
    "CDT_SOLIDWORKS_BEARER_TOKEN": "test-only-secret",
    "CDT_SOLIDWORKS_AUTH_ISSUER_URL": "https://auth.example.test/",
    "CDT_SOLIDWORKS_RESOURCE_URL": RESOURCE_URL,
}


def _config(tmp_path: Path) -> ServerConfig:
    guide = tmp_path / "guide.md"
    guide.write_text("# Current runtime\n", encoding="utf-8")
    return ServerConfig(
        network_mode=True,
        guide_path=guide,
        network_auth=NetworkAuthConfig.from_environ(AUTH_ENV),
    )


async def _raw_post(
    app,
    authorization: str | None,
    *,
    base_url: str = "https://solidworks.example.test",
    origin: str | None = None,
) -> httpx2.Response:
    headers = {"authorization": authorization} if authorization else {}
    if origin is not None:
        headers["origin"] = origin
    transport = httpx2.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=transport,
            base_url=base_url,
            headers=headers,
        ) as client:
            return await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", [None, "Bearer wrong"])
async def test_network_http_missing_or_invalid_auth_fails_closed(
    tmp_path: Path, authorization: str | None
) -> None:
    app = build_network_app(_config(tmp_path))

    response = await _raw_post(app, authorization)

    assert response.status_code == 401
    assert "test-only-secret" not in response.text
    assert response.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.asyncio
async def test_network_transport_rejects_unexpected_host_and_origin(tmp_path: Path) -> None:
    bad_host = await _raw_post(
        build_network_app(_config(tmp_path)),
        "Bearer test-only-secret",
        base_url="https://attacker.example.test",
    )
    bad_origin = await _raw_post(
        build_network_app(_config(tmp_path)),
        "Bearer test-only-secret",
        origin="https://attacker.example.test",
    )

    assert bad_host.status_code == 421
    assert bad_origin.status_code == 403


@pytest.mark.asyncio
async def test_valid_auth_reaches_read_only_help_and_unknown_fields_are_rejected(
    tmp_path: Path,
) -> None:
    app = build_network_app(_config(tmp_path))
    transport = httpx2.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="https://solidworks.example.test",
            headers={"authorization": "Bearer test-only-secret"},
        ) as http_client:
            target = streamable_http_client(
                RESOURCE_URL,
                http_client=http_client,
                terminate_on_close=False,
            )
            async with Client(target) as client:
                valid = await client.call_tool("help")
                with pytest.raises(MCPError) as exc_info:
                    await client.call_tool("help", {"unexpected": True})

    assert valid.is_error is False
    assert valid.structured_content["provider_name"] == "solidworks"
    assert exc_info.value.code == INVALID_PARAMS
    assert "unexpected" in exc_info.value.message.lower()
    assert "test-only-secret" not in exc_info.value.message


@pytest.mark.asyncio
async def test_unexpected_tool_failure_does_not_leak_exception_or_secret(tmp_path: Path, caplog) -> None:
    def register_crash(server) -> None:
        @server.tool(name="crash")
        def crash() -> str:
            raise RuntimeError("Bearer test-only-secret internal detail")

    app = build_network_app(_config(tmp_path), registrars=(register_crash,))
    transport = httpx2.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="https://solidworks.example.test",
            headers={"authorization": "Bearer test-only-secret"},
        ) as http_client:
            target = streamable_http_client(
                RESOURCE_URL,
                http_client=http_client,
                terminate_on_close=False,
            )
            async with Client(target) as client:
                result = await client.call_tool("crash")

    rendered = " ".join(getattr(item, "text", "") for item in result.content)
    assert result.is_error is True
    assert "test-only-secret" not in rendered
    assert "runtimeerror" not in rendered.lower()
    assert "test-only-secret" not in caplog.text


class _IntegrationProbeSession:
    session_id = "integration-probe"

    def probe(self, *, version=None, timeout=3.0):
        return NativeCallResult.success(
            ApplicationProbe(
                prog_id="SldWorks.Application",
                registered=True,
                running=True,
                revision="34.0",
                version_year=2026,
            ),
            call_id="probe-call",
        )


class _UnusedDocumentService:
    pass


@pytest.mark.asyncio
async def test_integrated_network_rejects_unknown_native_tool_arguments(tmp_path: Path) -> None:
    runtime = IntegratedProviderRuntime(
        allowed_roots=(tmp_path,),
        session=_IntegrationProbeSession(),
        document_service=_UnusedDocumentService(),
    )
    app = build_integrated_network_app(_config(tmp_path), runtime=runtime)
    transport = httpx2.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="https://solidworks.example.test",
            headers={"authorization": f"Bearer {AUTH_ENV['CDT_SOLIDWORKS_BEARER_TOKEN']}"},
        ) as http_client:
            target = streamable_http_client(
                RESOURCE_URL,
                http_client=http_client,
                terminate_on_close=False,
            )
            async with Client(target) as client:
                valid = await client.call_tool("application_probe")
                with pytest.raises(MCPError) as exc_info:
                    await client.call_tool("application_probe", {"unexpected": True})

    assert valid.is_error is False
    assert exc_info.value.code == INVALID_PARAMS
    assert "unexpected" in exc_info.value.message.lower()
