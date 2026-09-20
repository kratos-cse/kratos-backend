"""
*** TEMPORARY ***
Minimal stand-ins for models owned by other people's modules (Events,
Profiles). The Teams service only reads a handful of columns from
each - team_max_size, fee_charge_model, registration window, and a
profile's full_name - so these are intentionally partial.

DELETE THIS FILE once the real app/models/event.py and
app/models/profile.py are merged, and update the two imports in
app/services/team_service.py to point at them instead. Column names
below match kratos_database_schema_final.xlsx, so the swap should be
a pure import change with no logic change.
"""
import uuid

from sqlalchemy import Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.database import Base
from app.core.enums import FeeChargeModel


class Event(Base):
    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    status = Column(String, nullable=False, default="OPEN")  # OPEN/CLOSED/COMPLETED/CANCELLED


class EventRegistrationRules(Base):
    __tablename__ = "event_registration_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), unique=True, nullable=False)
    team_min_size = Column(Integer, nullable=False, default=1)
    team_max_size = Column(Integer, nullable=False, default=1)
    allow_individual = Column(Boolean, nullable=False, default=True)
    fee_charge_model = Column(SAEnum(FeeChargeModel, name="fee_charge_model"), nullable=False)
    allow_team_invite_flow = Column(Boolean, nullable=False, default=False)
    registration_opens_at = Column(DateTime(timezone=True), nullable=True)
    registration_closes_at = Column(DateTime(timezone=True), nullable=True)


class Profile(Base):
    __tablename__ = "profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    full_name = Column(String, nullable=False)