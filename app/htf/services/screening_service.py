"""HTF screening service for administrators.

Handles recording of shortlist/rejection outcomes, state synchronization,
auditing, lock enforcement against payments, and trigger hooks.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.htf.contracts import on_shortlist_decided
from app.htf.errors import (
    APPLICATION_NOT_FOUND,
    DECISION_LOCKED,
    SCREENING_NOT_ALLOWED,
    htf_error,
)
from app.htf.models.application import HTFApplication, HTFEventConfig
from app.htf.models.payment import HTFPayment
from app.htf.models.screening import HTFScreeningResult
from app.htf.schemas.screening import ScreeningResultOut
from app.models.admin import AdminUser
from app.services.audit_service import log_activity

logger = logging.getLogger("htf.screening")


async def record_screening_decision(
    db: AsyncSession,
    *,
    application_id: uuid.UUID,
    result: str,
    notes: str | None,
    admin: AdminUser,
) -> ScreeningResultOut:
    if result not in {"SHORTLISTED", "NOT_SHORTLISTED"}:
        raise htf_error("INVALID_SCREENING_RESULT", "Result must be SHORTLISTED or NOT_SHORTLISTED")

    now = datetime.now(timezone.utc)

    # 1. Row-lock application
    app_query = await db.execute(
        select(HTFApplication).where(HTFApplication.id == application_id).with_for_update()
    )
    application = app_query.scalar_one_or_none()
    if not application:
        raise htf_error(APPLICATION_NOT_FOUND, "HTF application not found", status_code=404)

    # 2. Check existing screening result: repeating the same result changes nothing
    screen_query = await db.execute(
        select(HTFScreeningResult).where(HTFScreeningResult.application_id == application_id).with_for_update()
    )
    screening = screen_query.scalar_one_or_none()

    if screening and screening.result == result:
        # Idempotent repeat: same result changes nothing, returns cleanly without 409
        return ScreeningResultOut.model_validate(screening)

    # 3. If changing result, block if application already confirmed or has live payments
    if application.status == "CONFIRMED":
        raise htf_error(DECISION_LOCKED, "Cannot change screening decision for a CONFIRMED application", status_code=409)

    payment_query = await db.execute(
        select(HTFPayment.id).where(
            HTFPayment.application_id == application_id,
            HTFPayment.status.in_(["CREATED", "PAID"]),
        )
    )
    if payment_query.scalar_one_or_none() is not None:
        raise htf_error(
            DECISION_LOCKED,
            "Cannot modify screening decision when an active or completed payment order exists",
            status_code=409,
        )

    # 4. Check event config - refused before ppt_submission_closes_at if configured
    config_query = await db.execute(
        select(HTFEventConfig).where(HTFEventConfig.event_id == application.event_id)
    )
    config = config_query.scalar_one_or_none()
    if config and config.ppt_submission_closes_at and now < config.ppt_submission_closes_at:
        raise htf_error(
            SCREENING_NOT_ALLOWED,
            "Screening decisions cannot be recorded before PPT submissions close",
            status_code=400,
        )

    # Must be PPT_SUBMITTED or already decided
    if application.status not in {"PPT_SUBMITTED", "SHORTLISTED", "NOT_SHORTLISTED"}:
        raise htf_error(
            SCREENING_NOT_ALLOWED,
            f"Screening cannot be performed on application in {application.status} status",
            status_code=400,
        )

    # 5. Upsert screening result
    if screening:
        screening.result = result
        screening.notes = notes
        screening.decided_by_admin_user_id = admin.id
        screening.decided_at = now
        screening.updated_at = now
    else:
        screening = HTFScreeningResult(
            id=uuid.uuid4(),
            application_id=application_id,
            result=result,
            notes=notes,
            decided_by_admin_user_id=admin.id,
            decided_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(screening)

    # 6. Synchronize application status
    application.status = result
    application.updated_at = now

    # 7. Audit logs
    role_name = admin.role.name if admin.role else "ADMIN"
    await log_activity(
        db,
        action="HTF_SCREENING_DECIDED",
        resource_type="HTF_APPLICATION",
        resource_id=application.id,
        actor_user_id=admin.user_id,
        actor_role=role_name,
        details={
            "result": result,
            "notes": notes,
            "admin_user_id": str(admin.id),
        },
    )
    decision_action = "HTF_SHORTLISTED" if result == "SHORTLISTED" else "HTF_NOT_SHORTLISTED"
    await log_activity(
        db,
        action=decision_action,
        resource_type="HTF_APPLICATION",
        resource_id=application.id,
        actor_user_id=admin.user_id,
        actor_role=role_name,
        details={"result": result},
    )

    await db.commit()
    await db.refresh(screening)

    # 8. Post-commit hook
    try:
        await on_shortlist_decided(application_id, result)
    except Exception as hook_exc:
        logger.warning("on_shortlist_decided hook failed for application %s: %s", application_id, hook_exc)

    return ScreeningResultOut.model_validate(screening)
