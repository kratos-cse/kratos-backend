import uuid
from datetime import datetime
from typing import List

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import TeamMemberRole, TeamMemberStatus, TeamStatus


class Team(Base):
    """
    TEAMS. Only what's needed to support registration creation (section 4)
    lives here — the dedicated Teams CRUD / invitations / join-leave-remove
    endpoints (sections 5-7 of the API reference) belong to a separate
    feature branch and are NOT implemented in this branch.
    """

    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    leader_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    status: Mapped[TeamStatus] = mapped_column(
        Enum(TeamStatus, name="team_status"), default=TeamStatus.FORMING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[List["TeamMember"]] = relationship("TeamMember", back_populates="team")


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (
        # A participant cannot belong to multiple teams in the same event.
        UniqueConstraint("event_id", "profile_id", name="uq_team_members_event_profile"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    role: Mapped[TeamMemberRole] = mapped_column(Enum(TeamMemberRole, name="team_member_role"), nullable=False)
    status: Mapped[TeamMemberStatus] = mapped_column(
        Enum(TeamMemberStatus, name="team_member_status"), default=TeamMemberStatus.PENDING_PAYMENT, nullable=False
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    team: Mapped["Team"] = relationship("Team", back_populates="members")
