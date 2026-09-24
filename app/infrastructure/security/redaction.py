"""Log redaction: never let secrets, cookies or tokens reach log output."""

from __future__ import annotations

import logging
import re

_PATTERNS = [
    (re.compile(r"(?i)bearer\s+[a-z0-9._\-]+"), "Bearer-[REDACTED]"),
    (re.compile(r"(?i)(authorization|x-api-token|x-api-key|api[_-]?key|token|secret|password|cookie|sessionid|li_at)"
                r"(\s*[=:]\s*|\"\s*:\s*\")([^\s\"',;&]+)"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)([?&](?:key|api_key|token|access_token)=)[^&\s]+"), r"\1[REDACTED]"),
]


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for h in root.handlers for f in h.filters):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "tensorflow", "absl", "h5py"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def safe_error(exc: BaseException, limit: int = 200) -> str:
    """Short, redacted error text suitable for storage / API responses."""
    text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return redact(text)[:limit]
