"""Bounded native topology discovery and persistent-reference validation.

R1 slice 1 deliberately exposes identity only. Geometry measurement and mutation
consumers are layered on this reference primitive after native acceptance.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from typing import Any, Iterable

from cdt_solidworks.document.models import DocumentContext, DocumentType
from cdt_solidworks.document.service import DocumentService
from cdt_solidworks.native.errors import NativeRuntimeError, failure_from_exception
from cdt_solidworks.native.models import NativeCallResult

from .models import TopologyItem, TopologyQueryResult, TopologyResolution


_ALLOWED_KINDS = frozenset({"body", "face", "edge", "vertex"})
_TOKEN_PREFIX = "swref1"


class TopologyNativeAdapter:
    """Discover and validate model-bound persistent topology references."""

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
        timeout: float | None = None,
    ) -> NativeCallResult[TopologyQueryResult]:
        try:
            requested = self._normalize_kinds(kinds)
            self._require_part_context(context)
        except Exception as exc:
            return self._local_failure(exc, "topology_query")

        def operation(app: Any) -> TopologyQueryResult:
            model = self.documents._resolve_context(app, context)
            items: list[TopologyItem] = []
            counts = {kind: 0 for kind in ("body", "face", "edge", "vertex")}
            seen = {kind: set() for kind in counts}

            def add(kind: str, entity: Any, body_name: str | None) -> bytes:
                pid = self._persistent_reference(model, entity)
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
                            reference=self._encode_reference(context, kind, pid),
                            body_name=body_name,
                            ordinal=counts[kind] - 1,
                        )
                    )
                return pid

            bodies = self._unique_bodies(model)
            for body in bodies:
                body_name = str(self.api.body_name(body) or "") or None
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

            return TopologyQueryResult(items=tuple(items), counts=counts)

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
        timeout: float | None = None,
    ) -> NativeCallResult[TopologyResolution]:
        try:
            self._require_part_context(context)
            payload = self._decode_reference(reference)
            kind = str(payload["kind"])
            if expected_kind is not None:
                normalized_expected = self._normalize_kind(expected_kind)
                if kind != normalized_expected:
                    raise NativeRuntimeError(
                        "topology_reference_kind_mismatch",
                        "topology_resolve",
                        "Topology reference kind does not match the requested kind.",
                    )
            self._require_binding(context, payload)
            pid = self._decode_pid(str(payload["pid"]))
        except Exception as exc:
            return self._local_failure(exc, "topology_resolve")

        def operation(app: Any) -> TopologyResolution:
            model = self.documents._resolve_context(app, context)
            entity, state = self.api.object_by_persistent_reference(model, pid)
            self._require_resolved_state(entity, int(state))
            self._require_entity_kind(entity, kind)
            roundtrip = self._persistent_reference(model, entity)
            if roundtrip != pid:
                raise NativeRuntimeError(
                    "topology_reference_roundtrip_mismatch",
                    "topology_resolve",
                    "Resolved entity did not reproduce the original persistent reference.",
                )
            return TopologyResolution(kind=kind, reference=reference, body_name=None)

        return self.session.execute(
            operation,
            stage="topology_resolve",
            timeout=self._timeout(timeout),
            mutation=False,
        )

    def _unique_bodies(self, model: Any) -> tuple[Any, ...]:
        result: list[Any] = []
        seen: set[bytes] = set()
        for body_type in (0, 1):
            for body in self.api.bodies(model, body_type, False):
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
                "topology_reference_read_failed",
                "topology_query",
                "SOLIDWORKS did not return a persistent reference for a topology entity.",
            ) from exc
        if not value:
            raise NativeRuntimeError(
                "topology_reference_missing",
                "topology_query",
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
            code, message = "topology_reference_missing", "Persistent topology reference did not resolve an entity."
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
                "topology_reference_kind_mismatch",
                "topology_resolve",
                "Resolved persistent reference does not match its recorded topology kind.",
            )

    def _encode_reference(self, context: DocumentContext, kind: str, pid: bytes) -> str:
        payload = {
            "version": 1,
            "document": self._document_fingerprint(context.path),
            "configuration": context.configuration or "",
            "update_stamp": context.update_stamp,
            "component": None,
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
            kind = self._normalize_kind(payload.get("kind"))
            payload["kind"] = kind
            if payload.get("component") is not None:
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
                "invalid_topology_reference",
                "topology_resolve",
                "Topology reference is malformed or failed its integrity checksum.",
            ) from exc

    def _require_binding(self, context: DocumentContext, payload: dict[str, object]) -> None:
        if payload["document"] != self._document_fingerprint(context.path):
            raise NativeRuntimeError(
                "topology_reference_context_mismatch",
                "topology_resolve",
                "Topology reference belongs to a different document.",
            )
        if payload["configuration"] != (context.configuration or ""):
            raise NativeRuntimeError(
                "topology_reference_context_mismatch",
                "topology_resolve",
                "Topology reference belongs to a different configuration.",
            )
        if payload["update_stamp"] != context.update_stamp:
            raise NativeRuntimeError(
                "stale_topology_reference",
                "topology_resolve",
                "Document revision changed after the topology reference was captured.",
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
                "invalid_topology_reference",
                "topology_resolve",
                "Topology reference contains an invalid persistent-reference payload.",
            ) from exc
        if not decoded:
            raise NativeRuntimeError(
                "invalid_topology_reference",
                "topology_resolve",
                "Topology reference contains an empty persistent-reference payload.",
            )
        return decoded

    @staticmethod
    def _normalize_kind(value: object) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in _ALLOWED_KINDS:
            raise NativeRuntimeError(
                "invalid_topology_kind",
                "topology_query",
                "Topology kind must be body, face, edge, or vertex.",
            )
        return normalized

    def _normalize_kinds(self, values: Iterable[str] | None) -> frozenset[str]:
        if values is None:
            return _ALLOWED_KINDS
        result = frozenset(self._normalize_kind(value) for value in values)
        if not result:
            raise NativeRuntimeError(
                "invalid_topology_kind",
                "topology_query",
                "Topology query requires at least one kind.",
            )
        return result

    @staticmethod
    def _require_part_context(context: DocumentContext) -> None:
        if context.document_type is not DocumentType.PART:
            raise NativeRuntimeError(
                "unsupported_topology_document_type",
                "topology_query",
                "R1 topology identity slice currently accepts part documents only.",
            )
        if context.update_stamp is None:
            raise NativeRuntimeError(
                "document_revision_unavailable",
                "topology_query",
                "Topology references require a document update stamp.",
            )

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
