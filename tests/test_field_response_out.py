"""Registration API surfaces stored custom field responses."""
import pytest

from app.models.enums import RegistrationFieldScope, RegistrationFieldType
from app.schemas.event_content import FieldResponseInput, RegistrationFieldCreate
from app.schemas.registration import RegistrationCreateRequest, RegistrationType
from app.services import registration_field_service as field_svc
from app.services.registration_service import create_registration, get_registration_or_404
from tests.conftest import _make_event, _make_user_profile, requires_db

pytestmark = requires_db


@pytest.mark.asyncio
async def test_registration_out_includes_field_responses(db):
    event = await _make_event(db, name="Field Out Test")
    profile = await _make_user_profile(db, email="fieldout@test.edu")
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
    reg = await create_registration(
        db,
        event.id,
        profile,
        RegistrationCreateRequest(
            registration_type=RegistrationType.SOLO,
            field_responses=[FieldResponseInput(field_id=field.id, value="hello world")],
        ),
    )
    loaded = await get_registration_or_404(db, reg.id)
    assert len(loaded.field_responses) == 1
    assert loaded.field_responses[0].label == "Notes"
    assert loaded.field_responses[0].value == "hello world"
