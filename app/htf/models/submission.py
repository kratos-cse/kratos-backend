import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.htf.models.application import HTFApplication


class HTFSubmission(Base):
    __tablename__ = "htf_submissions"
    __table_args__ = (
        UniqueConstraint("application_id", "version", name="uq_htf_submissions_app_version"),
        CheckConstraint("status IN ('SUBMITTED', 'REPLACED')", name="ck_htf_submissions_status"),
        Index("ix_htf_submissions_app_id", "application_id"),
        Index("ix_htf_submissions_status", "status"),
        Index("ix_htf_submissions_drive_file_id", "google_drive_file_id"),
        Index(
            "uq_htf_submissions_active",
            "application_id",
            unique=True,
            postgresql_where=text("status = 'SUBMITTED'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("htf_applications.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    google_drive_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    google_drive_folder_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="SUBMITTED", nullable=False)
    uploaded_by_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    application: Mapped["HTFApplication"] = relationship("HTFApplication", back_populates="submissions")
