from __future__ import annotations

import os
from pathlib import Path

import pytest

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession
from cdt_solidworks.weldment.native import WeldmentNativeAdapter

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native SOLIDWORKS on Windows")


def test_native_weldment_frame_cut_list_fixture(tmp_path) -> None:
    raw_profile = os.environ.get("CDT_SW_WELDMENT_PROFILE", "").strip()
    if not raw_profile:
        pytest.skip("CDT_SW_WELDMENT_PROFILE must point to an installed .sldlfp profile")
    profile = Path(raw_profile).expanduser().resolve(strict=False)
    if not profile.is_file() or profile.suffix.lower() != ".sldlfp":
        pytest.skip("CDT_SW_WELDMENT_PROFILE is not an existing .sldlfp file")

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
        adapter = WeldmentNativeAdapter(
            session,
            path_policy=policy,
            profile_roots=(profile.parent,),
        )
        path = tmp_path / "weldment-frame.sldprt"

        sketch = core.create_rectangle_sketch(path, width_mm=500.0, height_mm=300.0)
        assert sketch.state is NativeCallState.SUCCESS, sketch.failure
        assert sketch.value is not None
        sketch_name = str(sketch.value["sketch_name"])

        created = adapter.create_structural_member(
            path,
            sketch_feature_name=sketch_name,
            profile_path=profile,
            apply_corner_treatment=True,
            corner_treatment_type=0,
        )
        assert created.state is NativeCallState.SUCCESS, created.failure
        assert created.value is not None
        assert created.value["structural_member_count"] >= 1
        assert created.value["cut_list_items"]
        assert all(item["quantity"] > 0 for item in created.value["cut_list_items"])

        reopened = adapter.inspect(path)
        assert reopened.state is NativeCallState.SUCCESS, reopened.failure
        assert reopened.value is not None
        assert reopened.value["has_weldment"] is True
        assert reopened.value["cut_list_items"]
    finally:
        session.disconnect(timeout=10.0)
        session.close_dispatcher(timeout=5.0)
