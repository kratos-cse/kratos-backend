import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import TeamMemberRole, TeamMemberStatus, TeamStatus


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    leader_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False, index=True
    )
    status: Mapped[TeamStatus] = mapped_column(
        Enum(TeamStatus, name="team_status"), default=TeamStatus.FORMING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[List["TeamMember"]] = relationship("TeamMember", back_populates="team")
    invitations: Mapped[List["TeamInvitation"]] = relationship("TeamInvitation", back_populates="team")


class TeamMember(Base):
    """
    Partial unique indexes (non-terminal event membership + one active leader)
    are defined in Alembic 0003 — LEFT/REMOVED history is preserved.
    """

    __tablename__ = "team_members"
    __table_args__ = (
        Index("ix_team_members_team_id", "team_id"),
        Index("ix_team_members_event_id", "event_id"),
        Index("ix_team_members_profile_id", "profile_id"),
        Index(
            "uq_team_members_event_profile_active",
            "event_id",
            "profile_id",
            unique=True,
            postgresql_where=text("status NOT IN ('LEFT', 'REMOVED')"),
        ),
        Index(
            "uq_team_members_one_active_leader",
            "team_id",
            unique=True,
            postgresql_where=text("role = 'LEADER' AND status NOT IN ('LEFT', 'REMOVED')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    role: Mapped[TeamMemberRole] = mapped_column(Enum(TeamMemberRole, name="team_member_role"), nullable=False)
    status: Mapped[TeamMemberStatus] = mapped_column(
        Enum(TeamMemberStatus, name="team_member_status"),
        default=TeamMemberStatus.PENDING_PAYMENT,
        nullable=False,
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    team: Mapped["Team"] = relationship("Team", back_populates="members")
    profile: Mapped["Profile"] = relationship("Profile", foreign_keys=[profile_id], lazy="raise")


class TeamInvitation(Base):
    """Reusable, non-expiring invitation until is_active is set False."""

    __tablename__ = "team_invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_by_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    team: Mapped[Optional["Team"]] = relationship("Team", back_populates="invitations")
