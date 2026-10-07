"""Remote provider-side runtime adapter — W2 split-process path over transport.

Forwards only the serializable port lifecycle/identity ops over a
``SolidWorksRuntimeTransport``. No CAD validation, rebuild, dispatch or COM
logic lives here — the workstation agent owns the exact same W1 adapter /
``SolidWorksSession`` stack in its own process.

Fencing: ``connect``/``disconnect`` enter the provider-side writer lane via
the shared coordinator before dispatch; timeout/disconnect after dispatch
quarantines it and raises completion-unknown. Blind replay stays forbidden.

Explicit limits (fail-closed, not silent):

- ``api`` raises: no in-process COM exists on the provider side by design.
- ``execute`` raises: arbitrary callables cannot cross a process boundary.
  Service-level remote dispatch is W5/W6; it is refused here, never faked.
- ``close_dispatcher`` raises: the dispatcher lifecycle belongs to the
  workstation host.
- ``reconcile`` forwards without a verifier callable; recovery executes
  agent-side from the original mutation's provider-owned plan held by the
  agent session. Identity must start with the workstation session id
  observed via ``session_identity`` (pinned at construction).
"""

from __future__ import annotations

import threading
from typing import Any

from cdt_solidworks.native.models import (
    ApplicationOwnership,
    ApplicationProbe,
    NativeCallResult,
    NativeCallState,
    NativeFailure,
    SessionInfo,
)
from cdt_solidworks.runtime.port import SolidWorksRuntimePort
from cdt_solidworks.runtime.transport import (
    ALLOWED_OPS,
    DEFAULT_DEADLINE_MS,
    MUTATION_OPS,
    RuntimeOpRefusedError,
    RuntimeTransportError,
    RuntimeUnavailableError,
    RuntimeUncertainError,
    check_deadline,
)


def _failure(data: Any) -> NativeFailure | None:
    if not isinstance(data, dict):
        return None
    return NativeFailure(
        code=str(data.get("code", "native_call_failed")),
        stage=str(data.get("stage", "")),
        message=str(data.get("message", "Native operation failed.")),
        retryable=bool(data.get("retryable", False)),
        details=dict(data.get("details") or {}),
    )


def _probe_value(data: Any) -> ApplicationProbe | None:
    if not isinstance(data, dict):
        return None
    return ApplicationProbe(
        prog_id=str(data.get("prog_id", "")),
        registered=bool(data.get("registered", False)),
        running=bool(data.get("running", False)),
        revision=data.get("revision"),
        version_year=data.get("version_year"),
    )


def _session_value(data: Any) -> SessionInfo | None:
    if not isinstance(data, dict):
        return None
    return SessionInfo(
        session_id=str(data.get("session_id", "")),
        ownership=ApplicationOwnership(str(data.get("ownership", "user_owned"))),
        prog_id=str(data.get("prog_id", "")),
        revision=str(data.get("revision", "")),
        version_year=data.get("version_year"),
    )


def _result(op: str, payload: Any) -> NativeCallResult[Any]:
    """Rebuild a typed ``NativeCallResult`` from the agent wire dict."""
    if not isinstance(payload, dict):
        raise RuntimeTransportError(f"remote op {op!r} returned malformed result")
    try:
        state = NativeCallState(str(payload.get("state", "failure")))
    except ValueError as exc:
        raise RuntimeTransportError(f"remote op {op!r} returned unknown state") from exc
    value: Any = payload.get("value")
    if state is NativeCallState.SUCCESS and value is not None:
        if op == "probe":
            value = _probe_value(value)
        elif op == "connect":
            value = _session_value(value)
    return NativeCallResult(
        state=state,
        call_id=str(payload.get("call_id", "")),
        value=value,
        failure=_failure(payload.get("failure")),
        dispatched=bool(payload.get("dispatched", False)),
    )


