"""Static runtime-secret bearer verifier for network mode."""

from __future__ import annotations

import secrets

from mcp.server.auth.provider import AccessToken

from cdt_solidworks.auth.config import NetworkAuthConfig


class StaticBearerTokenVerifier:
    """Verify one deployment-managed token without exposing it in auth context."""

    def __init__(self, config: NetworkAuthConfig) -> None:
        self._expected_token = config.bearer_token
        self._resource_url = config.resource_url
        self._scopes = list(config.required_scopes)

    async def verify_token(self, token: str) -> AccessToken | None:
        if not secrets.compare_digest(token, self._expected_token):
            return None
        return AccessToken(
            token="verified",
            client_id="cdt-solidworks-network",
            scopes=self._scopes,
            resource=self._resource_url,
        )
