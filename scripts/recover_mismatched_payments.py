#!/usr/bin/env python3
"""
Bulk payment recovery — dry-run by default.

  python scripts/recover_mismatched_payments.py
  python scripts/recover_mismatched_payments.py --apply --operator admin@example.com

Never run --apply against production without reviewing the dry-run report first.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.enums import PaymentStatus, RegistrationStatus
from app.models.payment import Payment
from app.models.registration import Registration
from app.payments.razorpay_verify import find_verified_capture_for_payment
from app.payments.reconciliation import ReconcileOutcome, reconcile_local_payment_state, resolve_registration_for_payment


async def _scan(db: AsyncSession, *, event_id: uuid.UUID | None, apply: bool, operator: str) -> dict:
    stmt = select(Payment).where(Payment.status.in_([PaymentStatus.CREATED, PaymentStatus.FAILED]))
    result = await db.execute(stmt)
    payments = list(result.scalars().all())

    summary = {
        "inspected": 0,
        "eligible": 0,
        "already_correct": 0,
        "manual_review": 0,
        "corrected": 0,
        "dry_run": not apply,
        "operator": operator if apply else None,
        "rows": [],
    }

    for payment in payments:
        if event_id is not None:
            registration = await resolve_registration_for_payment(db, payment)
            if registration is None or registration.event_id != event_id:
                continue

        summary["inspected"] += 1
        registration = await resolve_registration_for_payment(db, payment)
        verified = await find_verified_capture_for_payment(payment)

        row = {
            "payment_id": str(payment.id),
            "registration_id": str(registration.id) if registration else None,
            "payment_status": payment.status.value,
            "registration_status": registration.status.value if registration else None,
            "razorpay_order_id": payment.razorpay_order_id,
            "provider_captured": verified is not None,
            "proposed": None,
            "outcome": None,
        }

        if registration is None:
            row["outcome"] = "manual_review"
            row["reason"] = "no verifiable registration link"
            summary["manual_review"] += 1
        elif registration.status == RegistrationStatus.CANCELLED:
            row["outcome"] = "manual_review"
            row["reason"] = "registration cancelled"
            summary["manual_review"] += 1
        elif verified is None:
            row["outcome"] = "no_capture_at_provider"
            if payment.status == PaymentStatus.PAID:
                summary["already_correct"] += 1
            else:
                summary["manual_review"] += 1
        elif registration.status == RegistrationStatus.CONFIRMED and payment.status == PaymentStatus.PAID:
            row["outcome"] = "already_correct"
            summary["already_correct"] += 1
        else:
            row["outcome"] = "eligible"
            row["proposed"] = "apply_payment_success via reconcile"
            summary["eligible"] += 1
            if apply:
                recon = await reconcile_local_payment_state(
                    db, payment, razorpay_payment_id=verified.razorpay_payment_id
                )
                row["reconcile_outcome"] = recon.outcome.value
                if recon.applied:
                    summary["corrected"] += 1

        summary["rows"].append(row)

    return summary


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Recover mismatched Razorpay captures")
    parser.add_argument("--apply", action="store_true", help="Apply corrections (default is dry-run)")
    parser.add_argument("--event-id", type=uuid.UUID, default=None)
    parser.add_argument("--operator", type=str, default="cli")
    args = parser.parse_args()

    url = settings.async_database_url
    engine = create_async_engine(url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        report = await _scan(
            db,
            event_id=args.event_id,
            apply=args.apply,
            operator=args.operator,
        )
        if args.apply:
            await db.commit()

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
