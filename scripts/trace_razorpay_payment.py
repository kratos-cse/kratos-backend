#!/usr/bin/env python3
"""
Trace Razorpay payment → internal Payment → Registration (read-only).

Usage:
  python scripts/trace_razorpay_payment.py --order-id order_xxx
  python scripts/trace_razorpay_payment.py --payment-id pay_xxx
  python scripts/trace_razorpay_payment.py --internal-payment-id <uuid>

Does not mutate production data. Set DATABASE_URL in the environment.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

# Allow running from repo root without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.payments.reconciliation import trace_payment, trace_to_dict


async def _run(args: argparse.Namespace) -> dict:
    url = (settings.DATABASE_URL or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit("DATABASE_URL is not set")

    engine = create_async_engine(settings.async_database_url)
    async with AsyncSession(engine) as db:
        trace = await trace_payment(
            db,
            razorpay_order_id=args.order_id,
            razorpay_payment_id=args.razorpay_payment_id,
            payment_id=args.internal_payment_id,
        )
        return trace_to_dict(trace)


def main() -> None:
    parser = argparse.ArgumentParser(description="Trace payment ↔ registration chain (read-only)")
    parser.add_argument("--order-id", dest="order_id", help="Razorpay order_id")
    parser.add_argument("--payment-id", dest="razorpay_payment_id", help="Razorpay payment_id")
    parser.add_argument("--internal-payment-id", type=uuid.UUID, dest="internal_payment_id")
    args = parser.parse_args()
    if not args.order_id and not args.razorpay_payment_id and not args.internal_payment_id:
        parser.error("Provide --order-id, --payment-id, or --internal-payment-id")

    payload = asyncio.run(_run(args))
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
