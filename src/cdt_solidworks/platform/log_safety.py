"""Logging redaction guard for MCP unexpected-exception paths."""

from __future__ import annotations

import logging
import re
import traceback
from collections.abc import Iterable


_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_MCP_TOOL_LOGGER = "mcp.server.mcpserver.server"


class SensitiveLogFilter(logging.Filter):
    """Redact known credentials and suppress tracebacks that contain them."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def _redact(self, text: str) -> str:
        redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
        for secret in self._secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted

    def _contains_sensitive(self, text: str) -> bool:
        if _BEARER_PATTERN.search(text):
            return True
        return any(secret in text for secret in self._secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        redacted = self._redact(rendered)
        if redacted != rendered:
            record.msg = redacted
            record.args = ()

        if record.exc_info is not None:
            formatted_exception = "".join(traceback.format_exception(*record.exc_info))
            if self._contains_sensitive(formatted_exception):
                exception_type = record.exc_info[0].__name__
                record.exc_info = None
                record.exc_text = None
                record.msg = f"{record.getMessage()} [exception details redacted; type={exception_type}]"
                record.args = ()

        return True


def install_mcp_tool_log_safety(*, bearer_token: str | None) -> SensitiveLogFilter:
    """Install redaction on the SDK logger that emits tool exception tracebacks."""

    log_filter = SensitiveLogFilter((bearer_token,) if bearer_token else ())
    logging.getLogger(_MCP_TOOL_LOGGER).addFilter(log_filter)
    return log_filter
