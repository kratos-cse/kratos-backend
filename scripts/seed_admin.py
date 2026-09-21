"""
Seed script — creates a dev user + profile + SUPER ADMIN in one shot.

Usage (from project root):
  $env:PYTHONPATH="."; .kratosvenv\\Scripts\\python.exe scripts\\seed_admin.py

This is idempotent — safe to re-run. It will:
  1. Ensure the SUPER ADMIN role exists (created by migration 0003).
  2. Create a User row for SUPER_ADMIN_EMAIL if it doesn't exist yet.
  3. Create a Profile row for that user if needed.
  4. Create / update the admin_users row to SUPER ADMIN.
  5. Print the user_id to use with POST /api/v1/auth/dev-token.
"""
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.admin import SUPER_ADMIN_ROLE_NAME, AdminUser, Role
from app.models.profile import Profile
from app.models.user import User


async def main() -> int:
    email = (settings.SUPER_ADMIN_EMAIL or "").strip().lower()
    if not email:
        print("ERROR: set SUPER_ADMIN_EMAIL in .env", file=sys.stderr)
        return 1
    if not settings.DATABASE_URL:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        return 1

    async with AsyncSessionLocal() as db:
        # 1. Ensure SUPER ADMIN role exists
        role_result = await db.execute(select(Role).where(Role.name == SUPER_ADMIN_ROLE_NAME))
        role = role_result.scalar_one_or_none()
        if not role:
            print(f"ERROR: role '{SUPER_ADMIN_ROLE_NAME}' missing — run: alembic upgrade head", file=sys.stderr)
            return 1

        # 2. Upsert user
        user_result = await db.execute(select(User).where(User.email == email))
        user = user_result.scalar_one_or_none()
        if not user:
            user = User(
                google_sub=f"dev-seed-{email}",  # fake sub for dev only
                email=email,
                is_admin_flagged=True,
            )
            db.add(user)
            await db.flush()
            print(f"  Created user row for {email}")
        else:
            user.is_admin_flagged = True
            print(f"  Found existing user for {email}")

        # 3. Upsert profile
        profile_result = await db.execute(select(Profile).where(Profile.user_id == user.id))
        profile = profile_result.scalar_one_or_none()
        if not profile:
            profile = Profile(user_id=user.id, full_name="Super Admin", contact_email=email)
            db.add(profile)
            await db.flush()
            print("  Created profile row")
        else:
            print("  Profile already exists")

        # 4. Upsert admin_users
        admin_result = await db.execute(
            select(AdminUser).options(selectinload(AdminUser.role)).where(AdminUser.user_id == user.id)
        )
        admin = admin_result.scalar_one_or_none()
        if admin:
            admin.role_id = role.id
            admin.is_active = True
            print(f"  Updated admin_users row -> {SUPER_ADMIN_ROLE_NAME}")
        else:
            db.add(AdminUser(user_id=user.id, role_id=role.id, is_active=True))
            print(f"  Created admin_users row -> {SUPER_ADMIN_ROLE_NAME}")

        await db.commit()

    print("\n" + "=" * 60)
    print("SUPER ADMIN seeded successfully!")
    print(f"  Email   : {email}")
    print(f"  User ID : {user.id}")
    print()
    print("Next steps in Swagger (http://localhost:8000/docs):")
    print(f"  1. POST /api/v1/auth/dev-token  body: {{\"user_id\": \"{user.id}\"}}")
    print("  2. Copy the access_token from the response")
    print("  3. Click Authorize -> enter:  Bearer <token>")
    print("  4. All Admin routes are now unlocked!")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
