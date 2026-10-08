from __future__ import annotations

import tomllib
from pathlib import Path

from cdt_solidworks.platform.identity import PROVIDER_VERSION


def test_runtime_provider_version_matches_package_metadata() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    metadata = tomllib.loads((repository_root / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["version"] == PROVIDER_VERSION
