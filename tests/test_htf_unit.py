import io
import json
import zipfile
import pytest

from app.core.errors import AppError
from app.htf.errors import INVALID_FILE_TYPE
from app.htf.schemas.payment import PaymentOrderOut, PaymentVerifyRequest
from app.htf.schemas.screening import ScreeningDecisionRequest
from app.htf.services import payment_service
from app.htf.services.submission_service import (
    _sanitize_filename,
    _validate_presentation_bytes,
)
from tests.test_htf_submission import VALID_PPT_BYTES, VALID_PPTX_BYTES


def test_sanitize_filename():
    assert _sanitize_filename("../../etc/passwd/hack.pptx") == "hack.pptx"
    assert _sanitize_filename("my presentation (v1) [final].pptx") == "my_presentation__v1___final_.pptx"
    assert _sanitize_filename("") == "presentation.pptx"


def test_validate_presentation_bytes_pptx():
    # Valid PPTX passes
    _validate_presentation_bytes(VALID_PPTX_BYTES, ".pptx")

    # Corrupt / empty zip fails
    with pytest.raises(AppError) as exc1:
        _validate_presentation_bytes(b"PK\x03\x04corrupt", ".pptx")
    assert exc1.value.code == INVALID_FILE_TYPE

    # Zip without ppt/presentation.xml fails
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "<w:document/>")
    with pytest.raises(AppError) as exc2:
        _validate_presentation_bytes(buf.getvalue(), ".pptx")
    assert exc2.value.code == INVALID_FILE_TYPE


def test_validate_presentation_bytes_ppt():
    # Valid OLE2 header passes
    _validate_presentation_bytes(VALID_PPT_BYTES, ".ppt")

    # Non-OLE2 header fails
    with pytest.raises(AppError) as exc:
        _validate_presentation_bytes(b"NOT_OLE2_FILE_CONTENT", ".ppt")
    assert exc.value.code == INVALID_FILE_TYPE


def test_validate_unsupported_extension():
    with pytest.raises(AppError) as exc:
        _validate_presentation_bytes(b"dummy", ".pdf")
    assert exc.value.code == INVALID_FILE_TYPE


def test_screening_request_schema():
    req = ScreeningDecisionRequest(result="SHORTLISTED", notes="Good work")
    assert req.result == "SHORTLISTED"

    with pytest.raises(ValueError):
        ScreeningDecisionRequest(result="MAYBE")  # not allowed


def test_payment_verify_request_schema():
    req = PaymentVerifyRequest(
        razorpay_order_id="order_1",
        razorpay_payment_id="pay_1",
        razorpay_signature="sig_1",
    )
    assert req.razorpay_order_id == "order_1"

    with pytest.raises(ValueError):
        PaymentVerifyRequest(razorpay_order_id="", razorpay_payment_id="pay_1", razorpay_signature="sig_1")


@pytest.mark.asyncio
async def test_verify_payment_fails_closed_empty_secret(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "")

    from app.models.profile import Profile
    mock_profile = Profile(id="00000000-0000-0000-0000-000000000001")

    with pytest.raises(AppError) as exc:
        await payment_service.verify_payment(
            db=None,
            razorpay_order_id="order_1",
            razorpay_payment_id="pay_1",
            razorpay_signature="sig_1",
            profile=mock_profile,
        )
    assert exc.value.status_code == 503
    assert exc.value.code == "PAYMENT_GATEWAY_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_webhook_fails_closed_empty_secret(monkeypatch):
    from app.htf.settings import htf_settings
    monkeypatch.setattr(htf_settings, "HTF_RAZORPAY_WEBHOOK_SECRET", "")

    with pytest.raises(AppError) as exc:
        await payment_service.handle_webhook(
            db=None,
            raw_body=b'{"test": 1}',
            signature="sig_1",
        )
    assert exc.value.status_code == 503
    assert exc.value.code == "WEBHOOK_SECRET_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_webhook_fails_closed_empty_signature(monkeypatch):
    from app.htf.settings import htf_settings
    monkeypatch.setattr(htf_settings, "HTF_RAZORPAY_WEBHOOK_SECRET", "secret_123")

    with pytest.raises(AppError) as exc:
        await payment_service.handle_webhook(
            db=None,
            raw_body=b'{"test": 1}',
            signature="",
        )
    assert exc.value.status_code == 400
    assert exc.value.code == "MISSING_SIGNATURE"


def test_htf_routes_registered_in_fastapi():
    from app.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    schema = app.openapi()
    paths = schema.get("paths", {})

    expected_endpoints = [
        "/api/v1/htf/applications/{application_id}/submission",
        "/api/v1/admin/htf/submissions/{submission_id}/download",
        "/api/v1/admin/htf/applications/{application_id}/screening",
        "/api/v1/htf/applications/{application_id}/payment/order",
        "/api/v1/htf/payments/verify",
        "/api/v1/htf/payments/webhook",
        "/api/v1/htf/applications/{application_id}/payment/sync",
    ]

    for ep in expected_endpoints:
        assert ep in paths, f"Expected endpoint {ep} not found in OpenAPI paths"
