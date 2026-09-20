import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class QRCode(Base):
    """
    QR_CODES. This branch only reads QR codes (GET /registrations/{id}/qr).
    QR generation itself belongs to the Registration/QR service (API
    reference section 11 / 23) triggered after a successful payment.
    """

    __tablename__ = "qr_codes"
    __table_args__ = (
        CheckConstraint(
            "(team_member_id IS NOT NULL AND registration_id IS NULL) OR "
            "(team_member_id IS NULL AND registration_id IS NOT NULL)",
            name="ck_qr_codes_owner_xor",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("team_members.id"), unique=True, nullable=True
    )
    registration_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("registrations.id"), unique=True, nullable=True
    )
    token: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
