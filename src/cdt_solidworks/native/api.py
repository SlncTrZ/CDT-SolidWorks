"""Late-bound Windows COM adapter for the SolidWorks API.

The adapter intentionally imports pywin32 only on the native worker thread so the
package remains importable and mock-testable on non-Windows integration hosts.
"""

from __future__ import annotations

import os
from typing import Any

from .errors import NativeRuntimeError


class WindowsComApi:
    """Narrow late-bound SolidWorks COM surface used by the B-lane services."""

    def __init__(self) -> None:
        self._pythoncom = None
        self._client = None
        self._initialized = False

    def initialize_thread(self) -> None:
        if os.name != "nt":
            raise NativeRuntimeError(
                "solidworks_unavailable",
                "com_initialize",
                "SolidWorks COM automation requires a Windows host.",
            )
        try:
            import pythoncom  # type: ignore[import-not-found]
            import win32com.client  # type: ignore[import-not-found]
        except ImportError as exc:
            raise NativeRuntimeError(
                "solidworks_dependency_missing",
                "com_initialize",
                "SolidWorks native runtime requires pywin32 on Windows.",
            ) from exc

        self._pythoncom = pythoncom
        self._client = win32com.client
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        self._initialized = True

    def uninitialize_thread(self) -> None:
        if self._initialized and self._pythoncom is not None:
            self._pythoncom.CoUninitialize()
        self._initialized = False

    @staticmethod
    def _member(obj: Any, name: str, *args: object) -> Any:
        member = getattr(obj, name)
        if args:
            return member(*args)
        if hasattr(member, "_oleobj_"):
            return member
        return member() if callable(member) else member

    def prog_id_registered(self, prog_id: str) -> bool:
        if os.name != "nt":
            return False
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"{prog_id}\\CLSID"):
                return True
        except FileNotFoundError:
            return False

    def attach_application(self, prog_id: str) -> Any:
        self._require_client()
        return self._client.GetActiveObject(prog_id)

    def start_application(self, prog_id: str) -> Any:
        self._require_client()
        # DispatchEx avoids silently treating an existing user process as provider-owned.
        return self._client.DispatchEx(prog_id)

    def set_visible(self, app: Any, visible: bool) -> None:
        app.Visible = bool(visible)

    def revision_number(self, app: Any) -> str:
        return str(self._member(app, "RevisionNumber"))

    def exit_application(self, app: Any) -> None:
        for name in ("ExitApp", "Quit"):
            try:
                member = getattr(app, name)
                if callable(member):
                    member()
                    return
            except Exception:
                continue
        raise NativeRuntimeError(
            "solidworks_exit_failed",
            "disconnect",
            "Provider-owned SolidWorks application could not be closed cleanly.",
        )

    def get_open_document(self, app: Any, path_or_title: str) -> Any | None:
        return app.GetOpenDocumentByName(str(path_or_title))

    def open_document(
        self,
        app: Any,
        path: str,
        doc_type: int,
        *,
        read_only: bool,
        silent: bool,
        configuration: str,
    ) -> tuple[Any | None, int, int]:
        self._require_client()
        errors = self._client.VARIANT(self._pythoncom.VT_BYREF | self._pythoncom.VT_I4, 0)
        warnings = self._client.VARIANT(self._pythoncom.VT_BYREF | self._pythoncom.VT_I4, 0)
        options = (2 if read_only else 0) | (1 if silent else 0)
        try:
            model = app.OpenDoc6(path, int(doc_type), options, configuration or "", errors, warnings)
        except TypeError:
            ole_object = getattr(app, "_oleobj_", None)
            if ole_object is None:
                raise
            dynamic_app = self._client.dynamic.DumbDispatch(ole_object, "SldWorks.Application")
            model = dynamic_app.OpenDoc6(path, int(doc_type), options, configuration or "", errors, warnings)

        if isinstance(model, (tuple, list)):
            values = list(model)
            model = values[0] if values else None
            if len(values) > 1 and values[1] is not None:
                errors.value = int(values[1])
            if len(values) > 2 and values[2] is not None:
                warnings.value = int(values[2])
        return model, int(errors.value), int(warnings.value)

    def save_document(self, model: Any) -> tuple[bool, int, int]:
        self._require_client()
        errors = self._client.VARIANT(self._pythoncom.VT_BYREF | self._pythoncom.VT_I4, 0)
        warnings = self._client.VARIANT(self._pythoncom.VT_BYREF | self._pythoncom.VT_I4, 0)
        value = model.Save3(1, errors, warnings)
        if isinstance(value, (tuple, list)):
            values = list(value)
            success = bool(values[0]) if values else False
            if len(values) > 1 and values[1] is not None:
                errors.value = int(values[1])
            if len(values) > 2 and values[2] is not None:
                warnings.value = int(values[2])
        else:
            success = bool(value)
        return success, int(errors.value), int(warnings.value)

    def save_as(self, model: Any, target_path: str) -> tuple[bool, int, int]:
        self._require_client()
        errors = self._client.VARIANT(self._pythoncom.VT_BYREF | self._pythoncom.VT_I4, 0)
        warnings = self._client.VARIANT(self._pythoncom.VT_BYREF | self._pythoncom.VT_I4, 0)
        empty_dispatch = self._client.VARIANT(self._pythoncom.VT_DISPATCH, None)
        value = model.Extension.SaveAs(target_path, 0, 1, empty_dispatch, errors, warnings)
        if isinstance(value, (tuple, list)):
            values = list(value)
            success = bool(values[0]) if values else False
            if len(values) > 1 and values[1] is not None:
                errors.value = int(values[1])
            if len(values) > 2 and values[2] is not None:
                warnings.value = int(values[2])
        else:
            success = bool(value)
        return success, int(errors.value), int(warnings.value)

    def close_document(self, app: Any, title: str) -> bool:
        app.CloseDoc(title)
        return True

    def document_path(self, model: Any) -> str:
        return str(self._member(model, "GetPathName") or "")

    def document_title(self, model: Any) -> str:
        return str(self._member(model, "GetTitle") or "")

    def document_type(self, model: Any) -> int:
        return int(self._member(model, "GetType"))

    def document_dirty(self, model: Any) -> bool:
        return bool(self._member(model, "GetSaveFlag"))

    def active_configuration(self, model: Any) -> str | None:
        try:
            manager = self._member(model, "ConfigurationManager")
            configuration = self._member(manager, "ActiveConfiguration")
            return str(self._member(configuration, "Name"))
        except Exception:
            return None

    def update_stamp(self, model: Any) -> int | None:
        try:
            return int(self._member(model, "GetUpdateStamp"))
        except Exception:
            return None

    def force_rebuild(self, model: Any, top_only: bool) -> bool:
        return bool(model.ForceRebuild3(bool(top_only)))

    def first_feature(self, model: Any) -> Any | None:
        return self._member(model, "FirstFeature")

    def next_feature(self, feature: Any) -> Any | None:
        return self._member(feature, "GetNextFeature")

    def feature_name(self, feature: Any) -> str:
        try:
            return str(self._member(feature, "Name"))
        except Exception:
            return str(self._member(feature, "GetNameForSelection"))

    def feature_type(self, feature: Any) -> str:
        return str(self._member(feature, "GetTypeName2") or "")

    def feature_error(self, feature: Any) -> tuple[int, bool]:
        self._require_client()
        warning = self._client.VARIANT(self._pythoncom.VT_BYREF | self._pythoncom.VT_BOOL, False)
        try:
            value = feature.GetErrorCode2(warning)
        except TypeError:
            value = feature.GetErrorCode2()
        if isinstance(value, (tuple, list)):
            values = list(value)
            code = int(values[0]) if values else 0
            warning_value = bool(values[1]) if len(values) > 1 else bool(warning.value)
            return code, warning_value
        return int(value), bool(warning.value)

    def bodies(self, model: Any, body_type: int, visible_only: bool) -> tuple[Any, ...]:
        value = model.GetBodies2(int(body_type), bool(visible_only))
        if value is None:
            return ()
        if isinstance(value, (tuple, list)):
            return tuple(value)
        return (value,)

    def body_name(self, body: Any) -> str:
        try:
            return str(self._member(body, "Name"))
        except Exception:
            return str(self._member(body, "GetName"))

    def persistent_reference(self, model: Any, entity: Any) -> bytes:
        extension = self._member(model, "Extension")
        value = self._member(extension, "GetPersistReference3", entity)
        if value is None:
            return b""
        if isinstance(value, bytes):
            return value
        if isinstance(value, bytearray):
            return bytes(value)
        if isinstance(value, (tuple, list)):
            return bytes(int(item) & 0xFF for item in value)
        try:
            return bytes(value)
        except Exception as exc:
            raise NativeRuntimeError(
                "topology_reference_read_failed",
                "topology_reference",
                "SOLIDWORKS returned an unsupported persistent-reference payload.",
            ) from exc

    def object_by_persistent_reference(self, model: Any, reference: bytes) -> tuple[Any | None, int]:
        self._require_client()
        extension = self._member(model, "Extension")
        payload = self._client.VARIANT(
            self._pythoncom.VT_ARRAY | self._pythoncom.VT_UI1,
            tuple(int(value) for value in reference),
        )
        error = self._client.VARIANT(
            self._pythoncom.VT_BYREF | self._pythoncom.VT_I4, 0
        )
        try:
            value = extension.GetObjectByPersistReference3(payload, error)
        except TypeError:
            value = extension.GetObjectByPersistReference3(payload)
        if isinstance(value, (tuple, list)):
            values = list(value)
            entity = values[0] if values else None
            state = int(values[1]) if len(values) > 1 and values[1] is not None else int(error.value)
            return entity, state
        return value, int(error.value)

    def sketch_reference_entity(self, sketch: Any) -> tuple[Any | None, int]:
        """Return a sketch reference entity and its swSelectType_e value."""
        self._require_client()
        entity_type = self._client.VARIANT(
            self._pythoncom.VT_BYREF | self._pythoncom.VT_I4, 0
        )
        reference = sketch.GetReferenceEntity(entity_type)
        return reference, int(entity_type.value)

    def null_dispatch(self) -> Any:
        self._require_client()
        return self._client.VARIANT(self._pythoncom.VT_DISPATCH, None)

    def dispatch_array(self, values: Any) -> Any:
        self._require_client()
        return self._client.VARIANT(
            self._pythoncom.VT_ARRAY | self._pythoncom.VT_DISPATCH,
            tuple(values),
        )

    def string_array(self, values: Any) -> Any:
        self._require_client()
        return self._client.VARIANT(
            self._pythoncom.VT_ARRAY | self._pythoncom.VT_BSTR,
            tuple(str(value) for value in values),
        )

    def empty_variant_array(self) -> Any:
        self._require_client()
        return self._client.VARIANT(
            self._pythoncom.VT_ARRAY | self._pythoncom.VT_VARIANT,
            (),
        )

    def components(self, model: Any, top_level_only: bool) -> tuple[Any, ...]:
        value = model.GetComponents(bool(top_level_only))
        if value is None:
            return ()
        if isinstance(value, (tuple, list)):
            return tuple(value)
        return (value,)

    def component_name(self, component: Any) -> str:
        return str(self._member(component, "Name2") or "")

    def component_path(self, component: Any) -> str:
        return str(self._member(component, "GetPathName") or "")

    def component_suppressed(self, component: Any) -> bool:
        return bool(self._member(component, "IsSuppressed"))

    def _require_client(self) -> None:
        if self._client is None or self._pythoncom is None:
            raise NativeRuntimeError(
                "solidworks_not_initialized",
                "com_call",
                "SolidWorks COM apartment is not initialized.",
            )
