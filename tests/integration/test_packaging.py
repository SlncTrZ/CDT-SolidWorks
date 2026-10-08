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
    plugin_root = ROOT / "src" / "cdt_solidworks" / "integration" / "plugins"
    assert (plugin_root / "__init__.py").is_file()
    assert (plugin_root / "loader.py").is_file()


def test_packaged_provider_guide_exactly_matches_public_tool_guide() -> None:
    public = (ROOT / "docs" / "TOOL_GUIDE.md").read_bytes()
    packaged = (
        ROOT / "src" / "cdt_solidworks" / "platform" / "PROVIDER_GUIDE.md"
    ).read_bytes()
    assert packaged == public
