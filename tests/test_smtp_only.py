"""SMTP-only email delivery — Mailjet must never be used."""
import pytest
from unittest.mock import AsyncMock, patch

from app.core.config import settings
from app.services import email_service, notification_service


@pytest.mark.asyncio
async def test_send_email_uses_aiosmtplib(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "noreply@test.local")
    monkeypatch.setattr(settings, "SMTP_TLS", True)
    monkeypatch.setattr(settings, "SMTP_SSL", False)
    monkeypatch.setattr(settings, "SMTP_PORT", 587)

    send_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("app.services.email_service.aiosmtplib.send", send_mock)

    ok, err = await email_service.send_email(
        to_email="user@test.local",
        subject="Hi",
        text_body="Hello",
        html_body="<p>Hello</p>",
    )
    assert ok is True
    assert err is None
    send_mock.assert_awaited_once()
    kwargs = send_mock.await_args.kwargs
    assert kwargs["start_tls"] is True
    assert kwargs["use_tls"] is False


@pytest.mark.asyncio
async def test_implicit_tls_on_port_465(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "noreply@test.local")
    monkeypatch.setattr(settings, "SMTP_TLS", False)
    monkeypatch.setattr(settings, "SMTP_SSL", False)
    monkeypatch.setattr(settings, "SMTP_PORT", 465)

    send_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("app.services.email_service.aiosmtplib.send", send_mock)

    ok, _ = await email_service.send_email(
        to_email="user@test.local",
        subject="Hi",
        text_body="Hello",
    )
    assert ok is True
    kwargs = send_mock.await_args.kwargs
    assert kwargs["use_tls"] is True
    assert kwargs["start_tls"] is False


def test_mailjet_not_importable_from_email_service():
    import importlib

    mod = importlib.import_module("app.services.email_service")
    assert "mailjet" not in mod.__doc__.lower()
    assert not hasattr(mod, "_mailjet_client")


@pytest.mark.asyncio
async def test_deliver_notification_routes_to_smtp(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "noreply@test.local")

    send_mock = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("app.services.email_service.send_email", send_mock)

    ok, err = await notification_service._send_email("a@b.c", "Sub", "body", html_body="<p>x</p>")
    assert ok is True
    assert err is None
    send_mock.assert_awaited_once()


def test_whatsapp_url_validation():
    from app.services.email_content import sanitize_whatsapp_group_url

    assert sanitize_whatsapp_group_url("https://chat.whatsapp.com/AbCdEf") == "https://chat.whatsapp.com/AbCdEf"
    assert sanitize_whatsapp_group_url("http://chat.whatsapp.com/x") is None
    assert sanitize_whatsapp_group_url("https://evil.com/x") is None
    assert sanitize_whatsapp_group_url("") is None
