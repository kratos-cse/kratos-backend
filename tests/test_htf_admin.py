import pytest
from httpx import AsyncClient
from uuid import uuid4

@pytest.mark.asyncio
async def test_list_htf_applications_unauthorized(client: AsyncClient):
    """Ensure unauthenticated users cannot access HTF admin APIs"""
    response = await client.get("/api/v1/admin/htf/applications")
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_list_htf_applications_forbidden(client: AsyncClient, normal_user_token_headers: dict):
    """Ensure normal users cannot access HTF admin APIs"""
    response = await client.get("/api/v1/admin/htf/applications", headers=normal_user_token_headers)
    assert response.status_code == 403

@pytest.mark.asyncio
async def test_list_htf_applications_authorized(client: AsyncClient, admin_token_headers: dict):
    """Ensure admins with the correct permission can access HTF admin APIs"""
    # Note: admin_token_headers should belong to a Super Admin or an Admin with HTF_APPLICATION_READ
    response = await client.get("/api/v1/admin/htf/applications", headers=admin_token_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

@pytest.mark.asyncio
async def test_screening_requires_permission(client: AsyncClient, normal_user_token_headers: dict):
    """Ensure screening endpoint requires admin permissions"""
    app_id = uuid4()
    response = await client.post(f"/api/v1/admin/htf/applications/{app_id}/screening", headers=normal_user_token_headers)
    assert response.status_code == 403
