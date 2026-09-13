"""Fail-closed network authentication configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import ParseResult, urlparse

from cdt_solidworks.platform.errors import StartupConfigError


TOKEN_ENV = "CDT_SOLIDWORKS_BEARER_TOKEN"
ISSUER_ENV = "CDT_SOLIDWORKS_AUTH_ISSUER_URL"
RESOURCE_ENV = "CDT_SOLIDWORKS_RESOURCE_URL"


@dataclass(frozen=True)
class NetworkAuthConfig:
    """Runtime-managed bearer credentials and public resource identity."""

    bearer_token: str = field(repr=False)
    issuer_url: str
    resource_url: str
    required_scopes: tuple[str, ...] = ("provider:read",)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> "NetworkAuthConfig":
        token = environ.get(TOKEN_ENV, "").strip()
        issuer_url = environ.get(ISSUER_ENV, "").strip()
        resource_url = environ.get(RESOURCE_ENV, "").strip()

        missing = [
            name
            for name, value in (
                (TOKEN_ENV, token),
                (ISSUER_ENV, issuer_url),
                (RESOURCE_ENV, resource_url),
            )
            if not value
        ]
        if missing:
            raise StartupConfigError(
                "Network authentication is incomplete; required runtime secrets/config are missing."
            )

        _validate_http_url(issuer_url, ISSUER_ENV)
        resource = _validate_http_url(resource_url, RESOURCE_ENV)
        if resource.path != "/mcp" or resource.query or resource.fragment:
            raise StartupConfigError("CDT_SOLIDWORKS_RESOURCE_URL must identify the exact /mcp endpoint.")

        return cls(
            bearer_token=token,
            issuer_url=issuer_url,
            resource_url=resource_url,
        )


def _validate_http_url(value: str, field_name: str) -> ParseResult:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise StartupConfigError(f"{field_name} must be an absolute http(s) URL.")
    if parsed.username is not None or parsed.password is not None:
        raise StartupConfigError(f"{field_name} must not contain URL credentials.")
    try:
        parsed.port
    except ValueError as exc:
        raise StartupConfigError(f"{field_name} contains an invalid port.") from exc
    return parsed
