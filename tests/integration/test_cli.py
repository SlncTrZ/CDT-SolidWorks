from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import cdt_solidworks.cli as cli
from cdt_solidworks.cli import (
    _allowed_roots_from_environ,
    _bind_from_environ,
    _bom_template_path_from_environ,
    _topology_reference_secret_from_environ,
    _weldment_profile_roots_from_environ,
)
from cdt_solidworks.native.models import NativeCallResult, NativeFailure
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


def test_topology_reference_secret_is_required_and_independent() -> None:
    with pytest.raises(StartupConfigError, match="CDT_SOLIDWORKS_TOPOLOGY_REFERENCE_SECRET"):
        _topology_reference_secret_from_environ({})
    assert _topology_reference_secret_from_environ(
        {"CDT_SOLIDWORKS_TOPOLOGY_REFERENCE_SECRET": " topology-signing-key "}
    ) == "topology-signing-key"


def test_weldment_profile_roots_are_independent_from_document_roots() -> None:
    env = {
        "CDT_SOLIDWORKS_ALLOWED_ROOTS": os.pathsep.join(("/docs-a", "/docs-b")),
        "CDT_SOLIDWORKS_WELDMENT_PROFILE_ROOTS": os.pathsep.join(("/profiles-a", "/profiles-b")),
    }
    assert _allowed_roots_from_environ(env) == ("/docs-a", "/docs-b")
    assert _weldment_profile_roots_from_environ(env) == ("/profiles-a", "/profiles-b")


def _install_main_fakes(monkeypatch, events: list[tuple[str, object]], *, run_error=None):
    monkeypatch.setenv("CDT_SOLIDWORKS_TOPOLOGY_REFERENCE_SECRET", "independent-topology-secret")
    class Session:
        def disconnect(self, *, timeout):
            events.append(("disconnect", timeout))
            return NativeCallResult.success(True, call_id="disconnect", dispatched=True)

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
        classmethod(lambda cls, environ: SimpleNamespace(bearer_token="test-topology-secret")),
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

    runtime_kwargs = next(value for event, value in events if event == "runtime")
    assert runtime_kwargs["topology_reference_secret"] == "independent-topology-secret"
    assert runtime_kwargs["topology_reference_secret"] != "test-topology-secret"
    assert events[-3:] == [
        ("uvicorn", ("127.0.0.1", 8000)),
        ("disconnect", 30.0),
        ("close_dispatcher", 5.0),
    ]


def test_shutdown_runtime_rejects_failed_disconnect_and_dispatcher_close(monkeypatch) -> None:
    events: list[tuple[str, object]] = []
    _install_main_fakes(monkeypatch, events)
    class BadSession:
        def disconnect(self, *, timeout):
            return NativeCallResult.failed(
                NativeFailure("disconnect_refused", "disconnect", "no"),
                call_id="bad",
                dispatched=True,
            )
        def close_dispatcher(self, *, timeout):
            return False

    runtime = SimpleNamespace(session=BadSession())
    with pytest.raises(RuntimeError, match="disconnect_refused"):
        cli._shutdown_runtime(runtime, suppress_errors=False)


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
