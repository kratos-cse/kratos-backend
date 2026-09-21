"""
*** TEMPORARY ***
Minimal stand-ins for models owned by other people's branches (Profiles,
Events, Registrations, Teams) — same approach teams/app/models/
temp_external_subs.py already uses for the identical problem. This module
only reads the handful of columns it actually needs from each; these are
intentionally partial, not full schema definitions.

DELETE THIS FILE once the real app/models/{profile,event,registration,
team}.py land (Authentication owns profile/event/registration; teams owns
team/team_member), and swap the imports in app/services and app/api/v1
to point at them instead. Column names match kratos_database_schema_final.xlsx,
so the swap should be a pure import change with no logic change.
"""
import uuid

from sqlalchemy import Column, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base
from app.models.enums import TeamMemberStatus, TeamStatus


class Profile(Base):
    __tablename__ = "profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)


class Event(Base):
    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fee = Column(Numeric(10, 2), nullable=True)


class EventRegistrationRule(Base):
    __tablename__ = "event_registration_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), unique=True, nullable=False)
    fee_charge_model = Column(String, nullable=False)  # stored as the FeeChargeModel enum value


class Registration(Base):
    __tablename__ = "registrations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=True)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=True)
    payment_id = Column(UUID(as_uuid=True), ForeignKey("payments.id"), nullable=True)
    status = Column(String, nullable=False, default="PENDING")  # RegistrationStatus value


class Team(Base):
    __tablename__ = "teams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    leader_profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    status = Column(String, nullable=False, default=TeamStatus.FORMING.value)


class TeamMember(Base):
    __tablename__ = "team_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    role = Column(String, nullable=False)  # TeamMemberRole value
    status = Column(String, nullable=False, default=TeamMemberStatus.PENDING_PAYMENT.value)
