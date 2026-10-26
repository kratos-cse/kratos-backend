"""Redact sensitive query parameters from log lines (access logs, URLs)."""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "receipt_token",
        "token",
        "access_token",
        "id_token",
        "code",
        "signature",
        "razorpay_signature",
    }
)

_REDACTED = "[REDACTED]"

# Bearer tokens in free-form log text
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+\b", re.IGNORECASE)

# receipt_token=... in query strings embedded in log lines
_QUERY_TOKEN_RE = re.compile(
    r"([?&](?:receipt_token|access_token|id_token|token)=)[^&\s\"']+",
    re.IGNORECASE,
)


def sanitize_url(url: str) -> str:
    if not url:
        return url
    parts = urlsplit(url)
    if not parts.query:
        return url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    sanitized = [
        (key, _REDACTED if key.lower() in _SENSITIVE_QUERY_KEYS else value)
        for key, value in pairs
    ]
    new_query = urlencode(sanitized)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def sanitize_request_line(line: str) -> str:
    if not line:
        return line
    text = _QUERY_TOKEN_RE.sub(r"\1" + _REDACTED, line)
    return _BEARER_RE.sub("Bearer " + _REDACTED, text)


def sanitize_log_message(message: str) -> str:
    return sanitize_request_line(message)


class SensitiveLogFilter(logging.Filter):
    """Apply to uvicorn.access and similar loggers."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = sanitize_log_message(record.msg)
        if record.args:
            record.args = tuple(
                sanitize_request_line(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        return True


def install_sensitive_log_filters() -> None:
    filt = SensitiveLogFilter()
    for name in ("uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).addFilter(filt)
