"""Google Sheets sync — one master spreadsheet, one worksheet tab per event."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.event import Event
from app.models.registration import Registration
from app.services.admin_ops_service import get_event_registration_sheet_rows

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_REQUIRED_SERVICE_ACCOUNT_FIELDS = ("type", "project_id", "private_key", "client_email")
INVALID_WORKSHEET_TITLE_CHARS = frozenset(r":\/?*[]")
REGISTRATION_ID_COLUMN = 0
HEADER_ROW_INDEX = 0
DATA_ROW_START = 1

_event_worksheet_locks: dict[uuid.UUID, asyncio.Lock] = {}
_registration_sync_locks: dict[uuid.UUID, asyncio.Lock] = {}


class GoogleSheetsConfigError(Exception):
    """Master spreadsheet or credentials not configured."""


class GoogleSheetsSyncError(Exception):
    """Sync failed; message is safe for API responses."""


@dataclass
class EventSheetSyncResult:
    success: bool
    spreadsheet_id: str
    spreadsheet_url: str
    worksheet_title: str
    synced_count: int = 0
    failed_count: int = 0
    errors: list[str] = field(default_factory=list)


def service_account_credentials_configured() -> bool:
    """True when JSON env or file path is set (not whether JSON is valid)."""
    if (settings.GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON or "").strip():
        return True
    return bool((settings.GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE or "").strip())


def sheets_sync_enabled() -> bool:
    return bool(
        (settings.GOOGLE_SHEETS_MASTER_SPREADSHEET_ID or "").strip()
        and service_account_credentials_configured()
    )


def probe_service_account_token() -> tuple[bool | None, str | None]:
    """Validate credentials with Google. Returns (token_valid, safe_error_message)."""
    if not service_account_credentials_configured():
        return None, None
    try:
        creds = load_service_account_credentials()
        from google.auth.transport.requests import Request

        if not creds.valid:
            creds.refresh(Request())
        return True, None
    except GoogleSheetsConfigError as exc:
        return False, str(exc)
    except Exception:
        logger.exception("google_sheets_token_probe_failed")
        return False, "Service account credentials could not be validated with Google."


def build_integration_status(
    *,
    token_valid: bool | None = None,
    token_error: str | None = None,
) -> dict[str, Any]:
    """Non-secret Google Sheets integration snapshot for admin status API."""
    master = (settings.GOOGLE_SHEETS_MASTER_SPREADSHEET_ID or "").strip() or None
    credentials_configured = service_account_credentials_configured()
    return {
        "sync_enabled": sheets_sync_enabled(),
        "credentials_configured": credentials_configured,
        "token_valid": token_valid if credentials_configured else None,
        "master_spreadsheet_id": master,
        "token_error": token_error if credentials_configured else None,
    }


def load_service_account_credentials():
    """Load Google service-account credentials (JSON env preferred over file)."""
    from google.oauth2 import service_account

    raw_json = (settings.GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON or "").strip()
    if raw_json:
        try:
            info = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.error("google_sheets_service_account_json_invalid parse_error=%s", exc.msg)
            raise GoogleSheetsConfigError(
                "GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON is not valid JSON."
            ) from exc
        if not isinstance(info, dict):
            raise GoogleSheetsConfigError(
                "GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON must be a JSON object."
            )
        missing = [key for key in _REQUIRED_SERVICE_ACCOUNT_FIELDS if not info.get(key)]
        if missing:
            raise GoogleSheetsConfigError(
                "GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON is missing required field(s): "
                + ", ".join(missing)
            )
        if info.get("type") != "service_account":
            raise GoogleSheetsConfigError(
                "GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON must have type 'service_account'."
            )
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    path = (settings.GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE or "").strip()
    if path:
        return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)

    logger.warning("google_sheets_credentials_not_configured")
    raise GoogleSheetsConfigError(
        "Configure GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON or GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE."
    )


def get_master_spreadsheet_id() -> str:
    sheet_id = (settings.GOOGLE_SHEETS_MASTER_SPREADSHEET_ID or "").strip()
    if not sheet_id:
        logger.error("google_sheets_master_spreadsheet_not_configured")
        raise GoogleSheetsConfigError(
            "GOOGLE_SHEETS_MASTER_SPREADSHEET_ID is not configured. "
            "Set it to the master registrations spreadsheet ID."
        )
    return sheet_id


def master_spreadsheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def sanitize_worksheet_title_part(name: str) -> str:
    cleaned = "".join(" " if c in INVALID_WORKSHEET_TITLE_CHARS else c for c in (name or "").strip())
    return " ".join(cleaned.split()) or "Event"


def propose_worksheet_title(
    event_name: str,
    event_id: uuid.UUID,
    existing_titles: set[str],
) -> str:
    """Readable tab title; disambiguate collisions with event id prefix."""
    base = sanitize_worksheet_title_part(event_name)[:22]
    short_id = str(event_id).replace("-", "")[:8]
    candidate = base[:31]
    if candidate not in existing_titles:
        return candidate
    candidate = f"{base[: max(1, 22)]} ({short_id})"[:31]
    if candidate not in existing_titles:
        return candidate
    n = 2
    while True:
        suffix = f" ({n})"
        candidate = f"{base[: max(1, 31 - len(suffix))]}{suffix}"[:31]
        if candidate not in existing_titles:
            return candidate
        n += 1


def _sheet_a1(title: str, range_suffix: str) -> str:
    """Quote worksheet title for A1 notation (handles apostrophes in tab names)."""
    quoted = "'" + title.replace("'", "''") + "'"
    return f"{quoted}!{range_suffix}"


def sanitize_cell_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if text and text[0] in ("=", "+", "-", "@"):
        return "'" + text
    return text


def headers_match(expected: list[str], actual: list[str]) -> bool:
    if len(expected) != len(actual):
        return False
    return all((a or "").strip() == (b or "").strip() for a, b in zip(expected, actual))


def _lock_for(key: uuid.UUID, store: dict[uuid.UUID, asyncio.Lock]) -> asyncio.Lock:
    if key not in store:
        store[key] = asyncio.Lock()
    return store[key]


class GoogleSheetsClient:
    """Thin wrapper around Sheets API v4 (synchronous; call via asyncio.to_thread)."""

    def __init__(self, service: Any, spreadsheet_id: str) -> None:
        self._service = service
        self.spreadsheet_id = spreadsheet_id

    @classmethod
    def from_settings(cls) -> "GoogleSheetsClient":
        from googleapiclient.discovery import build

        creds = load_service_account_credentials()
        service = build(
            "sheets",
            "v4",
            credentials=creds,
            cache_discovery=False,
        )
        return cls(service, get_master_spreadsheet_id())

    def list_worksheets(self) -> list[dict[str, Any]]:
        meta = (
            self._service.spreadsheets()
            .get(spreadsheetId=self.spreadsheet_id, fields="sheets.properties")
            .execute()
        )
        sheets = meta.get("sheets") or []
        return [s.get("properties") or {} for s in sheets]

    def create_worksheet(self, title: str) -> dict[str, Any]:
        body = {
            "requests": [
                {
                    "addSheet": {
                        "properties": {"title": title[:31]},
                    }
                }
            ]
        }
        resp = (
            self._service.spreadsheets()
            .batchUpdate(spreadsheetId=self.spreadsheet_id, body=body)
            .execute()
        )
        replies = resp.get("replies") or []
        if not replies:
            raise GoogleSheetsSyncError("Worksheet creation returned no reply from Google Sheets.")
        props = replies[0].get("addSheet", {}).get("properties", {})
        if not props.get("title"):
            raise GoogleSheetsSyncError("Worksheet creation did not return a title.")
        return props

    def read_range(self, range_a1: str) -> list[list[Any]]:
        result = (
            self._service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=range_a1)
            .execute()
        )
        return result.get("values") or []

    def write_range(self, range_a1: str, values: list[list[Any]], *, user_entered: bool = True) -> None:
        body = {"values": values}
        (
            self._service.spreadsheets()
            .values()
            .update(
                spreadsheetId=self.spreadsheet_id,
                range=range_a1,
                valueInputOption="USER_ENTERED" if user_entered else "RAW",
                body=body,
            )
            .execute()
        )

    def append_rows(self, range_a1: str, values: list[list[Any]]) -> None:
        (
            self._service.spreadsheets()
            .values()
            .append(
                spreadsheetId=self.spreadsheet_id,
                range=range_a1,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            )
            .execute()
        )

    def format_header_row(self, sheet_id: int, num_columns: int) -> None:
        if num_columns < 1:
            return
        body = {
            "requests": [
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                        "fields": "gridProperties.frozenRowCount",
                    }
                },
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": num_columns,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {"bold": True},
                                "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
                            }
                        },
                        "fields": "userEnteredFormat(textFormat,backgroundColor)",
                    }
                },
            ]
        }
        self._service.spreadsheets().batchUpdate(spreadsheetId=self.spreadsheet_id, body=body).execute()


async def _run_sync(client_fn):
    return await asyncio.to_thread(client_fn)


async def _get_client() -> GoogleSheetsClient:
    return await _run_sync(GoogleSheetsClient.from_settings)


async def resolve_event_worksheet_title(
    db: AsyncSession,
    event: Event,
    client: GoogleSheetsClient,
) -> str:
    """Stable tab title stored on the event; create mapping without touching unrelated tabs."""
    existing = {p.get("title") for p in await _run_sync(client.list_worksheets) if p.get("title")}

    if event.google_sheets_worksheet_title:
        title = event.google_sheets_worksheet_title
        if title in existing:
            return title
        await _run_sync(lambda: client.create_worksheet(title))
        refreshed = {p.get("title") for p in await _run_sync(client.list_worksheets) if p.get("title")}
        if title not in refreshed:
            raise GoogleSheetsSyncError(f"Failed to create worksheet tab '{title}'.")
        return title

    proposed = propose_worksheet_title(event.name, event.id, existing)
    await _run_sync(lambda: client.create_worksheet(proposed))
    refreshed = {p.get("title") for p in await _run_sync(client.list_worksheets) if p.get("title")}
    if proposed not in refreshed:
        raise GoogleSheetsSyncError("Worksheet tab was not found after creation.")

    event.google_sheets_worksheet_title = proposed
    await db.commit()
    return proposed


def _sheet_props_by_title(client: GoogleSheetsClient, title: str) -> Optional[dict[str, Any]]:
    for props in client.list_worksheets():
        if props.get("title") == title:
            return props
    return None


async def ensure_event_worksheet_initialized(
    db: AsyncSession,
    event_id: uuid.UUID,
    client: GoogleSheetsClient,
    expected_headers: list[str],
) -> str:
    lock = _lock_for(event_id, _event_worksheet_locks)
    async with lock:
        event = (
            await db.execute(select(Event).where(Event.id == event_id))
        ).scalar_one_or_none()
        if event is None:
            raise GoogleSheetsSyncError("Event not found.")

        title = await resolve_event_worksheet_title(db, event, client)
        props = await _run_sync(lambda: _sheet_props_by_title(client, title))
        sheet_id = int(props.get("sheetId", 0)) if props else 0

        current = await _run_sync(lambda: client.read_range(_sheet_a1(title, "1:1")))
        actual_header = [str(c) for c in (current[0] if current else [])]

        if not actual_header:
            row = [sanitize_cell_value(h) for h in expected_headers]
            await _run_sync(lambda: client.write_range(_sheet_a1(title, "1:1"), [row]))
            if sheet_id:
                await _run_sync(lambda: client.format_header_row(sheet_id, len(expected_headers)))
            return title

        if headers_match(expected_headers, actual_header):
            return title

        data_rows = await _run_sync(lambda: client.read_range(_sheet_a1(title, "A2:A")))
        if data_rows:
            raise GoogleSheetsSyncError(
                "Worksheet headers do not match the current export schema and the tab already "
                "contains registration rows. Update the sheet manually or use a new tab; "
                "automatic header migration was not applied to avoid corrupting data."
            )

        row = [sanitize_cell_value(h) for h in expected_headers]
        await _run_sync(lambda: client.write_range(_sheet_a1(title, "1:1"), [row]))
        if sheet_id:
            await _run_sync(lambda: client.format_header_row(sheet_id, len(expected_headers)))
        return title


def _find_registration_row_index(rows: list[list[Any]], registration_id: uuid.UUID) -> Optional[int]:
    reg_str = str(registration_id)
    for idx, row in enumerate(rows):
        if row and str(row[REGISTRATION_ID_COLUMN]).strip() == reg_str:
            return idx
    return None


async def upsert_registration_row(
    db: AsyncSession,
    registration_id: uuid.UUID,
    *,
    client: Optional[GoogleSheetsClient] = None,
) -> None:
    if not sheets_sync_enabled():
        return

    lock = _lock_for(registration_id, _registration_sync_locks)
    async with lock:
        reg = (
            await db.execute(
                select(Registration).where(Registration.id == registration_id)
            )
        ).scalar_one_or_none()
        if reg is None:
            return

        own_client = client is None
        if own_client:
            client = await _get_client()

        headers, rows_by_id = await get_event_registration_sheet_rows(db, reg.event_id)
        row = rows_by_id.get(registration_id)
        if row is None:
            return

        title = await ensure_event_worksheet_initialized(db, reg.event_id, client, headers)

        sheet_values = await _run_sync(lambda: client.read_range(_sheet_a1(title, "A:ZZ")))
        data_rows = sheet_values[DATA_ROW_START:] if sheet_values else []
        row_index = _find_registration_row_index(data_rows, registration_id)

        sanitized = [sanitize_cell_value(v) for v in row]
        if len(sanitized) != len(headers):
            raise GoogleSheetsSyncError("Row column count does not match header count.")

        if row_index is not None:
            sheet_row = DATA_ROW_START + 1 + row_index
            end_col = _column_letter(len(headers))
            await _run_sync(
                lambda: client.write_range(
                    _sheet_a1(title, f"A{sheet_row}:{end_col}{sheet_row}"), [sanitized]
                )
            )
        else:
            await _run_sync(lambda: client.append_rows(_sheet_a1(title, "A:A"), [sanitized]))


def _column_letter(n: int) -> str:
    """1-based column index to A1 letter(s)."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s or "A"


