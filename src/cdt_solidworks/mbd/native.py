"""Lane-local SOLIDWORKS adapter for bounded read-only PMI queries."""

from __future__ import annotations

from pathlib import PureWindowsPath
from typing import Any, Callable, Protocol, TypeVar

from cdt_solidworks.native.models import NativeCallState

from .domain import MbdPostconditionError, MbdSnapshot, PmiAnnotation, PmiKind


T = TypeVar("T")


class MbdPathPolicy(Protocol):
    def validate_open(self, path: str) -> str: ...


class SolidWorksMbdAdapter:
    """Reads native SOLIDWORKS annotations without exposing raw COM surfaces."""

    _DOC_TYPES = {".sldprt": 1, ".sldasm": 2}
    _KIND_BY_ANNOTATION_TYPE = {
        2: PmiKind.DATUM,
        4: PmiKind.REFERENCE_DIMENSION,
        5: PmiKind.GTOL,
        7: PmiKind.SURFACE_FINISH,
        8: PmiKind.WELD_SYMBOL,
        19: PmiKind.DIMXPERT,
    }

    def __init__(
        self,
        session: Any,
        *,
        path_policy: MbdPathPolicy,
        default_timeout: float = 60.0,
    ) -> None:
        self._session = session
        self._api = session.api
        self._path_policy = path_policy
        self._default_timeout = float(default_timeout)

    def supports_query(self, document_id: str) -> bool:
        try:
            source = self._path_policy.validate_open(document_id)
        except Exception:
            return False
        return PureWindowsPath(source).suffix.casefold() in self._DOC_TYPES

    def query_pmi(
        self, document_id: str, configuration: str | None
    ) -> MbdSnapshot | None:
        source = self._path_policy.validate_open(document_id)
        doc_type = self._DOC_TYPES.get(PureWindowsPath(source).suffix.casefold())
        if doc_type is None:
            return None

        def read(model: Any) -> MbdSnapshot:
            actual_configuration = self._api.active_configuration(model)
            if configuration is not None and actual_configuration != configuration:
                raise MbdPostconditionError(
                    "configuration_identity_mismatch", str(actual_configuration)
                )
            extension = self._api._member(model, "Extension")
            value = self._api._member(extension, "GetAnnotations")
            annotations = self._as_tuple(value)
            snapshots = tuple(self._snapshot(annotation) for annotation in annotations)
            return MbdSnapshot(
                document_id=document_id,
                configuration=configuration,
                annotations=snapshots,
            )

        return self._with_document(source, doc_type, read)

    def _snapshot(self, annotation: Any) -> PmiAnnotation:
        identity = str(self._api._member(annotation, "GetName") or "")
        annotation_type = int(self._api._member(annotation, "GetType"))
        try:
            is_dimxpert = bool(self._api._member(annotation, "IsDimXpert"))
        except Exception:
            is_dimxpert = False
        kind = (
            PmiKind.DIMXPERT
            if is_dimxpert
            else self._KIND_BY_ANNOTATION_TYPE.get(annotation_type, PmiKind.OTHER)
        )
        text = self._annotation_text(annotation, identity)
        dangling = self._annotation_dangling(annotation)
        return PmiAnnotation(
            identity=identity,
            kind=kind,
            text=text,
            dangling=dangling,
        )

    def _annotation_text(self, annotation: Any, identity: str) -> str:
        try:
            specific = self._api._member(annotation, "GetSpecificAnnotation")
        except Exception:
            specific = None
        if specific is not None:
            for name in ("GetText", "GetText2"):
                try:
                    value = self._api._member(specific, name)
                except Exception:
                    continue
                text = str(value or "").strip()
                if text:
                    return text
        return identity.strip()

    def _annotation_dangling(self, annotation: Any) -> bool:
        try:
            values = self._as_tuple(
                self._api._member(annotation, "GetAttachedEntityTypes")
            )
        except Exception:
            return False
        return any(int(value) == 0 for value in values)

    def _with_document(
        self,
        source: str,
        doc_type: int,
        reader: Callable[[Any], T],
    ) -> T:
        def operation(app: Any) -> T:
            model = self._api.get_open_document(app, source)
            owned = model is None
            if model is None:
                model, errors, warnings = self._api.open_document(
                    app,
                    source,
                    doc_type,
                    read_only=True,
                    silent=True,
                    configuration="",
                )
                if model is None or int(errors) != 0:
                    raise MbdPostconditionError(
                        "document_open_failed", f"errors={errors}, warnings={warnings}"
                    )
            try:
                if int(self._api.document_type(model)) != doc_type:
                    raise MbdPostconditionError("document_type_mismatch")
                native_path = str(self._api.document_path(model) or "")
                if native_path and PureWindowsPath(native_path) != PureWindowsPath(source):
                    raise MbdPostconditionError("document_identity_mismatch", native_path)
                return reader(model)
            finally:
                if owned:
                    title = str(self._api._member(model, "GetTitle") or "")
                    if title:
                        self._api.close_document(app, title)

        result = self._session.execute(
            operation,
            stage="mbd_query_pmi",
            timeout=self._default_timeout,
            mutation=False,
        )
        if result.state is not NativeCallState.SUCCESS:
            detail = result.state.value
            if result.failure is not None:
                detail = f"{result.failure.code}@{result.failure.stage}"
            raise MbdPostconditionError("native_mbd_query_failed", detail)
        return result.value

    @staticmethod
    def _as_tuple(value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, (tuple, list)):
            return tuple(value)
        return (value,)
