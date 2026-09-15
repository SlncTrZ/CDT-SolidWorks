"""Explicit-identity SolidWorks document lifecycle and query service."""

from __future__ import annotations

import os
from pathlib import Path
import uuid
from typing import Any

from cdt_solidworks.native.errors import NativeRuntimeError, failure_from_exception
from cdt_solidworks.native.models import NativeCallResult
from cdt_solidworks.native.rebuild import RebuildResult, rebuild_document

from .models import BodyInfo, ComponentInfo, DocumentContext, DocumentInfo, DocumentType, FeatureInfo
from .path_policy import DocumentPathPolicy


class DocumentService:
    """Document operations that always resolve and validate an explicit identity."""

    def __init__(
        self,
        session: Any,
        *,
        path_policy: DocumentPathPolicy | None = None,
        default_timeout: float = 10.0,
        max_query_items: int = 100_000,
    ) -> None:
        self.session = session
        self.api = session.api
        self.path_policy = path_policy or DocumentPathPolicy()
        self.default_timeout = default_timeout
        self.max_query_items = max_query_items

    def open(
        self,
        path: str | Path,
        *,
        expected_type: DocumentType | None = None,
        configuration: str = "",
        read_only: bool = False,
        silent: bool = True,
        timeout: float | None = None,
    ) -> NativeCallResult[DocumentContext]:
        try:
            canonical = self.path_policy.validate_open(path)
            inferred_type = DocumentType.from_path(canonical)
            document_type = expected_type or inferred_type
            if document_type is not inferred_type:
                raise NativeRuntimeError(
                    "document_type_mismatch",
                    "document_open",
                    "Expected document type does not match the native file extension.",
                )
        except Exception as exc:
            return self._local_failure(exc, "document_open")

        observed_configuration: list[str | None] = [configuration or None]

        def operation(app: Any) -> DocumentContext:
            model = self.api.get_open_document(app, canonical)
            if model is None:
                model, errors, warnings = self.api.open_document(
                    app,
                    canonical,
                    int(document_type),
                    read_only=read_only,
                    silent=silent,
                    configuration=configuration,
                )
                if model is None or int(errors) != 0:
                    raise NativeRuntimeError(
                        "document_open_failed",
                        "document_open",
                        "SolidWorks failed to open the requested document.",
                        details={"errors": int(errors), "warnings": int(warnings)},
                    )
            context = self._context_from_doc(model)
            self._require_document_type(context, document_type)
            if configuration and context.configuration != configuration:
                raise NativeRuntimeError(
                    "document_context_mismatch",
                    "document_open",
                    "Opened document configuration does not match the requested configuration.",
                )
            observed_configuration[0] = context.configuration
            return context

        return self.session.execute(
            operation,
            stage="document_open",
            timeout=self._timeout(timeout),
            mutation=True,
            recovery_identity=(canonical, int(document_type), True),
            recovery_stage="document_reconcile",
            recovery_verifier=self._document_state_verifier(
                canonical,
                document_type,
                True,
                lambda: observed_configuration[0],
            ),
        )

    def adopt_open_document(
        self,
        path: str | Path,
        *,
        expected_type: DocumentType | None = None,
        timeout: float | None = None,
    ) -> NativeCallResult[DocumentContext]:
        try:
            canonical = self.path_policy.validate_open(path)
            inferred = DocumentType.from_path(canonical)
            document_type = expected_type or inferred
        except Exception as exc:
            return self._local_failure(exc, "document_adopt")

        def operation(app: Any) -> DocumentContext:
            model = self.api.get_open_document(app, canonical)
            if model is None:
                raise NativeRuntimeError(
                    "document_not_open",
                    "document_adopt",
                    "Requested document is not open in the bound SolidWorks session.",
                )
            context = self._context_from_doc(model)
            self._require_document_type(context, document_type)
            return context

        return self.session.execute(
            operation,
            stage="document_adopt",
            timeout=self._timeout(timeout),
            mutation=False,
        )

    def info(self, context: DocumentContext, *, timeout: float | None = None) -> NativeCallResult[DocumentInfo]:
        def operation(app: Any) -> DocumentInfo:
            model = self._resolve_context(app, context)
            return DocumentInfo(context=self._context_from_doc(model), dirty=bool(self.api.document_dirty(model)))

        return self.session.execute(operation, stage="document_info", timeout=self._timeout(timeout))

    def refresh(self, context: DocumentContext, *, timeout: float | None = None) -> NativeCallResult[DocumentContext]:
        def operation(app: Any) -> DocumentContext:
            self._require_session(context)
            model = self.api.get_open_document(app, context.path)
            if model is None:
                raise NativeRuntimeError(
                    "document_not_open",
                    "document_refresh",
                    "Document is no longer open in the bound SolidWorks session.",
                )
            refreshed = self._context_from_doc(model)
            if self._same_path(refreshed.path, context.path) is False:
                raise NativeRuntimeError(
                    "document_context_mismatch",
                    "document_refresh",
                    "Resolved document path does not match the requested identity.",
                )
            return refreshed

        return self.session.execute(operation, stage="document_refresh", timeout=self._timeout(timeout))

    def save(self, context: DocumentContext, *, timeout: float | None = None) -> NativeCallResult[DocumentContext]:
        def operation(app: Any) -> DocumentContext:
            model = self._resolve_context(app, context)
            success, errors, warnings = self.api.save_document(model)
            if not success or int(errors) != 0:
                raise NativeRuntimeError(
                    "document_save_failed",
                    "document_save",
                    "SolidWorks failed to save the document.",
                    details={"errors": int(errors), "warnings": int(warnings)},
                )
            if bool(self.api.document_dirty(model)):
                raise NativeRuntimeError(
                    "document_save_postcondition_failed",
                    "document_save",
                    "Document still reports unsaved changes after save.",
                )
            return self._context_from_doc(model)

        return self.session.execute(
            operation,
            stage="document_save",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def save_as(
        self,
        context: DocumentContext,
        target_path: str | Path,
        *,
        timeout: float | None = None,
    ) -> NativeCallResult[DocumentContext]:
        try:
            target = self.path_policy.validate_save(target_path)
            if Path(target).suffix.lower() != context.document_type.native_extension:
                raise NativeRuntimeError(
                    "document_extension_mismatch",
                    "document_save_as",
                    "Save-as extension must match the native SolidWorks document type.",
                )
        except Exception as exc:
            return self._local_failure(exc, "document_save_as")

        def operation(app: Any) -> DocumentContext:
            model = self._resolve_context(app, context)
            success, errors, warnings = self.api.save_as(model, target)
            if not success or int(errors) != 0:
                raise NativeRuntimeError(
                    "document_save_as_failed",
                    "document_save_as",
                    "SolidWorks failed to save the document under the requested path.",
                    details={"errors": int(errors), "warnings": int(warnings)},
                )
            refreshed = self._context_from_doc(model)
            if not self._same_path(refreshed.path, target):
                raise NativeRuntimeError(
                    "document_save_postcondition_failed",
                    "document_save_as",
                    "SolidWorks document identity did not move to the requested save-as path.",
                )
            return refreshed

        return self.session.execute(
            operation,
            stage="document_save_as",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def close(self, context: DocumentContext, *, timeout: float | None = None) -> NativeCallResult[bool]:
        def operation(app: Any) -> bool:
            model = self._resolve_context(app, context)
            title = self.api.document_title(model)
            self.api.close_document(app, title)
            if self.api.get_open_document(app, context.path) is not None:
                raise NativeRuntimeError(
                    "document_close_postcondition_failed",
                    "document_close",
                    "Document remains open after close request.",
                )
            return True

        return self.session.execute(
            operation,
            stage="document_close",
            timeout=self._timeout(timeout),
            mutation=True,
            recovery_identity=(context.path, int(context.document_type), False),
            recovery_stage="document_reconcile",
            recovery_verifier=self._document_state_verifier(context.path, context.document_type, False, context.configuration),
        )

    def reopen(self, context: DocumentContext, *, timeout: float | None = None) -> NativeCallResult[DocumentContext]:
        def operation(app: Any) -> DocumentContext:
            model = self._resolve_context(app, context)
            title = self.api.document_title(model)
            self.api.close_document(app, title)
            if self.api.get_open_document(app, context.path) is not None:
                raise NativeRuntimeError(
                    "document_close_postcondition_failed",
                    "document_reopen",
                    "Document remains open; reopen was not attempted.",
                )
            reopened, errors, warnings = self.api.open_document(
                app,
                context.path,
                int(context.document_type),
                read_only=False,
                silent=True,
                configuration=context.configuration or "",
            )
            if reopened is None or int(errors) != 0:
                raise NativeRuntimeError(
                    "document_reopen_failed",
                    "document_reopen",
                    "SolidWorks failed to reopen the document after close.",
                    details={"errors": int(errors), "warnings": int(warnings)},
                )
            refreshed = self._context_from_doc(reopened)
            self._require_document_type(refreshed, context.document_type)
            return refreshed

        return self.session.execute(
            operation,
            stage="document_reopen",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def list_features(
        self,
        context: DocumentContext,
        *,
        timeout: float | None = None,
    ) -> NativeCallResult[tuple[FeatureInfo, ...]]:
        def operation(app: Any) -> tuple[FeatureInfo, ...]:
            model = self._resolve_context(app, context)
            items: list[FeatureInfo] = []
            feature = self.api.first_feature(model)
            while feature is not None:
                if len(items) >= self.max_query_items:
                    raise NativeRuntimeError(
                        "query_limit_exceeded",
                        "feature_list",
                        "Feature query exceeded its bounded item limit.",
                    )
                error_code, is_warning = self.api.feature_error(feature)
                items.append(
                    FeatureInfo(
                        name=self.api.feature_name(feature),
                        type_name=self.api.feature_type(feature),
                        error_code=int(error_code),
                        is_warning=bool(is_warning),
                    )
                )
                feature = self.api.next_feature(feature)
            return tuple(items)

        return self.session.execute(operation, stage="feature_list", timeout=self._timeout(timeout))

    def list_bodies(
        self,
        context: DocumentContext,
        *,
        visible_only: bool = False,
        timeout: float | None = None,
    ) -> NativeCallResult[tuple[BodyInfo, ...]]:
        def operation(app: Any) -> tuple[BodyInfo, ...]:
            model = self._resolve_context(app, context)
            current = self._context_from_doc(model)
            self._require_document_type(current, DocumentType.PART)
            items: list[BodyInfo] = []
            for body_type, label in ((0, "solid"), (1, "sheet")):
                for body in self.api.bodies(model, body_type, visible_only):
                    if len(items) >= self.max_query_items:
                        raise NativeRuntimeError(
                            "query_limit_exceeded",
                            "body_list",
                            "Body query exceeded its bounded item limit.",
                        )
                    items.append(BodyInfo(name=self.api.body_name(body), body_type=label))
            return tuple(items)

        return self.session.execute(operation, stage="body_list", timeout=self._timeout(timeout))

    def list_components(
        self,
        context: DocumentContext,
        *,
        top_level_only: bool = False,
        timeout: float | None = None,
    ) -> NativeCallResult[tuple[ComponentInfo, ...]]:
        def operation(app: Any) -> tuple[ComponentInfo, ...]:
            model = self._resolve_context(app, context)
            current = self._context_from_doc(model)
            self._require_document_type(current, DocumentType.ASSEMBLY)
            components = self.api.components(model, top_level_only)
            if len(components) > self.max_query_items:
                raise NativeRuntimeError(
                    "query_limit_exceeded",
                    "component_list",
                    "Component query exceeded its bounded item limit.",
                )
            return tuple(
                ComponentInfo(
                    name=self.api.component_name(component),
                    path=self.api.component_path(component),
                    suppressed=bool(self.api.component_suppressed(component)),
                    component_id=self.api.component_name(component),
                )
                for component in components
            )

        return self.session.execute(operation, stage="component_list", timeout=self._timeout(timeout))

    def rebuild(
        self,
        context: DocumentContext,
        *,
        timeout: float | None = None,
    ) -> NativeCallResult[RebuildResult]:
        def operation(app: Any) -> RebuildResult:
            model = self._resolve_context(app, context)
            result = rebuild_document(model, self.api, max_features=self.max_query_items)
            if not result.success:
                raise NativeRuntimeError(
                    "rebuild_failed",
                    "document_rebuild",
                    "SolidWorks rebuild or feature error verification failed.",
                    details={
                        "native_rebuild_ok": result.native_rebuild_ok,
                        "feature_error_count": sum(not issue.is_warning for issue in result.feature_issues),
                        "feature_warning_count": sum(issue.is_warning for issue in result.feature_issues),
                    },
                )
            return result

        return self.session.execute(
            operation,
            stage="document_rebuild",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def reconcile_document_state(
        self,
        call_id: str,
        *,
        path: str | Path,
        expected_type: DocumentType,
        should_be_open: bool,
        timeout: float | None = None,
    ) -> NativeCallResult[DocumentContext | bool]:
        try:
            candidate = self.path_policy.validate_save(path)
        except Exception as exc:
            return self._local_failure(exc, "document_reconcile")

        return self.session.reconcile(
            call_id, stage="document_reconcile", timeout=self._timeout(timeout),
            identity=(candidate, int(expected_type), should_be_open),
        )

    def _document_state_verifier(self, candidate, expected_type, should_be_open, configuration):
        def verifier(app):
            expected_configuration = configuration() if callable(configuration) else configuration
            model = self.api.get_open_document(app, candidate)
            if should_be_open:
                if model is None:
                    raise NativeRuntimeError("reconciliation_mismatch", "document_reconcile",
                                             "Original document is not open.")
                context = self._context_from_doc(model)
                self._require_document_type(context, expected_type)
                if not self._same_path(context.path, candidate) or (
                    expected_configuration is not None
                    and context.configuration != expected_configuration
                ):
                    raise NativeRuntimeError("reconciliation_mismatch", "document_reconcile",
                                             "Original document identity or configuration changed.")
                return context
            if model is not None:
                raise NativeRuntimeError("reconciliation_mismatch", "document_reconcile",
                                         "Original document is still open.")
            return True
        return verifier

    def _resolve_context(self, app: Any, context: DocumentContext) -> Any:
        self._require_session(context)
        model = self.api.get_open_document(app, context.path)
        if model is None:
            raise NativeRuntimeError(
                "document_not_open",
                "document_context",
                "Document identity no longer resolves in the bound SolidWorks session.",
            )
        actual = self._context_from_doc(model)
        if not self._same_path(actual.path, context.path) or actual.document_type is not context.document_type:
            raise NativeRuntimeError(
                "document_context_mismatch",
                "document_context",
                "Resolved document identity does not match the requested context.",
            )
        if context.configuration is not None and actual.configuration != context.configuration:
            raise NativeRuntimeError(
                "document_context_mismatch",
                "document_context",
                "Document configuration changed since the context was captured.",
            )
        if (
            context.update_stamp is not None
            and actual.update_stamp is not None
            and actual.update_stamp != context.update_stamp
        ):
            raise NativeRuntimeError(
                "stale_document_context",
                "document_context",
                "Document revision changed since the context was captured; refresh before mutation or query.",
            )
        return model

    def _context_from_doc(self, model: Any) -> DocumentContext:
        path = str(self.api.document_path(model) or "")
        if not path:
            raise NativeRuntimeError(
                "document_identity_unavailable",
                "document_context",
                "Unsaved documents without a stable path are not accepted as implicit mutation targets.",
            )
        canonical = self.path_policy.canonical(path)
        try:
            document_type = DocumentType(int(self.api.document_type(model)))
        except (TypeError, ValueError) as exc:
            raise NativeRuntimeError(
                "document_type_unknown",
                "document_context",
                "SolidWorks returned an unknown document type.",
            ) from exc
        configuration = self.api.active_configuration(model)
        if document_type in (DocumentType.PART, DocumentType.ASSEMBLY) and not configuration:
            raise NativeRuntimeError(
                "document_configuration_unavailable",
                "document_context",
                "SolidWorks did not provide the active configuration required for explicit document context.",
            )
        update_stamp = self.api.update_stamp(model)
        if update_stamp is None:
            raise NativeRuntimeError(
                "document_revision_unavailable",
                "document_context",
                "SolidWorks did not provide a document update stamp required for stale-context detection.",
            )
        return DocumentContext(
            session_id=self.session.session_id,
            path=canonical,
            title=str(self.api.document_title(model) or os.path.basename(canonical)),
            document_type=document_type,
            configuration=configuration,
            update_stamp=update_stamp,
        )

    def _require_session(self, context: DocumentContext) -> None:
        if context.session_id != self.session.session_id:
            raise NativeRuntimeError(
                "document_session_mismatch",
                "document_context",
                "Document context belongs to a different SolidWorks session.",
            )

    @staticmethod
    def _require_document_type(context: DocumentContext, expected: DocumentType) -> None:
        if context.document_type is not expected:
            raise NativeRuntimeError(
                "document_type_mismatch",
                "document_context",
                "Document type is not valid for the requested operation.",
            )

    @staticmethod
    def _same_path(left: str, right: str) -> bool:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))

    def _timeout(self, timeout: float | None) -> float:
        return self.default_timeout if timeout is None else max(0.0, float(timeout))

    @staticmethod
    def _local_failure(exc: Exception, stage: str) -> NativeCallResult[Any]:
        return NativeCallResult.failed(
            failure_from_exception(exc, stage),
            call_id=uuid.uuid4().hex,
            dispatched=False,
        )
