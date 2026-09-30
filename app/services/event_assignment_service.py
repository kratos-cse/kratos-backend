"""Super Admin CRUD for event admin assignments."""
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.admin import EVENT_COORDINATOR_ROLE_NAME, AdminUser
from app.models.event import Event
from app.models.event_admin_assignment import EventAdminAssignment
from app.models.profile import Profile
from app.models.user import User


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
