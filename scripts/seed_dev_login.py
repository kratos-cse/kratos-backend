"""
Dev-only: mint a JWT for a local user without Google OAuth.

Refuse to run outside development/test environments.

    python scripts/seed_dev_login.py [email]
"""
import asyncio
import sys
import uuid

from sqlalchemy import select

from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from app.models.profile import Profile
from app.models.user import User


async def seed(email: str) -> None:
    env = (settings.ENVIRONMENT or "development").strip().lower()
    if env not in ("development", "test", "testing"):
        raise SystemExit(
            f"Refusing to run seed_dev_login.py when ENVIRONMENT={settings.ENVIRONMENT!r}"
        )

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(google_sub=f"dev-{uuid.uuid4().hex[:12]}", email=email)
            db.add(user)
            await db.flush()
            db.add(Profile(user_id=user.id, full_name="Dev Tester"))
            await db.commit()
            await db.refresh(user)
            print(f"Created user {user.id} ({email})")
        else:
            print(f"Reusing existing user {user.id} ({email})")

        token, expires_in = create_access_token(user.id)
        print(f"\nAccess token (expires in {expires_in}s):\n{token}")


if __name__ == "__main__":
    email = sys.argv[1] if len(sys.argv) > 1 else "dev.tester@example.com"
    asyncio.run(seed(email))
