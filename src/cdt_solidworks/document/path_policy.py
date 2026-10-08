"""Document path validation for native open/save operations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from cdt_solidworks.native.errors import NativeRuntimeError


class DocumentPathPolicy:
    def __init__(self, allowed_roots: Iterable[str | Path] = ()) -> None:
        self._allowed_roots = tuple(self._canonical(root) for root in allowed_roots)

    @staticmethod
    def _canonical(path: str | Path) -> Path:
        expanded = os.path.expandvars(os.path.expanduser(str(path)))
        return Path(expanded).resolve(strict=False)

    def canonical(self, path: str | Path) -> str:
        return str(self._canonical(path))

    def validate_open(self, path: str | Path) -> str:
        raw = Path(os.path.expandvars(os.path.expanduser(str(path))))
        if not raw.is_absolute():
            raise NativeRuntimeError(
                "path_not_absolute",
                "path_validation",
                "Document paths must be absolute.",
            )
        candidate = self._canonical(raw)
        self._require_allowed(candidate)
        if not candidate.exists() or not candidate.is_file():
            raise NativeRuntimeError(
                "document_not_found",
                "path_validation",
                "Document path does not exist or is not a file.",
            )
        return str(candidate)

    def validate_save(self, path: str | Path) -> str:
        raw = Path(os.path.expandvars(os.path.expanduser(str(path))))
        if not raw.is_absolute():
            raise NativeRuntimeError(
                "path_not_absolute",
                "path_validation",
                "Document paths must be absolute.",
            )
        candidate = self._canonical(raw)
        self._require_allowed(candidate)
        if not candidate.parent.exists() or not candidate.parent.is_dir():
            raise NativeRuntimeError(
                "save_parent_missing",
                "path_validation",
                "Save target parent directory does not exist.",
            )
        return str(candidate)

    def _require_allowed(self, candidate: Path) -> None:
        if not self._allowed_roots:
            raise NativeRuntimeError(
                "path_policy_unconfigured",
                "path_validation",
                "Document access is disabled until at least one allowed root is configured.",
            )
        for root in self._allowed_roots:
            if candidate == root or root in candidate.parents:
                return
        raise NativeRuntimeError(
            "path_not_allowed",
            "path_validation",
            "Document path is outside configured allowed roots.",
        )
