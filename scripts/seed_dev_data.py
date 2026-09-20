"""
Inserts two sample events so GET /events and the registration flow have
something to hit in Swagger UI before Admin Event Management (section 15,
a different branch) exists. Run from the repo root, after `alembic upgrade
head`, once DATABASE_URL is set:

    python scripts/seed_dev_data.py
"""
import asyncio

from app.db.session import AsyncSessionLocal
from app.models.enums import CapacityType, EventStatus, FeeChargeModel, MemberRegistrationMode
from app.models.event import Event, EventRegistrationRule


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        solo_event = Event(
            name="Code Sprint (Solo)",
            short_desc="Individual competitive programming round.",
            category="Technical",
            fee=100,
            venue="Lab 2",
            status=EventStatus.OPEN,
        )
        db.add(solo_event)
        await db.flush()
        db.add(
            EventRegistrationRule(
                event_id=solo_event.id,
                team_min_size=1,
                team_max_size=1,
                allow_individual=True,
                fee_charge_model=FeeChargeModel.PER_MEMBER,
                capacity_type=CapacityType.PARTICIPANTS,
                member_registration_mode=MemberRegistrationMode.SELF_ENTRY,
            )
        )

        # team_max_size > 4 -> "Dynamic Flow" per the shared workflow diagram:
        # leader registers + pays, then shares an invite link members use to
        # join and self-enter their own details.
        team_event = Event(
            name="Hackathon 2026",
            short_desc="24-hour team hackathon.",
            category="Hackathon",
            fee=500,
            venue="Main Auditorium",
            status=EventStatus.OPEN,
        )
        db.add(team_event)
        await db.flush()
        db.add(
            EventRegistrationRule(
                event_id=team_event.id,
                team_min_size=2,
                team_max_size=6,
                allow_individual=False,
                fee_charge_model=FeeChargeModel.PER_TEAM,
                allow_team_invite_flow=True,
                capacity_type=CapacityType.TEAMS,
                member_registration_mode=MemberRegistrationMode.SELF_ENTRY,
            )
        )

        await db.commit()
        print(f"Seeded events: solo={solo_event.id}  team={team_event.id}")


if __name__ == "__main__":
    asyncio.run(seed())
