"""Safe provider error taxonomy and redaction boundary."""

from __future__ import annotations

from enum import Enum

from cdt_solidworks.platform.models import SafeErrorPayload


class ErrorCode(str, Enum):
    AUTHENTICATION_ERROR = "authentication_error"
    AUTHORIZATION_ERROR = "authorization_error"
    VALIDATION_ERROR = "validation_error"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INTERNAL_ERROR = "internal_error"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"


class ProviderError(Exception):
    """Expected provider error whose message is explicitly safe for clients."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        state: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable
        self.state = state


class ProviderTimeoutError(ProviderError):
    """Timeout that preserves whether a dispatched operation is uncertain."""

    def __init__(self, *, state: str) -> None:
        message = (
            "Operation timed out after dispatch; state requires reconciliation."
            if state == "uncertain"
            else "Operation timed out before dispatch completed."
        )
        super().__init__(ErrorCode.TIMEOUT, message, retryable=state != "uncertain", state=state)


class StartupConfigError(ProviderError):
    """Fail-closed startup configuration failure."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.PROVIDER_UNAVAILABLE, message, retryable=False)


def normalize_exception(exc: BaseException) -> SafeErrorPayload:
    """Convert exceptions to a client-safe structured payload without stack details."""

    if isinstance(exc, ProviderError):
        return SafeErrorPayload(
            code=exc.code.value,
            message=exc.safe_message,
            retryable=exc.retryable,
            state=exc.state,
        )

    return SafeErrorPayload(
        code=ErrorCode.INTERNAL_ERROR.value,
        message="Internal provider error.",
        retryable=False,
        state=None,
    )
