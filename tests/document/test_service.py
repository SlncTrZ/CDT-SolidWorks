import os
import tempfile
import unittest
from pathlib import Path

from cdt_solidworks.document.models import DocumentType
from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.document.service import DocumentService
from cdt_solidworks.native.errors import failure_from_exception
from cdt_solidworks.native.models import NativeCallResult, NativeCallState


class FakeDocument:
    def __init__(self, path: str, doc_type: DocumentType, configuration: str = "Default") -> None:
        self.path = os.path.realpath(path)
        self.doc_type = int(doc_type)
        self.title = os.path.basename(path)
        self.configuration = configuration
        self.update_stamp = 10
        self.dirty = False


class FakeApp:
    def __init__(self) -> None:
        self.documents = {}


class FakeApi:
    def __init__(self) -> None:
        self.save_calls = 0
        self.save_as_calls = 0
        self.close_calls = 0

    def get_open_document(self, app, path_or_title: str):
        canonical = os.path.realpath(path_or_title)
        if canonical in app.documents:
            return app.documents[canonical]
        for doc in app.documents.values():
            if doc.title == path_or_title:
                return doc
        return None

    def open_document(self, app, path: str, doc_type: int, *, read_only: bool, silent: bool, configuration: str):
        canonical = os.path.realpath(path)
        doc = app.documents.get(canonical)
        if doc is None:
            doc = FakeDocument(canonical, DocumentType(doc_type), configuration or "Default")
            app.documents[canonical] = doc
        return doc, 0, 0

    def document_path(self, doc) -> str:
        return doc.path

    def document_title(self, doc) -> str:
        return doc.title

    def document_type(self, doc) -> int:
        return doc.doc_type

    def active_configuration(self, doc):
        return doc.configuration

    def update_stamp(self, doc):
        return doc.update_stamp

    def document_dirty(self, doc) -> bool:
        return doc.dirty

    def save_document(self, doc):
        self.save_calls += 1
        doc.dirty = False
        doc.update_stamp += 1
        return True, 0, 0

    def save_as(self, doc, target_path: str):
        self.save_as_calls += 1
        doc.path = os.path.realpath(target_path)
        doc.title = os.path.basename(target_path)
        doc.update_stamp += 1
        return True, 0, 0

    def close_document(self, app, title: str):
        self.close_calls += 1
        for key, doc in list(app.documents.items()):
            if doc.title == title:
                del app.documents[key]
                return True
        return False


class FakeSession:
    def __init__(self, app, api) -> None:
        self.app = app
        self.api = api
        self.session_id = "session-test"
        self.calls = 0

    def execute(self, operation, *, stage: str, timeout: float, mutation: bool = False):
        self.calls += 1
        call_id = f"call-{self.calls}"
        try:
            return NativeCallResult.success(operation(self.app), call_id=call_id, dispatched=True)
        except Exception as exc:  # production dispatcher owns normalization
            return NativeCallResult.failed(failure_from_exception(exc, stage), call_id=call_id, dispatched=True)

    def reconcile(self, call_id, verifier, *, stage: str, timeout: float):
        try:
            return NativeCallResult.success(verifier(self.app), call_id=call_id, dispatched=True)
        except Exception as exc:
            return NativeCallResult.failed(failure_from_exception(exc, stage), call_id=call_id, dispatched=True)


class DocumentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.part_path = self.root / "fixture.SLDPRT"
        self.part_path.write_bytes(b"fixture")
        self.api = FakeApi()
        self.app = FakeApp()
        self.session = FakeSession(self.app, self.api)
        self.service = DocumentService(
            self.session,
            path_policy=DocumentPathPolicy((self.root,)),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_unconfigured_path_policy_fails_closed(self) -> None:
        service = DocumentService(self.session)
        result = service.open(self.part_path)
        self.assertEqual(NativeCallState.FAILURE, result.state)
        self.assertEqual("path_policy_unconfigured", result.failure.code)

    def test_open_returns_explicit_document_context(self) -> None:
        result = self.service.open(self.part_path, expected_type=DocumentType.PART)
        self.assertEqual(NativeCallState.SUCCESS, result.state)
        self.assertEqual(os.path.realpath(self.part_path), result.value.path)
        self.assertEqual(DocumentType.PART, result.value.document_type)
        self.assertEqual("Default", result.value.configuration)
        self.assertEqual("session-test", result.value.session_id)

    def test_info_and_save_return_fresh_verified_context(self) -> None:
        opened = self.service.open(self.part_path).value
        doc = self.api.get_open_document(self.app, opened.path)
        doc.dirty = True

        info = self.service.info(opened)
        self.assertEqual(NativeCallState.SUCCESS, info.state)
        self.assertTrue(info.value.dirty)

        saved = self.service.save(opened)
        self.assertEqual(NativeCallState.SUCCESS, saved.state)
        self.assertFalse(doc.dirty)
        self.assertGreater(saved.value.update_stamp, opened.update_stamp)
        self.assertEqual(1, self.api.save_calls)

    def test_stale_update_stamp_rejected_before_save_side_effect(self) -> None:
        opened = self.service.open(self.part_path).value
        doc = self.api.get_open_document(self.app, opened.path)
        doc.update_stamp += 1

        result = self.service.save(opened)
        self.assertEqual(NativeCallState.FAILURE, result.state)
        self.assertEqual("stale_document_context", result.failure.code)
        self.assertEqual(0, self.api.save_calls)

    def test_wrong_configuration_rejected_before_save_side_effect(self) -> None:
        opened = self.service.open(self.part_path).value
        doc = self.api.get_open_document(self.app, opened.path)
        doc.configuration = "Other"

        result = self.service.save(opened)
        self.assertEqual(NativeCallState.FAILURE, result.state)
        self.assertEqual("document_context_mismatch", result.failure.code)
        self.assertEqual(0, self.api.save_calls)

    def test_save_as_path_policy_blocks_outside_root_before_side_effect(self) -> None:
        opened = self.service.open(self.part_path).value
        outside = Path(self.tmp.name).parent / "outside.SLDPRT"

        result = self.service.save_as(opened, outside)
        self.assertEqual(NativeCallState.FAILURE, result.state)
        self.assertEqual("path_not_allowed", result.failure.code)
        self.assertEqual(0, self.api.save_as_calls)

    def test_save_as_preserves_native_document_extension_and_refreshes_identity(self) -> None:
        opened = self.service.open(self.part_path).value
        target = self.root / "renamed.sldprt"

        result = self.service.save_as(opened, target)
        self.assertEqual(NativeCallState.SUCCESS, result.state)
        self.assertEqual(os.path.realpath(target), result.value.path)
        self.assertEqual("renamed.sldprt", result.value.title)
        self.assertEqual(1, self.api.save_as_calls)

        wrong_ext = self.root / "renamed.step"
        rejected = self.service.save_as(result.value, wrong_ext)
        self.assertEqual(NativeCallState.FAILURE, rejected.state)
        self.assertEqual("document_extension_mismatch", rejected.failure.code)

    def test_close_then_reopen_resolves_by_identity_not_active_document(self) -> None:
        opened = self.service.open(self.part_path).value
        closed = self.service.close(opened)
        self.assertEqual(NativeCallState.SUCCESS, closed.state)
        self.assertIsNone(self.api.get_open_document(self.app, opened.path))

        reopened = self.service.open(self.part_path)
        self.assertEqual(NativeCallState.SUCCESS, reopened.state)
        self.assertEqual(opened.path, reopened.value.path)

    def test_reopen_is_single_mutation_and_returns_fresh_context(self) -> None:
        opened = self.service.open(self.part_path).value
        before_calls = self.session.calls
        result = self.service.reopen(opened)
        self.assertEqual(NativeCallState.SUCCESS, result.state)
        self.assertEqual(opened.path, result.value.path)
        self.assertEqual(before_calls + 1, self.session.calls)
        self.assertEqual(1, self.api.close_calls)

    def test_document_level_reconciliation_verifies_observed_state(self) -> None:
        opened = self.service.open(self.part_path).value
        result = self.service.reconcile_document_state(
            "uncertain-call",
            path=opened.path,
            expected_type=DocumentType.PART,
            should_be_open=True,
        )
        self.assertEqual(NativeCallState.SUCCESS, result.state)
        self.assertEqual(opened.path, result.value.path)


if __name__ == "__main__":
    unittest.main()