async def sync_event_to_sheet(db: AsyncSession, event_id: uuid.UUID) -> EventSheetSyncResult:
    spreadsheet_id = get_master_spreadsheet_id()
    client = await _get_client()

    event = (
        await db.execute(select(Event).where(Event.id == event_id))
    ).scalar_one_or_none()
    if event is None:
        raise GoogleSheetsSyncError("Event not found.")

    headers, rows_by_id = await get_event_registration_sheet_rows(db, event_id)
    title = await ensure_event_worksheet_initialized(db, event_id, client, headers)

    sheet_values = await _run_sync(lambda: client.read_range(_sheet_a1(title, "A:ZZ")))
    data_rows = sheet_values[DATA_ROW_START:] if sheet_values else []

    synced = 0
    failed = 0
    errors: list[str] = []

    for reg_id, row in rows_by_id.items():
        try:
            sanitized = [sanitize_cell_value(v) for v in row]
            if len(sanitized) != len(headers):
                failed += 1
                errors.append(f"{reg_id}: column count mismatch")
                continue
            row_index = _find_registration_row_index(data_rows, reg_id)
            if row_index is not None:
                sheet_row = DATA_ROW_START + 1 + row_index
                end_col = _column_letter(len(headers))
                await _run_sync(
                    lambda s=sheet_row, r=sanitized, ec=end_col, t=title: client.write_range(
                        _sheet_a1(t, f"A{s}:{ec}{s}"), [r]
                    )
                )
                data_rows[row_index] = [str(x) for x in sanitized]
            else:
                await _run_sync(lambda r=sanitized, t=title: client.append_rows(_sheet_a1(t, "A:A"), [r]))
                data_rows.append([str(sanitized[0])])
            synced += 1
        except GoogleSheetsSyncError as exc:
            failed += 1
            errors.append(f"{reg_id}: {exc}")
            logger.warning("google_sheets_row_sync_failed registration_id=%s error=%s", reg_id, exc)
        except Exception as exc:
            failed += 1
            err_name = type(exc).__name__
            if err_name == "HttpError":
                errors.append(f"{reg_id}: Google API error")
                logger.warning("google_sheets_row_sync_failed registration_id=%s api_error=1", reg_id)
            else:
                errors.append(f"{reg_id}: sync failed")
                logger.exception("google_sheets_row_sync_failed registration_id=%s", reg_id)

    return EventSheetSyncResult(
        success=failed == 0,
        spreadsheet_id=spreadsheet_id,
        spreadsheet_url=master_spreadsheet_url(spreadsheet_id),
        worksheet_title=title,
        synced_count=synced,
        failed_count=failed,
        errors=errors[:50],
    )


