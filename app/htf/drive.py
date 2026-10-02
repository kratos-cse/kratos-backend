"""Google Drive integration service for HTF 2026 PPT submissions.

Handles authentication (Service Account or OAuth Refresh Token), folder creation,
resumable uploads, chunked streaming downloads, and orphaned file cleanup.
All synchronous googleapiclient calls run off the event loop via asyncio.to_thread.
"""
import asyncio
import io
import json
import logging
import os
from typing import AsyncGenerator, BinaryIO, Optional

import httplib2
from google_auth_httplib2 import AuthorizedHttp

from app.htf.settings import htf_settings

logger = logging.getLogger("htf.drive")

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


class GoogleDriveService:
    def __init__(self) -> None:
        pass

    def _get_credentials(self):
        auth_mode = htf_settings.GOOGLE_DRIVE_AUTH_MODE

        if auth_mode == "service_account":
            from google.oauth2.service_account import Credentials

            creds_json = htf_settings.GOOGLE_DRIVE_CREDENTIALS_JSON
            if not creds_json:
                raise RuntimeError("Google Drive service account credentials not configured")

            if os.path.exists(creds_json):
                return Credentials.from_service_account_file(creds_json, scopes=DRIVE_SCOPES)
            else:
                data = json.loads(creds_json)
                return Credentials.from_service_account_info(data, scopes=DRIVE_SCOPES)

        elif auth_mode == "oauth":
            from google.oauth2.credentials import Credentials

            refresh_token = htf_settings.GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN
            client_id = htf_settings.GOOGLE_DRIVE_OAUTH_CLIENT_ID
            client_secret = htf_settings.GOOGLE_DRIVE_OAUTH_CLIENT_SECRET
            if not (refresh_token and client_id and client_secret):
                raise RuntimeError("Google Drive OAuth refresh token credentials not configured")

            return Credentials(
                token=None,
                refresh_token=refresh_token,
                client_id=client_id,
                client_secret=client_secret,
                token_uri="https://oauth2.googleapis.com/token",
                scopes=DRIVE_SCOPES,
            )
        else:
            raise ValueError(f"Unsupported GOOGLE_DRIVE_AUTH_MODE: {auth_mode}")

    def _build_client(self):
        from googleapiclient.discovery import build

        creds = self._get_credentials()
        timeout = float(htf_settings.GOOGLE_DRIVE_TIMEOUT_SECONDS or 30.0)
        http = AuthorizedHttp(creds, http=httplib2.Http(timeout=timeout))
        return build("drive", "v3", http=http, cache_discovery=False)

    def _sync_get_or_create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        client = self._build_client()
        escaped_name = name.replace("'", "\\'")
        query_parts = [
            f"name = '{escaped_name}'",
            "mimeType = 'application/vnd.google-apps.folder'",
            "trashed = false",
        ]
        if parent_id:
            query_parts.append(f"'{parent_id}' in parents")

        query = " and ".join(query_parts)
        response = (
            client.files()
            .list(
                q=query,
                spaces="drive",
                fields="files(id, name)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files = response.get("files", [])
        if files:
            return files[0]["id"]

        folder_metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            folder_metadata["parents"] = [parent_id]

        folder = (
            client.files()
            .create(
                body=folder_metadata,
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        return folder["id"]

    def _sync_upload(self, fileobj: BinaryIO, name: str, mime_type: str, folder_id: str) -> str:
        from googleapiclient.http import MediaIoBaseUpload

        client = self._build_client()
        file_metadata = {
            "name": name,
            "parents": [folder_id],
        }
        if hasattr(fileobj, "seek"):
            fileobj.seek(0)

        media = MediaIoBaseUpload(
            fileobj,
            mimetype=mime_type,
            resumable=True,
        )
        created = (
            client.files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        return created["id"]

    def _sync_delete(self, file_id: str) -> bool:
        client = self._build_client()
        try:
            client.files().delete(fileId=file_id, supportsAllDrives=True).execute()
            return True
        except Exception as exc:
            logger.warning("Failed to delete Google Drive file %s: %s", file_id, exc)
            return False

    async def get_or_create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        return await asyncio.to_thread(self._sync_get_or_create_folder, name, parent_id)

    async def upload(self, fileobj: BinaryIO, name: str, mime_type: str, folder_id: str) -> str:
        return await asyncio.to_thread(self._sync_upload, fileobj, name, mime_type, folder_id)

    async def stream_file(self, file_id: str, chunk_size: int = 1024 * 1024) -> AsyncGenerator[bytes, None]:
        """Streams file bytes chunk-by-chunk without buffering entire body in memory."""
        from googleapiclient.http import MediaIoBaseDownload

        client = self._build_client()
        request = client.files().get_media(fileId=file_id, supportsAllDrives=True)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request, chunksize=chunk_size)
        done = False
        cursor = 0
        while not done:
            status, done = await asyncio.to_thread(downloader.next_chunk)
            fh.seek(cursor)
            chunk = fh.read()
            cursor = fh.tell()
            if chunk:
                yield chunk

    async def delete(self, file_id: str) -> bool:
        return await asyncio.to_thread(self._sync_delete, file_id)


def get_drive_service() -> GoogleDriveService:
    """FastAPI dependency provider for GoogleDriveService."""
    return GoogleDriveService()
