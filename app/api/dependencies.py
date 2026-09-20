from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.admin import AdminUser, Role
from app.models.user import User # Adjust import based on your setup
from uuid import UUID

def get_current_user(db: Session = Depends(get_db)):
    # Placeholder: Implement your Google OAuth session validation here
    # Return the currently authenticated User instance
    pass

def get_current_active_admin(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> AdminUser:
    admin = db.query(AdminUser).filter(AdminUser.user_id == current_user.id).first()
    if not admin or not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have active administrative privileges."
        )
    return admin

def require_super_admin(
    current_admin: AdminUser = Depends(get_current_active_admin),
    db: Session = Depends(get_db)
) -> AdminUser:
    role = db.query(Role).filter(Role.id == current_admin.role_id).first()
    if not role or role.name != "SUPER ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires SUPER ADMIN privileges."
        )
    return current_admin