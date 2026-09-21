import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import PaymentStatus, PaymentType


class Payment(Base):
    """
    Minimal mirror of PAYMENTS, defined here only so Registration/Receipt
    foreign keys resolve and so the registration/receipt endpoints in this
    branch can READ payment status. The actual Razorpay create-order /
    verify / webhook logic (API reference section 8) belongs to the
    Payments feature branch — do not add write paths for payments here.
    """

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payer_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    payment_type: Mapped[PaymentType] = mapped_column(Enum(PaymentType, name="payment_type"), nullable=False)
    team_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("team_members.id"), nullable=True
    )
    razorpay_order_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    razorpay_payment_id: Mapped[Optional[str]] = mapped_column(String, unique=True, nullable=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String, default="INR", server_default="INR")
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status"), default=PaymentStatus.CREATED, nullable=False
    )
    refund_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    refund_amount_paise: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    refunded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    refund_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
