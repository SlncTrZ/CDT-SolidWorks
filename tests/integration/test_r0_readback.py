"""R0: native uncertainty and unreadable postconditions must survive wrappers."""

from types import SimpleNamespace
import pytest

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.integration.drawing_export_eval import IntegratedImportService
from cdt_solidworks.native.models import NativeCallResult, NativeCallState, NativeFailure
from cdt_solidworks.drawing.native import SolidWorksDrawingAdapter
from cdt_solidworks.mbd.native import SolidWorksMbdAdapter
from cdt_solidworks.assembly.native import AssemblyNativeAdapter


@pytest.mark.parametrize("state", [NativeCallState.UNCERTAIN_AFTER_DISPATCH,
                                    NativeCallState.TIMEOUT_BEFORE_DISPATCH,
                                    NativeCallState.FAILURE])
def test_import_preserves_native_result_end_to_end(tmp_path, state):
    source = tmp_path / "source.step"
    source.write_bytes(b"fixture")
    expected = NativeCallResult(state, "original-import-id",
                                failure=NativeFailure("injected", "import_foreign_model", "Injected"),
                                dispatched=state is not NativeCallState.TIMEOUT_BEFORE_DISPATCH)
    calls = []
    def execute(operation, **kwargs):
        calls.append(kwargs)
        return expected
    session = SimpleNamespace(api=object(), execute=execute)
    service = IntegratedImportService(session, path_policy=DocumentPathPolicy((tmp_path,)))
    actual = service.import_model(str(source), str(tmp_path / "target.SLDPRT"), "step")
    assert actual == expected
    assert len(calls) == 1
    assert not (tmp_path / "target.SLDPRT").exists()


@pytest.mark.parametrize("adapter_type", [SolidWorksDrawingAdapter, SolidWorksMbdAdapter])
def test_attachment_read_exception_does_not_report_clean(adapter_type):
    adapter = object.__new__(adapter_type)

    def fail(*args):
        raise RuntimeError("injected")

    adapter._api = SimpleNamespace(_member=fail)
    with pytest.raises(Exception, match="attachment_read_failed"):
        adapter._annotation_dangling(object())


@pytest.mark.parametrize("adapter_type", [SolidWorksDrawingAdapter, SolidWorksMbdAdapter])
def test_valid_free_note_is_not_rejected_as_dangling(adapter_type):
    adapter = object.__new__(adapter_type)
    adapter._api = SimpleNamespace(_member=lambda *args: ())
    assert adapter._annotation_dangling(object()) is False


@pytest.mark.parametrize("adapter_type", [SolidWorksDrawingAdapter, SolidWorksMbdAdapter])
def test_valid_attached_annotation_is_clean(adapter_type):
    adapter = object.__new__(adapter_type)
    adapter._api = SimpleNamespace(_member=lambda *args: (2,))
    assert adapter._annotation_dangling(object()) is False


@pytest.mark.parametrize("adapter_type", [SolidWorksDrawingAdapter, SolidWorksMbdAdapter])
def test_native_dangling_marker_is_reported(adapter_type):
    adapter = object.__new__(adapter_type)
    adapter._api = SimpleNamespace(_member=lambda *args: (0,))
    assert adapter._annotation_dangling(object()) is True


@pytest.mark.parametrize("member", ["ErrorStatus", "IsSuppressed2", "GetMateEntityCount", "ReferenceComponent", "Distance"])
def test_mate_required_read_exception_cannot_report_solved(member):
    adapter = object.__new__(AssemblyNativeAdapter)
    marker = object()
    values = {"GetSpecificFeature2": marker, "Type": 5, "IsSuppressed2": (False,),
              "GetDefinition": marker, "ErrorStatus": 0, "GetMateEntityCount": 2,
              "MateEntity": marker, "ReferenceComponent": marker, "Distance": 0.01}
    def read(obj, name, *args):
        if name == member:
            raise RuntimeError("injected")
        return values[name]
    adapter.api = SimpleNamespace(_member=read, feature_error=lambda _: (0, False),
                                  feature_name=lambda _: "mate", component_name=lambda _: "part")
    with pytest.raises(Exception):
        adapter._mate_snapshot(marker)
