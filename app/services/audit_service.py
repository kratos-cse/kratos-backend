import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Optional, Union

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog

logger = logging.getLogger("audit_service")


async def log_activity(
    db: Optional[AsyncSession] = None,
    *,
    action: str,
    resource_type: str,
    resource_id: Optional[Union[str, uuid.UUID]] = None,
    actor_user_id: Optional[uuid.UUID] = None,
    actor_profile_id: Optional[uuid.UUID] = None,
    actor_role: str = "USER",
    status: str = "SUCCESS",
    details: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Optional[AuditLog]:
    """
    Records an immutable audit log entry.
    If `db` is provided, attaches to the active transaction/session.
    If `db` is None (e.g. background tasks), creates and commits a standalone session.
    Failsafe: Never raises exceptions that break main business flows.
    """
    res_id_str = str(resource_id) if resource_id is not None else None
    entry = AuditLog(
        id=uuid.uuid4(),
        actor_user_id=actor_user_id,
        actor_profile_id=actor_profile_id,
        actor_role=actor_role,
        action=action,
        resource_type=resource_type,
        resource_id=res_id_str,
        status=status,
        details=details or {},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    try:
        if db is not None:
            db.add(entry)
            # flush so ID & server defaults are resolved without prematurely committing transaction
            await db.flush()
            return entry
        else:
            async with AsyncSessionLocal() as session:
                session.add(entry)
                await session.commit()
                return entry
    except Exception as exc:
        logger.warning("Failed to record audit log for action=%s: %s", action, exc)
        return None


def log_activity_bg(
    *,
    action: str,
    resource_type: str,
    resource_id: Optional[Union[str, uuid.UUID]] = None,
    actor_user_id: Optional[uuid.UUID] = None,
    actor_profile_id: Optional[uuid.UUID] = None,
    actor_role: str = "USER",
    status: str = "SUCCESS",
    details: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Non-blocking background task wrapper for logging audit entries."""
    asyncio.create_task(
        log_activity(
            None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_user_id=actor_user_id,
            actor_profile_id=actor_profile_id,
            actor_role=actor_role,
            status=status,
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )


async def get_user_journey(
    db: AsyncSession,
    profile_id: Optional[uuid.UUID] = None,
    user_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """
    Reconstructs the full chronological journey and timeline for a user/profile.
    Includes login, registration initiation, team events, payment checkout, verification,
    and notification delivery diagnostics.
    """
    conditions = []
    if profile_id:
        conditions.extend([
            AuditLog.actor_profile_id == profile_id,
            (AuditLog.resource_type == "PROFILE") & (AuditLog.resource_id == str(profile_id)),
            AuditLog.details["profile_id"].astext == str(profile_id),
            AuditLog.details["payer_profile_id"].astext == str(profile_id),
        ])
    if user_id:
        conditions.extend([
            AuditLog.actor_user_id == user_id,
            (AuditLog.resource_type == "USER") & (AuditLog.resource_id == str(user_id)),
            AuditLog.details["user_id"].astext == str(user_id),
        ])

    if not conditions:
        return {"events": [], "total_events": 0}

    stmt = (
        select(AuditLog)
        .where(or_(*conditions))
        .order_by(AuditLog.created_at.asc())
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    timeline = []
    for log in logs:
        timeline.append({
            "id": str(log.id),
            "timestamp": log.created_at.isoformat() if log.created_at else None,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "actor_role": log.actor_role,
            "status": log.status,
            "details": log.details,
            "ip_address": log.ip_address,
            "user_agent": log.user_agent,
        })

    # Summarize journey milestones
    milestones = {
        "has_registered": any("REGISTRATION" in item["action"] for item in timeline),
        "has_initiated_payment": any("PAYMENT_ORDER" in item["action"] or "PAYMENT_INITIATED" in item["action"] for item in timeline),
        "has_verified_payment": any("PAYMENT_VERIF" in item["action"] and item["status"] == "SUCCESS" for item in timeline),
        "email_delivery_issues": [
            item for item in timeline if item["action"] == "EMAIL_DELIVERY_FAILED"
        ],
    }

    return {
        "profile_id": str(profile_id) if profile_id else None,
        "user_id": str(user_id) if user_id else None,
        "total_events": len(timeline),
        "milestones": milestones,
        "timeline": timeline,
    }


async def get_payment_audit_trail(
    db: AsyncSession,
    payment_id: Union[str, uuid.UUID],
) -> dict[str, Any]:
    """
    Reconstructs the full audit trail for a specific payment,
    including order creation, gateway callbacks, verification, and confirmation emails.
    """
    p_id_str = str(payment_id)
    stmt = (
        select(AuditLog)
        .where(
            or_(
                (AuditLog.resource_type == "PAYMENT") & (AuditLog.resource_id == p_id_str),
                AuditLog.details["payment_id"].astext == p_id_str,
                AuditLog.details["razorpay_payment_id"].astext == p_id_str,
                AuditLog.details["order_id"].astext == p_id_str,
                AuditLog.details["razorpay_order_id"].astext == p_id_str,
            )
        )
        .order_by(AuditLog.created_at.asc())
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    timeline = [
        {
            "id": str(log.id),
            "timestamp": log.created_at.isoformat() if log.created_at else None,
            "action": log.action,
            "actor_role": log.actor_role,
            "actor_user_id": str(log.actor_user_id) if log.actor_user_id else None,
            "actor_profile_id": str(log.actor_profile_id) if log.actor_profile_id else None,
            "status": log.status,
            "details": log.details,
            "ip_address": log.ip_address,
        }
        for log in logs
    ]

    return {
        "payment_id": p_id_str,
        "total_events": len(timeline),
        "timeline": timeline,
    }


async def get_notification_diagnostics(
    db: AsyncSession,
    notification_id: Union[str, uuid.UUID],
) -> dict[str, Any]:
    """
    Returns deep diagnostics for why an email/notification succeeded or failed.
    """
    notif_id_str = str(notification_id)
    stmt = (
        select(AuditLog)
        .where(
            or_(
                (AuditLog.resource_type == "NOTIFICATION") & (AuditLog.resource_id == notif_id_str),
                AuditLog.details["notification_id"].astext == notif_id_str,
            )
        )
        .order_by(AuditLog.created_at.asc())
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    timeline = [
        {
            "id": str(log.id),
            "timestamp": log.created_at.isoformat() if log.created_at else None,
            "action": log.action,
            "status": log.status,
            "details": log.details,
        }
        for log in logs
    ]

    latest_failure = next(
        (item for item in reversed(timeline) if item["status"] == "FAILURE"),
        None
    )

    return {
        "notification_id": notif_id_str,
        "events_count": len(timeline),
        "latest_failure_diagnostic": latest_failure["details"] if latest_failure else None,
        "timeline": timeline,
    }


async def query_audit_logs(
    db: AsyncSession,
    *,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    actor_user_id: Optional[uuid.UUID] = None,
    actor_profile_id: Optional[uuid.UUID] = None,
    actor_role: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditLog], int]:
    """
    Fast, index-backed paginated query for the Admin Audit Log explorer.
    """
    conditions = []
    if action:
        conditions.append(AuditLog.action == action)
    if resource_type:
        conditions.append(AuditLog.resource_type == resource_type)
    if resource_id:
        conditions.append(AuditLog.resource_id == resource_id)
    if actor_user_id:
        conditions.append(AuditLog.actor_user_id == actor_user_id)
    if actor_profile_id:
        conditions.append(AuditLog.actor_profile_id == actor_profile_id)
    if actor_role:
        conditions.append(AuditLog.actor_role == actor_role)
    if status:
        conditions.append(AuditLog.status == status)
    if from_date:
        conditions.append(AuditLog.created_at >= from_date)
    if to_date:
        conditions.append(AuditLog.created_at <= to_date)
    if search:
        search_pattern = f"%{search}%"
        conditions.append(
            or_(
                AuditLog.action.ilike(search_pattern),
                AuditLog.resource_id.ilike(search_pattern),
                AuditLog.ip_address.ilike(search_pattern),
            )
        )

    count_stmt = select(func.count()).select_from(AuditLog)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total_res = await db.execute(count_stmt)
    total = total_res.scalar() or 0

    query_stmt = (
        select(AuditLog)
        .order_by(desc(AuditLog.created_at))
        .limit(limit)
        .offset(offset)
    )
    if conditions:
        query_stmt = query_stmt.where(*conditions)

    res = await db.execute(query_stmt)
    items = list(res.scalars().all())

    return items, total
