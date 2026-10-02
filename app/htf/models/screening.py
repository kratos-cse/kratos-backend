import uuid
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.htf.models.application import HTFApplication


class HTFScreeningResult(Base):
    __tablename__ = "htf_screening_results"
    __table_args__ = (
        UniqueConstraint("application_id", name="uq_htf_screening_results_app_id"),
        CheckConstraint("result IN ('SHORTLISTED', 'NOT_SHORTLISTED')", name="ck_htf_screening_result"),
        Index("ix_htf_screening_results_result", "result"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("htf_applications.id", ondelete="CASCADE"), nullable=False
    )
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decided_by_admin_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False
    )
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    application: Mapped["HTFApplication"] = relationship("HTFApplication", back_populates="screening_result")
