"""Safe native error normalization."""

from __future__ import annotations

from typing import Mapping

from .models import NativeFailure


class NativeRuntimeError(RuntimeError):
    """Typed error whose public message is safe to return to higher layers."""

    def __init__(
        self,
        code: str,
        stage: str,
        message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.stage = stage
        self.public_message = message
        self.retryable = retryable
        self.details = dict(details or {})
        super().__init__(message)


def failure_from_exception(exc: Exception, stage: str) -> NativeFailure:
    if isinstance(exc, NativeRuntimeError):
        return NativeFailure(
            code=exc.code,
            stage=exc.stage,
            message=exc.public_message,
            retryable=exc.retryable,
            details=exc.details,
        )

    return NativeFailure(
        code="native_call_failed",
        stage=stage,
        message=f"Native operation failed ({type(exc).__name__}).",
        retryable=False,
    )
