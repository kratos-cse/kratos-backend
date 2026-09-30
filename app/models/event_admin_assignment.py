import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class EventAdminAssignment(Base):
    """Maps an authenticated admin user to an event scope (not public contact info)."""

    __tablename__ = "event_admin_assignments"
    __table_args__ = (
        UniqueConstraint("event_id", "admin_user_id", name="uq_event_admin_assignments_event_admin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assignment_type: Mapped[str] = mapped_column(String(64), nullable=False, default="EVENT_COORDINATOR")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by_admin_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )

    admin_user: Mapped["AdminUser"] = relationship(  # noqa: F821
        "AdminUser", foreign_keys=[admin_user_id], back_populates="event_assignments"
    )
