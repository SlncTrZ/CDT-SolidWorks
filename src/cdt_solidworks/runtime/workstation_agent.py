"""Workstation-side runtime agent — minimal W2 executor.

Authenticated persistent listener that owns the native execution side of
the transport boundary. Scope is deliberately minimal:

- bearer-authenticated HTTP listener (loopback by default) + heartbeat;
- runtime generation minted at agent start (restart creates a new one);
- process/session discovery (pid, Windows session id, best-effort);
- approved application attachment = the injected W1 port adapter reusing
  the current ``SolidWorksSession``/STA dispatcher implementation;
- bounded adapter dispatch by op name from the shared allowlist.

Explicitly NOT in this agent: MCP server, engineering semantics, and any
COM import — the adapter injected here already owns all native access.
``execute`` (arbitrary callables) is refused at the boundary; only the
serializable port lifecycle/identity ops in ``ALLOWED_OPS`` dispatch.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from cdt_solidworks.runtime.transport import (
    ALLOWED_OPS,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    _jsonable,
    bearer_matches,
    check_deadline,
)


def _current_process_session() -> dict[str, Any]:
    identity: dict[str, Any] = {"pid": os.getpid()}
    if os.name != "nt":
        return {**identity, "windows_session": None, "note": "non-windows"}
    try:
        import ctypes

        session_id = ctypes.c_uint32()
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        if kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
            identity["windows_session"] = int(session_id.value)
        else:
            identity["windows_session"] = None
    except Exception:
        identity["windows_session"] = None
    return identity


def _adapter_health_summary(adapter: Any) -> dict[str, Any]:
    try:
        health = adapter.health()
        return health if isinstance(health, dict) else {"detail": str(health)}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


@dataclass
class WorkstationAgentConfig:
    host: str = "127.0.0.1"
    port: int = 0  # 0 = ephemeral; actual port read back after start
    auth_token: str = ""
    allow_remote_bind: bool = False
    max_request_bytes: int = MAX_REQUEST_BYTES

    def __post_init__(self) -> None:
        if not str(self.auth_token or "").strip():
            raise ValueError("WorkstationSolidWorksRuntimeAgent requires a non-empty auth_token")
        if self.host.lower() not in {"127.0.0.1", "localhost", "::1"} and not self.allow_remote_bind:
            raise ValueError(
                f"refusing non-loopback agent bind {self.host!r} without allow_remote_bind"
            )
        if not 0 <= self.port <= 65535:
            raise ValueError("port must be within [0, 65535]")
        if self.max_request_bytes <= 0:
            raise ValueError("max_request_bytes must be > 0")


class WorkstationSolidWorksRuntimeAgent:
    """Owns one W1 port adapter + one runtime generation behind an authed listener."""

    def __init__(self, adapter: Any, config: WorkstationAgentConfig) -> None:
        from cdt_solidworks.runtime.port import SolidWorksRuntimePort

        if not isinstance(adapter, SolidWorksRuntimePort):
            raise TypeError("WorkstationSolidWorksRuntimeAgent requires a SolidWorksRuntimePort adapter")
        self._adapter = adapter
        self._config = config
        self._generation = f"gen-{uuid4().hex}"
        self._started_at = time.time()
        self._dispatch_lock = threading.Lock()  # serializes dispatch only
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._session = _current_process_session()

    @property
    def generation(self) -> str:
        return self._generation

    @property
    def adapter(self) -> Any:
        return self._adapter

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("agent is not started")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def heartbeat(self) -> dict[str, Any]:
        return {
            "generation": self._generation,
            "uptime_s": round(time.time() - self._started_at, 3),
            "session": dict(self._session),
            "adapter": _adapter_health_summary(self._adapter),
        }

    def dispatch(
        self,
        op: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        *,
        expected_generation: str | None = None,
        deadline_ms: int | None = None,
    ) -> dict[str, Any]:
        """Run one allowlisted adapter op and wrap it in the wire envelope."""
        name = str(op or "").strip()
        if name not in ALLOWED_OPS:
            return self._envelope(
                False, None, "unknown_op", f"op refused (not in allowlist): {name!r}", False
            )
        if expected_generation is not None and expected_generation != self._generation:
            # Wrong generation refuses BEFORE touching the session.
            return self._envelope(
                False,
                None,
                "generation_mismatch",
                f"stale runtime generation: expected {expected_generation!r}, "
                f"agent generation is {self._generation!r}",
                False,
            )
        try:
            bound_ms = check_deadline(deadline_ms)
        except ValueError as exc:
            return self._envelope(False, None, "bad_request", str(exc), False)
        if name == "session_identity":
            return self._envelope(
                True,
                {"session_id": str(self._adapter.session_id)},
                "ok",
                "",
                False,
            )
        target = getattr(self._adapter, name, None)
        if not callable(target):
            return self._envelope(
                False, None, "unknown_op", f"op not implemented by adapter: {name!r}", False
            )
        call_kwargs = dict(kwargs or {})
        # Wire normalization (no CAD semantics): JSON turns tuples into
        # lists and enums into strings; restore the port shapes before the
        # adapter sees them so dispatcher/recovery identity comparison stays
        # exact (tuple) and policy binding stays typed.
        if name == "reconcile" and isinstance(call_kwargs.get("identity"), list):
            call_kwargs["identity"] = tuple(call_kwargs["identity"])
        with self._dispatch_lock:
            try:
                result = self._run_bounded(target, tuple(args or ()), call_kwargs, bound_ms)
            except TimeoutError as exc:
                return self._envelope(False, None, "dispatch_timeout_uncertain", str(exc), True)
            except Exception as exc:
                uncertain = bool(getattr(exc, "completion_unknown", False))
                code = "uncertain" if uncertain else "backend_error"
                return self._envelope(False, None, code, str(exc), uncertain)
        try:
            return self._envelope(True, _jsonable(result), "ok", "", False)
        except Exception as exc:
            return self._envelope(
                False, None, "backend_error", f"result not serializable: {exc}", False
            )

    def _envelope(
        self,
        ok: bool,
        result: Any,
        code: str,
        message: str,
        completion_unknown: bool,
    ) -> dict[str, Any]:
        return {
            "ok": ok,
            "result": result,
            "error_code": code,
            "error_message": message,
            "generation": self._generation,
            "completion_unknown": completion_unknown,
        }

    @staticmethod
    def _run_bounded(target: Any, args: tuple[Any, ...], kwargs: dict[str, Any], bound_ms: int) -> Any:
        outcome: dict[str, Any] = {}

        def _invoke() -> None:
            try:
                outcome["result"] = target(*args, **kwargs)
            except BaseException as exc:  # transported, not interpreted
                outcome["error"] = exc

        worker = threading.Thread(target=_invoke, daemon=True)
        worker.start()
        worker.join(timeout=bound_ms / 1000.0)
        if worker.is_alive():
            raise TimeoutError(
                f"adapter op exceeded {bound_ms}ms after dispatch; "
                "completion is unknown, blind retry is forbidden"
            )
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("result")

    def start(self) -> str:
        if self._server is not None:
            raise RuntimeError("agent is already started")
        agent = self

        class _Handler(BaseHTTPRequestHandler):
            server_version = "CDT-SolidWorks-WorkstationAgent/0.1"

            def _authed(self) -> bool:
                presented = self.headers.get("Authorization", "")
                scheme, _, token = presented.partition(" ")
                if scheme.lower() != "bearer":
                    return False
                return bearer_matches(token.strip(), agent._config.auth_token)

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                if len(raw) > MAX_RESPONSE_BYTES:
                    raw = json.dumps(
                        agent._envelope(False, None, "oversized", "response oversized", False),
                        separators=(",", ":"),
                    ).encode("utf-8")
                    status = 500
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except OSError:
                    pass  # client went away; uncertainty is fail-closed provider-side.

            def _refuse_auth(self) -> None:
                self._send(
                    401,
                    agent._envelope(False, None, "unauthorized", "invalid bearer token", False),
                )

            def do_GET(self) -> None:
                if not self._authed():
                    self._refuse_auth()
                    return
                path = urlparse(self.path).path.rstrip("/") or "/"
                if path in ("/health", "/heartbeat"):
                    self._send(200, agent._envelope(True, agent.heartbeat(), "ok", "", False))
                elif path == "/status":
                    self._send(
                        200,
                        agent._envelope(
                            True,
                            {
                                "heartbeat": agent.heartbeat(),
                                "adapter_status": _jsonable(agent._adapter.runtime_status()),
                            },
                            "ok",
                            "",
                            False,
                        ),
                    )
                else:
                    self._send(
                        404, agent._envelope(False, None, "not_found", f"no route: {path}", False)
                    )

            def do_POST(self) -> None:
                if not self._authed():
                    self._refuse_auth()
                    return
                path = urlparse(self.path).path.rstrip("/") or "/"
                if path != "/dispatch":
                    self._send(
                        404, agent._envelope(False, None, "not_found", f"no route: {path}", False)
                    )
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0 or length > agent._config.max_request_bytes:
                    self._send(
                        400,
                        agent._envelope(
                            False, None, "oversized", "request body missing or oversized", False
                        ),
                    )
                    return
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                except (ValueError, UnicodeDecodeError):
                    self._send(
                        400,
                        agent._envelope(False, None, "bad_request", "malformed JSON body", False),
                    )
                    return
                if not isinstance(body, dict):
                    self._send(
                        400,
                        agent._envelope(False, None, "bad_request", "body must be an object", False),
                    )
                    return
                envelope = agent.dispatch(
                    body.get("op", ""),
                    body.get("args"),
                    body.get("kwargs"),
                    expected_generation=body.get("expected_generation"),
                    deadline_ms=body.get("deadline_ms"),
                )
                if envelope.get("error_code") == "unknown_op":
                    self._send(400, envelope)
                else:
                    self._send(200, envelope)

            def log_message(self, *args: Any) -> None:
                pass  # quiet by design; heartbeat carries observability

        server = ThreadingHTTPServer((self._config.host, self._config.port), _Handler)
        server.daemon_threads = True
        self._server = server
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.daemon = True
        thread.start()
        self._thread = thread
        return self.base_url

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5.0)
