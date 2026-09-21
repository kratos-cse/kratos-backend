# scripts/seed_test_users.py
import asyncio
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.profile import Profile

async def main():
    async with AsyncSessionLocal() as db:
        for n in ["leader", "member1", "member2", "outsider", "scanner"]:
            sub = f"dev-{n}"
            new_email = f"{n}@example.com"
            result = await db.execute(select(User).where(User.google_sub == sub))
            u = result.scalar_one_or_none()
            if u:
                u.email = new_email
            else:
                u = User(google_sub=sub, email=new_email)
                db.add(u)
            await db.flush()

            prof_result = await db.execute(select(Profile).where(Profile.user_id == u.id))
            prof = prof_result.scalar_one_or_none()
            if prof:
                prof.contact_email = new_email
                prof.full_name = n.title()
            else:
                db.add(Profile(user_id=u.id, full_name=n.title(), contact_email=new_email))
            print(n, u.id, u.email)
        await db.commit()

if __name__ == "__main__":
    asyncio.run(main())