"""Bounded native topology discovery, inspection, and persistent-reference validation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import uuid
from typing import Any, Iterable

from cdt_solidworks.document.models import DocumentContext, DocumentType
from cdt_solidworks.document.service import DocumentService
from cdt_solidworks.native.errors import NativeRuntimeError, failure_from_exception
from cdt_solidworks.native.models import NativeCallResult

from .models import (
    BodyGeometry,
    EdgeGeometry,
    FaceGeometry,
    Point3D,
    TopologyInspection,
    TopologyItem,
    TopologyQueryResult,
    TopologyResolution,
    Vector3D,
    VertexGeometry,
)

_ALLOWED_KINDS = frozenset({"body", "face", "edge", "vertex"})
_TOKEN_PREFIX = "swref1"
_M_TO_MM = 1000.0
_M2_TO_MM2 = 1_000_000.0
_M3_TO_MM3 = 1_000_000_000.0


class TopologyNativeAdapter:
    """Discover, inspect, and validate model/component-bound persistent references."""

    def __init__(
        self,
        session: Any,
        *,
        document_service: DocumentService,
        default_timeout: float = 15.0,
        max_items: int = 4096,
    ) -> None:
        self.session = session
        self.api = session.api
        self.documents = document_service
        self.default_timeout = float(default_timeout)
        self.max_items = int(max_items)
        if self.max_items < 1:
            raise ValueError("max_items must be positive")

    def query(
        self,
        context: DocumentContext,
        *,
        kinds: Iterable[str] | None = None,
        component_id: str | None = None,
        timeout: float | None = None,
    ) -> NativeCallResult[TopologyQueryResult]:
        try:
            requested = self._normalize_kinds(kinds)
            component_id = self._normalize_component_request(context, component_id)
        except Exception as exc:
            return self._local_failure(exc, "topology_query")

        def operation(app: Any) -> TopologyQueryResult:
            document = self.documents._resolve_context(app, context)
            source_model, component = self._source_model(document, context, component_id)
            items: list[TopologyItem] = []
            counts = {kind: 0 for kind in ("body", "face", "edge", "vertex")}
            seen = {kind: set() for kind in counts}

            def add(kind: str, entity: Any, body_name: str | None) -> bytes:
                pid = self._persistent_reference(source_model, entity)
                if pid in seen[kind]:
                    return pid
                seen[kind].add(pid)
                if kind in requested:
                    if len(items) >= self.max_items:
                        raise NativeRuntimeError(
                            "topology_query_limit_exceeded",
                            "topology_query",
                            "Topology query exceeds the configured bounded result limit.",
                            details={"max_items": self.max_items},
                        )
                    counts[kind] += 1
                    items.append(
                        TopologyItem(
                            kind=kind,
                            reference=self._encode_reference(context, kind, pid, component_id=component_id),
                            body_name=body_name,
                            ordinal=counts[kind] - 1,
                            component_id=component_id,
                        )
                    )
                return pid

            for body in self._unique_bodies(source_model, component):
                body_name = str(self._safe_api("body_name", body) or "") or None
                add("body", body, body_name)
                if not requested.intersection({"face", "edge", "vertex"}):
                    continue
                for face in self._as_tuple(self.api._member(body, "GetFaces")):
                    if face is None:
                        continue
                    add("face", face, body_name)
                    if not requested.intersection({"edge", "vertex"}):
                        continue
                    for edge in self._as_tuple(self.api._member(face, "GetEdges")):
                        if edge is None:
                            continue
                        add("edge", edge, body_name)
                        if "vertex" not in requested:
                            continue
                        for getter in ("GetStartVertex", "GetEndVertex"):
                            vertex = self.api._member(edge, getter)
                            if vertex is not None:
                                add("vertex", vertex, body_name)

            return TopologyQueryResult(items=tuple(items), counts=counts, component_id=component_id)

        return self.session.execute(
            operation,
            stage="topology_query",
            timeout=self._timeout(timeout),
            mutation=False,
        )

    def resolve(
        self,
        context: DocumentContext,
        reference: str,
        *,
        expected_kind: str | None = None,
        component_id: str | None = None,
        timeout: float | None = None,
    ) -> NativeCallResult[TopologyResolution]:
        prepared = self._prepare_resolution(context, reference, expected_kind, component_id, "topology_resolve")
        if isinstance(prepared, NativeCallResult):
            return prepared
        payload, kind, pid = prepared

        def operation(app: Any) -> TopologyResolution:
            entity, _, component = self._resolve_native(app, context, payload, kind, pid)
            return TopologyResolution(
                kind=kind,
                reference=reference,
                body_name=self._body_name_for_entity(entity, kind),
                component_id=payload.get("component") if isinstance(payload.get("component"), str) else None,
            )

        return self.session.execute(
            operation,
            stage="topology_resolve",
            timeout=self._timeout(timeout),
            mutation=False,
        )

    def resolve_native_for_document(
        self,
        document_id: str,
        reference: str,
        *,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        """Resolve an opaque reference to a native entity for trusted in-provider consumers."""
        stage = "topology_resolve_native"
        try:
            canonical = self.documents.path_policy.validate_open(document_id)
        except Exception as exc:
            return self._local_failure(exc, stage)

        def operation(app: Any) -> dict[str, Any]:
            document = self.api.get_open_document(app, canonical)
            if document is None:
                raise NativeRuntimeError(
                    "document_not_open",
                    stage,
                    "Topology native resolution requires the target document to be open.",
                )
            context = self.documents._context_from_doc(document)
            payload = self._decode_reference(reference)
            kind = str(payload["kind"])
            self._require_binding(context, payload, component_id=None, stage=stage)
            pid = self._decode_pid(str(payload["pid"]))
            entity, _, _ = self._resolve_native(app, context, payload, kind, pid)
            component_id = payload.get("component")
            return {
                "native_entity": entity,
                "component_id": component_id if isinstance(component_id, str) else None,
                "kind": kind,
                "reference": reference,
            }

        return self.session.execute(
            operation,
            stage=stage,
            timeout=self._timeout(timeout),
            mutation=False,
        )

    def inspect(
        self,
        context: DocumentContext,
        reference: str,
        *,
        expected_kind: str | None = None,
        component_id: str | None = None,
        timeout: float | None = None,
    ) -> NativeCallResult[TopologyInspection]:
        prepared = self._prepare_resolution(context, reference, expected_kind, component_id, "topology_inspect")
        if isinstance(prepared, NativeCallResult):
            return prepared
        payload, kind, pid = prepared

        def operation(app: Any) -> TopologyInspection:
            entity, _, _ = self._resolve_native(app, context, payload, kind, pid)
            return TopologyInspection(
                kind=kind,
                reference=reference,
                body_name=self._body_name_for_entity(entity, kind),
                component_id=payload.get("component") if isinstance(payload.get("component"), str) else None,
                geometry=self._inspect_geometry(kind, entity),
            )

        return self.session.execute(
            operation,
            stage="topology_inspect",
            timeout=self._timeout(timeout),
            mutation=False,
        )

    def _prepare_resolution(
        self,
        context: DocumentContext,
        reference: str,
        expected_kind: str | None,
        component_id: str | None,
        stage: str,
    ) -> tuple[dict[str, object], str, bytes] | NativeCallResult[Any]:
        try:
            payload = self._decode_reference(reference)
            kind = str(payload["kind"])
            if expected_kind is not None and kind != self._normalize_kind(expected_kind):
                raise NativeRuntimeError(
                    "topology_reference_kind_mismatch", stage,
                    "Topology reference kind does not match the requested kind.",
                )
            self._require_binding(context, payload, component_id=component_id, stage=stage)
            return payload, kind, self._decode_pid(str(payload["pid"]))
        except Exception as exc:
            return self._local_failure(exc, stage)

    def _resolve_native(
        self,
        app: Any,
        context: DocumentContext,
        payload: dict[str, object],
        kind: str,
        pid: bytes,
    ) -> tuple[Any, Any, Any | None]:
        document = self.documents._resolve_context(app, context)
        component_id = payload.get("component") if isinstance(payload.get("component"), str) else None
        source_model, component = self._source_model(document, context, component_id)
        entity, state = self.api.object_by_persistent_reference(source_model, pid)
        self._require_resolved_state(entity, int(state))
        self._require_entity_kind(entity, kind)
        if self._persistent_reference(source_model, entity) != pid:
            raise NativeRuntimeError(
                "topology_reference_roundtrip_mismatch", "topology_resolve",
                "Resolved entity did not reproduce the original persistent reference.",
            )
        if component is not None and kind in {"face", "edge", "vertex"}:
            corresponding = self._safe_member(component, "GetCorrespondingEntity", entity)
            if corresponding is None:
                raise NativeRuntimeError(
                    "topology_reference_lost", "topology_resolve",
                    "Referenced topology entity has no corresponding entity in the bound component instance.",
                )
            entity = corresponding
        return entity, source_model, component

    def _source_model(
        self,
        document: Any,
        context: DocumentContext,
        component_id: str | None,
    ) -> tuple[Any, Any | None]:
        if component_id is None:
            return document, None
        component = self._find_component(document, component_id)
        if self._component_suppressed(component):
            raise NativeRuntimeError(
                "topology_reference_suppressed", "topology_resolve",
                "Bound component instance is suppressed.",
            )
        source_model = self._safe_member(component, "GetModelDoc2")
        if source_model is None:
            raise NativeRuntimeError(
                "topology_component_unavailable", "topology_resolve",
                "Bound component model is unavailable or lightweight.",
            )
        return source_model, component

    def _find_component(self, assembly: Any, component_id: str) -> Any:
        matches = [
            component for component in self.api.components(assembly, False)
            if str(self.api.component_name(component) or "") == component_id
        ]
        if not matches:
            raise NativeRuntimeError(
                "topology_component_missing", "topology_resolve",
                "Bound assembly component instance no longer exists.",
            )
        if len(matches) != 1:
            raise NativeRuntimeError(
                "topology_component_ambiguous", "topology_resolve",
                "Component identity is not unique in the assembly traversal.",
            )
        return matches[0]

    def _unique_bodies(self, model: Any, component: Any | None = None) -> tuple[Any, ...]:
        result: list[Any] = []
        seen: set[bytes] = set()
        for body_type in (0, 1):
            if component is None:
                bodies = self.api.bodies(model, body_type, False)
            else:
                try:
                    bodies = self._as_tuple(self.api._member(component, "GetBodies3", body_type, 0))
                except Exception:
                    try:
                        bodies = self._as_tuple(self.api._member(component, "GetBodies2", body_type))
                    except Exception as exc:
                        raise NativeRuntimeError(
                            "topology_component_body_read_failed", "topology_query",
                            "SOLIDWORKS could not enumerate bodies for the bound component instance.",
                        ) from exc
            for body in bodies:
                if body is None:
                    continue
                pid = self._persistent_reference(model, body)
                if pid not in seen:
                    seen.add(pid)
                    result.append(body)
        return tuple(result)

    def _persistent_reference(self, model: Any, entity: Any) -> bytes:
        try:
            value = self.api.persistent_reference(model, entity)
        except Exception as exc:
            raise NativeRuntimeError(
                "topology_reference_read_failed", "topology_query",
                "SOLIDWORKS did not return a persistent reference for a topology entity.",
            ) from exc
        if not value:
            raise NativeRuntimeError(
                "topology_reference_missing", "topology_query",
                "SOLIDWORKS returned an empty persistent reference.",
            )
        return bytes(value)

    @staticmethod
    def _require_resolved_state(entity: Any, state: int) -> None:
        if state & 4:
            code, message = "topology_reference_deleted", "Referenced topology entity was deleted."
        elif state & 2:
            code, message = "topology_reference_suppressed", "Referenced topology entity is suppressed."
        elif state & 1:
            code, message = "topology_reference_invalid", "Persistent topology reference is invalid."
        elif state != 0:
            code, message = "topology_reference_invalid", "Persistent topology reference returned an unknown state."
        elif entity is None:
            code, message = "topology_reference_lost", "Persistent topology reference no longer resolves an entity."
        else:
            return
        raise NativeRuntimeError(code, "topology_resolve", message, details={"native_state": state})

    @staticmethod
    def _require_entity_kind(entity: Any, kind: str) -> None:
        required = {
            "body": ("GetFaces",),
            "face": ("GetSurface", "GetEdges"),
            "edge": ("GetStartVertex", "GetEndVertex"),
            "vertex": ("GetPoint",),
        }[kind]
        if any(getattr(entity, name, None) is None for name in required):
            raise NativeRuntimeError(
                "topology_reference_kind_mismatch", "topology_resolve",
                "Resolved persistent reference does not match its recorded topology kind.",
            )

    def _inspect_geometry(self, kind: str, entity: Any) -> BodyGeometry | FaceGeometry | EdgeGeometry | VertexGeometry:
        if kind == "body":
            return self._inspect_body(entity)
        if kind == "face":
            return self._inspect_face(entity)
        if kind == "edge":
            return self._inspect_edge(entity)
        point = self._point(self._safe_member(entity, "GetPoint"))
        if point is None:
            raise NativeRuntimeError(
                "topology_geometry_unavailable", "topology_inspect",
                "Vertex point coordinates are unavailable or non-finite.",
            )
        return VertexGeometry(point_mm=point)

    def _inspect_body(self, body: Any) -> BodyGeometry:
        raw_type = self._safe_member(body, "GetType")
        body_kind = {0: "solid", 1: "sheet", 2: "wire"}.get(raw_type, "unknown") if isinstance(raw_type, int) else "unknown"
        name = str(self._safe_api("body_name", body) or "") or None
        bbox = self._finite_tuple(self._safe_member(body, "GetBodyBox"), 6)
        bbox_mm = tuple(value * _M_TO_MM for value in bbox) if bbox is not None else None
        mass = self._finite_tuple(self._safe_member(body, "GetMassProperties", 1.0), 12)
        volume_mm3 = None
        area_mm2 = None
        if mass is not None:
            if body_kind == "solid":
                volume_mm3 = self._nonnegative(mass[3] * _M3_TO_MM3)
                area_mm2 = self._nonnegative(mass[4] * _M2_TO_MM2)
            elif body_kind == "sheet":
                area_mm2 = self._nonnegative(mass[3] * _M2_TO_MM2)
        return BodyGeometry(
            body_kind=body_kind, name=name, bbox_mm=bbox_mm,
            volume_mm3=volume_mm3, area_mm2=area_mm2,
        )

    def _inspect_face(self, face: Any) -> FaceGeometry:
        surface = self._safe_member(face, "GetSurface")
        if surface is None:
            return FaceGeometry(surface_type="other")
        surface_type = "other"
        origin = normal = axis = None
        radius_mm = half_angle_deg = None
        if self._truthy_member(surface, "IsPlane"):
            surface_type = "planar"
            params = self._finite_tuple(self._safe_member(surface, "PlaneParams"), 6)
            if params is not None:
                normal = self._vector(params[:3])
                origin = self._point(params[3:6])
        elif self._truthy_member(surface, "IsCylinder"):
            surface_type = "cylindrical"
            params = self._finite_tuple(self._safe_member(surface, "CylinderParams"), 7)
            if params is not None:
                origin = self._point(params[:3])
                axis = self._vector(params[3:6])
                radius_mm = self._nonnegative(params[6] * _M_TO_MM)
        elif self._truthy_member(surface, "IsCone"):
            surface_type = "conical"
            params = self._finite_tuple(self._safe_member(surface, "ConeParams2"), 8)
            if params is None:
                params = self._finite_tuple(self._safe_member(surface, "ConeParams"), 8)
            if params is not None:
                origin = self._point(params[:3])
                axis = self._vector(params[3:6])
                radius_mm = self._nonnegative(params[6] * _M_TO_MM)
                half_angle_deg = math.degrees(params[7])
        elif self._truthy_member(surface, "IsSphere"):
            surface_type = "spherical"
            params = self._finite_tuple(self._safe_member(surface, "SphereParams"), 4)
            if params is not None:
                origin = self._point(params[:3])
                radius_mm = self._nonnegative(params[3] * _M_TO_MM)
        area = self._finite_number(self._safe_member(face, "GetArea"))
        uv = self._finite_tuple(self._safe_member(face, "GetUVBounds"), 4)
        return FaceGeometry(
            surface_type=surface_type, origin_mm=origin, normal=normal, axis=axis,
            radius_mm=radius_mm, half_angle_deg=half_angle_deg,
            area_mm2=None if area is None else self._nonnegative(area * _M2_TO_MM2),
            uv_bounds=uv,
        )

    def _inspect_edge(self, edge: Any) -> EdgeGeometry:
        start_vertex = self._safe_member(edge, "GetStartVertex")
        end_vertex = self._safe_member(edge, "GetEndVertex")
        start = self._point(self._safe_member(start_vertex, "GetPoint")) if start_vertex is not None else None
        end = self._point(self._safe_member(end_vertex, "GetPoint")) if end_vertex is not None else None
        closed = start_vertex is None and end_vertex is None
        if start_vertex is not None and end_vertex is not None:
            closed = start_vertex is end_vertex or (start is not None and end is not None and start == end)
        curve = self._safe_member(edge, "GetCurve")
        curve_type = "other"
        radius_mm = None
        if curve is not None:
            if self._truthy_member(curve, "IsLine"):
                curve_type = "line"
            elif self._truthy_member(curve, "IsCircle"):
                curve_type = "circle"
                params = self._finite_tuple(self._safe_member(curve, "CircleParams"), 7)
                if params is not None:
                    radius_mm = self._nonnegative(params[6] * _M_TO_MM)
            elif self._truthy_member(curve, "IsEllipse"):
                curve_type = "ellipse"
        length = self._finite_number(self._safe_member(edge, "GetLength"))
        if length is None and curve_type == "line" and start is not None and end is not None:
            length = math.dist(
                (start.x_mm, start.y_mm, start.z_mm),
                (end.x_mm, end.y_mm, end.z_mm),
            ) / _M_TO_MM
        if length is None and curve_type == "circle" and closed and radius_mm is not None:
            length_mm = 2.0 * math.pi * radius_mm
        else:
            length_mm = None if length is None else self._nonnegative(length * _M_TO_MM)
        return EdgeGeometry(
            curve_type=curve_type, start_mm=start, end_mm=end,
            length_mm=length_mm, radius_mm=radius_mm, closed=closed,
        )

    def _body_name_for_entity(self, entity: Any, kind: str) -> str | None:
        body = entity if kind == "body" else self._safe_member(entity, "GetBody")
        if body is None:
            return None
        return str(self._safe_api("body_name", body) or "") or None

    def _encode_reference(
        self,
        context: DocumentContext,
        kind: str,
        pid: bytes,
        *,
        component_id: str | None = None,
    ) -> str:
        payload = {
            "version": 1,
            "document": self._document_fingerprint(context.path),
            "configuration": context.configuration or "",
            "update_stamp": context.update_stamp,
            "component": component_id,
            "kind": kind,
            "pid": self._encode_pid(pid),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        checksum = hashlib.sha256(raw).hexdigest()[:24]
        return f"{_TOKEN_PREFIX}.{encoded}.{checksum}"

    def _decode_reference(self, reference: str) -> dict[str, object]:
        try:
            prefix, encoded, checksum = str(reference).split(".", 2)
            if prefix != _TOKEN_PREFIX:
                raise ValueError("prefix")
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            expected = hashlib.sha256(raw).hexdigest()[:24]
            if not hmac.compare_digest(checksum, expected):
                raise ValueError("checksum")
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise ValueError("version")
            payload["kind"] = self._normalize_kind(payload.get("kind"))
            component = payload.get("component")
            if component is not None and (not isinstance(component, str) or not component.strip()):
                raise ValueError("component")
            if not isinstance(payload.get("document"), str):
                raise ValueError("document")
            if not isinstance(payload.get("configuration"), str):
                raise ValueError("configuration")
            if not isinstance(payload.get("update_stamp"), int):
                raise ValueError("update_stamp")
            self._decode_pid(str(payload.get("pid", "")))
            return payload
        except NativeRuntimeError:
            raise
        except Exception as exc:
            raise NativeRuntimeError(
                "invalid_topology_reference", "topology_resolve",
                "Topology reference is malformed or failed its integrity checksum.",
            ) from exc

    def _require_binding(
        self,
        context: DocumentContext,
        payload: dict[str, object],
        *,
        component_id: str | None,
        stage: str,
    ) -> None:
        if payload["document"] != self._document_fingerprint(context.path):
            raise NativeRuntimeError(
                "topology_reference_context_mismatch", stage,
                "Topology reference belongs to a different document.",
            )
        if payload["configuration"] != (context.configuration or ""):
            raise NativeRuntimeError(
                "topology_reference_context_mismatch", stage,
                "Topology reference belongs to a different configuration.",
            )
        if payload["update_stamp"] != context.update_stamp:
            raise NativeRuntimeError(
                "stale_topology_reference", stage,
                "Document revision changed after the topology reference was captured.",
            )
        bound_component = payload.get("component")
        if component_id is not None and bound_component != component_id:
            raise NativeRuntimeError(
                "topology_reference_component_mismatch", stage,
                "Topology reference belongs to a different assembly component instance.",
            )
        if context.document_type is DocumentType.PART and bound_component is not None:
            raise NativeRuntimeError(
                "topology_reference_context_mismatch", stage,
                "Component-bound topology reference cannot resolve in a part document.",
            )
        if context.document_type is DocumentType.ASSEMBLY and bound_component is None:
            raise NativeRuntimeError(
                "topology_reference_component_mismatch", stage,
                "Assembly topology references require an explicit component instance binding.",
            )

    @staticmethod
    def _document_fingerprint(path: str) -> str:
        canonical = os.path.normcase(os.path.abspath(path))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _encode_pid(pid: bytes) -> str:
        return base64.urlsafe_b64encode(bytes(pid)).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_pid(value: str) -> bytes:
        try:
            decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        except Exception as exc:
            raise NativeRuntimeError(
                "invalid_topology_reference", "topology_resolve",
                "Topology reference contains an invalid persistent-reference payload.",
            ) from exc
        if not decoded:
            raise NativeRuntimeError(
                "invalid_topology_reference", "topology_resolve",
                "Topology reference contains an empty persistent-reference payload.",
            )
        return decoded

    @staticmethod
    def _normalize_kind(value: object) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in _ALLOWED_KINDS:
            raise NativeRuntimeError(
                "invalid_topology_kind", "topology_query",
                "Topology kind must be body, face, edge, or vertex.",
            )
        return normalized

    def _normalize_kinds(self, values: Iterable[str] | None) -> frozenset[str]:
        if values is None:
            return _ALLOWED_KINDS
        result = frozenset(self._normalize_kind(value) for value in values)
        if not result:
            raise NativeRuntimeError(
                "invalid_topology_kind", "topology_query",
                "Topology query requires at least one kind.",
            )
        return result

    @staticmethod
    def _normalize_component_request(context: DocumentContext, component_id: str | None) -> str | None:
        if context.update_stamp is None:
            raise NativeRuntimeError(
                "document_revision_unavailable", "topology_query",
                "Topology references require a document update stamp.",
            )
        if context.document_type is DocumentType.PART:
            if component_id is not None:
                raise NativeRuntimeError(
                    "topology_reference_component_mismatch", "topology_query",
                    "Part topology queries cannot specify an assembly component instance.",
                )
            return None
        if context.document_type is DocumentType.ASSEMBLY:
            if component_id is None or not str(component_id).strip():
                raise NativeRuntimeError(
                    "topology_component_required", "topology_query",
                    "Assembly topology queries require one explicit component instance identity.",
                )
            return str(component_id).strip()
        raise NativeRuntimeError(
            "unsupported_topology_document_type", "topology_query",
            "Topology queries accept part documents or explicit part components in assemblies.",
        )

    def _component_suppressed(self, component: Any) -> bool:
        try:
            return bool(self.api.component_suppressed(component))
        except Exception:
            value = self._safe_member(component, "IsSuppressed")
            return bool(value) if value is not None else False

    def _safe_member(self, obj: Any, name: str, *args: Any) -> Any:
        if obj is None:
            return None
        try:
            return self.api._member(obj, name, *args)
        except Exception:
            return None

    def _safe_api(self, name: str, *args: Any) -> Any:
        try:
            return getattr(self.api, name)(*args)
        except Exception:
            return None

    def _truthy_member(self, obj: Any, name: str) -> bool:
        return bool(self._safe_member(obj, name))

    @staticmethod
    def _finite_number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def _finite_tuple(cls, value: Any, size: int) -> tuple[float, ...] | None:
        values = cls._as_tuple(value)
        if len(values) < size:
            return None
        result: list[float] = []
        for raw in values[:size]:
            number = cls._finite_number(raw)
            if number is None:
                return None
            result.append(number)
        return tuple(result)

    @classmethod
    def _point(cls, value: Any) -> Point3D | None:
        values = cls._finite_tuple(value, 3)
        if values is None:
            return None
        return Point3D(*(item * _M_TO_MM for item in values))

    @classmethod
    def _vector(cls, value: Any) -> Vector3D | None:
        values = cls._finite_tuple(value, 3)
        if values is None:
            return None
        length = math.sqrt(sum(item * item for item in values))
        if not math.isfinite(length) or length <= 0:
            return None
        return Vector3D(*(item / length for item in values))

    @staticmethod
    def _nonnegative(value: float) -> float | None:
        return value if math.isfinite(value) and value >= 0 else None

    @staticmethod
    def _as_tuple(value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, (tuple, list)):
            return tuple(value)
        return (value,)

    def _timeout(self, timeout: float | None) -> float:
        return self.default_timeout if timeout is None else max(0.0, float(timeout))

    @staticmethod
    def _local_failure(exc: Exception, stage: str) -> NativeCallResult[Any]:
        return NativeCallResult.failed(
            failure_from_exception(exc, stage),
            call_id=uuid.uuid4().hex,
            dispatched=False,
        )
