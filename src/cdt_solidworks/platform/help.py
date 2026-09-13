"""Runtime-backed provider help contract."""

from __future__ import annotations

import hashlib
from importlib import resources
from pathlib import Path

from cdt_solidworks.platform.identity import (
    AUTHENTICATION_DESCRIPTION,
    CONTRACT_UPDATED_AT,
    CONTRACT_VERSION,
    PROTOCOL_VERSION,
    PROVIDER_ID,
    PROVIDER_VERSION,
)
from cdt_solidworks.platform.models import HelpResponse


class HelpService:
    """Read the current provider guide without mutating runtime or guide state."""

    def __init__(self, guide_path: Path | None = None) -> None:
        self._guide_path = guide_path

    def _read_content(self) -> str:
        if self._guide_path is not None:
            raw = self._guide_path.read_text(encoding="utf-8")
        else:
            raw = (
                resources.files("cdt_solidworks.platform")
                .joinpath("PROVIDER_GUIDE.md")
                .read_text(encoding="utf-8")
            )
        return raw.replace("\r\n", "\n").replace("\r", "\n")

    def read(self) -> HelpResponse:
        content = self._read_content()
        contract_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return HelpResponse(
            provider_name=PROVIDER_ID,
            provider_version=PROVIDER_VERSION,
            protocol_version=PROTOCOL_VERSION,
            contract_version=CONTRACT_VERSION,
            contract_hash=contract_hash,
            updated_at=CONTRACT_UPDATED_AT,
            authentication=AUTHENTICATION_DESCRIPTION,
            capabilities=("help", "system_status", "system_capabilities"),
            content=content,
        )
