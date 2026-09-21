"""Thin Razorpay client wrapper.

Mutating calls (such as order creation and refunds) are invoked directly
without blind retries to prevent duplicate remote orders or double-refunds.
"""
from typing import Callable, TypeVar

import razorpay

from app.core.config import settings

T = TypeVar("T")

_client: razorpay.Client | None = None


def get_razorpay() -> razorpay.Client:
    global _client
    if _client is None:
        _client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    return _client
