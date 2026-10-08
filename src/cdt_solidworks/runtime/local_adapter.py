"""Local single-host runtime adapter — W1 seam over existing session code.

Wraps exactly one existing ``SolidWorksSession``. Every method is 1:1
delegation: no COM logic is moved, copied, or reinterpreted here. The STA
dispatcher, session identity, ownership, quarantine and rebuild/read-back
semantics stay inside the wrapped session.
"""

from __future__ import annotations

from typing import Any

from cdt_solidworks.native.session import SolidWorksSession
from cdt_solidworks.runtime.port import SolidWorksRuntimePort


class LocalSessionAdapter(SolidWorksRuntimePort):
    """Single-host adapter: 1:1 delegation over the current session stack."""

    def __init__(self, session: SolidWorksSession) -> None:
        if not isinstance(session, SolidWorksSession):
            raise TypeError("LocalSessionAdapter requires a SolidWorksSession")
        self._session = session

    @property
    def session(self) -> SolidWorksSession:
        """Return the wrapped execution session (controlled escape hatch)."""
        return self._session

    @property
    def api(self) -> Any:
        return self._session.api

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def connected(self) -> bool:
        return self._session.connected

    @property
    def ownership(self) -> Any | None:
        return self._session.ownership

    @property
    def uncertain_call_id(self) -> str | None:
        return self._session.uncertain_call_id

    def probe(self, *, version: int | None = None, timeout: float = 3.0) -> Any:
        return self._session.probe(version=version, timeout=timeout)

    def connect(
        self,
        *,
        policy: Any = None,
        version: int | None = None,
        visible: bool = True,
        timeout: float = 10.0,
    ) -> Any:
        if policy is None:
            return self._session.connect(version=version, visible=visible, timeout=timeout)
        if isinstance(policy, str):
            from cdt_solidworks.native.session import AttachPolicy

            policy = AttachPolicy(policy)
        return self._session.connect(
            policy=policy, version=version, visible=visible, timeout=timeout
        )

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
        return self._session.execute(
            operation,
            stage=stage,
            timeout=timeout,
            mutation=mutation,
            recovery_identity=recovery_identity,
            recovery_stage=recovery_stage,
            recovery_verifier=recovery_verifier,
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
        return self._session.reconcile(
            call_id, verifier, stage=stage, timeout=timeout, identity=identity
        )

    def disconnect(self, *, timeout: float = 5.0) -> Any:
        return self._session.disconnect(timeout=timeout)

    def close_dispatcher(self, *, timeout: float = 2.0) -> bool:
        return self._session.close_dispatcher(timeout=timeout)

    def session_identity(self) -> dict[str, str]:
        """Return the stable session identity (transport-friendly)."""
        return {"session_id": self._session.session_id}

    def runtime_status(self) -> dict[str, Any]:        return {
            "transport": "local",
            "session_id": self._session.session_id,
            "connected": self._session.connected,
            "ownership": str(self._session.ownership) if self._session.ownership else None,
            "uncertain_call_id": self._session.uncertain_call_id,
        }

    def health(self) -> dict[str, Any]:
        return {**self.runtime_status(), "reachable": True}