async def sync_registration_to_sheet(registration_id: uuid.UUID) -> None:
    """Load registration and upsert one row (uses its own DB session)."""
    if not sheets_sync_enabled():
        return
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            await upsert_registration_row(db, registration_id)
        except GoogleSheetsConfigError:
            logger.warning("google_sheets_sync_skipped_not_configured")
        except GoogleSheetsSyncError as exc:
            logger.warning("google_sheets_registration_sync_failed registration_id=%s error=%s", registration_id, exc)
        except Exception as exc:
            if type(exc).__name__ == "HttpError":
                logger.warning(
                    "google_sheets_api_error registration_id=%s status=%s",
                    registration_id,
                    getattr(exc, "status_code", None),
                )
                return
            logger.exception("google_sheets_registration_sync_failed registration_id=%s", registration_id)


async def sync_event_registrations_for_team(db: AsyncSession, team_id: uuid.UUID) -> None:
    result = await db.execute(select(Registration.id).where(Registration.team_id == team_id))
    reg_id = result.scalar_one_or_none()
    if reg_id:
        await upsert_registration_row(db, reg_id)


# --- Public trigger API (schedule after DB commit) ---


def trigger_registration_sheet_sync(registration_id: uuid.UUID) -> None:
    if not sheets_sync_enabled():
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(sync_registration_to_sheet(registration_id))
    except RuntimeError:
        pass


def trigger_team_sheet_sync(team_id: uuid.UUID) -> None:
    if not sheets_sync_enabled():
        return

    async def _run() -> None:
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            try:
                await sync_event_registrations_for_team(db, team_id)
            except Exception:
                logger.exception("google_sheets_team_sync_failed team_id=%s", team_id)

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        pass


def trigger_profile_sheet_sync(profile_id: uuid.UUID) -> None:
    if not sheets_sync_enabled():
        return

    async def _run() -> None:
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            solo = await db.execute(
                select(Registration.id).where(Registration.profile_id == profile_id)
            )
            for (reg_id,) in solo.all():
                try:
                    await upsert_registration_row(db, reg_id)
                except Exception:
                    logger.exception("google_sheets_profile_sync_failed registration_id=%s", reg_id)
            from app.models.team import TeamMember

            team_regs = await db.execute(
                select(Registration.id)
                .join(TeamMember, TeamMember.team_id == Registration.team_id)
                .where(TeamMember.profile_id == profile_id)
            )
            for (reg_id,) in team_regs.all():
                try:
                    await upsert_registration_row(db, reg_id)
                except Exception:
                    logger.exception("google_sheets_profile_sync_failed registration_id=%s", reg_id)

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        pass
