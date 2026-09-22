import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import RegistrationStatus


class Registration(Base):
    __tablename__ = "registrations"
    __table_args__ = (
        CheckConstraint(
            "(team_id IS NOT NULL AND profile_id IS NULL) OR (team_id IS NULL AND profile_id IS NOT NULL)",
            name="ck_registrations_team_xor_profile",
        ),
        Index(
            "uq_registrations_event_profile_active",
            "event_id",
            "profile_id",
            unique=True,
            postgresql_where=text("profile_id IS NOT NULL AND status <> 'CANCELLED'"),
        ),
        Index(
            "uq_registrations_event_team_active",
            "event_id",
            "team_id",
            unique=True,
            postgresql_where=text("team_id IS NOT NULL AND status <> 'CANCELLED'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    team_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=True)
    profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=True
    )
    payment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id"), nullable=True
    )
    status: Mapped[RegistrationStatus] = mapped_column(
        Enum(RegistrationStatus, name="registration_status"), default=RegistrationStatus.PENDING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    team: Mapped[Optional["Team"]] = relationship("Team")
    payment: Mapped[Optional["Payment"]] = relationship("Payment")
