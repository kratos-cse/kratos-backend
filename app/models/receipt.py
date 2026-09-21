import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Receipt(Base):
    """
    RECEIPTS. This branch only reads receipts (GET /registrations/{id}/receipt).
    Receipt generation itself is triggered by the Payments feature branch
    after a successful payment (API reference section 23).
    """

    __tablename__ = "receipts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("payments.id"), unique=True, nullable=False)
    receipt_number: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    pdf_url: Mapped[str] = mapped_column(String, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
