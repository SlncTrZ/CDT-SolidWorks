"""Rebuild verification primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import NativeRuntimeError


@dataclass(frozen=True)
class FeatureIssue:
    feature_name: str
    error_code: int
    is_warning: bool


@dataclass(frozen=True)
class RebuildResult:
    success: bool
    native_rebuild_ok: bool
    feature_issues: tuple[FeatureIssue, ...]


def rebuild_document(model: Any, api: Any, *, max_features: int = 100_000) -> RebuildResult:
    native_ok = bool(api.force_rebuild(model, False))
    issues: list[FeatureIssue] = []
    feature = api.first_feature(model)
    visited = 0
    while feature is not None:
        visited += 1
        if visited > max_features:
            raise NativeRuntimeError(
                "feature_traversal_limit",
                "rebuild_verify",
                "Feature traversal exceeded its bounded verification limit.",
            )
        code, is_warning = api.feature_error(feature)
        if int(code) != 0:
            issues.append(
                FeatureIssue(
                    feature_name=api.feature_name(feature),
                    error_code=int(code),
                    is_warning=bool(is_warning),
                )
            )
        feature = api.next_feature(feature)

    has_errors = any(not issue.is_warning for issue in issues)
    return RebuildResult(
        success=native_ok and not has_errors,
        native_rebuild_ok=native_ok,
        feature_issues=tuple(issues),
    )
