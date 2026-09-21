"""Calls into services owned by other tracks. This module triggers them and
passes context — it does not generate QR tokens, send emails, or render PDFs
itself.

TODO(integration-owner): wire these to the real QR / notification / receipts
services once their endpoints or internal APIs exist. Logging is a
placeholder, not a delivery mechanism.
"""
import logging
import uuid
from typing import Optional

logger = logging.getLogger("payments.handoffs")


def trigger_qr(registration_id: Optional[uuid.UUID] = None, team_member_id: Optional[uuid.UUID] = None) -> None:
    logger.info("[handoff] trigger_qr registration_id=%s team_member_id=%s", registration_id, team_member_id)


def send_notification(kind: str, payer_profile_id: uuid.UUID, amount_paise: int, reason: Optional[str] = None) -> None:
    logger.info(
        "[handoff] send_notification kind=%s payer_profile_id=%s amount_paise=%s reason=%s",
        kind, payer_profile_id, amount_paise, reason,
    )


# TODO(receipts-owner): Receipt.pdf_url is NOT NULL (app/models/receipt.py),
# so this can't insert a row until a PDF exists. Left as a call-out rather
# than a write.
def issue_receipt(payment_id: uuid.UUID) -> None:
    logger.info("[handoff] issue_receipt payment_id=%s", payment_id)
