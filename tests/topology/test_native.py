from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path

from cdt_solidworks.document.models import DocumentContext, DocumentType
from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.document.service import DocumentService
from cdt_solidworks.native.errors import failure_from_exception
from cdt_solidworks.native.models import NativeCallResult, NativeCallState
from cdt_solidworks.topology.native import TopologyNativeAdapter


class FakeVertex:
    def __init__(self, pid: bytes, point=(0.0, 0.0, 0.0)):
        self.pid = pid
        self.point = point

    def GetPoint(self):
        return self.point


class FakeEdge:
    def __init__(self, pid: bytes, start: FakeVertex, end: FakeVertex):
        self.pid = pid
        self.start = start
        self.end = end

    def GetStartVertex(self):
        return self.start

    def GetEndVertex(self):
        return self.end


class FakeSurface:
    def IsPlane(self):
        return True


class FakeFace:
    def __init__(self, pid: bytes, edges):
        self.pid = pid
        self.edges = tuple(edges)

    def GetEdges(self):
        return self.edges

    def GetSurface(self):
        return FakeSurface()


class FakeBody:
    def __init__(self, pid: bytes, name: str, faces):
        self.pid = pid
        self.Name = name
        self.faces = tuple(faces)

    def GetFaces(self):
        return self.faces


class FakeExtension:
    def __init__(self):
        self.objects = {}
        self.states = {}
        self.resolve_calls = 0

    def GetPersistReference3(self, obj):
        self.objects[bytes(obj.pid)] = obj
        return tuple(obj.pid)

    def GetObjectByPersistReference3(self, pid, *args):
        self.resolve_calls += 1
        key = bytes(pid)
        return self.objects.get(key), self.states.get(key, 0)


class FakeDocument:
    def __init__(self, path: str, body: FakeBody):
        self.path = os.path.realpath(path)
        self.title = os.path.basename(path)
        self.doc_type = int(DocumentType.PART)
        self.configuration = "Default"
        self.update_stamp = 10
        self.dirty = False
        self.Extension = FakeExtension()
        self.body = body


class FakeApp:
    def __init__(self, document: FakeDocument):
        self.document = document


class FakeApi:
    @staticmethod
    def _member(obj, name, *args):
        member = getattr(obj, name)
        if args:
            return member(*args)
        return member() if callable(member) else member

    def get_open_document(self, app, path):
        return app.document if os.path.realpath(path) == app.document.path else None

    def document_path(self, doc): return doc.path
    def document_title(self, doc): return doc.title
    def document_type(self, doc): return doc.doc_type
    def active_configuration(self, doc): return doc.configuration
    def update_stamp(self, doc): return doc.update_stamp
    def document_dirty(self, doc): return doc.dirty
    def bodies(self, doc, body_type, visible_only):
        return (doc.body,) if int(body_type) == 0 else ()
    def body_name(self, body): return body.Name

    @staticmethod
    def persistent_reference(model, entity):
        return bytes(model.Extension.GetPersistReference3(entity))

    @staticmethod
    def object_by_persistent_reference(model, reference):
        return model.Extension.GetObjectByPersistReference3(reference)


class FakeSession:
    def __init__(self, app, api):
        self.app = app
        self.api = api
        self.session_id = os.urandom(16).hex()
        self.calls = 0

    def execute(self, operation, *, stage: str, timeout: float, mutation: bool = False, **kwargs):
        self.calls += 1
        call_id = f"call-{self.calls}"
        try:
            return NativeCallResult.success(operation(self.app), call_id=call_id, dispatched=True)
        except Exception as exc:
            return NativeCallResult.failed(failure_from_exception(exc, stage), call_id=call_id, dispatched=True)


def fixture(root: Path, *, reference_secret: str | bytes | None = None):
    v1 = FakeVertex(b"v1", (0.0, 0.0, 0.0))
    v2 = FakeVertex(b"v2", (1.0, 0.0, 0.0))
    v3 = FakeVertex(b"v3", (1.0, 1.0, 0.0))
    e1 = FakeEdge(b"e1", v1, v2)
    e2 = FakeEdge(b"e2", v2, v3)
    f1 = FakeFace(b"f1", (e1, e2))
    f2 = FakeFace(b"f2", (e1,))
    body = FakeBody(b"b1", "Body1", (f1, f2))
    path = root / "fixture.SLDPRT"
    if not path.exists():
        path.write_bytes(b"fixture")
    doc = FakeDocument(str(path), body)
    api = FakeApi()
    session = FakeSession(FakeApp(doc), api)
    documents = DocumentService(session, path_policy=DocumentPathPolicy((root,)))
    service = TopologyNativeAdapter(
        session,
        document_service=documents,
        max_items=64,
        reference_secret=reference_secret,
    )
    context = DocumentContext(
        session_id=session.session_id, path=doc.path, title=doc.title,
        document_type=DocumentType.PART, configuration=doc.configuration, update_stamp=doc.update_stamp,
    )
    return service, context, doc


