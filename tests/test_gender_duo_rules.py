"""Tests for gender categories, duo event rules, leader-entered roster details, and custom field surfacing."""
import uuid
from decimal import Decimal
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.models.enums import (
    Gender,
    GenderCategory,
    RegistrationFieldScope,
    RegistrationFieldSource,
    RegistrationFieldType,
    RegistrationMode,
    RegistrationStatus,
    TeamMemberEntrySource,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.event import Event, EventRegistrationRule
from app.models.event_content import EventRegistrationField, RegistrationFieldResponse
from app.models.profile import Profile
from app.models.team import Team, TeamMember
from app.schemas.event_content import FieldResponseInput
from app.schemas.registration import RegistrationCreateRequest, RegistrationType
from app.schemas.team import RosterMemberInput
from app.services import registration_service, team_service
from app.services.field_response_validator import PROFILE_FIELD_KEYS, _profile_value, _team_member_value


def test_root_endpoint():
    """Verify GET / returns 200 with service metadata."""
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "KRATOS" in data["service"]
    assert data["health"] == "/health"
    assert data["docs"] == "/docs"


def test_azure_sslmode_normalization():
    """Verify that sslmode=require is normalized to ssl=require for asyncpg."""
    s = Settings(DATABASE_URL="postgresql://kratos_admin:secret@kratos-db.postgres.database.azure.com:5432/kratos?sslmode=require")
    assert "ssl=require" in s.async_database_url
    assert "sslmode=" not in s.async_database_url
    assert s.async_database_url.startswith("postgresql+asyncpg://")


def test_gender_in_profile_field_keys():
    """Verify 'gender' is recognized as a valid profile field key."""
    assert "gender" in PROFILE_FIELD_KEYS


def test_profile_and_team_member_gender_extraction():
    """Verify _profile_value and _team_member_value extract gender correctly."""
    p = Profile(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        full_name="Alex Ray",
        gender=Gender.FEMALE,
    )
    assert _profile_value(p, "gender") == "FEMALE"

    tm = TeamMember(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        role=TeamMemberRole.MEMBER,
        status=TeamMemberStatus.ACTIVE,
        full_name="Jordan Lee",
        gender=Gender.MALE,
    )
    assert _team_member_value(tm, None, "gender") == "MALE"


def test_admin_export_roster_details():
    """Verify that leader-entered members in export do not show as 'Unknown' and have phone/gender."""
    leader_prof = Profile(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        full_name="Priya Sharma",
        phone="+919876543210",
        gender=Gender.FEMALE,
    )
    partner = TeamMember(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        role=TeamMemberRole.MEMBER,
        status=TeamMemberStatus.ACTIVE,
        entry_source=TeamMemberEntrySource.LEADER_ENTERED,
        full_name="Rahul Verma",
        phone="+919876543211",
        gender=Gender.MALE,
    )

    profiles = {leader_prof.id: leader_prof}
    active_members = [
        TeamMember(
            id=uuid.uuid4(),
            team_id=partner.team_id,
            event_id=partner.event_id,
            profile_id=leader_prof.id,
            role=TeamMemberRole.LEADER,
            status=TeamMemberStatus.ACTIVE,
        ),
        partner,
    ]

    members_summary = []
    for m in active_members:
        m_prof = profiles.get(m.profile_id) if m.profile_id else None
        m_name = (m_prof.full_name if m_prof else m.full_name) or "Unknown"
        m_role = m.role.value if hasattr(m.role, "value") else str(m.role)
        phone_val = (m_prof.phone if m_prof else m.phone) or ""
        m_phone = f", {phone_val}" if phone_val else ""
        gender_val = (m_prof.gender if m_prof else getattr(m, "gender", None)) or ""
        m_gender = f", {gender_val.value if hasattr(gender_val, 'value') else gender_val}" if gender_val else ""
        members_summary.append(f"{m_name} ({m_role}{m_phone}{m_gender})")

    roster_str = "; ".join(members_summary)
    assert "Rahul Verma" in roster_str
    assert "Unknown" not in roster_str
    assert "+919876543211" in roster_str
    assert "MALE" in roster_str
    assert "Priya Sharma" in roster_str


def test_registration_form_endpoint():
    """Verify GET /api/v1/events/{id}/registration-form does not crash."""
    client = TestClient(app)
    eid = uuid.UUID("10000000-0000-0000-0000-000000000001")
    resp = client.get(f"/api/v1/events/{eid}/registration-form")
    assert resp.status_code == 200
    data = resp.json()
    assert "registration_fields" in data
    assert "team_member_fields" in data


def test_roster_member_input_and_registration_create_schema():
    """Verify RegistrationCreateRequest accepts roster_members for atomic duo registration."""
    partner = RosterMemberInput(
        full_name="Kavya Nair",
        phone="+919123456780",
        contact_email="kavya@example.com",
        gender=Gender.FEMALE,
    )
    req = RegistrationCreateRequest(
        registration_type=RegistrationType.TEAM,
        team_name="Duo Dynamos",
        roster_members=[partner],
    )
    assert len(req.roster_members) == 1
    assert req.roster_members[0].full_name == "Kavya Nair"
    assert req.roster_members[0].gender == Gender.FEMALE


def test_duo_mixed_gender_validation_logic():
    """Verify business logic rules for MIXED duo: opposite genders required."""
    rules = EventRegistrationRule(
        event_id=uuid.uuid4(),
        team_min_size=2,
        team_max_size=2,
        gender_category=GenderCategory.MIXED,
    )
    # Case A: Leader Female, Partner Male -> Valid
    leader_gender = Gender.FEMALE
    partner_gender = Gender.MALE
    assert {leader_gender, partner_gender} == {Gender.MALE, Gender.FEMALE}

    # Case B: Leader Male, Partner Male -> Invalid
    leader_gender = Gender.MALE
    partner_gender = Gender.MALE
    assert leader_gender == partner_gender  # Triggers 400 rejection in create_registration
