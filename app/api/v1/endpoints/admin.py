from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps_admin import get_current_active_admin, invalidate_admin_cache, require_super_admin
from app.db.session import get_db
from app.models.admin import AdminUser, Permission, Role
from app.models.user import User
from app.schemas.admin import AdminUserCreate, AdminUserUpdate, RoleCreate, RoleUpdate

router = APIRouter(prefix="/admin", tags=["Admin Authentication / RBAC"])


@router.get("/me")
async def get_current_admin_profile(
    current_admin: AdminUser = Depends(get_current_active_admin),
):
    role = current_admin.role
    permissions = [p.permission_key for p in (role.permissions if role else [])]
    return {
        "status": "success",
        "data": {
            "admin_user_id": current_admin.id,
            "user_id": current_admin.user_id,
            "is_active": current_admin.is_active,
            "role": {"id": role.id, "name": role.name} if role else None,
            "permissions": permissions,
        },
    }


@router.get("/roles")
async def list_roles(
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_super_admin),
):
    result = await db.execute(select(Role).options(selectinload(Role.permissions)))
    roles = result.scalars().all()
    data = []
    for role in roles:
        permissions = [p.permission_key for p in role.permissions]
        data.append(
            {
                "role_id": role.id,
                "name": role.name,
                "description": role.description,
                "permissions": permissions,
                "created_at": role.permissions[0].created_at if role.permissions else None,
            }
        )
    return {"status": "success", "data": data}


@router.post("/roles", status_code=status.HTTP_201_CREATED)
async def create_role(
    role_in: RoleCreate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_super_admin),
):
    new_role = Role(name=role_in.name, description=role_in.description)
    db.add(new_role)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Role name already exists.")

    for perm_key in role_in.permissions:
        db.add(Permission(role_id=new_role.id, permission_key=perm_key))
    await db.commit()
    await db.refresh(new_role)
    invalidate_admin_cache()

    return {
        "status": "success",
        "data": {
            "role_id": new_role.id,
            "name": new_role.name,
            "permissions": role_in.permissions,
        },
    }


@router.patch("/roles/{role_id}")
async def update_role(
    role_id: UUID,
    role_in: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_super_admin),
):
    result = await db.execute(
        select(Role).options(selectinload(Role.permissions)).where(Role.id == role_id)
    )
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found.")

    if role_in.name is not None:
        role.name = role_in.name
    if role_in.description is not None:
        role.description = role_in.description

    if role_in.permissions is not None:
        for existing in list(role.permissions):
            await db.delete(existing)
        await db.flush()
        for perm_key in role_in.permissions:
            db.add(Permission(role_id=role.id, permission_key=perm_key))

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Role update failed. Name might exist.")

    invalidate_admin_cache()

    result = await db.execute(
        select(Permission).where(Permission.role_id == role_id)
    )
    updated_permissions = [p.permission_key for p in result.scalars().all()]
    return {
        "status": "success",
        "data": {"role_id": role.id, "name": role.name, "permissions": updated_permissions},
    }


@router.get("/admin-users")
async def list_admin_users(
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_super_admin),
):
    result = await db.execute(
        select(AdminUser)
        .options(selectinload(AdminUser.role))
        .offset(skip)
        .limit(min(limit, 100))
    )
    admins = result.scalars().all()
    data = [
        {
            "admin_user_id": admin.id,
            "user_id": admin.user_id,
            "role": {"id": admin.role.id, "name": admin.role.name} if admin.role else None,
            "is_active": admin.is_active,
        }
        for admin in admins
    ]
    return {"status": "success", "data": data}


@router.post("/admin-users", status_code=status.HTTP_201_CREATED)
async def grant_admin_access(
    admin_in: AdminUserCreate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_super_admin),
):
    user = await db.execute(select(User).where(User.id == admin_in.user_id))
    if not user.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="User not found.")
    role = await db.execute(select(Role).where(Role.id == admin_in.role_id))
    if not role.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Role not found.")

    new_admin = AdminUser(user_id=admin_in.user_id, role_id=admin_in.role_id, is_active=True)
    db.add(new_admin)
    try:
        await db.commit()
        await db.refresh(new_admin)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="User is already an admin.")

    invalidate_admin_cache(admin_in.user_id)
    return {
        "status": "success",
        "data": {"admin_user_id": new_admin.id, "is_active": new_admin.is_active},
    }


@router.patch("/admin-users/{admin_user_id}")
async def update_admin_user(
    admin_user_id: UUID,
    admin_in: AdminUserUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_super_admin),
):
    result = await db.execute(select(AdminUser).where(AdminUser.id == admin_user_id))
    admin = result.scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin user not found.")

    if admin_in.role_id is not None:
        role = await db.execute(select(Role).where(Role.id == admin_in.role_id))
        if not role.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Target role not found.")
        admin.role_id = admin_in.role_id

    if admin_in.is_active is not None:
        admin.is_active = admin_in.is_active

    await db.commit()
    invalidate_admin_cache(admin.user_id)
    return {
        "status": "success",
        "data": {"admin_user_id": admin.id, "is_active": admin.is_active},
    }