def test_query_returns_deduplicated_opaque_body_face_edge_vertex_refs():
    with tempfile.TemporaryDirectory() as tmp:
        service, context, _ = fixture(Path(tmp))
        result = service.query(context)
        assert result.state is NativeCallState.SUCCESS
        value = result.value
        assert value is not None
        assert value.counts == {"body": 1, "face": 2, "edge": 2, "vertex": 3}
        assert len(value.items) == 8
        assert all(item.reference.startswith("swref1.") for item in value.items)
        assert len({item.reference for item in value.items}) == 8
        assert all(context.path not in item.reference for item in value.items)


def test_open_document_native_resolve_does_not_enqueue_nested_dispatch():
    with tempfile.TemporaryDirectory() as tmp:
        service, context, doc = fixture(Path(tmp))
        query = service.query(context).value
        assert query is not None
        edge = next(item for item in query.items if item.kind == "edge")
        before = service.session.calls

        result = service.resolve_native_for_open_document(doc, edge.reference)

        assert result.state is NativeCallState.SUCCESS
        assert result.value is not None
        assert result.value["native_entity"] is not None
        assert result.value["kind"] == "edge"
        assert result.value["reference"] == edge.reference
        assert service.session.calls == before


def test_explicit_reference_secret_allows_restart_and_rejects_other_secret():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first, first_context, _ = fixture(root, reference_secret="shared-secret")
        reference = first.query(first_context, kinds=("face",)).value.items[0].reference

        restarted, restarted_context, _ = fixture(root, reference_secret="shared-secret")
        restarted.query(restarted_context, kinds=("face",))
        resolved = restarted.resolve(restarted_context, reference, expected_kind="face")
        assert resolved.state is NativeCallState.SUCCESS

        wrong, wrong_context, _ = fixture(root, reference_secret="different-secret")
        before = wrong.session.calls
        rejected = wrong.resolve(wrong_context, reference, expected_kind="face")
        assert rejected.state is NativeCallState.FAILURE
        assert rejected.dispatched is False
        assert rejected.failure is not None
        assert rejected.failure.code == "invalid_topology_reference"
        assert wrong.session.calls == before


def test_reference_round_trip_resolves_same_kind():
    with tempfile.TemporaryDirectory() as tmp:
        service, context, _ = fixture(Path(tmp))
        query = service.query(context).value
        assert query is not None
        edge = next(item for item in query.items if item.kind == "edge")
        result = service.resolve(context, edge.reference, expected_kind="edge")
        assert result.state is NativeCallState.SUCCESS
        assert result.value is not None
        assert result.value.kind == "edge"
        assert result.value.reference == edge.reference


def test_reference_bound_to_revision_rejects_refreshed_context_after_change():
    with tempfile.TemporaryDirectory() as tmp:
        service, context, doc = fixture(Path(tmp))
        ref = service.query(context).value.items[1].reference
        doc.update_stamp += 1
        refreshed = DocumentContext(
            session_id=context.session_id, path=context.path, title=context.title,
            document_type=context.document_type, configuration=context.configuration, update_stamp=doc.update_stamp,
        )
        result = service.resolve(refreshed, ref)
        assert result.state is NativeCallState.FAILURE
        assert result.failure is not None
        assert result.failure.code == "stale_topology_reference"
        assert doc.Extension.resolve_calls == 0


def test_reference_allows_cross_session_stamp_drift_when_persisted_file_is_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first, first_context, _ = fixture(root, reference_secret="shared-secret")
        ref = first.query(first_context, kinds=("face",)).value.items[0].reference

        restarted, restarted_context, restarted_doc = fixture(root, reference_secret="shared-secret")
        restarted_context = DocumentContext(
            session_id=restarted_context.session_id,
            path=restarted_context.path,
            title=restarted_context.title,
            document_type=restarted_context.document_type,
            configuration=restarted_context.configuration,
            update_stamp=restarted_context.update_stamp + 77,
        )
        restarted_doc.update_stamp = restarted_context.update_stamp
        restarted.query(restarted_context, kinds=("face",))
        resolved = restarted.resolve(restarted_context, ref, expected_kind="face")

        assert resolved.state is NativeCallState.SUCCESS


