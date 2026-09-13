"""MBD/PMI domain — bounded, read-only DimXpert and annotation queries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class MbdRefusal(RuntimeError):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else f"{reason}: {detail}")


class MbdPostconditionError(RuntimeError):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else f"{reason}: {detail}")


class PmiKind(str, Enum):
    DIMXPERT = "dimxpert"
    DATUM = "datum"
    GTOL = "gtol"
    REFERENCE_DIMENSION = "reference_dimension"
    SURFACE_FINISH = "surface_finish"
    WELD_SYMBOL = "weld_symbol"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class PmiAnnotation:
    identity: str
    kind: PmiKind
    text: str
    dangling: bool


@dataclass(frozen=True, slots=True)
class MbdSnapshot:
    document_id: str
    configuration: str | None
    annotations: tuple[PmiAnnotation, ...]


class MbdAdapter(Protocol):
    def supports_query(self, document_id: str) -> bool: ...

    def query_pmi(
        self, document_id: str, configuration: str | None
    ) -> MbdSnapshot | None: ...


class MbdService:
    """Promotes PMI only when native identity/context read-back is coherent."""

    def __init__(self, adapter: MbdAdapter) -> None:
        self._adapter = adapter

    def query_pmi(
        self, document_id: str, configuration: str | None = None
    ) -> MbdSnapshot:
        self._require_identity("document_id", document_id)
        if configuration is not None:
            self._require_identity("configuration", configuration)
        if not self._adapter.supports_query(document_id):
            raise MbdRefusal("unsupported_capability", "solidworks.mbd.pmi_query")
        snapshot = self._adapter.query_pmi(document_id, configuration)
        if snapshot is None:
            raise MbdPostconditionError("pmi_readback_missing")
        if snapshot.document_id != document_id:
            raise MbdPostconditionError(
                "document_identity_mismatch", snapshot.document_id
            )
        if snapshot.configuration != configuration:
            raise MbdPostconditionError(
                "configuration_identity_mismatch", str(snapshot.configuration)
            )

        seen: set[str] = set()
        for annotation in snapshot.annotations:
            if not annotation.identity.strip() or annotation.identity in seen:
                raise MbdPostconditionError("invalid_pmi_identity")
            seen.add(annotation.identity)
            if annotation.dangling:
                raise MbdPostconditionError("dangling_pmi", annotation.identity)
            if not isinstance(annotation.kind, PmiKind):
                raise MbdPostconditionError("invalid_pmi_kind", annotation.identity)
            if not annotation.text.strip():
                raise MbdPostconditionError("empty_pmi_text", annotation.identity)
        return snapshot

    @staticmethod
    def _require_identity(label: str, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise MbdRefusal(f"invalid_{label}")
