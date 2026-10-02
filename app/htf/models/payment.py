import uuid
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.htf.models.application import HTFApplication


class HTFPayment(Base):
    __tablename__ = "htf_payments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED', 'PAID', 'FAILED', 'REFUNDED')",
            name="ck_htf_payments_status",
        ),
        Index("ix_htf_payments_app_id", "application_id"),
        Index("ix_htf_payments_status", "status"),
        Index(
            "uq_htf_payments_live",
            "application_id",
            unique=True,
            postgresql_where=text("status IN ('CREATED', 'PAID')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("htf_applications.id", ondelete="CASCADE"), nullable=False
    )
    payer_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False
    )
    razorpay_order_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    razorpay_payment_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="CREATED", nullable=False)

    refund_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    refund_amount_paise: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    refunded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    refund_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    application: Mapped["HTFApplication"] = relationship("HTFApplication", back_populates="payments")
