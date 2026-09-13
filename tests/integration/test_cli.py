from __future__ import annotations

import os

import pytest

from cdt_solidworks.cli import _allowed_roots_from_environ, _bind_from_environ
from cdt_solidworks.platform.errors import StartupConfigError


def test_allowed_roots_are_split_by_platform_path_separator() -> None:
    env = {"CDT_SOLIDWORKS_ALLOWED_ROOTS": os.pathsep.join(("/one", "/two"))}
    assert _allowed_roots_from_environ(env) == ("/one", "/two")


def test_bind_defaults_are_loopback_and_bounded_port() -> None:
    assert _bind_from_environ({}) == ("127.0.0.1", 8000)
    assert _bind_from_environ({"CDT_SOLIDWORKS_BIND_HOST": "0.0.0.0", "CDT_SOLIDWORKS_PORT": "8123"}) == ("0.0.0.0", 8123)


@pytest.mark.parametrize("value", ["0", "65536", "not-a-port"])
def test_bind_rejects_invalid_port(value: str) -> None:
    with pytest.raises(StartupConfigError):
        _bind_from_environ({"CDT_SOLIDWORKS_PORT": value})
