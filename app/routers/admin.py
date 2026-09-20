from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.db.session import get_db

from app.schemas.admin import (
    RoleCreate, RoleUpdate, RoleResponse, 
    AdminUserBase, AdminUserUpdate, AdminUserResponse
)
from app.models.admin import Role, Permission, AdminUser
from app.models.user import User # Adjust based on your setup
from app.api.dependencies import get_current_active_admin, require_super_admin
from uuid import UUID

router = APIRouter(prefix="/admin", tags=["Admin Authentication / RBAC"])

@router.get("/me", response_model=dict)
def get_current_admin_profile(
    current_admin: AdminUser = Depends(get_current_active_admin),
    db: Session = Depends(get_db)
):
    role = db.query(Role).filter(Role.id == current_admin.role_id).first()
    permissions = [p.permission_key for p in db.query(Permission).filter(Permission.role_id == role.id).all()]
    
    return {
        "status": "success",
        "data": {
            "admin_user_id": current_admin.id,
            "user_id": current_admin.user_id,
            "is_active": current_admin.is_active,
            "role": {"id": role.id, "name": role.name},
            "permissions": permissions,
        }
    }

@router.get("/roles", response_model=dict)
def list_roles(
    db: Session = Depends(get_db),
    _ = Depends(require_super_admin)
):
    roles = db.query(Role).all()
    result = []
    for role in roles:
        permissions = [p.permission_key for p in role.permissions]
        result.append({
            "role_id": role.id,
            "name": role.name,
            "description": role.description,
            "permissions": permissions,
            "created_at": role.permissions[0].created_at if role.permissions else None
        })
    return {"status": "success", "data": result}

@router.post("/roles", response_model=dict, status_code=status.HTTP_201_CREATED)
def create_role(
    role_in: RoleCreate,
    db: Session = Depends(get_db),
    _ = Depends(require_super_admin)
):
    new_role = Role(name=role_in.name, description=role_in.description)
    db.add(new_role)
    try:
        db.commit()
        db.refresh(new_role)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Role name already exists.")

    for perm_key in role_in.permissions:
        new_perm = Permission(role_id=new_role.id, permission_key=perm_key)
        db.add(new_perm)
    db.commit()

    return {
        "status": "success",
        "data": {
            "role_id": new_role.id,
            "name": new_role.name,
            "permissions": role_in.permissions
        }
    }

@router.patch("/roles/{role_id}", response_model=dict)
def update_role(
    role_id: UUID,
    role_in: RoleUpdate,
    db: Session = Depends(get_db),
    _ = Depends(require_super_admin)
):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found.")

    if role_in.name is not None:
        role.name = role_in.name
    if role_in.description is not None:
        role.description = role_in.description

    if role_in.permissions is not None:
        db.query(Permission).filter(Permission.role_id == role_id).delete()
        for perm_key in role_in.permissions:
            db.add(Permission(role_id=role.id, permission_key=perm_key))

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Role update failed. Name might exist.")

    updated_permissions = [p.permission_key for p in db.query(Permission).filter(Permission.role_id == role_id).all()]
    return {"status": "success", "data": {"role_id": role.id, "name": role.name, "permissions": updated_permissions}}

@router.get("/admin-users", response_model=dict)
def list_admin_users(
    skip: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db),
    _ = Depends(require_super_admin)
):
    admins = db.query(AdminUser).offset(skip).limit(limit).all()
    result = []
    for admin in admins:
        user = db.query(User).filter(User.id == admin.user_id).first()
        result.append({
            "admin_user_id": admin.id,
            "user_id": admin.user_id,
            "role": {"id": admin.role.id, "name": admin.role.name},
            "is_active": admin.is_active,
        })
    return {"status": "success", "data": result}

@router.post("/admin-users", response_model=dict, status_code=status.HTTP_201_CREATED)
def grant_admin_access(
    admin_in: AdminUserBase,
    db: Session = Depends(get_db),
    _ = Depends(require_super_admin)
):
    if not db.query(User).filter(User.id == admin_in.user_id).first():
        raise HTTPException(status_code=404, detail="User not found.")
    if not db.query(Role).filter(Role.id == admin_in.role_id).first():
        raise HTTPException(status_code=404, detail="Role not found.")

    new_admin = AdminUser(user_id=admin_in.user_id, role_id=admin_in.role_id, is_active=True)
    db.add(new_admin)
    
    try:
        db.commit()
        db.refresh(new_admin)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="User is already an admin.")

    return {"status": "success", "data": {"admin_user_id": new_admin.id, "is_active": new_admin.is_active}}

@router.patch("/admin-users/{admin_user_id}", response_model=dict)
def update_admin_user(
    admin_user_id: UUID,
    admin_in: AdminUserUpdate,
    db: Session = Depends(get_db),
    _ = Depends(require_super_admin)
):
    admin = db.query(AdminUser).filter(AdminUser.id == admin_user_id).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin user not found.")

    if admin_in.role_id is not None:
        if not db.query(Role).filter(Role.id == admin_in.role_id).first():
            raise HTTPException(status_code=404, detail="Target role not found.")
        admin.role_id = admin_in.role_id

    if admin_in.is_active is not None:
        admin.is_active = admin_in.is_active

    db.commit()
    return {"status": "success", "data": {"admin_user_id": admin.id, "is_active": admin.is_active}}