"""
Real-time Google Sheets synchronization service.

Maintains real-time synchronization between registration database records
and corresponding Google Spreadsheets (per-event spreadsheet or master spreadsheet).
"""
import asyncio
import base64
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.enums import RegistrationFieldSource, TeamMemberRole, TeamMemberStatus
from app.models.event import Event, EventRegistrationRule
from app.models.event_content import EventRegistrationField, RegistrationFieldResponse
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.services.admin_ops_service import (
    BASE_REGISTRATION_HEADERS_END,
    BASE_REGISTRATION_HEADERS_START,
    _build_registration_row,
    _excel_sheet_title,
    _get_event_member_columns_count,
)

logger = logging.getLogger("google_sheets_service")

SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# Cached credentials and access token
_cached_credentials: Optional[Credentials] = None
_cached_token: Optional[str] = None
_token_expiry: Optional[datetime] = None


def get_service_account_credentials() -> Optional[Credentials]:
    """Load Google Service Account credentials from settings or credentials file."""
    global _cached_credentials
    if _cached_credentials is not None:
        return _cached_credentials

    settings = get_settings()

    # 1. Raw or base64-encoded JSON string in settings
    raw_json = settings.GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON.strip()
    if raw_json:
        try:
            if not raw_json.startswith("{"):
                # Try decoding base64
                raw_json = base64.b64decode(raw_json).decode("utf-8")
            data = json.loads(raw_json)
            if data.get("type") == "service_account":
                _cached_credentials = Credentials.from_service_account_info(data, scopes=SCOPES)
                logger.info("google_sheets_credentials_loaded_from_json_string")
                return _cached_credentials
        except Exception as exc:
            logger.error("failed_to_parse_google_sheets_service_account_json: %s", exc)

    # 2. Path to credentials file
    cred_file = settings.GOOGLE_SHEETS_CREDENTIALS_FILE.strip()
    if cred_file and os.path.isfile(cred_file):
        try:
            _cached_credentials = Credentials.from_service_account_file(cred_file, scopes=SCOPES)
            logger.info("google_sheets_credentials_loaded_from_file path=%s", cred_file)
            return _cached_credentials
        except Exception as exc:
            logger.error("failed_to_load_google_sheets_credentials_file: %s", exc)

    # 3. Default common filenames in current directory or backend directory
    for candidate in ("service_account.json", "credentials.json", "google_credentials.json"):
        if os.path.isfile(candidate):
            try:
                _cached_credentials = Credentials.from_service_account_file(candidate, scopes=SCOPES)
                logger.info("google_sheets_credentials_auto_detected path=%s", candidate)
                return _cached_credentials
            except Exception:
                pass

    return None


def get_access_token() -> Optional[str]:
    """Retrieve or refresh an OAuth2 access token using service account credentials."""
    global _cached_token, _token_expiry
    creds = get_service_account_credentials()
    if creds is None:
        return None

    now = datetime.now(timezone.utc)
    if _cached_token and _token_expiry and _token_expiry > now:
        return _cached_token

    try:
        req = Request()
        creds.refresh(req)
        _cached_token = creds.token
        # credentials.expiry is in UTC
        _token_expiry = creds.expiry
        return _cached_token
    except Exception as exc:
        logger.error("failed_to_refresh_google_sheets_token: %s", exc)
        return None


def resolve_spreadsheet_id(event: Optional[Event]) -> Optional[str]:
    """
    Determine the target Google Spreadsheet ID for an event.
    Order of precedence:
    1. event.google_sheet_id
    2. event.google_sheet_url (extracted via regex)
    3. GOOGLE_SHEETS_MASTER_SPREADSHEET_ID from settings
    """
    if event:
        if event.google_sheet_id and event.google_sheet_id.strip():
            return event.google_sheet_id.strip()
        if event.google_sheet_url and event.google_sheet_url.strip():
            match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", event.google_sheet_url)
            if match:
                return match.group(1)

    settings = get_settings()
    if settings.GOOGLE_SHEETS_MASTER_SPREADSHEET_ID and settings.GOOGLE_SHEETS_MASTER_SPREADSHEET_ID.strip():
        return settings.GOOGLE_SHEETS_MASTER_SPREADSHEET_ID.strip()

    return None


