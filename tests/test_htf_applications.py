"""Service logic, validation, and error contract tests for HTF Applications."""

import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.errors import AppError
from app.core.htf_errors import (
    HTF_APPLICATION_NOT_EDITABLE,
    HTF_APPLICATION_NOT_FOUND,
    HTF_NOT_A_TEAM_MEMBER,
    HTF_TEAM_INCOMPLETE,
)
from app.models.enums import RegistrationFieldScope, RegistrationFieldType
from app.models.event_content import EventRegistrationField
from app.models.htf_application import HtfApplication, HtfApplicationStatus
from app.models.profile import Profile
from app.models.team import Team, TeamMember
from app.schemas.event_content import FieldResponseInput
from app.services import htf_application_service
from app.services.field_response_validator import validate_and_prepare_responses


@pytest.mark.asyncio
async def test_transition_status_valid_flow():
    db = AsyncMock()
    app = HtfApplication(
        id=uuid.uuid4(),
        status=HtfApplicationStatus.DRAFT,
    )

    # DRAFT -> SUBMITTED
    app = await htf_application_service.transition_status(
        db, app, HtfApplicationStatus.SUBMITTED
    )
    assert app.status == HtfApplicationStatus.SUBMITTED

    # SUBMITTED -> PPT_PENDING
    app = await htf_application_service.transition_status(
        db, app, HtfApplicationStatus.PPT_PENDING
    )
    assert app.status == HtfApplicationStatus.PPT_PENDING

    # Same state = no-op
    app = await htf_application_service.transition_status(
        db, app, HtfApplicationStatus.PPT_PENDING
    )
    assert app.status == HtfApplicationStatus.PPT_PENDING


@pytest.mark.asyncio
async def test_transition_status_illegal():
    db = AsyncMock()
    app = HtfApplication(
        id=uuid.uuid4(),
        status=HtfApplicationStatus.DRAFT,
    )

    # Cannot jump DRAFT -> CONFIRMED directly
    with pytest.raises(AppError) as exc_info:
        await htf_application_service.transition_status(
            db, app, HtfApplicationStatus.CONFIRMED
        )
    assert exc_info.value.code == HTF_APPLICATION_NOT_EDITABLE
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_under_screening_transitions():
    db = AsyncMock()
    app1 = HtfApplication(id=uuid.uuid4(), status=HtfApplicationStatus.UNDER_SCREENING)
    app2 = HtfApplication(id=uuid.uuid4(), status=HtfApplicationStatus.UNDER_SCREENING)

    # SHORTLISTED allowed
    app1 = await htf_application_service.transition_status(
        db, app1, HtfApplicationStatus.SHORTLISTED
    )
    assert app1.status == HtfApplicationStatus.SHORTLISTED

    # NOT_SHORTLISTED allowed
    app2 = await htf_application_service.transition_status(
        db, app2, HtfApplicationStatus.NOT_SHORTLISTED
    )
    assert app2.status == HtfApplicationStatus.NOT_SHORTLISTED


@pytest.mark.asyncio
async def test_lenient_draft_validation():
    db = AsyncMock()
    field1 = EventRegistrationField(
        id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        scope=RegistrationFieldScope.REGISTRATION,
        field_key="problem_statement",
        label="Problem Statement",
        field_type=RegistrationFieldType.TEXT,
        required=True,
    )

    with patch("app.services.field_response_validator.load_visible_fields", return_value=[field1]):
        # enforce_required=False allows missing required field in drafts
        pairs = await validate_and_prepare_responses(
            db,
            field1.event_id,
            RegistrationFieldScope.REGISTRATION,
            responses=[],
            enforce_required=False,
        )
        assert len(pairs) == 0

        # enforce_required=True raises missing required field error on submission
        with pytest.raises(AppError) as exc_info:
            await validate_and_prepare_responses(
                db,
                field1.event_id,
                RegistrationFieldScope.REGISTRATION,
                responses=[],
                enforce_required=True,
            )
        assert exc_info.value.code == "MISSING_REQUIRED_FIELD"


@pytest.mark.asyncio
async def test_get_application_access_control():
    db = AsyncMock()
    creator_id = uuid.uuid4()
    app = HtfApplication(
        id=uuid.uuid4(),
        created_by_profile_id=creator_id,
        team_id=uuid.uuid4(),
    )

    mock_res1 = MagicMock()
    mock_res1.scalar_one_or_none.return_value = app

    mock_res2 = MagicMock()
    mock_res2.scalar_one_or_none.return_value = None

    db.execute.side_effect = [mock_res1, mock_res2, mock_res2]

    # Non-member / non-creator gets 404
    other_profile = Profile(id=uuid.uuid4(), user_id=uuid.uuid4())
    with pytest.raises(AppError) as exc_info:
        await htf_application_service.get_application(db, app.id, other_profile)

    assert exc_info.value.code == HTF_APPLICATION_NOT_FOUND
    assert exc_info.value.status_code == 404
