"""Typed native runtime results for SolidWorks COM execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, Mapping, TypeVar


T = TypeVar("T")


class NativeCallState(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT_BEFORE_DISPATCH = "timeout_before_dispatch"
    UNCERTAIN_AFTER_DISPATCH = "uncertain_after_dispatch"


class ApplicationOwnership(str, Enum):
    USER_OWNED = "user_owned"
    PROVIDER_OWNED = "provider_owned"


@dataclass(frozen=True)
class NativeFailure:
    code: str
    stage: str
    message: str
    retryable: bool = False
    details: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class UncertainState:
    call_id: str
    stage: str
    reason: str


@dataclass(frozen=True)
class NativeCallResult(Generic[T]):
    state: NativeCallState
    call_id: str
    value: T | None = None
    failure: NativeFailure | None = None
    dispatched: bool = False

    @classmethod
    def success(cls, value: T, *, call_id: str, dispatched: bool = True) -> "NativeCallResult[T]":
        return cls(
            state=NativeCallState.SUCCESS,
            call_id=call_id,
            value=value,
            dispatched=dispatched,
        )

    @classmethod
    def failed(
        cls,
        failure: NativeFailure,
        *,
        call_id: str,
        dispatched: bool,
    ) -> "NativeCallResult[T]":
        return cls(
            state=NativeCallState.FAILURE,
            call_id=call_id,
            failure=failure,
            dispatched=dispatched,
        )


@dataclass(frozen=True)
class ApplicationProbe:
    prog_id: str
    registered: bool
    running: bool
    revision: str | None
    version_year: int | None


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    ownership: ApplicationOwnership
    prog_id: str
    revision: str
    version_year: int | None
