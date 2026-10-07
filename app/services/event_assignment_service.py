"""Super Admin CRUD for event admin assignments."""
import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.admin import EVENT_COORDINATOR_ROLE_NAME, AdminUser
from app.models.event import Event
from app.models.event_admin_assignment import EventAdminAssignment
from app.models.profile import Profile
from app.models.user import User


async def list_assignments_for_admin(
    db: AsyncSession, admin_user_id: uuid.UUID
) -> list[EventAdminAssignment]:
    result = await db.execute(
        select(EventAdminAssignment)
        .where(EventAdminAssignment.admin_user_id == admin_user_id)
        .order_by(EventAdminAssignment.created_at)
    )
    return list(result.scalars().all())


async def get_assigned_event_summaries(
    db: AsyncSession, admin_user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict]]:
    """Map admin_user_id → [{id, name}, ...] for list/detail responses."""
    if not admin_user_ids:
        return {}
    result = await db.execute(
        select(EventAdminAssignment.admin_user_id, Event.id, Event.name)
        .join(Event, Event.id == EventAdminAssignment.event_id)
        .where(EventAdminAssignment.admin_user_id.in_(admin_user_ids))
        .order_by(Event.name)
    )
    out: dict[uuid.UUID, list[dict]] = {aid: [] for aid in admin_user_ids}
    for admin_user_id, event_id, name in result.all():
        out.setdefault(admin_user_id, []).append({"id": event_id, "name": name})
    return out


async def clear_assignments_for_admin(db: AsyncSession, admin_user_id: uuid.UUID) -> int:
    """Remove all event assignments for an admin. Caller commits."""
    result = await db.execute(
        delete(EventAdminAssignment).where(EventAdminAssignment.admin_user_id == admin_user_id)
    )
    return int(result.rowcount or 0)


async def reconcile_coordinator_assignments(
    db: AsyncSession,
    admin_user_id: uuid.UUID,
    desired_event_ids: Optional[list[uuid.UUID]],
    created_by_admin_user_id: uuid.UUID,
    *,
    role_name: str,
    apply_event_ids: bool,
) -> list[dict]:
    """
    Reconcile EventAdminAssignment rows for an admin user.

    - Non-coordinator roles: always clear assignments. Reject non-empty desired_event_ids.
    - EVENT COORDINATOR: when apply_event_ids is True, set assignments to exactly desired_event_ids
      (None/[] → empty). When False, leave existing assignments unchanged.
    Caller is responsible for commit.
    """
    if role_name != EVENT_COORDINATOR_ROLE_NAME:
        if desired_event_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="event_ids may only be set for EVENT COORDINATOR admins.",
            )
        await clear_assignments_for_admin(db, admin_user_id)
        await db.flush()
        return []

    if not apply_event_ids:
        summaries = await get_assigned_event_summaries(db, [admin_user_id])
        return summaries.get(admin_user_id, [])

    desired = {eid for eid in (desired_event_ids or [])}
    if desired:
        found = await db.execute(select(Event.id).where(Event.id.in_(desired)))
        found_ids = {row[0] for row in found.all()}
        missing = desired - found_ids
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Event(s) not found: {', '.join(str(m) for m in sorted(missing))}",
            )

    admin_result = await db.execute(
        select(AdminUser)
        .options(selectinload(AdminUser.role))
        .where(AdminUser.id == admin_user_id, AdminUser.is_active.is_(True))
    )
    admin = admin_result.scalar_one_or_none()
    if admin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin user not found.")
    if not admin.role or admin.role.name != EVENT_COORDINATOR_ROLE_NAME:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only active EVENT COORDINATOR admins can be assigned.",
        )

    existing = await list_assignments_for_admin(db, admin_user_id)
    existing_by_event = {a.event_id: a for a in existing}
    existing_ids = set(existing_by_event)

    to_remove = existing_ids - desired
    to_add = desired - existing_ids

    for eid in to_remove:
        await db.delete(existing_by_event[eid])

    for eid in to_add:
        db.add(
            EventAdminAssignment(
                event_id=eid,
                admin_user_id=admin_user_id,
                assignment_type="EVENT_COORDINATOR",
                created_by_admin_user_id=created_by_admin_user_id,
            )
        )

    await db.flush()
    summaries = await get_assigned_event_summaries(db, [admin_user_id])
    return summaries.get(admin_user_id, [])


