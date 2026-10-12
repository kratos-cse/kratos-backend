"""Unit and integration tests for real-time Google Sheets synchronization."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.enums import PaymentStatus, RegistrationStatus
from app.models.event import Event, EventRegistrationRule
from app.models.profile import Profile
from app.models.registration import Registration
from app.services import google_sheets_service as gss


def test_resolve_spreadsheet_id_from_event_id():
    ev = Event(name="Test", google_sheet_id="sheet_abc_123")
    assert gss.resolve_spreadsheet_id(ev) == "sheet_abc_123"


def test_resolve_spreadsheet_id_from_url():
    ev = Event(
        name="Test",
        google_sheet_url="https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit#gid=0",
    )
    assert gss.resolve_spreadsheet_id(ev) == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"


def test_resolve_spreadsheet_id_fallback_to_master(monkeypatch):
    monkeypatch.setattr(
        get_settings(), "GOOGLE_SHEETS_MASTER_SPREADSHEET_ID", "master_sheet_999"
    )
    ev = Event(name="Test")
    assert gss.resolve_spreadsheet_id(ev) == "master_sheet_999"


@pytest.mark.asyncio
async def test_ensure_worksheet_and_headers_writes_headers_when_empty():
    mock_client = AsyncMock()

    # GET metadata -> sheet exists
    mock_client.get.side_effect = [
        # 1. GET metadata
        MagicMock(
            status_code=200,
            json=lambda: {
                "sheets": [{"properties": {"title": "Registrations", "sheetId": 0}}]
            },
        ),
        # 2. GET row 1 values -> empty
        MagicMock(status_code=200, json=lambda: {"values": []}),
    ]
    # PUT headers
    mock_client.put.return_value = MagicMock(status_code=200)
    # POST format
    mock_client.post.return_value = MagicMock(status_code=200)

    title = await gss._ensure_worksheet_and_headers(
        mock_client,
        spreadsheet_id="test_sheet",
        sheet_title="Registrations",
        headers=["Registration ID", "Event ID", "Event Name"],
    )

    assert title == "Registrations"
    assert mock_client.put.called
    put_args = mock_client.put.call_args
    assert put_args[1]["json"]["values"] == [["Registration ID", "Event ID", "Event Name"]]


@pytest.mark.asyncio
async def test_sync_registration_to_sheet_updates_existing_row(monkeypatch):
    reg_id = uuid.uuid4()
    mock_reg = MagicMock()
    mock_reg.id = reg_id
    mock_event = MagicMock()
    mock_event.name = "Test Event"
    headers = ["Registration ID", "Event ID", "Event Name"]
    row_data = [str(reg_id), str(uuid.uuid4()), "Test Event"]

    monkeypatch.setattr(gss, "get_access_token", lambda: "fake-test-token")
    monkeypatch.setattr(
        gss,
        "_load_registration_row_data",
        AsyncMock(return_value=(mock_reg, mock_event, headers, row_data, "sheet_123")),
    )
    monkeypatch.setattr(
        gss, "_ensure_worksheet_and_headers", AsyncMock(return_value="Registrations")
    )

    # Mock httpx AsyncClient
    with patch("httpx.AsyncClient") as MockClientClass:
        mock_client_instance = AsyncMock()
        MockClientClass.return_value.__aenter__.return_value = mock_client_instance

        # GET column A has reg_id at row 2
        mock_client_instance.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"values": [["Registration ID"], [str(reg_id)]]},
        )
        mock_client_instance.put.return_value = MagicMock(status_code=200)

        success = await gss.sync_registration_to_sheet(reg_id)
        assert success is True
        assert mock_client_instance.put.called
        # Check that update was sent to row 2
        update_call_url = mock_client_instance.put.call_args[0][0]
        assert "A2" in update_call_url


@pytest.mark.asyncio
async def test_sync_registration_to_sheet_appends_new_row(monkeypatch):
    reg_id = uuid.uuid4()
    mock_reg = MagicMock()
    mock_reg.id = reg_id
    mock_event = MagicMock()
    mock_event.name = "Test Event"
    headers = ["Registration ID", "Event ID", "Event Name"]
    row_data = [str(reg_id), str(uuid.uuid4()), "Test Event"]

    monkeypatch.setattr(gss, "get_access_token", lambda: "fake-test-token")
    monkeypatch.setattr(
        gss,
        "_load_registration_row_data",
        AsyncMock(return_value=(mock_reg, mock_event, headers, row_data, "sheet_123")),
    )
    monkeypatch.setattr(
        gss, "_ensure_worksheet_and_headers", AsyncMock(return_value="Registrations")
    )

    # Mock httpx AsyncClient
    with patch("httpx.AsyncClient") as MockClientClass:
        mock_client_instance = AsyncMock()
        MockClientClass.return_value.__aenter__.return_value = mock_client_instance

        # GET column A does NOT have reg_id
        mock_client_instance.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"values": [["Registration ID"], ["some-other-uuid"]]},
        )
        mock_client_instance.post.return_value = MagicMock(status_code=200)

        success = await gss.sync_registration_to_sheet(reg_id)
        assert success is True
        assert mock_client_instance.post.called
        append_call_url = mock_client_instance.post.call_args[0][0]
        assert "append" in append_call_url


def test_trigger_helpers_dont_raise():
    """Ensure non-blocking trigger calls are safe and never raise."""
    dummy_id = uuid.uuid4()
    gss.trigger_registration_sheet_sync(dummy_id)
    gss.trigger_registration_sheet_sync(None)
    gss.trigger_team_sheet_sync(dummy_id)
    gss.trigger_team_sheet_sync(None)
    gss.trigger_profile_sheet_sync(dummy_id)
    gss.trigger_profile_sheet_sync(None)