def test_reference_rejects_cross_session_when_persisted_file_changed_before_native_lookup():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first, first_context, _ = fixture(root, reference_secret="shared-secret")
        ref = first.query(first_context, kinds=("face",)).value.items[0].reference

        Path(first_context.path).write_bytes(b"changed-on-disk")
        restarted, restarted_context, restarted_doc = fixture(root, reference_secret="shared-secret")
        restarted.query(restarted_context, kinds=("face",))
        before = restarted_doc.Extension.resolve_calls
        result = restarted.resolve(restarted_context, ref, expected_kind="face")

        assert result.state is NativeCallState.FAILURE
        assert result.dispatched is False
        assert result.failure is not None
        assert result.failure.code == "stale_topology_reference"
        assert restarted_doc.Extension.resolve_calls == before


def test_dirty_reference_is_not_restartable_even_when_persisted_file_is_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first, first_context, first_doc = fixture(root, reference_secret="shared-secret")
        first_doc.dirty = True
        ref = first.query(first_context, kinds=("face",)).value.items[0].reference

        restarted, restarted_context, restarted_doc = fixture(root, reference_secret="shared-secret")
        restarted.query(restarted_context, kinds=("face",))
        before = restarted_doc.Extension.resolve_calls
        result = restarted.resolve(restarted_context, ref, expected_kind="face")

        assert result.state is NativeCallState.FAILURE
        assert result.dispatched is False
        assert result.failure is not None
        assert result.failure.code == "stale_topology_reference"
        assert restarted_doc.Extension.resolve_calls == before


def test_reference_bound_to_document_rejects_substitution_before_native_lookup():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        service, context, doc = fixture(root)
        ref = service.query(context).value.items[1].reference
        other = root / "other.SLDPRT"
        other.write_bytes(b"other")
        substituted = DocumentContext(
            session_id=context.session_id, path=os.path.realpath(other), title=other.name,
            document_type=DocumentType.PART, configuration=context.configuration, update_stamp=context.update_stamp,
        )
        result = service.resolve(substituted, ref)
        assert result.state is NativeCallState.FAILURE
        assert result.failure is not None
        assert result.failure.code == "topology_reference_context_mismatch"
        assert doc.Extension.resolve_calls == 0


def test_native_deleted_suppressed_and_invalid_states_fail_closed():
    with tempfile.TemporaryDirectory() as tmp:
        service, context, doc = fixture(Path(tmp))
        item = service.query(context).value.items[1]
        payload = service._decode_reference(item.reference)
        pid = service._decode_pid(payload["pid"])
        for state, code in ((1, "topology_reference_invalid"), (2, "topology_reference_suppressed"), (4, "topology_reference_deleted")):
            doc.Extension.states[pid] = state
            result = service.resolve(context, item.reference)
            assert result.state is NativeCallState.FAILURE
            assert result.failure is not None
            assert result.failure.code == code


def test_rehashed_client_forgery_cannot_bypass_stale_revision_binding():
    with tempfile.TemporaryDirectory() as tmp:
        service, context, doc = fixture(Path(tmp))
        item = service.query(context, kinds=("face",)).value.items[0]
        doc.update_stamp += 1
        refreshed = DocumentContext(
            session_id=context.session_id,
            path=context.path,
            title=context.title,
            document_type=context.document_type,
            configuration=context.configuration,
            update_stamp=doc.update_stamp,
        )

        prefix, encoded, _ = item.reference.split(".", 2)
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw.decode("utf-8"))
        payload["update_stamp"] = refreshed.update_stamp
        forged_raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        forged_encoded = base64.urlsafe_b64encode(forged_raw).decode("ascii").rstrip("=")
        forged_checksum = hashlib.sha256(forged_raw).hexdigest()[:24]
        forged = f"{prefix}.{forged_encoded}.{forged_checksum}"
        before = service.session.calls

        result = service.resolve(refreshed, forged, expected_kind="face")

        assert result.state is NativeCallState.FAILURE
        assert result.dispatched is False
        assert result.failure is not None
        assert result.failure.code == "invalid_topology_reference"
        assert service.session.calls == before


def test_tampered_reference_is_rejected_before_dispatch():
    with tempfile.TemporaryDirectory() as tmp:
        service, context, _ = fixture(Path(tmp))
        item = service.query(context).value.items[0]
        before = service.session.calls
        bad = item.reference[:-1] + ("0" if item.reference[-1] != "0" else "1")
        result = service.resolve(context, bad)
        assert result.state is NativeCallState.FAILURE
        assert result.dispatched is False
        assert result.failure is not None
        assert result.failure.code == "invalid_topology_reference"
        assert service.session.calls == before


def test_query_refuses_to_silently_truncate_topology():
    with tempfile.TemporaryDirectory() as tmp:
        service, context, _ = fixture(Path(tmp))
        service.max_items = 3
        result = service.query(context)
        assert result.state is NativeCallState.FAILURE
        assert result.failure is not None
        assert result.failure.code == "topology_query_limit_exceeded"
