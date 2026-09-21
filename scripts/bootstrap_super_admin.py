"""
Bootstrap the first Super Admin.

Usage:
  set SUPER_ADMIN_EMAIL in .env (must already exist in users.email via Google login)
  python scripts/bootstrap_super_admin.py

Idempotent: re-running updates the existing admin_users row to the SUPER ADMIN role
and reactivates it.
"""
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.admin import SUPER_ADMIN_ROLE_NAME, AdminUser, Role
from app.models.user import User


async def main() -> int:
    email = (settings.SUPER_ADMIN_EMAIL or "").strip().lower()
    if not email:
        print("ERROR: set SUPER_ADMIN_EMAIL in .env to an existing users.email", file=sys.stderr)
        return 1
    if not settings.DATABASE_URL:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        return 1

    async with AsyncSessionLocal() as db:
        user_result = await db.execute(select(User).where(User.email == email))
        user = user_result.scalar_one_or_none()
        if not user:
            print(
                f"ERROR: no user with email={email}. Sign in via POST /api/v1/auth/google first.",
                file=sys.stderr,
            )
            return 1

        role_result = await db.execute(select(Role).where(Role.name == SUPER_ADMIN_ROLE_NAME))
        role = role_result.scalar_one_or_none()
        if not role:
            print(
                f"ERROR: role '{SUPER_ADMIN_ROLE_NAME}' missing — run alembic upgrade head",
                file=sys.stderr,
            )
            return 1

        admin_result = await db.execute(
            select(AdminUser).options(selectinload(AdminUser.role)).where(AdminUser.user_id == user.id)
        )
        admin = admin_result.scalar_one_or_none()
        if admin:
            admin.role_id = role.id
            admin.is_active = True
            user.is_admin_flagged = True
            await db.commit()
            print(f"Updated existing admin_users row for {email} -> {SUPER_ADMIN_ROLE_NAME}")
            return 0

        db.add(AdminUser(user_id=user.id, role_id=role.id, is_active=True))
        user.is_admin_flagged = True
        await db.commit()
        print(f"Created SUPER ADMIN for {email} (user_id={user.id})")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
