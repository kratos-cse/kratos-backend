import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class HtfApplicationStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PPT_PENDING = "PPT_PENDING"
    PPT_SUBMITTED = "PPT_SUBMITTED"
    UNDER_SCREENING = "UNDER_SCREENING"
    SHORTLISTED = "SHORTLISTED"
    NOT_SHORTLISTED = "NOT_SHORTLISTED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    CONFIRMED = "CONFIRMED"
    WITHDRAWN = "WITHDRAWN"


class HtfApplication(Base):
    __tablename__ = "htf_applications"
    __table_args__ = (
        UniqueConstraint("event_id", "team_id", name="uq_htf_applications_event_team"),
        Index("ix_htf_applications_event_id", "event_id"),
        Index("ix_htf_applications_team_id", "team_id"),
        Index("ix_htf_applications_status", "status"),
        Index("ix_htf_applications_submitted_at", "submitted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    status: Mapped[HtfApplicationStatus] = mapped_column(
        Enum(HtfApplicationStatus, name="htf_application_status", native_enum=True),
        default=HtfApplicationStatus.DRAFT,
        nullable=False,
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    application_data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_by_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    event: Mapped["Event"] = relationship("Event")
    team: Mapped["Team"] = relationship("Team")
    created_by_profile: Mapped["Profile"] = relationship("Profile", foreign_keys=[created_by_profile_id])
