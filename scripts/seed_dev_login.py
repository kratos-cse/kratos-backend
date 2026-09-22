"""
Mints a valid access token for a fake local profile, bypassing real Google
sign-in, so the payment flow can be exercised locally without a Google OAuth
client configured. Inserts (or reuses) a USERS + PROFILES row directly and
signs a token with the same JWT_SECRET_KEY the app itself uses — this is
local dev-only, never for use against a real deployment.

    python scripts/seed_dev_login.py [email]
"""
import asyncio
import sys
import uuid

from sqlalchemy import select

from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from app.models.profile import Profile
from app.models.user import User


async def seed(email: str) -> None:
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
