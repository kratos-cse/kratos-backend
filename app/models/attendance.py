import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import AttendanceScanResult, DuplicateScanBehavior


class AttendanceCheckpoint(Base):
    __tablename__ = "attendance_checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    allow_repeat_scan: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    duplicate_scan_behavior: Mapped[DuplicateScanBehavior] = mapped_column(
        Enum(DuplicateScanBehavior, name="duplicate_scan_behavior"),
        default=DuplicateScanBehavior.REJECT,
        server_default="REJECT",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AttendanceScan(Base):
    """Immutable scan audit row — no updates in application code."""

    __tablename__ = "attendance_scans"
    __table_args__ = (
        CheckConstraint(
            "profile_id IS NOT NULL OR team_member_id IS NOT NULL",
            name="ck_attendance_scans_participant_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    checkpoint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attendance_checkpoints.id"), nullable=False, index=True
    )
    qr_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("qr_codes.id"), nullable=False, index=True)
    profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=True, index=True
    )
    team_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("team_members.id"), nullable=True, index=True
    )
    result: Mapped[AttendanceScanResult] = mapped_column(
        Enum(AttendanceScanResult, name="attendance_scan_result"), nullable=False
    )
    scanned_by_admin_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id"), nullable=True
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
