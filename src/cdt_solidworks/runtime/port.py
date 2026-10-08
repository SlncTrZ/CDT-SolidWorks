"""SolidWorks runtime port — provider/execution seam (W1).

Structural port only: declares the SAME surface ``SolidWorksSession``
exposes today (``native/session.py``) plus two read-only identity methods
(``runtime_status``/``health``). Nothing is redefined — implementers satisfy
the session contract by 1:1 delegation (see ``LocalSessionAdapter``), which
keeps dispatcher ownership, rebuild/read-back, timeout and quarantine
semantics untouched.

- ``api`` is the controlled escape hatch services already consume.
- ``session_id`` is the stable document-identity scope (topology/document
  guards bind to it; adapters must not mint a new one).
- ``runtime_status``/``health`` are read-only liveness without CAD mutation.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SolidWorksRuntimePort(Protocol):
    """Seam between the MCP provider layer and SolidWorks execution."""

    @property
    def api(self) -> Any:
        """Return the underlying COM API surface (controlled escape hatch)."""
        ...

    @property
    def session_id(self) -> str:
        """Return the stable session identity (never re-minted by adapters)."""
        ...

    @property
    def connected(self) -> bool:
        """Return whether an application binding is held."""
        ...

    @property
    def ownership(self) -> Any | None:
        """Return the current application ownership, if any."""
        ...

    @property
    def uncertain_call_id(self) -> str | None:
        """Return the quarantined uncertain call id, if any."""
        ...

    def probe(self, *, version: int | None = None, timeout: float = 3.0) -> Any:
        """Probe SolidWorks registration/running state without mutation."""
        ...

    def connect(
        self,
        *,
        policy: Any = None,
        version: int | None = None,
        visible: bool = True,
        timeout: float = 10.0,
    ) -> Any:
        """Attach to or start an explicit SolidWorks application session."""
        ...

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
        """Dispatch one serialized operation through the STA dispatcher."""
        ...

    def reconcile(
        self,
        call_id: str,
        verifier: Any | None = None,
        *,
        stage: str,
        timeout: float,
        identity: tuple[object, ...] | None = None,
    ) -> Any:
        """Reconcile one quarantined uncertain mutation."""
        ...

    def disconnect(self, *, timeout: float = 5.0) -> Any:
        """Disconnect the session; only provider-owned apps may be exited."""
        ...

    def close_dispatcher(self, *, timeout: float = 2.0) -> bool:
        """Close the underlying serialized dispatcher."""
        ...

    def runtime_status(self) -> dict[str, Any]:
        """Return backend status for this runtime (read-only)."""
        ...

    def session_identity(self) -> dict[str, str]:
        """Return the stable session identity in transport-friendly form."""
        ...

    def health(self) -> dict[str, Any]:
        """Return read-only liveness without CAD mutation."""
        ...
