import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Receipt(Base):
    """RECEIPTS — one per payment. pdf_url is NOT NULL per the brief's §2
    schema, so a row can't be inserted until a PDF exists; see the TODO in
    app/services/handoffs.py on who's expected to generate it.
    """

    __tablename__ = "receipts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("payments.id"), unique=True, nullable=False)
    receipt_number: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    pdf_url: Mapped[str] = mapped_column(String, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
