"""Google Sheets master spreadsheet sync (mocked API)."""
import json
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from app.services import google_sheets_service as gs

_VALID_SA_INFO = {
    "type": "service_account",
    "project_id": "kratos-test",
    "private_key_id": "key1",
    "private_key": "-----BEGIN PRIVATE KEY-----\nSECRET\n-----END PRIVATE KEY-----\n",
    "client_email": "sheets@test.iam.gserviceaccount.com",
    "client_id": "123",
}


def test_master_spreadsheet_id_required():
    with patch.object(settings, "GOOGLE_SHEETS_MASTER_SPREADSHEET_ID", ""):
        with pytest.raises(gs.GoogleSheetsConfigError):
            gs.get_master_spreadsheet_id()


def test_master_spreadsheet_id_resolution():
    with patch.object(settings, "GOOGLE_SHEETS_MASTER_SPREADSHEET_ID", "abc123"):
        assert gs.get_master_spreadsheet_id() == "abc123"


def test_sheets_sync_disabled_without_credentials():
    with patch.object(settings, "GOOGLE_SHEETS_MASTER_SPREADSHEET_ID", ""):
        assert gs.sheets_sync_enabled() is False
    with patch.object(settings, "GOOGLE_SHEETS_MASTER_SPREADSHEET_ID", "x"), patch.object(
        settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE", ""
    ), patch.object(settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON", ""):
        assert gs.sheets_sync_enabled() is False


def test_sheets_sync_enabled_with_json_env_only():
    with patch.object(settings, "GOOGLE_SHEETS_MASTER_SPREADSHEET_ID", "sheet-id"), patch.object(
        settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON", json.dumps(_VALID_SA_INFO)
    ), patch.object(settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE", ""):
        assert gs.sheets_sync_enabled() is True


def test_load_credentials_from_json_env():
    with patch.object(settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON", json.dumps(_VALID_SA_INFO)), patch.object(
        settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE", "/should/not/use"
    ), patch(
        "google.oauth2.service_account.Credentials.from_service_account_info",
        return_value=MagicMock(),
    ) as from_info, patch(
        "google.oauth2.service_account.Credentials.from_service_account_file",
    ) as from_file:
        gs.load_service_account_credentials()
        from_info.assert_called_once()
        from_file.assert_not_called()


def test_load_credentials_file_fallback():
    with patch.object(settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON", ""), patch.object(
        settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE", "/keys/sa.json"
    ), patch(
        "google.oauth2.service_account.Credentials.from_service_account_file",
        return_value=MagicMock(),
    ) as from_file:
        gs.load_service_account_credentials()
        from_file.assert_called_once_with("/keys/sa.json", scopes=gs.SCOPES)


def test_load_credentials_missing_raises():
    with patch.object(settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON", ""), patch.object(
        settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE", ""
    ):
        with pytest.raises(gs.GoogleSheetsConfigError, match="GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON"):
            gs.load_service_account_credentials()


def test_load_credentials_malformed_json():
    with patch.object(settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON", "{not-json"):
        with pytest.raises(gs.GoogleSheetsConfigError, match="not valid JSON"):
            gs.load_service_account_credentials()


def test_load_credentials_missing_required_fields():
    bad = dict(_VALID_SA_INFO)
    del bad["private_key"]
    with patch.object(settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON", json.dumps(bad)):
        with pytest.raises(gs.GoogleSheetsConfigError, match="private_key"):
            gs.load_service_account_credentials()


def test_malformed_json_does_not_log_secret(caplog):
    secret_json = json.dumps(_VALID_SA_INFO)
    with patch.object(settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON", secret_json[:-1] + "x"):
        with caplog.at_level(logging.ERROR):
            with pytest.raises(gs.GoogleSheetsConfigError):
                gs.load_service_account_credentials()
        joined = " ".join(caplog.messages)
        assert "SECRET" not in joined
        assert "BEGIN PRIVATE KEY" not in joined


def test_sanitize_worksheet_title_strips_invalid_chars():
    title = gs.sanitize_worksheet_title_part("Hackathon: Finals / Round 1")
    assert ":" not in title
    assert "/" not in title


def test_propose_worksheet_title_collision():
    eid = uuid.uuid4()
    existing = {"Hackathon"}
    title = gs.propose_worksheet_title("Hackathon", eid, existing)
    assert title not in existing
    assert len(title) <= 31


def test_duplicate_event_names_get_distinct_titles():
    a, b = uuid.uuid4(), uuid.uuid4()
    first = gs.propose_worksheet_title("Robotics", a, set())
    second = gs.propose_worksheet_title("Robotics", b, {first})
    assert first != second


def test_sheet_a1_quotes_apostrophes():
    assert gs._sheet_a1("Team's Day", "1:1") == "'Team''s Day'!1:1"


def test_headers_match_full_order():
    assert gs.headers_match(["A", "B"], ["A", "B"])
    assert not gs.headers_match(["A", "B"], ["A", "C"])
    assert not gs.headers_match(["A"], ["A", "B"])


def test_formula_injection_prefix():
    assert gs.sanitize_cell_value("=1+1").startswith("'")
    assert gs.sanitize_cell_value("+9198") == "'+9198"
    assert gs.sanitize_cell_value("normal") == "normal"


def test_find_registration_row_index():
    rid = uuid.uuid4()
    rows = [["other"], [str(rid), "x"], ["y"]]
    assert gs._find_registration_row_index(rows, rid) == 1
    assert gs._find_registration_row_index(rows, uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_upsert_updates_existing_row():
    reg_id = uuid.uuid4()
    event_id = uuid.uuid4()
    headers = ["Registration ID", "Name"]
    row = [str(reg_id), "Alice"]

    mock_client = MagicMock()
    mock_client.read_range.return_value = [headers, [str(reg_id), "Old"]]

    mock_reg = MagicMock()
    mock_reg.event_id = event_id

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_reg
    mock_db.execute.return_value = mock_result

    with (
        patch.object(gs, "sheets_sync_enabled", return_value=True),
        patch(
            "app.services.google_sheets_service.get_event_registration_sheet_rows",
            new=AsyncMock(return_value=(headers, {reg_id: row})),
        ),
        patch(
            "app.services.google_sheets_service.ensure_event_worksheet_initialized",
            new=AsyncMock(return_value="EventTab"),
        ),
        patch.object(gs, "_run_sync", side_effect=lambda fn: fn()),
    ):
        await gs.upsert_registration_row(mock_db, reg_id, client=mock_client)

    assert mock_client.write_range.called


@pytest.mark.asyncio
async def test_sync_event_one_event_only():
    event_id = uuid.uuid4()
    reg_id = uuid.uuid4()
    headers = ["Registration ID", "Event ID"]
    rows = {reg_id: [str(reg_id), str(event_id)]}

    mock_client = MagicMock()
    mock_client.read_range.return_value = [headers]

    mock_event = MagicMock()
    mock_event.id = event_id
    mock_event.name = "E"

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_event
    mock_db.execute.return_value = mock_result

    with (
        patch.object(gs, "get_master_spreadsheet_id", return_value="master-id"),
        patch.object(gs, "_get_client", new=AsyncMock(return_value=mock_client)),
        patch(
            "app.services.google_sheets_service.get_event_registration_sheet_rows",
            new=AsyncMock(return_value=(headers, rows)),
        ),
        patch(
            "app.services.google_sheets_service.ensure_event_worksheet_initialized",
            new=AsyncMock(return_value="Hackathon"),
        ),
        patch.object(gs, "_run_sync", side_effect=lambda fn: fn()),
    ):
        result = await gs.sync_event_to_sheet(mock_db, event_id)

    assert result.success is True
    assert result.synced_count == 1
    assert result.worksheet_title == "Hackathon"
    mock_client.append_rows.assert_called_once()


def test_sheets_disabled_skips_trigger():
    with patch.object(gs, "sheets_sync_enabled", return_value=False):
        gs.trigger_registration_sheet_sync(uuid.uuid4())


def test_build_integration_status_no_secrets():
    with patch.object(settings, "GOOGLE_SHEETS_MASTER_SPREADSHEET_ID", "master-abc"), patch.object(
        settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON", json.dumps(_VALID_SA_INFO)
    ), patch.object(settings, "GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE", ""):
        payload = gs.build_integration_status(token_valid=True, token_error=None)
    assert payload["sync_enabled"] is True
    assert payload["credentials_configured"] is True
    assert payload["master_spreadsheet_id"] == "master-abc"
    assert payload["token_valid"] is True
    assert "private_key" not in str(payload)
