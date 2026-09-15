"""Minimal production entrypoint for the integrated CDT-SolidWorks provider."""

from __future__ import annotations

import os
from collections.abc import Mapping

import uvicorn

from cdt_solidworks.auth.config import NetworkAuthConfig
from cdt_solidworks.integration.runtime import IntegratedProviderRuntime
from cdt_solidworks.integration.server import build_integrated_network_app
from cdt_solidworks.platform.errors import StartupConfigError
from cdt_solidworks.server.factory import ServerConfig


ALLOWED_ROOTS_ENV = "CDT_SOLIDWORKS_ALLOWED_ROOTS"
WELDMENT_PROFILE_ROOTS_ENV = "CDT_SOLIDWORKS_WELDMENT_PROFILE_ROOTS"
BOM_TEMPLATE_PATH_ENV = "CDT_SOLIDWORKS_BOM_TEMPLATE_PATH"
BIND_HOST_ENV = "CDT_SOLIDWORKS_BIND_HOST"
PORT_ENV = "CDT_SOLIDWORKS_PORT"
VERSION_ENV = "CDT_SOLIDWORKS_VERSION"


def _allowed_roots_from_environ(environ: Mapping[str, str]) -> tuple[str, ...]:
    raw = environ.get(ALLOWED_ROOTS_ENV, "")
    return tuple(item.strip() for item in raw.split(os.pathsep) if item.strip())


def _weldment_profile_roots_from_environ(environ: Mapping[str, str]) -> tuple[str, ...]:
    raw = environ.get(WELDMENT_PROFILE_ROOTS_ENV, "")
    return tuple(item.strip() for item in raw.split(os.pathsep) if item.strip())


def _bom_template_path_from_environ(environ: Mapping[str, str]) -> str | None:
    raw = environ.get(BOM_TEMPLATE_PATH_ENV, "").strip()
    return raw or None


def _bind_from_environ(environ: Mapping[str, str]) -> tuple[str, int]:
    host = environ.get(BIND_HOST_ENV, "127.0.0.1").strip() or "127.0.0.1"
    raw_port = environ.get(PORT_ENV, "8000").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise StartupConfigError(f"{PORT_ENV} must be an integer port.") from exc
    if not 1 <= port <= 65535:
        raise StartupConfigError(f"{PORT_ENV} must be between 1 and 65535.")
    return host, port


def _version_from_environ(environ: Mapping[str, str]) -> int | None:
    raw = environ.get(VERSION_ENV, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise StartupConfigError(f"{VERSION_ENV} must be a SolidWorks year integer.") from exc


def main() -> None:
    """Build the authenticated integrated provider and serve Streamable HTTP."""

    auth = NetworkAuthConfig.from_environ(os.environ)
    runtime = IntegratedProviderRuntime(
        allowed_roots=_allowed_roots_from_environ(os.environ),
        weldment_profile_roots=_weldment_profile_roots_from_environ(os.environ),
        drawing_bom_template_path=_bom_template_path_from_environ(os.environ),
        version=_version_from_environ(os.environ),
    )
    app = build_integrated_network_app(
        ServerConfig(network_mode=True, network_auth=auth),
        runtime=runtime,
    )
    host, port = _bind_from_environ(os.environ)
    uvicorn.run(app, host=host, port=port)
