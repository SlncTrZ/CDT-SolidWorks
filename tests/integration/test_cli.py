from __future__ import annotations

import os

import pytest

import cdt_solidworks.cli as cli
from cdt_solidworks.cli import (
    _allowed_roots_from_environ,
    _bind_from_environ,
    _bom_template_path_from_environ,
    _weldment_profile_roots_from_environ,
)
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


def test_bom_template_path_is_explicit_single_dependency() -> None:
    assert _bom_template_path_from_environ({}) is None
    assert _bom_template_path_from_environ(
        {"CDT_SOLIDWORKS_BOM_TEMPLATE_PATH": "  /templates/bom-standard.sldbomtbt  "}
    ) == "/templates/bom-standard.sldbomtbt"


def test_weldment_profile_roots_are_independent_from_document_roots() -> None:
    env = {
        "CDT_SOLIDWORKS_ALLOWED_ROOTS": os.pathsep.join(("/docs-a", "/docs-b")),
        "CDT_SOLIDWORKS_WELDMENT_PROFILE_ROOTS": os.pathsep.join(("/profiles-a", "/profiles-b")),
    }
    assert _allowed_roots_from_environ(env) == ("/docs-a", "/docs-b")
    assert _weldment_profile_roots_from_environ(env) == ("/profiles-a", "/profiles-b")


def _install_main_fakes(monkeypatch, events: list[tuple[str, object]], *, run_error=None):
    class Session:
        def disconnect(self, *, timeout):
            events.append(("disconnect", timeout))
            return object()

        def close_dispatcher(self, *, timeout):
            events.append(("close_dispatcher", timeout))
            return True

    class Runtime:
        def __init__(self, **kwargs):
            events.append(("runtime", kwargs))
            self.session = Session()

    monkeypatch.setattr(cli, "IntegratedProviderRuntime", Runtime)
    monkeypatch.setattr(
        cli.NetworkAuthConfig,
        "from_environ",
        classmethod(lambda cls, environ: object()),
    )
    monkeypatch.setattr(
        cli,
        "build_integrated_network_app",
        lambda config, *, runtime: object(),
    )

    def run(app, *, host, port):
        events.append(("uvicorn", (host, port)))
        if run_error is not None:
            raise run_error

    monkeypatch.setattr(cli.uvicorn, "run", run)


def test_main_cleans_up_native_runtime_after_normal_server_return(monkeypatch) -> None:
    events: list[tuple[str, object]] = []
    _install_main_fakes(monkeypatch, events)

    cli.main()

    assert events[-3:] == [
        ("uvicorn", ("127.0.0.1", 8000)),
        ("disconnect", 30.0),
        ("close_dispatcher", 5.0),
    ]


def test_main_cleans_up_native_runtime_without_masking_server_failure(monkeypatch) -> None:
    events: list[tuple[str, object]] = []
    failure = RuntimeError("server failed")
    _install_main_fakes(monkeypatch, events, run_error=failure)

    with pytest.raises(RuntimeError, match="server failed"):
        cli.main()

    assert events[-3:] == [
        ("uvicorn", ("127.0.0.1", 8000)),
        ("disconnect", 30.0),
        ("close_dispatcher", 5.0),
    ]


def test_main_cleans_up_native_runtime_when_app_construction_fails(monkeypatch) -> None:
    events: list[tuple[str, object]] = []
    _install_main_fakes(monkeypatch, events)

    def fail_build(config, *, runtime):
        raise RuntimeError("app build failed")

    monkeypatch.setattr(cli, "build_integrated_network_app", fail_build)

    with pytest.raises(RuntimeError, match="app build failed"):
        cli.main()

    assert ("uvicorn", ("127.0.0.1", 8000)) not in events
    assert events[-2:] == [
        ("disconnect", 30.0),
        ("close_dispatcher", 5.0),
    ]
