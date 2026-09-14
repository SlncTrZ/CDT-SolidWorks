from __future__ import annotations

import os

import pytest

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession
from cdt_solidworks.surface.native import SurfaceNativeAdapter

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native SOLIDWORKS on Windows")


def test_native_surface_to_solid_thicken_fixture(tmp_path) -> None:
    session = SolidWorksSession()
    connected = session.connect(
        policy=AttachPolicy.ATTACH_OR_START,
        version=2024,
        visible=True,
        timeout=30.0,
    )
    assert connected.state is NativeCallState.SUCCESS, connected.failure
    try:
        policy = DocumentPathPolicy((tmp_path,))
        core = CadCoreService(session, path_policy=policy)
        adapter = SurfaceNativeAdapter(session, path_policy=policy)
        path = tmp_path / "surface-thicken.sldprt"

        created = core.create_surface_extrude(path, line_length_mm=50, depth_mm=30)
        assert created.state is NativeCallState.SUCCESS, created.failure

        before = adapter.inspect(path)
        assert before.state is NativeCallState.SUCCESS, before.failure
        assert before.value is not None
        surfaces = before.value["surface_bodies"]
        assert len(surfaces) == 1

        offset = adapter.offset(
            path,
            surface_body_name=surfaces[0]["name"],
            distance_mm=3.0,
            reverse=False,
        )
        assert offset.state is NativeCallState.SUCCESS, offset.failure
        assert offset.value is not None
        assert offset.value["surface_body_count_after"] == 2

        reopened_offset = adapter.inspect(path)
        assert reopened_offset.state is NativeCallState.SUCCESS, reopened_offset.failure
        assert reopened_offset.value is not None
        assert len(reopened_offset.value["surface_bodies"]) == 2

        thickened = adapter.thicken(
            path,
            surface_body_name=reopened_offset.value["surface_bodies"][0]["name"],
            thickness_mm=2.0,
            merge=False,
        )
        assert thickened.state is NativeCallState.SUCCESS, thickened.failure
        assert thickened.value is not None
        assert thickened.value["solid_body_count_after"] >= 1

        reopened = adapter.inspect(path)
        assert reopened.state is NativeCallState.SUCCESS, reopened.failure
        assert reopened.value is not None
        assert len(reopened.value["solid_bodies"]) >= 1
    finally:
        session.disconnect(timeout=10.0)
        session.close_dispatcher(timeout=5.0)