async def _ensure_worksheet_and_headers(
    client: httpx.AsyncClient,
    spreadsheet_id: str,
    sheet_title: str,
    headers: list[str],
) -> str:
    """
    Ensure the worksheet tab exists and row 1 contains the headers.
    Returns the resolved sheet title.
    """
    # 1. Fetch spreadsheet metadata to check if tab exists
    url = f"{SHEETS_API_BASE}/{spreadsheet_id}?fields=sheets.properties"
    res = await client.get(url)
    if res.status_code != 200:
        logger.error("failed_to_fetch_spreadsheet_metadata code=%s body=%s", res.status_code, res.text)
        res.raise_for_status()

    meta = res.json()
    sheets_info = [s.get("properties", {}) for s in meta.get("sheets", [])]
    existing_titles = [p.get("title") for p in sheets_info]
    sheet_id_map = {p.get("title"): p.get("sheetId") for p in sheets_info}

    target_title = sheet_title
    target_sheet_id = None

    if target_title not in existing_titles:
        # If there is only one sheet and it's named "Sheet1", use or rename it
        if len(existing_titles) == 1 and existing_titles[0] == "Sheet1":
            target_title = "Sheet1"
            target_sheet_id = sheet_id_map.get("Sheet1")
        else:
            # Create a new sheet tab with target_title
            batch_url = f"{SHEETS_API_BASE}/{spreadsheet_id}:batchUpdate"
            add_req = {
                "requests": [
                    {
                        "addSheet": {
                            "properties": {
                                "title": target_title,
                            }
                        }
                    }
                ]
            }
            create_res = await client.post(batch_url, json=add_req)
            if create_res.status_code == 200:
                try:
                    target_sheet_id = (
                        create_res.json()
                        .get("replies", [{}])[0]
                        .get("addSheet", {})
                        .get("properties", {})
                        .get("sheetId")
                    )
                except Exception:
                    pass
                logger.info("created_google_sheet_tab spreadsheet=%s title=%s", spreadsheet_id, target_title)
            else:
                logger.warning("failed_to_add_sheet_tab code=%s body=%s", create_res.status_code, create_res.text)
                target_title = existing_titles[0] if existing_titles else "Sheet1"
    else:
        target_sheet_id = sheet_id_map.get(target_title)

    # 2. Check Row 1 for headers
    val_url = f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{target_title}!1:1"
    row1_res = await client.get(val_url)
    row1_data = row1_res.json().get("values", [])

    need_headers = False
    if not row1_data or not row1_data[0]:
        need_headers = True
    elif len(row1_data[0]) < len(headers) or row1_data[0][0] != headers[0]:
        need_headers = True

    if need_headers:
        put_url = f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{target_title}!A1?valueInputOption=USER_ENTERED"
        await client.put(put_url, json={"values": [headers]})
        logger.info("wrote_google_sheet_headers spreadsheet=%s title=%s", spreadsheet_id, target_title)

        # Freeze row 1 and bold headers if sheetId is known
        if target_sheet_id is not None:
            format_url = f"{SHEETS_API_BASE}/{spreadsheet_id}:batchUpdate"
            format_req = {
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": target_sheet_id,
                                "gridProperties": {"frozenRowCount": 1},
                            },
                            "fields": "gridProperties.frozenRowCount",
                        }
                    },
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": target_sheet_id,
                                "startRowIndex": 0,
                                "endRowIndex": 1,
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "textFormat": {"bold": True},
                                }
                            },
                            "fields": "userEnteredFormat.textFormat.bold",
                        }
                    },
                ]
            }
            try:
                await client.post(format_url, json=format_req)
            except Exception:
                pass

    return target_title


