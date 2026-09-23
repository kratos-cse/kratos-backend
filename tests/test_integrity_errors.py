"""IntegrityError must map only known duplicate constraints."""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.errors import ALREADY_REGISTERED, AppError
from app.core.integrity_errors import (
    is_duplicate_registration_error,
    is_duplicate_team_member_error,
    raise_duplicate_registration,
    raise_duplicate_team_member,
)
from app.models.enums import RegistrationStatus
from app.models.registration import Registration
from app.models.team import TeamMember
from app.schemas.registration import RegistrationCreateRequest, RegistrationType
from app.services.registration_service import create_registration
from tests.conftest import _make_event, _make_user_profile, requires_db

pytestmark = requires_db


class _FakeOrig:
    def __init__(self, constraint_name: str | None):
        self.constraint_name = constraint_name


def test_constraint_detection_helpers():
    reg_exc = IntegrityError("insert", {}, _FakeOrig("uq_registrations_event_profile_active"))
    fk_exc = IntegrityError("insert", {}, _FakeOrig("fk_registrations_event_id_events"))
    assert is_duplicate_registration_error(reg_exc)
    assert not is_duplicate_registration_error(fk_exc)

    mem_exc = IntegrityError("insert", {}, _FakeOrig("uq_team_members_event_profile_active"))
    assert is_duplicate_team_member_error(mem_exc)
    assert not is_duplicate_team_member_error(fk_exc)


def test_raise_helpers_passthrough_unknown():
    fk_exc = IntegrityError("insert", {}, _FakeOrig("fk_registrations_event_id_events"))
    with pytest.raises(IntegrityError):
        raise_duplicate_registration(fk_exc, "dup")

    with pytest.raises(IntegrityError):
        raise_duplicate_team_member(fk_exc, "dup")


@pytest.mark.asyncio
async def test_duplicate_registration_maps_to_already_registered(db):
    profile = await _make_user_profile(db, email=f"dup-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"Dup {uuid.uuid4().hex[:6]}")
    await create_registration(
        db, event.id, profile, RegistrationCreateRequest(registration_type=RegistrationType.SOLO)
    )

    with pytest.raises(AppError) as exc:
        await create_registration(
            db, event.id, profile, RegistrationCreateRequest(registration_type=RegistrationType.SOLO)
        )
    assert exc.value.code == ALREADY_REGISTERED


@pytest.mark.asyncio
async def test_fk_violation_not_mapped_to_already_registered(db):
    profile = await _make_user_profile(db, email=f"fk-{uuid.uuid4().hex}@t.local")
    db.add(
        Registration(
            event_id=uuid.uuid4(),
            profile_id=profile.id,
            status=RegistrationStatus.PENDING,
        )
    )
    with pytest.raises(IntegrityError):
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise


@pytest.mark.asyncio
async def test_not_null_violation_not_mapped_to_already_registered(db):
    from app.models.enums import TeamMemberRole, TeamMemberStatus

    db.add(
        TeamMember(
            team_id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            role=TeamMemberRole.MEMBER,
            status=TeamMemberStatus.ACTIVE,
        )
    )
    with pytest.raises(IntegrityError):
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise
