"""Bounded async operation helper with explicit uncertain-state semantics."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from threading import Event
from typing import TypeVar

import anyio

from cdt_solidworks.platform.errors import ProviderTimeoutError


T = TypeVar("T")


class DispatchState:
    """Thread-safe marker for the point where an operation reaches its dependency."""

    def __init__(self) -> None:
        self._dispatched = Event()

    @property
    def dispatched(self) -> bool:
        return self._dispatched.is_set()

    def mark_dispatched(self) -> None:
        self._dispatched.set()


async def run_bounded(
    operation: Callable[[], Awaitable[T]],
    *,
    timeout_seconds: float,
    dispatch_state: DispatchState,
) -> T:
    """Run a bounded operation while preserving post-dispatch uncertainty.

    The caller marks `dispatch_state` immediately before crossing the native or
    external dependency boundary. A later timeout is then `uncertain`; a timeout
    before that boundary is `not_started` with respect to the dependency side effect.
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    try:
        with anyio.fail_after(timeout_seconds):
            return await operation()
    except TimeoutError as exc:
        state = "uncertain" if dispatch_state.dispatched else "not_started"
        raise ProviderTimeoutError(state=state) from exc
