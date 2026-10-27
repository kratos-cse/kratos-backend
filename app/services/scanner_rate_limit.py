"""In-process rate limit for unauthenticated scanner writes."""
from __future__ import annotations

import time

from fastapi import HTTPException, status

_WINDOW_SEC = 60.0
_MAX_HITS = 30
_hits: dict[str, list[float]] = {}


def reset_scanner_rate_limit() -> None:
    _hits.clear()


def enforce_scanner_rate_limit(client_key: str) -> None:
    now = time.monotonic()
    recent = [t for t in _hits.get(client_key, []) if now - t < _WINDOW_SEC]
    if len(recent) >= _MAX_HITS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many check-in requests. Wait a moment and try again.",
        )
    recent.append(now)
    _hits[client_key] = recent[-_MAX_HITS:]
