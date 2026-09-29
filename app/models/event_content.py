import uuid
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    ContentSectionType,
    RegistrationFieldScope,
    RegistrationFieldSource,
    RegistrationFieldType,
)

if TYPE_CHECKING:
    from app.models.event import Event


class EventContentSection(Base):
    __tablename__ = "event_content_sections"
    __table_args__ = (Index("ix_event_content_sections_event_id", "event_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    section_type: Mapped[ContentSectionType] = mapped_column(
        Enum(ContentSectionType, name="content_section_type", native_enum=True),
        default=ContentSectionType.CUSTOM,
        nullable=False,
    )
    display_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    event: Mapped["Event"] = relationship("Event", back_populates="content_sections")


class EventCoordinator(Base):
    __tablename__ = "event_coordinators"
    __table_args__ = (Index("ix_event_coordinators_event_id", "event_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    event: Mapped["Event"] = relationship("Event", back_populates="coordinators")


class EventRegistrationField(Base):
    __tablename__ = "event_registration_fields"
    __table_args__ = (
        UniqueConstraint("event_id", "scope", "field_key", name="uq_event_registration_fields_event_scope_key"),
        Index("ix_event_registration_fields_event_id", "event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    scope: Mapped[RegistrationFieldScope] = mapped_column(
        Enum(RegistrationFieldScope, name="registration_field_scope", native_enum=True),
        nullable=False,
    )
    field_key: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    field_type: Mapped[RegistrationFieldType] = mapped_column(
        Enum(RegistrationFieldType, name="registration_field_type", native_enum=True),
        nullable=False,
    )
    required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    placeholder: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    help_text: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    options: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    source: Mapped[RegistrationFieldSource] = mapped_column(
        Enum(RegistrationFieldSource, name="registration_field_source", native_enum=True),
        default=RegistrationFieldSource.CUSTOM,
        nullable=False,
    )
    profile_field_key: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    event: Mapped["Event"] = relationship("Event", back_populates="registration_fields")
    responses: Mapped[list["RegistrationFieldResponse"]] = relationship(
        "RegistrationFieldResponse", back_populates="field"
    )


class RegistrationFieldResponse(Base):
    __tablename__ = "registration_field_responses"
    __table_args__ = (
        CheckConstraint(
            "(registration_id IS NOT NULL AND team_member_id IS NULL) OR "
            "(registration_id IS NULL AND team_member_id IS NOT NULL)",
            name="ck_registration_field_responses_target",
        ),
        UniqueConstraint("field_id", "registration_id", name="uq_field_response_registration"),
        UniqueConstraint("field_id", "team_member_id", name="uq_field_response_team_member"),
        Index("ix_registration_field_responses_registration_id", "registration_id"),
        Index("ix_registration_field_responses_team_member_id", "team_member_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_registration_fields.id"), nullable=False
    )
    registration_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("registrations.id"), nullable=True
    )
    team_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("team_members.id"), nullable=True
    )
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    field: Mapped["EventRegistrationField"] = relationship("EventRegistrationField", back_populates="responses")