class _WriterLane:
    """Provider-side uncertainty quarantine, with native identity bound by evidence."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._quarantined = False
        self._quarantined_call_id: str | None = None
        self._quarantine_reason: str = ""

    def check(self, *, stage: str) -> None:
        with self._lock:
            quarantined = self._quarantined
            call_id = self._quarantined_call_id
        if quarantined:
            raise RuntimeUncertainError(
                f"A prior mutation ({call_id or 'native call identity unknown'}) must be "
                f"reconciled before another mutation ({stage})."
            )

    def quarantine(self, call_id: str | None, reason: str) -> None:
        with self._lock:
            if not self._quarantined:
                self._quarantined = True
                self._quarantined_call_id = call_id
                self._quarantine_reason = reason

    def bind_native_call(self, call_id: str) -> None:
        """Bind a transport-loss fence only after authenticated runtime observation."""
        with self._lock:
            if not self._quarantined or self._quarantined_call_id not in (None, call_id):
                raise RuntimeOpRefusedError("Native recovery identity does not match quarantine.")
            self._quarantined_call_id = call_id

    def clear(self, call_id: str) -> None:
        with self._lock:
            if self._quarantined_call_id == call_id:
                self._quarantined = False
                self._quarantined_call_id = None
                self._quarantine_reason = ""

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "quarantined": self._quarantined,
                "quarantined_call_id": self._quarantined_call_id,
                "reason": self._quarantine_reason,
            }


class RemoteSessionAdapter(SolidWorksRuntimePort):
    """Provider-side adapter: port contract over a ``SolidWorksRuntimeTransport``."""

    def __init__(
        self,
        transport: Any,
        *,
        workstation_session_id: str,
        expected_generation: str | None = None,
        default_deadline_ms: int = DEFAULT_DEADLINE_MS,
    ) -> None:
        from cdt_solidworks.runtime.transport import SolidWorksRuntimeTransport

        if not isinstance(transport, SolidWorksRuntimeTransport):
            raise TypeError("RemoteSessionAdapter requires a SolidWorksRuntimeTransport")
        pinned = str(workstation_session_id or "").strip()
        if not pinned:
            raise ValueError("RemoteSessionAdapter requires the workstation session id")
        self._transport = transport
        self._workstation_session_id = pinned
        self._expected_generation = expected_generation
        self._default_deadline_ms = check_deadline(default_deadline_ms)
        self._lane = _WriterLane()
        self._snapshot: dict[str, Any] = {
            "connected": False,
            "ownership": None,
            "uncertain_call_id": None,
        }

    @property
    def expected_generation(self) -> str | None:
        return self._expected_generation

    def pin_generation(self, generation: str) -> None:
        value = str(generation or "").strip()
        if not value:
            raise ValueError("generation pin must be non-empty")
        self._expected_generation = value

    # -- port surface: snapshots are last-observed, never live COM --

    @property
    def api(self) -> Any:
        raise RuntimeUnavailableError(
            "remote adapter has no in-process COM API; native execution lives "
            "in the workstation agent process"
        )

    @property
    def session_id(self) -> str:
        return self._workstation_session_id

    @property
    def connected(self) -> bool:
        return bool(self._snapshot["connected"])

    @property
    def ownership(self) -> Any | None:
        return self._snapshot["ownership"]

    @property
    def uncertain_call_id(self) -> str | None:
        quarantined = self._lane.status()["quarantined_call_id"]
        return quarantined if quarantined else self._snapshot["uncertain_call_id"]

    @property
    def writer_lane(self) -> _WriterLane:
        """Return the provider-side writer lane (single authority, no duplicate)."""
        return self._lane

    # -- dispatch core: fencing + uncertainty, zero CAD semantics --

    def _call(self, op: str, *args: Any, **kwargs: Any) -> Any:
        if op not in ALLOWED_OPS:
            raise RuntimeOpRefusedError(f"remote op refused (not in allowlist): {op!r}")
        if op in MUTATION_OPS:
            self._lane.check(stage=op)
        try:
            return self._transport.call(
                op,
                args,
                kwargs,
                deadline_ms=self._default_deadline_ms,
                expected_generation=self._expected_generation,
            )
        except RuntimeUncertainError as exc:
            if op in MUTATION_OPS:
                self._lane.quarantine(None, str(exc))
            raise

    def probe(self, *, version: int | None = None, timeout: float = 3.0) -> Any:
        payload = self._call("probe", version=version, timeout=timeout)
        return _result("probe", payload)

    def connect(
        self,
        *,
        policy: Any = None,
        version: int | None = None,
        visible: bool = True,
        timeout: float = 10.0,
    ) -> Any:
        policy_value = getattr(policy, "value", policy)
        payload = self._call(
            "connect",
            policy=str(policy_value) if policy_value is not None else None,
            version=version,
            visible=visible,
            timeout=timeout,
        )
        result = _result("connect", payload)
        if result.state is NativeCallState.SUCCESS and result.value is not None:
            self._snapshot["connected"] = True
            self._snapshot["ownership"] = result.value.ownership
        elif result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH:
            self._lane.quarantine(result.call_id, "connect uncertain after dispatch")
            self._snapshot["uncertain_call_id"] = result.call_id
        return result

    def execute(
        self,
        operation: Any,
        *,
        stage: str,
        timeout: float,
        mutation: bool = False,
        recovery_identity: tuple[object, ...] | None = None,
        recovery_stage: str | None = None,
        recovery_verifier: Any | None = None,
    ) -> Any:
        raise RuntimeOpRefusedError(
            f"remote execute refused for stage {stage!r}: arbitrary callables cannot "
            "cross the runtime boundary; service-level remote dispatch is W5/W6."
        )

    def reconcile(
        self,
        call_id: str,
        verifier: Any | None = None,
        *,
        stage: str,
        timeout: float,
        identity: tuple[object, ...] | None = None,
    ) -> Any:
        if verifier is not None:
            raise RuntimeOpRefusedError(
                "remote reconcile refuses caller-supplied verifiers; recovery executes "
                "agent-side from the original mutation plan."
            )
        lane = self._lane.status()
        if lane["quarantined"]:
            if lane["quarantined_call_id"] is None:
                snapshot = self.runtime_status()
                if (
                    not call_id
                    or snapshot.get("session_id") != self._workstation_session_id
                    or snapshot.get("uncertain_call_id") != call_id
                ):
                    raise RuntimeOpRefusedError(
                        "Transport loss requires an observed uncertain native call "
                        "in the pinned workstation session before reconciliation."
                    )
                self._lane.bind_native_call(call_id)
            elif lane["quarantined_call_id"] != call_id:
                raise RuntimeOpRefusedError(
                    "Reconciliation call identity does not match the quarantined mutation."
                )
        payload = self._call(
            "reconcile", call_id, stage=stage, timeout=timeout, identity=identity
        )
        result = _result("reconcile", payload)
        if result.state is NativeCallState.SUCCESS:
            if result.call_id != call_id:
                raise RuntimeTransportError("Reconciliation receipt has a different native call id.")
            self._lane.clear(call_id)
            self._snapshot["uncertain_call_id"] = None
        return result

    def disconnect(self, *, timeout: float = 5.0) -> Any:
        payload = self._call("disconnect", timeout=timeout)
        result = _result("disconnect", payload)
        if result.state is NativeCallState.SUCCESS:
            self._snapshot["connected"] = False
            self._snapshot["ownership"] = None
        elif result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH:
            self._lane.quarantine(result.call_id, "disconnect uncertain after dispatch")
            self._snapshot["uncertain_call_id"] = result.call_id
        return result

    def close_dispatcher(self, *, timeout: float = 2.0) -> bool:
        raise RuntimeOpRefusedError(
            "remote close_dispatcher refused: the dispatcher lifecycle belongs to "
            "the workstation host."
        )

    def runtime_status(self) -> dict[str, Any]:
        result = self._call("runtime_status")
        return result if isinstance(result, dict) else {"detail": result}

    def health(self) -> dict[str, Any]:
        result = self._transport.health()
        base: dict[str, Any] = {
            "transport": "remote",
            "reachable": True,
            "session_id": self._workstation_session_id,
        }
        if isinstance(result, dict):
            base["agent"] = result
        base["writer_lane"] = self._lane.status()
        return base
