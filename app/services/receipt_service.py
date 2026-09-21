"""Payment receipt PDF generation (idempotent per payment_id)."""
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NOT_FOUND, AppError
from app.models.payment import Payment
from app.models.receipt import Receipt


def _receipt_number() -> str:
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = secrets.token_hex(4).upper()
    return f"KR-{date_part}-{suffix}"


def _write_receipt_pdf(*, path: Path, payment: Payment, receipt_number: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    y = height - 72
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, y, "KRATOS'26 Payment Receipt")
    y -= 36
    c.setFont("Helvetica", 11)
    lines = [
        f"Receipt number: {receipt_number}",
        f"Payment ID: {payment.id}",
        f"Type: {payment.payment_type.value}",
        f"Amount: {payment.amount_paise / 100:.2f} {payment.currency}",
        f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
    ]
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.showPage()
    c.save()


async def ensure_receipt(db: AsyncSession, payment_id: uuid.UUID) -> Receipt:
    existing = await db.execute(select(Receipt).where(Receipt.payment_id == payment_id))
    receipt = existing.scalar_one_or_none()
    if receipt is not None:
        return receipt

    payment_result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = payment_result.scalar_one_or_none()
    if payment is None:
        raise AppError(NOT_FOUND, "Payment not found", status_code=404)

    receipt_number = _receipt_number()
    storage_dir = Path(settings.RECEIPT_STORAGE_DIR)
    filename = f"{receipt_number}.pdf"
    pdf_path = storage_dir / filename
    _write_receipt_pdf(path=pdf_path, payment=payment, receipt_number=receipt_number)

    pdf_url = f"{settings.APP_PUBLIC_BASE_URL.rstrip('/')}/media/receipts/{filename}"
    receipt = Receipt(
        payment_id=payment_id,
        receipt_number=receipt_number,
        pdf_url=pdf_url,
    )
    db.add(receipt)
    await db.flush()
    return receipt
