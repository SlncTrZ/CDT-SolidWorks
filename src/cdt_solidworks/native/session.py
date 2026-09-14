"""SolidWorks application session ownership and execution boundary."""

from __future__ import annotations

from enum import Enum
import uuid
from typing import Any, Callable, TypeVar

from .api import WindowsComApi
from .dispatcher import SerializedNativeDispatcher
from .errors import NativeRuntimeError
from .models import ApplicationOwnership, ApplicationProbe, NativeCallResult, NativeCallState, NativeFailure, SessionInfo


T = TypeVar("T")


class AttachPolicy(str, Enum):
    ATTACH_ONLY = "attach_only"
    ATTACH_OR_START = "attach_or_start"
    START_NEW = "start_new"


class SolidWorksSession:
    """Owns one serialized COM apartment and one SolidWorks application binding."""

    def __init__(self, *, api: Any | None = None) -> None:
        self.api = api if api is not None else WindowsComApi()
        self.session_id = uuid.uuid4().hex
        self._application: Any | None = None
        self._ownership: ApplicationOwnership | None = None
        self._dispatcher = SerializedNativeDispatcher(
            initializer=self.api.initialize_thread,
            finalizer=self.api.uninitialize_thread,
        )

    @property
    def connected(self) -> bool:
        return self._application is not None

    @property
    def ownership(self) -> ApplicationOwnership | None:
        return self._ownership

    @staticmethod
    def prog_id_for_version(version: int | None) -> str:
        if version is None:
            return "SldWorks.Application"
        year = int(version)
        if year < 2010 or year > 2035:
            raise ValueError("SolidWorks version must be a supported year between 2010 and 2035.")
        return f"SldWorks.Application.{(year - 2000) + 8}"

    @staticmethod
    def year_from_revision(revision: str) -> int | None:
        try:
            major = int(str(revision).split(".", 1)[0])
        except (TypeError, ValueError):
            return None
        year = major - 8 + 2000
        return year if 2010 <= year <= 2100 else None

    def probe(
        self,
        *,
        version: int | None = None,
        timeout: float = 3.0,
    ) -> NativeCallResult[ApplicationProbe]:
        prog_id = self.prog_id_for_version(version)

        def operation() -> ApplicationProbe:
            registered = bool(self.api.prog_id_registered(prog_id))
            if self._application is not None:
                revision = self.api.revision_number(self._application)
                return ApplicationProbe(
                    prog_id=prog_id,
                    registered=registered,
                    running=True,
                    revision=revision,
                    version_year=self.year_from_revision(revision),
                )
            try:
                app = self.api.attach_application(prog_id)
            except Exception:
                return ApplicationProbe(
                    prog_id=prog_id,
                    registered=registered,
                    running=False,
                    revision=None,
                    version_year=None,
                )
            revision = self.api.revision_number(app)
            return ApplicationProbe(
                prog_id=prog_id,
                registered=registered,
                running=True,
                revision=revision,
                version_year=self.year_from_revision(revision),
            )

        return self._dispatcher.run(operation, stage="probe", timeout=timeout, mutation=False)

    def connect(
        self,
        *,
        policy: AttachPolicy = AttachPolicy.ATTACH_OR_START,
        version: int | None = None,
        visible: bool = True,
        timeout: float = 10.0,
    ) -> NativeCallResult[SessionInfo]:
        prog_id = self.prog_id_for_version(version)

        def operation() -> SessionInfo:
            if self._application is not None and self._ownership is not None:
                revision = self.api.revision_number(self._application)
                return SessionInfo(
                    session_id=self.session_id,
                    ownership=self._ownership,
                    prog_id=prog_id,
                    revision=revision,
                    version_year=self.year_from_revision(revision),
                )

            app = None
            ownership = None
            attach_error = None
            if policy is not AttachPolicy.START_NEW:
                try:
                    app = self.api.attach_application(prog_id)
                    ownership = ApplicationOwnership.USER_OWNED
                except Exception as exc:
                    attach_error = exc
                    if policy is AttachPolicy.ATTACH_ONLY:
                        raise NativeRuntimeError(
                            "solidworks_attach_failed",
                            "connect",
                            "No running SolidWorks instance could be attached.",
                        ) from exc

            if app is None:
                try:
                    app = self.api.start_application(prog_id)
                    ownership = ApplicationOwnership.PROVIDER_OWNED
                    self.api.set_visible(app, visible)
                except Exception as exc:
                    raise NativeRuntimeError(
                        "solidworks_start_failed",
                        "connect",
                        "SolidWorks could not be started by the provider.",
                        details={"attach_attempted": attach_error is not None},
                    ) from exc

            try:
                revision = self.api.revision_number(app)
            except Exception as exc:
                if ownership is ApplicationOwnership.PROVIDER_OWNED:
                    try:
                        self.api.exit_application(app)
                    except Exception:
                        pass
                raise NativeRuntimeError(
                    "solidworks_not_ready",
                    "connect",
                    "SolidWorks started or attached but did not become API-ready.",
                ) from exc

            self._application = app
            self._ownership = ownership
            return SessionInfo(
                session_id=self.session_id,
                ownership=ownership,
                prog_id=prog_id,
                revision=revision,
                version_year=self.year_from_revision(revision),
            )

        return self._dispatcher.run(operation, stage="connect", timeout=timeout, mutation=True)

    def execute(
        self,
        operation: Callable[[Any], T],
        *,
        stage: str,
        timeout: float,
        mutation: bool = False,
    ) -> NativeCallResult[T]:
        if self._application is None:
            return NativeCallResult.failed(
                NativeFailure(
                    "session_not_connected",
                    stage,
                    "SolidWorks session is not connected.",
                ),
                call_id=uuid.uuid4().hex,
                dispatched=False,
            )
        return self._dispatcher.run(
            lambda: operation(self._application),
            stage=stage,
            timeout=timeout,
            mutation=mutation,
        )

    def reconcile(
        self,
        call_id: str,
        verifier: Callable[[Any], T],
        *,
        stage: str,
        timeout: float,
    ) -> NativeCallResult[T]:
        if self._application is None:
            return NativeCallResult.failed(
                NativeFailure("session_not_connected", stage, "SolidWorks session is not connected."),
                call_id=uuid.uuid4().hex,
                dispatched=False,
            )
        return self._dispatcher.reconcile(
            call_id,
            lambda: verifier(self._application),
            stage=stage,
            timeout=timeout,
        )

    def disconnect(self, *, timeout: float = 5.0) -> NativeCallResult[bool]:
        if self._application is None:
            return NativeCallResult.success(False, call_id=uuid.uuid4().hex, dispatched=False)

        def operation() -> bool:
            app = self._application
            ownership = self._ownership
            if app is not None and ownership is ApplicationOwnership.PROVIDER_OWNED:
                self.api.exit_application(app)
            self._application = None
            self._ownership = None
            return True

        return self._dispatcher.run(operation, stage="disconnect", timeout=timeout, mutation=True)

    def close_dispatcher(self, *, timeout: float = 2.0) -> bool:
        return self._dispatcher.close(timeout=timeout)

    @property
    def uncertain_call_id(self) -> str | None:
        return self._dispatcher.quarantined_call_id
