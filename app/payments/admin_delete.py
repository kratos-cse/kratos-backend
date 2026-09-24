"""Super-admin hard delete for payment records (including PAID/REFUNDED)."""
import uuid

from fastapi import HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import AdminUser
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.receipt import Receipt
from app.models.registration import Registration
from app.services import audit_service


async def admin_delete_payment(db: AsyncSession, payment_id: uuid.UUID, admin: AdminUser) -> dict:
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    await db.execute(delete(Notification).where(Notification.payment_id == payment_id))
    await db.execute(delete(Receipt).where(Receipt.payment_id == payment_id))
    await db.execute(update(Registration).where(Registration.payment_id == payment_id).values(payment_id=None))
    await db.execute(delete(Payment).where(Payment.id == payment_id))
    await db.commit()

    audit_service.log_activity_bg(
        action="ADMIN_PAYMENT_DELETE",
        resource_type="payment",
        resource_id=payment_id,
        actor_user_id=admin.user_id,
        actor_role="SUPER_ADMIN",
        details={
            "status": str(payment.status),
            "razorpay_order_id": payment.razorpay_order_id,
            "amount_paise": payment.amount_paise,
        },
    )

    return {"id": str(payment_id), "deleted": True}
