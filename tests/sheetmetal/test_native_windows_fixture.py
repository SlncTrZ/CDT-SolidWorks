from __future__ import annotations

import os

import pytest

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession
from cdt_solidworks.sheetmetal.native import SheetMetalNativeAdapter

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native SOLIDWORKS on Windows")


def test_native_sheet_metal_flatten_reopen_fixture(tmp_path) -> None:
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
        adapter = SheetMetalNativeAdapter(session, path_policy=policy)
        path = tmp_path / "sheet-metal-bracket.sldprt"

        created = adapter.create_base_flange(
            path,
            width_mm=100.0,
            height_mm=60.0,
            thickness_mm=2.0,
            bend_radius_mm=1.5,
        )
        assert created.state is NativeCallState.SUCCESS, created.failure

        state = adapter.inspect(path)
        assert state.state is NativeCallState.SUCCESS, state.failure
        assert state.value is not None
        assert state.value["is_sheet_metal"] is True
        assert state.value["thickness_mm"] == pytest.approx(2.0, abs=1e-6)
        assert state.value["bend_radius_mm"] == pytest.approx(1.5, abs=1e-6)

        hem = adapter.add_hem(
            path,
            edge_selector="bbox:+x",
            length_mm=12.0,
            gap_mm=0.5,
            position="outside",
        )
        assert hem.state is NativeCallState.SUCCESS, hem.failure
        assert hem.value is not None
        assert hem.value["length_mm"] == pytest.approx(12.0, abs=1e-6)
        assert hem.value["gap_mm"] == pytest.approx(0.5, abs=1e-6)

        reopened_hem = adapter.inspect(path)
        assert reopened_hem.state is NativeCallState.SUCCESS, reopened_hem.failure
        assert reopened_hem.value is not None and reopened_hem.value["is_sheet_metal"] is True

        flat = adapter.set_flattened(path, flattened=True)
        assert flat.state is NativeCallState.SUCCESS, flat.failure
        assert flat.value is not None and flat.value["flattened"] is True
        assert flat.value["flat_pattern_name"]

        reopened_flat = adapter.inspect(path)
        assert reopened_flat.state is NativeCallState.SUCCESS, reopened_flat.failure
        assert reopened_flat.value is not None and reopened_flat.value["flattened"] is True

        formed = adapter.set_flattened(path, flattened=False)
        assert formed.state is NativeCallState.SUCCESS, formed.failure
        assert formed.value is not None and formed.value["flattened"] is False

        reopened_formed = adapter.inspect(path)
        assert reopened_formed.state is NativeCallState.SUCCESS, reopened_formed.failure
        assert reopened_formed.value is not None and reopened_formed.value["flattened"] is False
    finally:
        session.disconnect(timeout=10.0)
        session.close_dispatcher(timeout=5.0)