async def list_event_assignments(db: AsyncSession, event_id: uuid.UUID) -> list[EventAdminAssignment]:
    result = await db.execute(
        select(EventAdminAssignment)
        .options(
            selectinload(EventAdminAssignment.admin_user)
            .selectinload(AdminUser.role)
        )
        .where(EventAdminAssignment.event_id == event_id)
        .order_by(EventAdminAssignment.created_at)
    )
    return list(result.scalars().all())


async def list_assignable_event_coordinators(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(AdminUser, User.email, Profile.full_name)
        .join(User, User.id == AdminUser.user_id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .join(AdminUser.role)
        .where(AdminUser.is_active.is_(True), AdminUser.role.has(name=EVENT_COORDINATOR_ROLE_NAME))
        .order_by(AdminUser.created_at)
    )
    rows = []
    for admin, email, full_name in result.all():
        rows.append(
            {
                "admin_user_id": admin.id,
                "user_id": admin.user_id,
                "email": email,
                "name": full_name,
                "role": EVENT_COORDINATOR_ROLE_NAME,
            }
        )
    return rows


async def create_event_assignment(
    db: AsyncSession,
    event_id: uuid.UUID,
    admin_user_id: uuid.UUID,
    created_by_admin_user_id: uuid.UUID,
) -> EventAdminAssignment:
    event = await db.execute(select(Event.id).where(Event.id == event_id))
    if event.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found.")

    admin_result = await db.execute(
        select(AdminUser)
        .options(selectinload(AdminUser.role))
        .where(AdminUser.id == admin_user_id, AdminUser.is_active.is_(True))
    )
    admin = admin_result.scalar_one_or_none()
    if admin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin user not found.")
    if not admin.role or admin.role.name != EVENT_COORDINATOR_ROLE_NAME:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only active EVENT COORDINATOR admins can be assigned.",
        )

    existing = await db.execute(
        select(EventAdminAssignment).where(
            EventAdminAssignment.event_id == event_id,
            EventAdminAssignment.admin_user_id == admin_user_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Admin is already assigned to this event.")

    assignment = EventAdminAssignment(
        event_id=event_id,
        admin_user_id=admin_user_id,
        assignment_type="EVENT_COORDINATOR",
        created_by_admin_user_id=created_by_admin_user_id,
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return assignment


async def delete_event_assignment(
    db: AsyncSession, event_id: uuid.UUID, assignment_id: uuid.UUID
) -> None:
    result = await db.execute(
        select(EventAdminAssignment).where(
            EventAdminAssignment.id == assignment_id,
            EventAdminAssignment.event_id == event_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if assignment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found.")
    await db.delete(assignment)
    await db.commit()


def serialize_assignment(assignment: EventAdminAssignment, user_email: str | None = None) -> dict:
    admin = assignment.admin_user
    return {
        "assignment_id": assignment.id,
        "event_id": assignment.event_id,
        "admin_user_id": assignment.admin_user_id,
        "email": user_email,
        "name": None,
        "role": admin.role.name if admin and admin.role else EVENT_COORDINATOR_ROLE_NAME,
        "assigned_at": assignment.created_at,
        "assignment_type": assignment.assignment_type,
    }


async def enrich_assignments_with_user_info(
    db: AsyncSession, assignments: list[EventAdminAssignment]
) -> list[dict]:
    if not assignments:
        return []
    admin_user_ids = [a.admin_user_id for a in assignments]
    result = await db.execute(
        select(AdminUser, User.email, Profile.full_name)
        .join(User, User.id == AdminUser.user_id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .where(AdminUser.id.in_(admin_user_ids))
    )
    info = {row[0].id: {"email": row[1], "name": row[2]} for row in result.all()}
    out = []
    for a in assignments:
        meta = info.get(a.admin_user_id, {})
        payload = serialize_assignment(a, user_email=meta.get("email"))
        payload["name"] = meta.get("name")
        out.append(payload)
    return out
