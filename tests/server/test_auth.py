from __future__ import annotations

import pytest

from cdt_solidworks.auth.config import NetworkAuthConfig
from cdt_solidworks.auth.verifier import StaticBearerTokenVerifier
from cdt_solidworks.platform.errors import StartupConfigError


BASE_ENV = {
    "CDT_SOLIDWORKS_BEARER_TOKEN": "test-only-secret",
    "CDT_SOLIDWORKS_AUTH_ISSUER_URL": "https://auth.example.test/",
    "CDT_SOLIDWORKS_RESOURCE_URL": "https://solidworks.example.test/mcp",
}


def test_network_auth_config_fails_closed_when_secret_is_missing() -> None:
    env = dict(BASE_ENV)
    env.pop("CDT_SOLIDWORKS_BEARER_TOKEN")

    with pytest.raises(StartupConfigError):
        NetworkAuthConfig.from_environ(env)


def test_auth_config_repr_never_contains_bearer_secret() -> None:
    config = NetworkAuthConfig.from_environ(BASE_ENV)

    assert "test-only-secret" not in repr(config)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("CDT_SOLIDWORKS_AUTH_ISSUER_URL", "https://user:password@auth.example.test/"),
        ("CDT_SOLIDWORKS_RESOURCE_URL", "https://user:password@solidworks.example.test/mcp"),
    ],
)
def test_auth_config_rejects_credentials_embedded_in_urls(field: str, value: str) -> None:
    env = dict(BASE_ENV)
    env[field] = value

    with pytest.raises(StartupConfigError):
        NetworkAuthConfig.from_environ(env)


@pytest.mark.parametrize(
    "resource_url",
    [
        "https://solidworks.example.test/base/mcp",
        "https://solidworks.example.test/mcp/",
        "https://solidworks.example.test/mcp?token=bad",
        "https://solidworks.example.test/mcp#fragment",
        "https://solidworks.example.test:99999/mcp",
    ],
)
def test_auth_config_requires_exact_mcp_resource_endpoint(resource_url: str) -> None:
    env = dict(BASE_ENV)
    env["CDT_SOLIDWORKS_RESOURCE_URL"] = resource_url

    with pytest.raises(StartupConfigError):
        NetworkAuthConfig.from_environ(env)


@pytest.mark.asyncio
async def test_static_verifier_accepts_only_exact_token_without_returning_secret() -> None:
    config = NetworkAuthConfig.from_environ(BASE_ENV)
    verifier = StaticBearerTokenVerifier(config)

    assert await verifier.verify_token("wrong") is None
    access = await verifier.verify_token("test-only-secret")

    assert access is not None
    assert access.token != "test-only-secret"
    assert access.client_id == "cdt-solidworks-network"
    assert access.resource == "https://solidworks.example.test/mcp"
