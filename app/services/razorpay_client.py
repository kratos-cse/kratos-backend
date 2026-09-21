"""Thin Razorpay client wrapper + async retry-with-backoff.

Razorpay enforces per-minute API rate limits. Retry transient failures (5xx,
and a 429 rate limit — which the SDK raises as a BadRequestError since it's
< 500, distinguished here by message) with exponential backoff instead of
failing a user's registration outright on a momentary limit hit.

The razorpay-python SDK is synchronous (blocking `requests` calls), so each
attempt runs in a worker thread via asyncio.to_thread rather than blocking
the event loop.
"""
import asyncio
from typing import Callable, TypeVar

import razorpay
from razorpay.errors import BadRequestError, GatewayError, ServerError

from app.core.config import settings

T = TypeVar("T")

_client: razorpay.Client | None = None


def get_razorpay() -> razorpay.Client:
    global _client
    if _client is None:
        _client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    return _client


def _is_retryable(err: Exception) -> bool:
    if isinstance(err, (ServerError, GatewayError)):
        return True
    if isinstance(err, BadRequestError):
        return "too many requests" in str(err).lower() or "rate limit" in str(err).lower()
    return False


# ponytail: fixed 5-attempt cap, no jitter — revisit if concurrent order creation
# at scale starts thundering-herding on the same backoff schedule.
async def with_retry(fn: Callable[[], T], max_attempts: int = 5) -> T:
    delay_seconds = 1.0
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await asyncio.to_thread(fn)
        except Exception as err:  # noqa: BLE001 - re-raised immediately unless retryable
            last_error = err
            if not _is_retryable(err) or attempt == max_attempts:
                raise
            await asyncio.sleep(delay_seconds)
            delay_seconds *= 2

    raise last_error  # unreachable, satisfies type checker
