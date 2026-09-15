from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

from cdt_solidworks.document.models import DocumentContext, DocumentType
from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.document.service import DocumentService
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.topology.native import TopologyNativeAdapter

from .test_native import (
    FakeApi,
    FakeApp,
    FakeBody,
    FakeDocument,
    FakeEdge,
    FakeFace,
    FakeSession,
    FakeSurface,
    FakeVertex,
    fixture,
)


class CircleCurve:
    CircleParams = (0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.005)

    def IsLine(self): return False
    def IsCircle(self): return True
    def IsEllipse(self): return False


class ClosedCircleEdge(FakeEdge):
    def __init__(self, pid: bytes):
        self.pid = pid
        self.start = None
        self.end = None

    def GetCurve(self): return CircleCurve()


class CylinderSurface(FakeSurface):
    CylinderParams = (0.001, 0.002, 0.003, 0.0, 0.0, 2.0, 0.004)

    def IsPlane(self): return False
    def IsCylinder(self): return True
    def IsCone(self): return False
    def IsSphere(self): return False


class Component:
    def __init__(self, name: str, model: FakeDocument):
        self.name = name
        self.model = model
        self.suppressed = False

    def GetModelDoc2(self): return self.model
    def GetBodies3(self, body_type, options): return (self.model.body,) if body_type == 0 else ()
    def GetBodies2(self, body_type): return self.GetBodies3(body_type, 0)
    def GetCorrespondingEntity(self, entity): return entity


class AssemblyDocument(FakeDocument):
    def __init__(self, path: str, components: tuple[Component, ...]):
        super().__init__(path, components[0].model.body)
        self.doc_type = int(DocumentType.ASSEMBLY)
        self.components = components


class AssemblyApi(FakeApi):
    def components(self, doc, top_level_only): return doc.components
    def component_name(self, component): return component.name
    def component_suppressed(self, component): return component.suppressed


def test_geometry_inspection_is_typed_unit_explicit_and_finite():
    with tempfile.TemporaryDirectory() as tmp:
        service, context, doc = fixture(Path(tmp))
        body = doc.body
        body.GetType = lambda: 0
        body.GetBodyBox = lambda: (0.0, 0.0, 0.0, 0.01, 0.02, 0.03)
        body.GetMassProperties = lambda density: (0, 0, 0, 0.000006, 0.0022, 0, 0, 0, 0, 0, 0, 0)
        face = body.faces[0]
        face.GetSurface = lambda: CylinderSurface()
        face.GetArea = lambda: 0.0002
        face.GetUVBounds = lambda: (0.0, 1.0, -2.0, 2.0)
        edge = face.edges[0]
        edge.GetCurve = lambda: CircleCurve()
        edge.GetLength = lambda: 0.012

        query = service.query(context).value
        assert query is not None
        body_ref = next(item.reference for item in query.items if item.kind == "body")
        face_ref = next(item.reference for item in query.items if item.kind == "face")
        edge_ref = next(item.reference for item in query.items if item.kind == "edge")
        vertex_ref = next(item.reference for item in query.items if item.kind == "vertex")

        body_geo = service.inspect(context, body_ref).value.geometry
        assert body_geo.body_kind == "solid"
        assert body_geo.bbox_mm == (0.0, 0.0, 0.0, 10.0, 20.0, 30.0)
        assert math.isclose(body_geo.volume_mm3, 6000.0)
        assert body_geo.area_mm2 == 2200.0

        face_geo = service.inspect(context, face_ref).value.geometry
        assert face_geo.surface_type == "cylindrical"
        assert face_geo.origin_mm.x_mm == 1.0
        assert face_geo.axis.z == 1.0
        assert face_geo.radius_mm == 4.0
        assert face_geo.area_mm2 == 200.0
        assert face_geo.uv_bounds == (0.0, 1.0, -2.0, 2.0)

        edge_geo = service.inspect(context, edge_ref).value.geometry
        assert edge_geo.curve_type == "circle"
        assert edge_geo.radius_mm == 5.0
        assert edge_geo.length_mm == 12.0

        vertex_result = service.inspect(context, vertex_ref)
        assert vertex_result.state is NativeCallState.SUCCESS, vertex_result.failure
        vertex_geo = vertex_result.value.geometry
        assert vertex_geo.point_mm.x_mm in {0.0, 1000.0}


def test_unknown_geometry_stays_unknown_instead_of_default_zero():
    with tempfile.TemporaryDirectory() as tmp:
        service, context, _ = fixture(Path(tmp))
        face_ref = next(item.reference for item in service.query(context).value.items if item.kind == "face")
        geometry = service.inspect(context, face_ref).value.geometry
        assert geometry.surface_type == "planar"
        assert geometry.origin_mm is None
        assert geometry.normal is None
        assert geometry.radius_mm is None
        assert geometry.area_mm2 is None


