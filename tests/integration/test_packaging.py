"""Packaging regressions for the installable R0 provider artifact."""

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def test_packaging_includes_root_namespace_cli_and_runtime_guide() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    include = config["tool"]["setuptools"]["packages"]["find"]["include"]
    scripts = config["project"]["scripts"]

    assert "cdt_solidworks" in include
    assert "cdt_solidworks.*" in include
    assert scripts["cdt-solidworks"] == "cdt_solidworks.cli:main"
    assert (ROOT / "src" / "cdt_solidworks" / "cli.py").is_file()
    assert (ROOT / "src" / "cdt_solidworks" / "platform" / "PROVIDER_GUIDE.md").is_file()