async def _load_registration_row_data(
    db: AsyncSession, registration_id: uuid.UUID
) -> Optional[tuple[Registration, Event, list[str], list[Any], str]]:
    """
    Load a registration and build its standardized row data.
    Returns (registration, event, headers, row_data, resolved_spreadsheet_id).
    """
    stmt = (
        select(Registration)
        .options(
            selectinload(Registration.event).selectinload(Event.rules),
            selectinload(Registration.team).selectinload(Team.members),
            selectinload(Registration.payment),
        )
        .where(Registration.id == registration_id)
    )
    result = await db.execute(stmt)
    reg = result.scalar_one_or_none()
    if not reg or not reg.event:
        return None

    event = reg.event
    spreadsheet_id = resolve_spreadsheet_id(event)
    if not spreadsheet_id:
        return None

    # Load all relevant profiles
    profile_ids: set[uuid.UUID] = set()
    if reg.profile_id:
        profile_ids.add(reg.profile_id)
    if reg.team:
        if reg.team.leader_profile_id:
            profile_ids.add(reg.team.leader_profile_id)
        for m in reg.team.members or []:
            if m.profile_id:
                profile_ids.add(m.profile_id)

    profiles: dict[uuid.UUID, Profile] = {}
    if profile_ids:
        prof_res = await db.execute(select(Profile).where(Profile.id.in_(profile_ids)))
        profiles = {p.id: p for p in prof_res.scalars().all()}

    # Load custom fields for event
    cf_res = await db.execute(
        select(EventRegistrationField)
        .where(
            EventRegistrationField.event_id == event.id,
            EventRegistrationField.source == RegistrationFieldSource.CUSTOM,
        )
        .order_by(EventRegistrationField.display_order)
    )
    custom_fields = list(cf_res.scalars().all())

    # Load field responses
    fr_res = await db.execute(
        select(RegistrationFieldResponse).where(
            RegistrationFieldResponse.registration_id == reg.id
        )
    )
    field_resp_map: dict[tuple[uuid.UUID, uuid.UUID], Any] = {
        (fr.registration_id, fr.field_id): fr.value for fr in fr_res.scalars().all()
    }

    # Determine member columns
    member_cols = _get_event_member_columns_count(event, [reg])

    headers = (
        list(BASE_REGISTRATION_HEADERS_START)
        + [f"Member {i}" for i in range(1, member_cols + 1)]
        + list(BASE_REGISTRATION_HEADERS_END)
        + [f"Field: {cf.label}" for cf in custom_fields]
    )

    row_data = _build_registration_row(
        reg,
        profiles=profiles,
        member_cols_count=member_cols,
        custom_fields=custom_fields,
        field_resp_map=field_resp_map,
    )

    return reg, event, headers, row_data, spreadsheet_id


async def sync_registration_to_sheet(registration_id: uuid.UUID) -> bool:
    """
    Synchronize a single registration to its target Google Spreadsheet.
    Upserts row in real-time (updates row if exists, appends if new).
    """
    settings = get_settings()
    if not settings.GOOGLE_SHEETS_SYNC_ENABLED:
        logger.debug("google_sheets_sync_disabled")
        return False

    token = get_access_token()
    if not token:
        logger.warning(
            "google_sheets_sync_skipped: No Google Service Account configured. "
            "Set GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON or GOOGLE_SHEETS_CREDENTIALS_FILE in .env."
        )
        return False

    async with AsyncSessionLocal() as db:
        loaded = await _load_registration_row_data(db, registration_id)
        if not loaded:
            logger.info("google_sheets_sync_no_sheet_id_or_not_found registration_id=%s", registration_id)
            return False

        reg, event, headers, row_data, spreadsheet_id = loaded

    # Determine tab name:
    # If using dedicated event spreadsheet, default to event name or 'Registrations'
    # If using master spreadsheet, use event name
    is_master = (
        settings.GOOGLE_SHEETS_MASTER_SPREADSHEET_ID
        and spreadsheet_id == settings.GOOGLE_SHEETS_MASTER_SPREADSHEET_ID.strip()
    )
    sheet_title = _excel_sheet_title(event.name, fallback="Registrations") if is_master else "Registrations"

    headers_auth = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers_auth, timeout=15.0) as client:
        # 1. Ensure tab and headers exist
        target_tab = await _ensure_worksheet_and_headers(
            client, spreadsheet_id, sheet_title, headers
        )

        # 2. Fetch Column A (Registration IDs) to locate row
        col_a_url = f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{target_tab}!A:A"
        col_a_res = await client.get(col_a_url)
        col_a_data = col_a_res.json().get("values", [])

        reg_id_str = str(reg.id)
        found_row_idx: Optional[int] = None
        for idx, row in enumerate(col_a_data, start=1):
            if row and row[0].strip() == reg_id_str:
                found_row_idx = idx
                break

        if found_row_idx is not None:
            # Update existing row
            update_url = (
                f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{target_tab}!A{found_row_idx}"
                f"?valueInputOption=USER_ENTERED"
            )
            up_res = await client.put(update_url, json={"values": [row_data]})
            if up_res.status_code == 200:
                logger.info(
                    "google_sheets_sync_updated registration_id=%s row=%s spreadsheet=%s tab=%s",
                    reg_id_str,
                    found_row_idx,
                    spreadsheet_id,
                    target_tab,
                )
                return True
            else:
                logger.error("failed_to_update_sheet_row code=%s body=%s", up_res.status_code, up_res.text)
                return False
        else:
            # Append new row
            append_url = (
                f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{target_tab}!A1:append"
                f"?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
            )
            ap_res = await client.post(append_url, json={"values": [row_data]})
            if ap_res.status_code == 200:
                logger.info(
                    "google_sheets_sync_appended registration_id=%s spreadsheet=%s tab=%s",
                    reg_id_str,
                    spreadsheet_id,
                    target_tab,
                )
                return True
            else:
                logger.error("failed_to_append_sheet_row code=%s body=%s", ap_res.status_code, ap_res.text)
                return False


