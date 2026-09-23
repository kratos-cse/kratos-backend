import asyncio
import time
from typing import Callable, TypeVar

from app.core.config import settings

T = TypeVar("T")
_client = None


def get_razorpay():
    """Lazy-import razorpay so the API can boot without the SDK until pay time."""
    global _client
    import razorpay

    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise RuntimeError("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set")
    if _client is None:
        _client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    return _client


def _is_retryable(err: Exception) -> bool:
    from razorpay.errors import BadRequestError, GatewayError, ServerError

    if isinstance(err, (ServerError, GatewayError)):
        return True
    if isinstance(err, BadRequestError):
        return "too many requests" in str(err).lower() or "rate limit" in str(err).lower()
    return False


def with_retry(fn: Callable[[], T], max_attempts: int = 5) -> T:
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
            delay_seconds *= 2
    raise last_error  # type: ignore[misc]


async def with_retry_async(fn: Callable[[], T], max_attempts: int = 5) -> T:
    """Run blocking Razorpay SDK + retry backoff off the asyncio event loop."""
    return await asyncio.to_thread(with_retry, fn, max_attempts)
