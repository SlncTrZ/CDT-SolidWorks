from __future__ import annotations

from types import SimpleNamespace

import pytest

from cdt_solidworks.cli import _shutdown_runtime
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession


_NATIVE_RUNTIME_BLOCKED = pytest.StashKey[str]()


@pytest.fixture
def native_solidworks_session(request):
    """Bound native setup/cleanup; never replay a completion-unknown connect."""
    blocked = request.session.stash.get(_NATIVE_RUNTIME_BLOCKED, None)
    if blocked is not None:
        pytest.skip(f"native runtime blocked until reviewed: {blocked}")

    session = SolidWorksSession()
    failed = True
    try:
        try:
            connected = session.connect(
                policy=AttachPolicy.ATTACH_OR_START,
                version=2024,
                visible=True,
                timeout=30.0,
            )
        except BaseException:
            request.session.stash[_NATIVE_RUNTIME_BLOCKED] = "connect raised without a verified outcome"
            raise
        if connected.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH:
            request.session.stash[_NATIVE_RUNTIME_BLOCKED] = f"connect completion unknown: {connected.call_id}"
        assert connected.state is NativeCallState.SUCCESS, connected.failure
        assert connected.value is not None
        assert connected.value.version_year == 2024
        failed = False
        yield session
    except BaseException:
        failed = True
        raise
    finally:
        if session.uncertain_call_id is not None:
            request.session.stash[_NATIVE_RUNTIME_BLOCKED] = f"native completion unknown: {session.uncertain_call_id}"
        try:
            _shutdown_runtime(SimpleNamespace(session=session), suppress_errors=False)
        except Exception:
            request.session.stash[_NATIVE_RUNTIME_BLOCKED] = "native cleanup did not verify a clean shutdown"
            if not failed:
                raise