def test_closed_circular_edge_with_zero_vertices_is_valid_and_inspectable():
    with tempfile.TemporaryDirectory() as tmp:
        service, context, doc = fixture(Path(tmp))
        edge = ClosedCircleEdge(b"circle")
        doc.body.faces = (FakeFace(b"fc", (edge,)),)
        result = service.query(context)
        assert result.state is NativeCallState.SUCCESS
        edge_item = next(item for item in result.value.items if item.kind == "edge")
        geometry = service.inspect(context, edge_item.reference).value.geometry
        assert geometry.closed is True
        assert geometry.start_mm is None and geometry.end_mm is None
        assert geometry.radius_mm == 5.0
        assert math.isclose(geometry.length_mm, 2.0 * math.pi * 5.0)


def test_configuration_binding_rejects_wrong_configuration_before_native_dispatch():
    with tempfile.TemporaryDirectory() as tmp:
        service, context, _ = fixture(Path(tmp))
        reference = service.query(context, kinds=("face",)).value.items[0].reference
        wrong = DocumentContext(
            session_id=context.session_id,
            path=context.path,
            title=context.title,
            document_type=context.document_type,
            configuration="OtherConfig",
            update_stamp=context.update_stamp,
        )
        before = service.session.calls
        result = service.resolve(wrong, reference)
        assert result.state is NativeCallState.FAILURE
        assert result.dispatched is False
        assert result.failure.code == "topology_reference_context_mismatch"
        assert service.session.calls == before


def test_component_instance_binding_prevents_cross_instance_resolution():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        part_path = root / "shared.SLDPRT"
        part_path.write_bytes(b"part")
        v1 = FakeVertex(b"v1")
        v2 = FakeVertex(b"v2", (0.001, 0.0, 0.0))
        edge = FakeEdge(b"e1", v1, v2)
        body = FakeBody(b"b1", "SharedBody", (FakeFace(b"f1", (edge,)),))
        part_doc = FakeDocument(str(part_path), body)
        component_a = Component("Shared-1", part_doc)
        component_b = Component("Shared-2", part_doc)
        asm_path = root / "fixture.SLDASM"
        asm_path.write_bytes(b"asm")
        assembly = AssemblyDocument(str(asm_path), (component_a, component_b))
        api = AssemblyApi()
        session = FakeSession(FakeApp(assembly), api)
        documents = DocumentService(session, path_policy=DocumentPathPolicy((root,)))
        service = TopologyNativeAdapter(session, document_service=documents, max_items=64)
        context = DocumentContext(
            session_id=session.session_id,
            path=os.path.realpath(asm_path),
            title=asm_path.name,
            document_type=DocumentType.ASSEMBLY,
            configuration="Default",
            update_stamp=10,
        )

        ref_a = service.query(context, kinds=("face",), component_id="Shared-1").value.items[0].reference
        ref_b = service.query(context, kinds=("face",), component_id="Shared-2").value.items[0].reference
        assert ref_a != ref_b

        before = session.calls
        wrong = service.resolve(context, ref_a, component_id="Shared-2")
        assert wrong.state is NativeCallState.FAILURE
        assert wrong.dispatched is False
        assert wrong.failure.code == "topology_reference_component_mismatch"
        assert session.calls == before

        right = service.resolve(context, ref_a, component_id="Shared-1")
        assert right.state is NativeCallState.SUCCESS
        assert right.value.component_id == "Shared-1"


def test_component_query_persists_underlying_model_entity_not_assembly_proxy():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        part_path = root / "shared.SLDPRT"
        part_path.write_bytes(b"part")
        v1 = FakeVertex(b"v1")
        v2 = FakeVertex(b"v2", (0.001, 0.0, 0.0))
        edge = FakeEdge(b"e1", v1, v2)
        source_face = FakeFace(b"source-face", (edge,))
        source_body = FakeBody(b"source-body", "SharedBody", (source_face,))
        part_doc = FakeDocument(str(part_path), source_body)

        proxy_face = FakeFace(b"assembly-face", (edge,))
        proxy_body = FakeBody(b"assembly-body", "SharedBody", (proxy_face,))

        class ProxyComponent(Component):
            def GetBodies3(self, body_type, options):
                return (proxy_body,) if body_type == 0 else ()

            def GetBodies2(self, body_type):
                return self.GetBodies3(body_type, 0)

            def GetCorrespondingEntity(self, entity):
                return proxy_face if entity is source_face else entity

        component = ProxyComponent("Shared-1", part_doc)
        asm_path = root / "fixture.SLDASM"
        asm_path.write_bytes(b"asm")
        assembly = AssemblyDocument(str(asm_path), (component,))
        api = AssemblyApi()
        session = FakeSession(FakeApp(assembly), api)
        documents = DocumentService(session, path_policy=DocumentPathPolicy((root,)))
        service = TopologyNativeAdapter(session, document_service=documents, max_items=64)
        context = DocumentContext(
            session_id=session.session_id,
            path=os.path.realpath(asm_path),
            title=asm_path.name,
            document_type=DocumentType.ASSEMBLY,
            configuration="Default",
            update_stamp=10,
        )

        result = service.query(context, kinds=("face",), component_id="Shared-1")
        assert result.state is NativeCallState.SUCCESS
        reference = result.value.items[0].reference
        part_doc.Extension.states[b"assembly-face"] = 1

        resolved = service.resolve(context, reference, component_id="Shared-1")
        assert resolved.state is NativeCallState.SUCCESS
        assert resolved.value.component_id == "Shared-1"
