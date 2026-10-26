import logging

from app.core.log_sanitize import SensitiveLogFilter, sanitize_request_line, sanitize_url


def test_sanitize_url_redacts_receipt_token():
    raw = (
        "https://api.example.com/api/v1/payments/abc/receipt/pdf"
        "?receipt_token=eyJhbGciOiJIUzI1NiJ9.secret.signature"
    )
    clean = sanitize_url(raw)
    assert "eyJhbGci" not in clean
    assert "receipt_token=[REDACTED]" in clean or "receipt_token=%5BREDACTED%5D" in clean


def test_sanitize_request_line_uvicorn_style():
    line = (
        '169.254.1.1:1234 - "GET /api/v1/payments/x/receipt/pdf?receipt_token=eyJsecret HTTP/1.1" 200'
    )
    clean = sanitize_request_line(line)
    assert "eyJsecret" not in clean
    assert "[REDACTED]" in clean


def test_sensitive_log_filter_on_record():
    filt = SensitiveLogFilter()
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %s',
        args=("1.2.3.4:9", "GET", "/receipt?receipt_token=leaked", "1.1", 200),
        exc_info=None,
    )
    assert filt.filter(record) is True
    assert "leaked" not in str(record.args)


def test_bearer_redacted_in_message():
    msg = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"
    clean = sanitize_request_line(msg)
    assert "eyJhbGci" not in clean
    assert "[REDACTED]" in clean
