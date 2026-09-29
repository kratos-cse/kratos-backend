"""Tests for registration form fields and validation."""
import uuid

import pytest

from app.core.errors import MISSING_REQUIRED_FIELD, UNKNOWN_FIELD, AppError
from app.models.enums import RegistrationFieldScope, RegistrationFieldType
from app.schemas.event_content import FieldResponseInput, RegistrationFieldCreate
from app.services import registration_field_service as field_svc
from app.services.field_response_validator import validate_and_prepare_responses
from tests.conftest import _make_event, _make_user_profile, requires_db

pytestmark = requires_db


@pytest.mark.asyncio
async def test_create_registration_field(db):
    event = await _make_event(db, name="Field Test")
    field = await field_svc.create_registration_field(
        db,
        event.id,
        RegistrationFieldCreate(
            scope=RegistrationFieldScope.REGISTRATION,
            field_key="tshirt_size",
            label="T-shirt size",
            field_type=RegistrationFieldType.SINGLE_SELECT,
            required=True,
            options={"choices": ["S", "M", "L"]},
        ),
    )
    form = await field_svc.get_registration_form(db, event.id)
    assert len(form.registration_fields) == 1
    assert form.registration_fields[0].field_key == "tshirt_size"


@pytest.mark.asyncio
async def test_validate_required_custom_field(db):
    event = await _make_event(db, name="Validate Test")
    profile = await _make_user_profile(db, email="field@test.edu")
    field = await field_svc.create_registration_field(
        db,
        event.id,
        RegistrationFieldCreate(
            scope=RegistrationFieldScope.REGISTRATION,
            field_key="notes",
            label="Notes",
            field_type=RegistrationFieldType.TEXT,
            required=True,
        ),
    )
    with pytest.raises(AppError) as exc:
        await validate_and_prepare_responses(
            db, event.id, RegistrationFieldScope.REGISTRATION, [], profile=profile
        )
    assert exc.value.code == MISSING_REQUIRED_FIELD

    pairs = await validate_and_prepare_responses(
        db,
        event.id,
        RegistrationFieldScope.REGISTRATION,
        [FieldResponseInput(field_id=field.id, value="hello")],
        profile=profile,
    )
    assert len(pairs) == 1
    assert pairs[0][1] == "hello"


@pytest.mark.asyncio
async def test_reject_unknown_field_id(db):
    event = await _make_event(db, name="Unknown Field")
    profile = await _make_user_profile(db, email="unknown@test.edu")
    with pytest.raises(AppError) as exc:
        await validate_and_prepare_responses(
            db,
            event.id,
            RegistrationFieldScope.REGISTRATION,
            [FieldResponseInput(field_id=uuid.uuid4(), value="x")],
            profile=profile,
        )
    assert exc.value.code == UNKNOWN_FIELD
