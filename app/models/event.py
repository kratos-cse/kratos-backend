import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    CapacityType,
    EventCategory,
    EventSlot,
    EventStatus,
    MemberRegistrationMode,
    RegistrationMode,
)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_starts_at", "starts_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    tagline: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    short_desc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    long_desc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[EventCategory]] = mapped_column(
        Enum(EventCategory, name="event_category", native_enum=True),
        nullable=True,
    )
    coordinator: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    coord_contact: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    venue: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    capacity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    whatsapp_group_link: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    google_sheet_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    google_sheet_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, name="event_status"), default=EventStatus.OPEN, nullable=False
    )
    slot: Mapped[Optional[EventSlot]] = mapped_column(Enum(EventSlot, name="event_slot"), nullable=True)

    rules: Mapped[Optional["EventRegistrationRule"]] = relationship(
        "EventRegistrationRule", back_populates="event", uselist=False
    )


class EventRegistrationRule(Base):
    __tablename__ = "event_registration_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), unique=True, nullable=False
    )
    team_min_size: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    team_max_size: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # Explicit roster model (5+2 etc). team_min/max stay synced for legacy callers.
    required_member_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    substitute_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    allow_individual: Mapped[bool] = mapped_column(default=True, server_default="true")
    registration_mode: Mapped[RegistrationMode] = mapped_column(
        Enum(RegistrationMode, name="registration_mode"),
        default=RegistrationMode.TEAM_OR_INDIVIDUAL,
        nullable=False,
    )
    allow_team_invite_flow: Mapped[bool] = mapped_column(default=False, server_default="false")
    requires_qr_checkin: Mapped[bool] = mapped_column(default=True, server_default="true")
    capacity_type: Mapped[CapacityType] = mapped_column(
        Enum(CapacityType, name="capacity_type"), default=CapacityType.PARTICIPANTS, nullable=False
    )
    member_registration_mode: Mapped[MemberRegistrationMode] = mapped_column(
        Enum(MemberRegistrationMode, name="member_registration_mode"),
        default=MemberRegistrationMode.SELF_ENTRY,
        nullable=False,
    )
    custom_fields: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    registration_opens_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    registration_closes_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    event: Mapped["Event"] = relationship("Event", back_populates="rules")
