"""Admin Audit & Diagnostics Endpoints."""
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps_admin import get_current_active_admin, require_permission
from app.db.session import get_db
from app.models.admin import AdminUser
from app.services import audit_service

router = APIRouter(prefix="/admin", tags=["Admin Audit & Diagnostics"])


@router.get("/audit-logs")
async def list_audit_logs(
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    actor_user_id: Optional[UUID] = None,
    actor_profile_id: Optional[UUID] = None,
    actor_role: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_active_admin),
):
    """
    Search and filter audit logs across all system lifecycle events.
    Indexed for fast responses under high volume.
    """
    logs, total = await audit_service.query_audit_logs(
        db,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_user_id=actor_user_id,
        actor_profile_id=actor_profile_id,
        actor_role=actor_role,
        status=status_filter,
        search=search,
        from_date=from_date,
        to_date=to_date,
        offset=skip,
        limit=limit,
    )

    items = [
        {
            "id": str(log.id),
            "created_at": log.created_at.isoformat() if log.created_at else None,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "actor_user_id": str(log.actor_user_id) if log.actor_user_id else None,
            "actor_profile_id": str(log.actor_profile_id) if log.actor_profile_id else None,
            "actor_role": log.actor_role,
            "status": log.status,
            "details": log.details,
            "ip_address": log.ip_address,
            "user_agent": log.user_agent,
        }
        for log in logs
    ]

    return {
        "status": "success",
        "total": total,
        "skip": skip,
        "limit": limit,
        "data": items,
    }


@router.get("/audit-logs/user-journey/{profile_id}")
async def get_user_journey(
    profile_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_active_admin),
):
    """
    Reconstructs the full chronological journey for a user / profile
    from initial auth, profile creation, team formation, payment, to email delivery diagnostics.
    """
    journey = await audit_service.get_user_journey(db, profile_id=profile_id)
    return {"status": "success", "data": journey}


@router.get("/audit-logs/payment/{payment_id}")
async def get_payment_audit_trail(
    payment_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_active_admin),
):
    """
    Reconstructs the complete lifecycle of a payment transaction
    including order creation, webhook reception, signature verification, and receipt issuance.
    """
    trail = await audit_service.get_payment_audit_trail(db, payment_id=payment_id)
    return {"status": "success", "data": trail}


@router.get("/notifications/{notification_id}/diagnostics")
async def get_notification_diagnostics(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_active_admin),
):
    """
    Deep diagnostic inspection for an email delivery attempt:
    categorizes why it failed (e.g. SMTP unconfigured, timeout, missing email, auth error)
    and provides the timeline.
    """
    diagnostics = await audit_service.get_notification_diagnostics(db, notification_id=notification_id)
    return {"status": "success", "data": diagnostics}
