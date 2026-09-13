from __future__ import annotations

import hashlib
from pathlib import Path

from mcp_types import LATEST_PROTOCOL_VERSION

from cdt_solidworks.platform.help import HelpService


def test_help_is_runtime_backed_read_only_and_deterministically_fingerprinted(tmp_path: Path) -> None:
    guide = tmp_path / "guide.md"
    guide.write_text("# Runtime guide\r\n\r\nOnly current platform tools.\r\n", encoding="utf-8")
    before = guide.read_bytes()

    service = HelpService(guide_path=guide)
    first = service.read()
    second = service.read()

    expected_content = "# Runtime guide\n\nOnly current platform tools.\n"
    assert first == second
    assert first.provider_name == "solidworks"
    assert first.provider_version == "0.1.0"
    assert first.protocol_version == LATEST_PROTOCOL_VERSION
    assert first.contract_version == "0.1.0"
    assert first.authentication == "Bearer token required in network mode"
    assert first.content == expected_content
    assert first.contract_hash == hashlib.sha256(expected_content.encode("utf-8")).hexdigest()
    assert guide.read_bytes() == before


def test_default_help_guide_is_current_platform_contract_only() -> None:
    result = HelpService().read()

    assert "help" in result.content
    assert "system_status" in result.content
    assert "system_capabilities" in result.content
    assert "W2" not in result.content
    assert "W3" not in result.content
    assert "W4" not in result.content
