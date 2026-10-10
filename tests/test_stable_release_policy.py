"""Stable CDT release convention; avoid package/tag/contract identity drift."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_version_is_stable_three_numeric_fields():
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"\s*$', metadata, re.MULTILINE)
    assert match is not None
    version = match.group(1)
    assert re.fullmatch(r"0\.\d+\.\d+", version) is not None
    for relative in ["src/cdt_solidworks/platform/identity.py"]:
        assert f'"{version}"' in (ROOT / relative).read_text(encoding="utf-8"), relative


def test_release_workflow_publishes_only_stable_v_dot_versions():
    workflow = (ROOT / ".github/workflows/publish-release.yml").read_text(
        encoding="utf-8"
    )
    assert 'tags: ["v.0.*.*"]' in workflow
    assert 'tag != f"v.{version}"' in workflow
    assert "--prerelease" not in workflow
    assert "--verify-tag" in workflow
