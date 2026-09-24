"""Read-only audit for final team member identity review."""
import asyncio
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.qr_code import QRCode
from app.models.registration import Registration
from app.models.team import Team

REGS = [
    ("0e8e7596-3654-48a0-98cf-9769e7e407f9", "5fd7be9b-199d-4e4f-a469-df673f5515f0"),
    ("7421b694-2f93-4712-819d-a8570f44c5ff", "d64f16ae-ab98-4e19-b063-8f93e12bb6d7"),
]


async def main() -> None:
    engine = create_async_engine(settings.async_database_url)
    async with AsyncSession(engine) as db:
        try:
            total = await db.scalar(text("SELECT COUNT(*) FROM attendance_scans"))
            null_profile = await db.scalar(
                text("SELECT COUNT(*) FROM attendance_scans WHERE profile_id IS NULL")
            )
            print(f"attendance_scans_total={total}")
            print(f"attendance_null_profile={null_profile}")
            sample = await db.execute(
                text("SELECT id, profile_id, qr_id, result FROM attendance_scans LIMIT 5")
            )
            for row in sample:
                print("scan_sample", dict(row._mapping))
        except Exception as exc:
            print("attendance_query_error", exc)

        col = await db.scalar(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='attendance_scans' AND column_name='team_member_id'"
            )
        )
        print(f"team_member_id_column_exists={col is not None}")

        for rid, tid in REGS:
            reg = (
                await db.execute(
                    select(Registration)
                    .where(Registration.id == uuid.UUID(rid))
                    .options(
                        selectinload(Registration.team).selectinload(Team.members),
                        selectinload(Registration.payment),
                    )
                )
            ).scalar_one_or_none()
            if reg is None:
                print("MISSING_REG", rid)
                continue
            print("===", rid, "===")
            print(
                "team_id",
                reg.team_id,
                "expected",
                tid,
                "match",
                str(reg.team_id) == tid,
            )
            print("payment", reg.payment_id, reg.payment.status if reg.payment else None)
            for member in reg.team.members:
                print(
                    " member",
                    member.id,
                    "profile",
                    member.profile_id,
                    "entry",
                    member.entry_source,
                    "role",
                    member.role,
                    "name",
                    member.full_name,
                )
            qrs = await db.execute(
                select(QRCode).where(QRCode.team_member_id.in_([m.id for m in reg.team.members]))
            )
            for qr in qrs.scalars():
                print("  qr", qr.id, "tm", qr.team_member_id, "active", qr.is_active)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
