import asyncio
import time
from typing import Callable, TypeVar

import requests

from app.core.config import settings

T = TypeVar("T")
_client = None

# (connect, read) seconds — avoids hanging until the platform kills the worker.
_DEFAULT_TIMEOUT = (5.0, 30.0)


def _http_timeout() -> tuple[float, float]:
    connect = float(getattr(settings, "RAZORPAY_HTTP_TIMEOUT_CONNECT", 5.0) or 5.0)
    read = float(getattr(settings, "RAZORPAY_HTTP_TIMEOUT_READ", 30.0) or 30.0)
    return (connect, read)


def _install_request_timeout(client) -> None:
    session = client.session
    timeout = _http_timeout()
    original = session.request

    def request_with_timeout(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return original(method, url, **kwargs)

    session.request = request_with_timeout  # type: ignore[method-assign]


def get_razorpay():
    """Lazy-import razorpay so the API can boot without the SDK until pay time."""
    global _client
    import razorpay

    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise RuntimeError("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set")
    if _client is None:
        _client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        _install_request_timeout(_client)
    return _client


def _is_retryable(err: Exception) -> bool:
    from razorpay.errors import BadRequestError, GatewayError, ServerError

    if isinstance(err, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(err, (ServerError, GatewayError)):
        return True
    if isinstance(err, BadRequestError):
        return "too many requests" in str(err).lower() or "rate limit" in str(err).lower()
    return False


def with_retry(fn: Callable[[], T], max_attempts: int = 3) -> T:
    delay_seconds = 1.0
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as err:
            last_error = err
            if not _is_retryable(err) or attempt == max_attempts:
                raise
            time.sleep(delay_seconds)
            delay_seconds = min(delay_seconds * 2, 8.0)
    raise last_error  # type: ignore[misc]


async def with_retry_async(fn: Callable[[], T], max_attempts: int = 3) -> T:
    """Run blocking Razorpay SDK + retry backoff off the asyncio event loop."""
    return await asyncio.to_thread(with_retry, fn, max_attempts)


def razorpay_unavailable_message() -> str:
    return "Payment gateway is temporarily unavailable. Please try again in a moment."
