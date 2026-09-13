from __future__ import annotations

import os
from pathlib import Path

import pytest

from cdt_solidworks.body.models import CombineOperation
from cdt_solidworks.body.native import BodyNativeAdapter
from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native SOLIDWORKS on Windows")


def _connected_session() -> SolidWorksSession:
    session = SolidWorksSession()
    result = session.connect(policy=AttachPolicy.ATTACH_OR_START, version=2024, visible=True, timeout=30.0)
    assert result.state is NativeCallState.SUCCESS, result.failure
    assert result.value is not None
    assert result.value.version_year == 2024
    return session


def _create_overlap(core: CadCoreService, path: Path) -> None:
    created = core.create_rect_extrude(path, width_mm=40, height_mm=40, depth_mm=10)
    assert created.state is NativeCallState.SUCCESS, created.failure
    added = core.add_rect_extrude(
        path,
        width_mm=40,
        height_mm=40,
        depth_mm=10,
        center_x_mm=20,
        merge=False,
    )
    assert added.state is NativeCallState.SUCCESS, added.failure
    assert added.value is not None and added.value["body_count"] == 2


@pytest.mark.parametrize(
    ("operation", "needs_main"),
    [
        (CombineOperation.ADD, False),
        (CombineOperation.SUBTRACT, True),
        (CombineOperation.COMMON, False),
    ],
)
def test_native_multibody_boolean_fixture(tmp_path, operation: CombineOperation, needs_main: bool) -> None:
    session = _connected_session()
    try:
        policy = DocumentPathPolicy((tmp_path,))
        core = CadCoreService(session, path_policy=policy)
        adapter = BodyNativeAdapter(session, path_policy=policy)
        path = tmp_path / f"boolean-{operation.value}.sldprt"
        _create_overlap(core, path)

        before = adapter.inspect(path)
        assert before.state is NativeCallState.SUCCESS, before.failure
        assert before.value is not None
        names = tuple(item["name"] for item in before.value["solid_bodies"])
        assert len(names) == 2 and all(names)

        result = adapter.combine(
            path,
            operation=operation,
            body_names=names,
            main_body_name=names[0] if needs_main else None,
        )
        assert result.state is NativeCallState.SUCCESS, result.failure
        assert result.value is not None
        assert result.value["body_count_after"] == 1

        reopened = adapter.inspect(path)
        assert reopened.state is NativeCallState.SUCCESS, reopened.failure
        assert reopened.value is not None
        assert len(reopened.value["solid_bodies"]) == 1
    finally:
        session.disconnect(timeout=10.0)
        session.close_dispatcher(timeout=5.0)
