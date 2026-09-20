import uuid

from sqlalchemy import Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base
from app.core.enums import TeamMemberRole, TeamMemberStatus, TeamStatus


class Team(Base):
    __tablename__ = "teams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    name = Column(String, nullable=False)
    leader_profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    status = Column(SAEnum(TeamStatus, name="team_status"), nullable=False, default=TeamStatus.FORMING)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    members = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")
    invitations = relationship("TeamInvitation", back_populates="team", cascade="all, delete-orphan")


class TeamMember(Base):
    __tablename__ = "team_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    role = Column(SAEnum(TeamMemberRole, name="team_member_role"), nullable=False)
    status = Column(
        SAEnum(TeamMemberStatus, name="team_member_status"),
        nullable=False,
        default=TeamMemberStatus.PENDING_PAYMENT,
    )
    joined_at = Column(DateTime(timezone=True), server_default=func.now())

    team = relationship("Team", back_populates="members")

    # NOTE: the two partial-unique constraints from the schema
    # (UNIQUE(event_id, profile_id) among non-terminal rows, and one
    # active LEADER per team) are defined as raw-SQL partial indexes
    # in db/migrations/0001_teams_module.sql instead of here, because
    # SQLAlchemy's declarative __table_args__ can't cleanly express a
    # WHERE-filtered unique index across dialects. Apply that
    # migration - the ORM alone will NOT enforce these two rules.


class TeamInvitation(Base):
    __tablename__ = "team_invitations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    code = Column(String, unique=True, nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_by_profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    team = relationship("Team", back_populates="invitations")