async def _safe_sync_registration(registration_id: uuid.UUID) -> None:
    try:
        await sync_registration_to_sheet(registration_id)
    except Exception as exc:
        logger.exception("google_sheets_sync_background_failed registration_id=%s error=%s", registration_id, exc)


def trigger_registration_sheet_sync(registration_id: Optional[uuid.UUID]) -> None:
    """Non-blocking background sync trigger for a registration."""
    if not registration_id:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_safe_sync_registration(registration_id))
    except RuntimeError:
        # No running loop (e.g. testing or synchronous context)
        pass


async def _safe_sync_team(team_id: uuid.UUID) -> None:
    try:
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(Registration.id).where(Registration.team_id == team_id)
            )
            reg_ids = res.scalars().all()
        for r_id in reg_ids:
            await sync_registration_to_sheet(r_id)
    except Exception as exc:
        logger.exception("google_sheets_sync_team_failed team_id=%s error=%s", team_id, exc)


def trigger_team_sheet_sync(team_id: Optional[uuid.UUID]) -> None:
    """Non-blocking background sync trigger for all registrations of a team."""
    if not team_id:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_safe_sync_team(team_id))
    except RuntimeError:
        pass


async def _safe_sync_profile(profile_id: uuid.UUID) -> None:
    try:
        async with AsyncSessionLocal() as db:
            # Solo registrations
            solo_q = select(Registration.id).where(Registration.profile_id == profile_id)
            solo_ids = list((await db.execute(solo_q)).scalars().all())

            # Team registrations where profile is leader or member
            team_ids_q = select(TeamMember.team_id).where(TeamMember.profile_id == profile_id)
            team_ids = list((await db.execute(team_ids_q)).scalars().all())

            team_reg_ids: list[uuid.UUID] = []
            if team_ids:
                team_q = select(Registration.id).where(Registration.team_id.in_(team_ids))
                team_reg_ids = list((await db.execute(team_q)).scalars().all())

            all_reg_ids = list(set(solo_ids + team_reg_ids))

        for r_id in all_reg_ids:
            await sync_registration_to_sheet(r_id)
    except Exception as exc:
        logger.exception("google_sheets_sync_profile_failed profile_id=%s error=%s", profile_id, exc)


def trigger_profile_sheet_sync(profile_id: Optional[uuid.UUID]) -> None:
    """Non-blocking background sync trigger when a user updates their profile details."""
    if not profile_id:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_safe_sync_profile(profile_id))
    except RuntimeError:
        pass


async def sync_event_to_sheet(db: AsyncSession, event_id: uuid.UUID) -> dict[str, Any]:
    """
    Sync all registrations of an event to its corresponding Google Sheet.
    Useful for initial setup, manual admin trigger, or bulk repair.
    """
    event_res = await db.execute(
        select(Event).options(selectinload(Event.rules)).where(Event.id == event_id)
    )
    event = event_res.scalar_one_or_none()
    if not event:
        return {"success": False, "error": "Event not found"}

    spreadsheet_id = resolve_spreadsheet_id(event)
    if not spreadsheet_id:
        return {
            "success": False,
            "error": "No Google Sheet configured for this event and no master sheet configured.",
        }

    token = get_access_token()
    if not token:
        return {
            "success": False,
            "error": "Google Service Account credentials not configured in backend.",
        }

    # Fetch all registrations for event
    regs_res = await db.execute(
        select(Registration.id)
        .where(Registration.event_id == event_id)
        .order_by(Registration.created_at)
    )
    reg_ids = list(regs_res.scalars().all())

    synced_count = 0
    errors = []
    for r_id in reg_ids:
        try:
            ok = await sync_registration_to_sheet(r_id)
            if ok:
                synced_count += 1
            else:
                errors.append(str(r_id))
        except Exception as exc:
            errors.append(f"{r_id}: {exc}")

    return {
        "success": True,
        "event_id": str(event_id),
        "event_name": event.name,
        "spreadsheet_id": spreadsheet_id,
        "total_registrations": len(reg_ids),
        "synced_count": synced_count,
        "errors": errors,
    }
