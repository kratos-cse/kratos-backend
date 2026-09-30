"""HTTP middleware: request IDs and structured request logging via pure ASGI."""
import logging
import re
import time
import uuid

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("kratos.api")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")


def _resolve_request_id(headers: Headers) -> str:
    incoming = (headers.get("x-request-id") or "").strip()
    if incoming and _REQUEST_ID_RE.match(incoming):
        return incoming
    return str(uuid.uuid4())


class RequestLoggingMiddleware:
    """High-performance pure ASGI middleware (zero BaseHTTPMiddleware memory overhead)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = _resolve_request_id(headers)

        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["request_id"] = request_id

        path = scope.get("path", "")
        method = scope.get("method", "")
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
                duration_ms = (time.perf_counter() - started) * 1000

                raw_headers = list(message.get("headers", []))
                raw_headers.append((b"x-request-id", request_id.encode("latin-1")))
                raw_headers.append((b"x-response-time-ms", f"{duration_ms:.1f}".encode("latin-1")))
                message["headers"] = raw_headers

                if path.startswith("/api/v1") or path == "/health":
                    logger.info(
                        "request_completed request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
                        request_id,
                        method,
                        path,
                        status_code,
                        duration_ms,
                    )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "request_failed request_id=%s method=%s path=%s duration_ms=%.1f",
                request_id,
                method,
                path,
                duration_ms,
            )
            raise
