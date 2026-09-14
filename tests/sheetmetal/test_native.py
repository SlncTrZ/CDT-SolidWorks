from __future__ import annotations

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.errors import NativeRuntimeError
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.sheetmetal.native import SheetMetalNativeAdapter, _SW_HEM_POSITION


class FakePythonCom:
    DISPATCH_METHOD = 1
    VT_I4 = 3
    VT_VARIANT = 12


class FakeApi:
    _pythoncom = FakePythonCom()

    @staticmethod
    def _member(obj, name, *args):
        member = getattr(obj, name)
        return member(*args) if callable(member) else member

    @staticmethod
    def dispatch_array(values):
        return ("dispatch-array", tuple(values))


class FakeSession:
    def __init__(self) -> None:
        self.api = FakeApi()
        self.calls = 0

    def execute(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("validation failure must not dispatch to session")


class Feature:
    def __init__(self, raw):
        self.raw = raw

    def IsSuppressed2(self, config_opt, config_names):
        assert config_opt == 1
        assert config_names is None
        return self.raw


def test_target_hem_position_values_match_solidworks_2024_type_library() -> None:
    assert _SW_HEM_POSITION == {"inside": 0, "outside": 1}


def test_flat_pattern_suppression_readback_handles_com_array_shape() -> None:
    adapter = SheetMetalNativeAdapter(FakeSession(), path_policy=None)
    assert adapter._is_suppressed(Feature((True,))) is True
    assert adapter._is_suppressed(Feature((False,))) is False


def test_flat_pattern_suppression_readback_handles_scalar_shape() -> None:
    adapter = SheetMetalNativeAdapter(FakeSession(), path_policy=None)
    assert adapter._is_suppressed(Feature(True)) is True
    assert adapter._is_suppressed(Feature(False)) is False


def test_edge_flange_rejects_raw_edge_identity_before_dispatch(tmp_path) -> None:
    source = tmp_path / "part.sldprt"
    source.write_bytes(b"fixture")
    session = FakeSession()
    adapter = SheetMetalNativeAdapter(session, path_policy=DocumentPathPolicy((tmp_path,)))

    result = adapter.add_edge_flange(
        source,
        edge_selector="Edge<123>",
        length_mm=20.0,
        angle_deg=90.0,
    )

    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert result.failure is not None
    assert result.failure.code == "cad_validation_error"
    assert session.calls == 0


def test_sketched_bend_rejects_nonfinite_line_before_dispatch(tmp_path) -> None:
    source = tmp_path / "part.sldprt"
    source.write_bytes(b"fixture")
    session = FakeSession()
    adapter = SheetMetalNativeAdapter(session, path_policy=DocumentPathPolicy((tmp_path,)))

    result = adapter.add_sketched_bend(
        source,
        line_x_mm=float("nan"),
        angle_deg=90.0,
        bend_radius_mm=1.5,
    )

    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert result.failure is not None
    assert result.failure.code == "cad_validation_error"
    assert session.calls == 0


def test_unfold_rejects_empty_bend_identity_before_dispatch(tmp_path) -> None:
    source = tmp_path / "part.sldprt"
    source.write_bytes(b"fixture")
    session = FakeSession()
    adapter = SheetMetalNativeAdapter(session, path_policy=DocumentPathPolicy((tmp_path,)))

    result = adapter.unfold_bend(source, bend_feature_name="", fixed_x_mm=25.0)

    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert result.failure is not None
    assert result.failure.code == "cad_validation_error"
    assert session.calls == 0


def test_fold_rejects_nonfinite_fixed_x_before_dispatch(tmp_path) -> None:
    source = tmp_path / "part.sldprt"
    source.write_bytes(b"fixture")
    session = FakeSession()
    adapter = SheetMetalNativeAdapter(session, path_policy=DocumentPathPolicy((tmp_path,)))

    result = adapter.fold_bend(
        source,
        unfold_feature_name="Unfold1",
        bend_feature_name="SketchBend1",
        fixed_x_mm=float("nan"),
    )

    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert result.failure is not None
    assert result.failure.code == "cad_validation_error"
    assert session.calls == 0


def test_hem_rejects_raw_edge_identity_before_dispatch(tmp_path) -> None:
    source = tmp_path / "part.sldprt"
    source.write_bytes(b"fixture")
    session = FakeSession()
    adapter = SheetMetalNativeAdapter(session, path_policy=DocumentPathPolicy((tmp_path,)))

    result = adapter.add_hem(
        source,
        edge_selector="Edge<123>",
        length_mm=12.0,
        gap_mm=0.5,
    )

    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert result.failure is not None
    assert result.failure.code == "cad_validation_error"
    assert session.calls == 0


class Vertex:
    def __init__(self, point):
        self._point = point

    def GetPoint(self):
        return self._point


class Edge:
    def __init__(self, start, end):
        self._start = Vertex(start)
        self._end = Vertex(end)

    def GetStartVertex(self):
        return self._start

    def GetEndVertex(self):
        return self._end


class Body:
    def __init__(self, edges):
        self._edges = edges

    def GetEdges(self):
        return self._edges


class FakeEdgeFlangeOle:
    def __init__(self, error_code: int = 0) -> None:
        self.error_code = error_code
        self.calls = []

    def GetIDsOfNames(self, name):
        assert name == "AddEdges"
        return 37

    def InvokeTypes(self, *args):
        self.calls.append(args)
        return self.error_code


class FakeEdgeFlangeDefinition:
    def __init__(self, error_code: int = 0) -> None:
        self._oleobj_ = FakeEdgeFlangeOle(error_code)


def test_edge_flange_add_edges_uses_raw_idispatch_contract() -> None:
    adapter = SheetMetalNativeAdapter(FakeSession(), path_policy=None)
    definition = FakeEdgeFlangeDefinition()
    edge = object()
    sketch = object()

    adapter._add_edge_flange_edges(definition, edge, sketch)

    assert len(definition._oleobj_.calls) == 1
    call = definition._oleobj_.calls[0]
    assert call[0] == 37
    assert call[2] == FakePythonCom.DISPATCH_METHOD
    assert call[-2] == ("dispatch-array", (edge,))
    assert call[-1] == ("dispatch-array", (sketch,))


def test_edge_flange_add_edges_surfaces_solidworks_error_code() -> None:
    adapter = SheetMetalNativeAdapter(FakeSession(), path_policy=None)
    definition = FakeEdgeFlangeDefinition(error_code=5)

    try:
        adapter._add_edge_flange_edges(definition, object(), object())
    except NativeRuntimeError as exc:
        assert exc.code == "cad_mutation_failed"
        assert exc.details == {"edge_flange_error": 5}
    else:
        raise AssertionError("expected NativeRuntimeError")


def test_boundary_edge_selector_resolves_top_bbox_edge_deterministically() -> None:
    edges = (
        Edge((0.0, 0.0, 0.002), (0.1, 0.0, 0.002)),
        Edge((0.1, 0.0, 0.002), (0.1, 0.06, 0.002)),
        Edge((0.1, 0.06, 0.002), (0.0, 0.06, 0.002)),
        Edge((0.0, 0.06, 0.002), (0.0, 0.0, 0.002)),
        Edge((0.0, 0.0, 0.0), (0.1, 0.0, 0.0)),
    )
    adapter = SheetMetalNativeAdapter(FakeSession(), path_policy=None)

    assert adapter._resolve_boundary_edge(Body(edges), "bbox:+x") is edges[1]
    assert adapter._resolve_boundary_edge(Body(edges), "bbox:-y") is edges[0]
