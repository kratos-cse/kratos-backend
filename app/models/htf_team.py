"""
HTF 2026 – Team-level extensions.

Two tables are added here (no changes to existing `teams` table):

1. htf_problem_statements  – admin-seeded list of PS options (with domain mapping).
2. htf_team_meta           – per-team HTF metadata: selected PS + application status.
                              Linked 1-to-1 with `teams.id`.

Design note
-----------
We deliberately keep the HTF lifecycle status (draft → submitted → shortlisted →
confirmed) on `htf_team_meta` rather than on the shared `teams.status` column.
The shared column is used by the generic payment/QR flow (FORMING → PAID → COMPLETE).
For HTF the payment gate is deferred *after* screening, so we drive that transition
from `htf_team_meta.application_status`.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import HTFApplicationStatus, HTFProblemDomain


class HTFProblemStatement(Base):
    """
    Admin-seeded catalogue of Problem Statements available for HTF 2026.

    Columns
    -------
    event_id        → scoped to the HTF Event row in `events`.
    code            → short human key e.g. "PS-01"  (shown in UI/pass).
    title           → one-liner headline.
    description     → full brief (markdown supported).
    domain          → HTFProblemDomain enum (used for PS ↔ domain mapping).
    is_active       → admin can soft-deactivate a PS without deleting it.
    unique_claim    → if True, only one team may choose this PS (first-come wins).
    """

    __tablename__ = "htf_problem_statements"
    __table_args__ = (
        Index("ix_htf_ps_event_id", "event_id"),
        UniqueConstraint("event_id", "code", name="uq_htf_ps_event_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    domain: Mapped[HTFProblemDomain] = mapped_column(
        Enum(HTFProblemDomain, name="htf_problem_domain"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    unique_claim: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False,
        comment="If true, only one team can claim this PS (enforced with FOR UPDATE lock)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationship: all teams that have selected this PS
    team_metas: Mapped[list["HTFTeamMeta"]] = relationship(
        "HTFTeamMeta", back_populates="problem_statement"
    )


class HTFTeamMeta(Base):
    """
    HTF-specific metadata for a team — linked 1-to-1 with `teams`.

    This row is created immediately when a team is created for the HTF event.
    It holds:
      - which Problem Statement (PS) the team has chosen (nullable until they pick)
      - the HTF application lifecycle status (drives Member 1's dashboard)
      - roster_locked flag (True once application is submitted)
      - payment_deadline (set by admin via admin panel — we just read it here)

    Columns
    -------
    team_id              → FK → teams.id  (1-to-1, unique)
    event_id             → denormalized for quick scoped queries
    ps_id                → FK → htf_problem_statements.id (nullable)
    application_status   → HTFApplicationStatus enum
    roster_locked        → True when application_status > DRAFT
    payment_deadline     → Admin-set deadline datetime for payment after shortlisting
    """

    __tablename__ = "htf_team_meta"
    __table_args__ = (
        Index("ix_htf_team_meta_event_id", "event_id"),
        Index("ix_htf_team_meta_application_status", "application_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 1-to-1 with teams
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False
    )

    # Problem Statement selection (nullable until team picks one)
    ps_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("htf_problem_statements.id"),
        nullable=True,
    )

    application_status: Mapped[HTFApplicationStatus] = mapped_column(
        Enum(HTFApplicationStatus, name="htf_application_status"),
        default=HTFApplicationStatus.DRAFT,
        nullable=False,
    )

    # True as soon as the application moves past DRAFT; blocks join/leave/remove
    roster_locked: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Nullable — admin sets a datetime after which payment is no longer accepted
    payment_deadline: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    problem_statement: Mapped[Optional[HTFProblemStatement]] = relationship(
        "HTFProblemStatement", back_populates="team_metas"
    )
