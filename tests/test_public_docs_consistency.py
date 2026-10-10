#!/usr/bin/env python3
"""Public documentation release identity and internal link safeguards."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _public_docs() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return [
        ROOT / Path(raw.decode("utf-8"))
        for raw in result.stdout.split(b"\0")
        if raw
        and raw.lower().endswith((b".md", b".mdx", b".rst"))
        and not raw.startswith(b"_private/")
    ]


def test_release_document_matches_package_and_is_linked_from_readme():
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = package["project"]["version"]
    assert re.fullmatch(r"0\.\d+\.\d+", version)
    release = ROOT / "docs" / "RELEASE_AND_DEPLOYMENT.md"
    assert release.is_file()
    text = release.read_text(encoding="utf-8")
    assert "`" + version + "`" in text
    assert f"v.{version}" in text
    assert "rollback" in text.lower()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/RELEASE_AND_DEPLOYMENT.md" in readme


def test_public_markdown_local_links_point_to_files():
    missing = []
    for file in _public_docs():
        if not file.is_file():
            continue
        text = file.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            target = match.group(1).split("#", 1)[0].split("?", 1)[0]
            if not target or target.startswith(
                ("/", "#", "https:", "http:", "mailto:", "data:")
            ):
                continue
            target = target.strip("<>").replace("%20", " ")
            if not (file.parent / target).exists():
                missing.append((str(file.relative_to(ROOT)), target))
    assert not missing, missing[:20]
