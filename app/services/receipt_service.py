"""Payment receipt generation (on-demand HTML/PDF; metadata only in DB)."""
import html
import io
import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

import qrcode
import qrcode.image.svg
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.branding.documents import render_document_header_html
from app.core.config import settings

logger = logging.getLogger("receipt_service")
from app.core.errors import FORBIDDEN, NOT_FOUND, RECEIPT_DATA_INCOMPLETE, AppError
from app.models.enums import PaymentStatus, RegistrationStatus, TeamMemberStatus
from app.models.event import Event
from app.models.payment import Payment
from app.models.profile import Profile
from app.models.qr_code import QRCode
from app.models.receipt import Receipt
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.models.user import User


def _receipt_number() -> str:
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = secrets.token_hex(4).upper()
    return f"KR-{date_part}-{suffix}"


def generate_qr_svg(data: str) -> str:
    """Generates a crisp inline SVG QR code."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    stream = io.BytesIO()
    img.save(stream)
    svg_bytes = stream.getvalue()
    # Decode to utf-8 string and remove XML declaration for inline HTML insertion
    svg_str = svg_bytes.decode("utf-8")
    if "<?xml" in svg_str:
        svg_str = svg_str[svg_str.find("<svg") :]
    return svg_str


def receipt_html_url(payment_id: uuid.UUID, receipt_token: str | None = None) -> str:
    base = settings.APP_PUBLIC_BASE_URL.rstrip("/")
    url = f"{base}/api/v1/payments/{payment_id}/receipt/html"
    if receipt_token:
        url = f"{url}?receipt_token={quote(receipt_token, safe='')}"
    return url


def receipt_pdf_url(payment_id: uuid.UUID, receipt_token: str | None = None) -> str:
    base = settings.APP_PUBLIC_BASE_URL.rstrip("/")
    url = f"{base}/api/v1/payments/{payment_id}/receipt/pdf"
    if receipt_token:
        url = f"{url}?receipt_token={quote(receipt_token, safe='')}"
    return url


def render_pdf_receipt(ctx: dict[str, Any]) -> bytes:
    """Render a branded PDF receipt in memory (never written to disk)."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    y = height - 56

    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.rect(0, height - 88, width, 88, fill=1, stroke=0)
    c.setFillColorRGB(0.98, 0.98, 0.99)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(72, height - 52, "KRATOS'26")
    c.setFont("Helvetica", 10)
    c.drawString(72, height - 68, "EEC · ACE · CSE")
    c.setFillColorRGB(0.1, 0.1, 0.12)

    y = height - 120
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, y, "Payment Confirmed")
    y -= 24
    c.setFont("Helvetica", 11)
    for label, value in [
        ("Receipt", ctx["receipt_number"]),
        ("Issued", ctx["issued_at_formatted"]),
        ("Event", ctx["event_name"]),
        ("Participant", ctx["payer_name"]),
        ("Amount", f"₹{ctx['amount_inr']} {ctx['currency']}"),
        ("Payment ID", ctx["payment_id"][:18] + "..."),
    ]:
        c.drawString(72, y, f"{label}: {value}")
        y -= 16

    if ctx.get("team_name"):
        c.drawString(72, y, f"Team: {ctx['team_name']}")
        y -= 16

    qr_png = io.BytesIO()
    qr_img = qrcode.make(ctx["qr_verify_url"])
    qr_img.save(qr_png, format="PNG")
    qr_png.seek(0)
    c.drawImage(ImageReader(qr_png), width - 160, 96, width=96, height=96, mask="auto")
    c.setFont("Helvetica", 9)
    c.drawString(width - 160, 84, "Event Pass QR")

    c.setFont("Helvetica", 9)
    c.drawString(72, 72, "KRATOS'26 Organizing Committee · kratos.cse@gmail.com")
    c.showPage()
    c.save()
    return buffer.getvalue()


async def get_receipt_data_context(db: AsyncSession, payment_id: uuid.UUID) -> dict[str, Any]:
    """Gathers complete relational data needed for rendering a rich receipt & pass.

    Fails closed when required persisted data is missing — no placeholder fabrication.
    """
    p_result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = p_result.scalar_one_or_none()
    if not payment:
        raise AppError(NOT_FOUND, f"Payment {payment_id} not found", status_code=404)

    if payment.status != PaymentStatus.PAID:
        raise AppError(
            FORBIDDEN,
            "Receipt data is only available for paid payments",
            status_code=403,
        )

    if not payment.payer_profile_id:
        raise AppError(
            RECEIPT_DATA_INCOMPLETE,
            "Payment has no payer profile",
            status_code=422,
        )

    prof_res = await db.execute(
        select(Profile).options(selectinload(Profile.user)).where(Profile.id == payment.payer_profile_id)
    )
    prof = prof_res.scalar_one_or_none()
    if not prof or not prof.full_name:
        raise AppError(
            RECEIPT_DATA_INCOMPLETE,
            "Payer profile data is incomplete",
            status_code=422,
        )

    payer_name = prof.full_name
    payer_phone = prof.phone or ""
    payer_college = prof.college_name or ""
    payer_email = prof.contact_email or (prof.user.email if prof.user else "")

    reg_result = await db.execute(
        select(Registration)
        .options(
            selectinload(Registration.event),
            selectinload(Registration.team).selectinload(Team.members).selectinload(TeamMember.profile),
        )
        .where(Registration.payment_id == payment_id)
    )
    registration = reg_result.scalar_one_or_none()
    if registration is None:
        raise AppError(
            RECEIPT_DATA_INCOMPLETE,
            "No registration linked to this payment",
            status_code=422,
        )

    if registration.status != RegistrationStatus.CONFIRMED:
        raise AppError(
            RECEIPT_DATA_INCOMPLETE,
            "Registration is not confirmed",
            status_code=422,
        )

    if registration.event is None:
        raise AppError(
            RECEIPT_DATA_INCOMPLETE,
            "Registration event data is missing",
            status_code=422,
        )

    event = registration.event
    event_name = event.name
    event_category = event.category.value if hasattr(event.category, "value") else str(event.category)
    event_venue = event.venue or ""
    if event.slot:
        event_slot = event.slot
    elif event.starts_at:
        event_slot = event.starts_at.strftime("%B %d, %Y %I:%M %p")
    else:
        event_slot = ""
    whatsapp_link = event.whatsapp_group_link

    team_name = None
    team_members: list[dict[str, str]] = []
    if registration.team:
        team_name = registration.team.name
        for m in registration.team.members:
            if m.status not in (TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED):
                if m.profile:
                    m_name = m.profile.full_name
                elif m.full_name:
                    m_name = m.full_name
                else:
                    raise AppError(
                        RECEIPT_DATA_INCOMPLETE,
                        "Team member name is missing from roster data",
                        status_code=422,
                    )
                team_members.append({
                    "name": m_name,
                    "role": m.role.value if hasattr(m.role, "value") else str(m.role),
                    "status": m.status.value if hasattr(m.status, "value") else str(m.status),
                })

    qr_token: str | None = None
    if registration.profile_id:
        qr_res = await db.execute(
            select(QRCode).where(QRCode.registration_id == registration.id, QRCode.is_active.is_(True))
        )
        qr = qr_res.scalar_one_or_none()
        if qr:
            qr_token = qr.token
    elif registration.team_id:
        leader_member = next(
            (m for m in registration.team.members if m.profile_id == payment.payer_profile_id),
            None,
        )
        if leader_member:
            qr_res = await db.execute(
                select(QRCode).where(QRCode.team_member_id == leader_member.id, QRCode.is_active.is_(True))
            )
            qr = qr_res.scalar_one_or_none()
            if qr:
                qr_token = qr.token

    if not qr_token:
        raise AppError(
            RECEIPT_DATA_INCOMPLETE,
            "Event pass QR code is missing for this registration",
            status_code=422,
        )

    receipt_res = await db.execute(select(Receipt).where(Receipt.payment_id == payment_id))
    receipt = receipt_res.scalar_one_or_none()
    if receipt is None or not receipt.receipt_number:
        raise AppError(
            RECEIPT_DATA_INCOMPLETE,
            "Receipt metadata has not been issued for this payment",
            status_code=422,
        )

    receipt_number = receipt.receipt_number
    issued_at = receipt.issued_at or payment.created_at
    if issued_at is None:
        raise AppError(
            RECEIPT_DATA_INCOMPLETE,
            "Receipt issue timestamp is missing",
            status_code=422,
        )

    # Generate QR Code SVG
    qr_verify_url = f"{settings.APP_PUBLIC_BASE_URL.rstrip('/')}/checkin?token={qr_token}"
    qr_svg = generate_qr_svg(qr_verify_url)

    amount_inr = f"{payment.amount_paise / 100:.2f}"

    return {
        "receipt_number": receipt_number,
        "payment_id": str(payment.id),
        "razorpay_order_id": payment.razorpay_order_id or "N/A",
        "razorpay_payment_id": payment.razorpay_payment_id or "N/A",
        "payment_type": payment.payment_type.value if hasattr(payment.payment_type, "value") else str(payment.payment_type),
        "amount_inr": amount_inr,
        "currency": payment.currency or "INR",
        "status": payment.status.value if hasattr(payment.status, "value") else str(payment.status),
        "issued_at_formatted": issued_at.strftime("%d %b %Y, %I:%M %p UTC"),
        "payer_name": payer_name,
        "payer_email": payer_email,
        "payer_phone": payer_phone,
        "payer_college": payer_college,
        "event_name": event_name,
        "event_category": event_category,
        "event_venue": event_venue,
        "event_slot": event_slot,
        "whatsapp_link": whatsapp_link,
        "team_name": team_name,
        "team_members": team_members,
        "qr_token": qr_token,
        "qr_svg": qr_svg,
        "qr_verify_url": qr_verify_url,
    }


def render_html_receipt(ctx: dict[str, Any]) -> str:
    """Renders a modern, responsive, beautifully styled HTML receipt with embedded QR code."""
    team_members_html = ""
    if ctx["team_members"]:
        rows = "".join(
            f"""
            <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 8px 12px; color: #1e293b; font-weight: 500;">{html.escape(m['name'])}</td>
                <td style="padding: 8px 12px; color: #64748b; font-size: 13px;">{html.escape(m['role'])}</td>
                <td style="padding: 8px 12px; text-align: right;">
                    <span style="background: #ecfdf5; color: #059669; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 9999px;">
                        {html.escape(m['status'])}
                    </span>
                </td>
            </tr>
            """
            for m in ctx["team_members"]
        )
        team_members_html = f"""
        <div style="margin-top: 20px;">
            <h4 style="margin: 0 0 10px 0; font-size: 13px; font-weight: 600; color: #475569; text-transform: uppercase; letter-spacing: 0.05em;">
                Team Roster ({html.escape(ctx['team_name'] or 'Team')})
            </h4>
            <table style="width: 100%; border-collapse: collapse; background: #f8fafc; border-radius: 8px; overflow: hidden; border: 1px solid #e2e8f0; font-size: 13px;">
                <thead>
                    <tr style="background: #f1f5f9; color: #475569; text-align: left; font-size: 11px; text-transform: uppercase;">
                        <th style="padding: 8px 12px;">Member</th>
                        <th style="padding: 8px 12px;">Role</th>
                        <th style="padding: 8px 12px; text-align: right;">Status</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
            </table>
        </div>
        """

    whatsapp_btn_html = ""
    if ctx.get("whatsapp_link"):
        whatsapp_btn_html = f"""
        <div style="margin-top: 16px; text-align: center;">
            <a href="{html.escape(ctx['whatsapp_link'])}" target="_blank" rel="noopener" style="display: inline-flex; align-items: center; gap: 8px; background: #25D366; color: #ffffff; text-decoration: none; padding: 10px 18px; border-radius: 8px; font-size: 13px; font-weight: 600;">
                <svg width="16" height="16" fill="currentColor" viewBox="0 0 24 24"><path d="M12.031 6.172c-3.181 0-5.767 2.586-5.768 5.766-.001 1.298.38 2.27 1.019 3.287l-.711 2.598 2.664-.699c.971.53 1.954.814 2.796.814 3.18 0 5.767-2.587 5.768-5.766.001-3.181-2.586-5.77-5.768-5.77zm0 10.378c-.767 0-1.611-.225-2.273-.62l-.162-.097-1.579.414.421-1.54-.106-.169c-.435-.694-.666-1.428-.665-2.599.001-2.54 2.068-4.606 4.609-4.606 2.542 0 4.608 2.067 4.608 4.607 0 2.541-2.066 4.61-4.608 4.61z"/></svg>
                Join Official WhatsApp Group
            </a>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KRATOS'26 Receipt - {html.escape(ctx['receipt_number'])}</title>
    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: #0f172a;
            color: #1e293b;
            padding: 40px 16px;
            display: flex;
            justify-content: center;
            align-items: flex-start;
            min-height: 100vh;
        }}
        .receipt-container {{
            width: 100%;
            max-width: 820px;
            background: #ffffff;
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.35);
            border: 1px solid rgba(255, 255, 255, 0.1);
        }}
        .action-bar {{
            background: #1e293b;
            padding: 12px 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #334155;
        }}
        .btn {{
            cursor: pointer;
            border: none;
            padding: 8px 16px;
            font-size: 13px;
            font-weight: 600;
            border-radius: 6px;
            transition: all 0.2s ease;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}
        .btn-primary {{
            background: #6366f1;
            color: #ffffff;
        }}
        .btn-primary:hover {{
            background: #4f46e5;
        }}
        .btn-secondary {{
            background: #334155;
            color: #f8fafc;
        }}
        .btn-secondary:hover {{
            background: #475569;
        }}
        .header {{
            background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 50%, #312e81 100%);
            padding: 32px 36px;
            color: #ffffff;
            position: relative;
        }}
        .header-top {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 20px;
        }}
        .logo-title {{
            font-size: 28px;
            font-weight: 800;
            letter-spacing: -0.03em;
            background: linear-gradient(135deg, #ffffff 0%, #a5b4fc 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .sub-title {{
            font-size: 12px;
            color: #94a3b8;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            font-weight: 600;
            margin-top: 4px;
        }}
        .badge-verified {{
            background: rgba(16, 185, 129, 0.15);
            border: 1px solid #10b981;
            color: #34d399;
            padding: 6px 14px;
            border-radius: 9999px;
            font-size: 12px;
            font-weight: 700;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        .meta-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 16px;
            border-top: 1px solid rgba(255, 255, 255, 0.1);
            padding-top: 16px;
        }}
        .meta-item .label {{
            font-size: 11px;
            color: #94a3b8;
            text-transform: uppercase;
            font-weight: 600;
            letter-spacing: 0.05em;
        }}
        .meta-item .value {{
            font-size: 14px;
            color: #f8fafc;
            font-weight: 600;
            margin-top: 2px;
        }}
        .content-body {{
            padding: 32px 36px;
        }}
        .ticket-strip {{
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-left: 4px solid #6366f1;
            padding: 18px 20px;
            border-radius: 8px;
            margin-bottom: 28px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
        }}
        .event-heading {{
            font-size: 20px;
            font-weight: 700;
            color: #0f172a;
        }}
        .event-meta {{
            font-size: 13px;
            color: #64748b;
            margin-top: 4px;
            display: flex;
            gap: 16px;
            flex-wrap: wrap;
        }}
        .pass-type-badge {{
            background: #e0e7ff;
            color: #4338ca;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 700;
        }}
        .grid-2col {{
            display: grid;
            grid-template-columns: 1.3fr 1fr;
            gap: 28px;
            margin-bottom: 28px;
        }}
        @media (max-width: 680px) {{
            .grid-2col {{
                grid-template-columns: 1fr;
            }}
        }}
        .section-title {{
            font-size: 12px;
            font-weight: 700;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 12px;
        }}
        .info-card {{
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 12px;
            padding: 20px;
        }}
        .info-row {{
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px dashed #f1f5f9;
            font-size: 13px;
        }}
        .info-row:last-child {{
            border-bottom: none;
        }}
        .info-row .k {{
            color: #64748b;
        }}
        .info-row .v {{
            color: #0f172a;
            font-weight: 600;
            text-align: right;
        }}
        .qr-card {{
            background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
            border: 2px dashed #cbd5e1;
            border-radius: 12px;
            padding: 20px;
            text-align: center;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }}
        .qr-svg-wrapper {{
            width: 170px;
            height: 170px;
            background: #ffffff;
            padding: 8px;
            border-radius: 8px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
            border: 1px solid #e2e8f0;
            margin-bottom: 12px;
        }}
        .qr-svg-wrapper svg {{
            width: 100%;
            height: 100%;
        }}
        .qr-token-text {{
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 11px;
            color: #475569;
            background: #e2e8f0;
            padding: 4px 8px;
            border-radius: 4px;
            margin-bottom: 6px;
            word-break: break-all;
        }}
        .qr-help {{
            font-size: 11px;
            color: #94a3b8;
            line-height: 1.4;
        }}
        .table-payment {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 12px;
            font-size: 13px;
        }}
        .table-payment th {{
            background: #f8fafc;
            color: #475569;
            font-weight: 600;
            padding: 10px 14px;
            text-align: left;
            border-bottom: 2px solid #e2e8f0;
            font-size: 11px;
            text-transform: uppercase;
        }}
        .table-payment td {{
            padding: 12px 14px;
            border-bottom: 1px solid #f1f5f9;
        }}
        .total-box {{
            background: #f8fafc;
            border-radius: 8px;
            padding: 16px 20px;
            margin-top: 20px;
            border: 1px solid #e2e8f0;
        }}
        .total-row {{
            display: flex;
            justify-content: space-between;
            font-size: 14px;
            padding: 4px 0;
            color: #475569;
        }}
        .total-grand {{
            font-size: 20px;
            font-weight: 800;
            color: #0f172a;
            border-top: 2px solid #e2e8f0;
            padding-top: 10px;
            margin-top: 8px;
        }}
        .footer {{
            background: #f8fafc;
            border-top: 1px solid #e2e8f0;
            padding: 24px 36px;
            text-align: center;
            font-size: 12px;
            color: #64748b;
            line-height: 1.5;
        }}
        @media print {{
            body {{
                background: #ffffff;
                padding: 0;
            }}
            .receipt-container {{
                box-shadow: none;
                border: none;
                max-width: 100%;
            }}
            .action-bar {{
                display: none !important;
            }}
            .header {{
                background: #0f172a !important;
                -webkit-print-color-adjust: exact;
                print-color-adjust: exact;
            }}
            .badge-verified {{
                border-color: #10b981 !important;
                color: #059669 !important;
            }}
            .ticket-strip, .info-card, .total-box, .qr-card {{
                -webkit-print-color-adjust: exact;
                print-color-adjust: exact;
            }}
        }}
    </style>
</head>
<body>
    <div class="receipt-container">
        <div class="action-bar">
            <span style="color: #94a3b8; font-size: 12px; font-weight: 500;">
                KRATOS'26 Official Digital Receipt
            </span>
            <div style="display: flex; gap: 10px;">
                <button onclick="window.print()" class="btn btn-primary">
                    <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M6 9V2h12v7M6 18H4a2 2 0 01-2-2v-5a2 2 0 012-2h16a2 2 0 012 2v5a2 2 0 01-2 2h-2m-10 0v4h12v-4M8 14h.01"/></svg>
                    Print / Save PDF
                </button>
            </div>
        </div>

        <div class="header">
            <div class="header-top">
                <div>
                    {render_document_header_html()}
                    <div class="sub-title">National Level Technical Symposium</div>
                </div>
                <div class="badge-verified">
                    <svg width="14" height="14" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>
                    Payment Confirmed
                </div>
            </div>
            <div class="meta-grid">
                <div class="meta-item">
                    <div class="label">Receipt Number</div>
                    <div class="value">{html.escape(ctx['receipt_number'])}</div>
                </div>
                <div class="meta-item">
                    <div class="label">Issued Date</div>
                    <div class="value">{html.escape(ctx['issued_at_formatted'])}</div>
                </div>
                <div class="meta-item">
                    <div class="label">Payment ID</div>
                    <div class="value" style="font-family: monospace; font-size: 12px;">{html.escape(ctx['payment_id'][:12])}...</div>
                </div>
                <div class="meta-item">
                    <div class="label">Amount Paid</div>
                    <div class="value" style="color: #34d399; font-size: 16px;">₹{html.escape(ctx['amount_inr'])}</div>
                </div>
            </div>
        </div>

        <div class="content-body">
            <div class="ticket-strip">
                <div>
                    <div class="event-heading">{html.escape(ctx['event_name'])}</div>
                    <div class="event-meta">
                        <span>🏷️ {html.escape(ctx['event_category'])}</span>
                        <span>📍 {html.escape(ctx['event_venue'])}</span>
                        <span>⏰ {html.escape(ctx['event_slot'])}</span>
                    </div>
                </div>
                <div>
                    <span class="pass-type-badge">
                        {html.escape(ctx['payment_type'].replace('_', ' '))}
                    </span>
                </div>
            </div>

            <div class="grid-2col">
                <div>
                    <div class="section-title">Participant & Pass Details</div>
                    <div class="info-card">
                        <div class="info-row">
                            <span class="k">Full Name</span>
                            <span class="v">{html.escape(ctx['payer_name'])}</span>
                        </div>
                        <div class="info-row">
                            <span class="k">Email Address</span>
                            <span class="v">{html.escape(ctx['payer_email'])}</span>
                        </div>
                        <div class="info-row">
                            <span class="k">Phone</span>
                            <span class="v">{html.escape(ctx['payer_phone'])}</span>
                        </div>
                        <div class="info-row">
                            <span class="k">College / Institute</span>
                            <span class="v">{html.escape(ctx['payer_college'])}</span>
                        </div>
                        <div class="info-row">
                            <span class="k">Payment Status</span>
                            <span class="v" style="color: #059669;">{html.escape(ctx['status'])}</span>
                        </div>
                    </div>

                    {team_members_html}
                </div>

                <div>
                    <div class="section-title">Event Check-in QR Pass</div>
                    <div class="qr-card">
                        <div class="qr-svg-wrapper">
                            {ctx['qr_svg']}
                        </div>
                        <div class="qr-token-text">{html.escape(ctx['qr_token'])}</div>
                        <div class="qr-help">
                            Present this QR pass at the entrance registration desk for instant scanning and check-in.
                        </div>
                    </div>
                    {whatsapp_btn_html}
                </div>
            </div>

            <div class="section-title">Payment Breakdown</div>
            <div class="info-card" style="padding: 0; overflow: hidden;">
                <table class="table-payment">
                    <thead>
                        <tr>
                            <th>Item Description</th>
                            <th>Gateway / Ref</th>
                            <th style="text-align: right;">Amount</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td>
                                <strong>{html.escape(ctx['event_name'])}</strong>
                                <div style="font-size: 11px; color: #64748b;">Registration Fee ({html.escape(ctx['payment_type'].replace('_', ' '))})</div>
                            </td>
                            <td style="font-family: monospace; font-size: 11px; color: #64748b;">
                                Razorpay Ref: {html.escape(ctx['razorpay_payment_id'])}
                            </td>
                            <td style="text-align: right; font-weight: 600; color: #0f172a;">
                                ₹{html.escape(ctx['amount_inr'])}
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <div class="total-box">
                <div class="total-row">
                    <span>Subtotal</span>
                    <span>₹{html.escape(ctx['amount_inr'])}</span>
                </div>
                <div class="total-row">
                    <span>Platform / Convenience Fee</span>
                    <span>₹0.00</span>
                </div>
                <div class="total-row">
                    <span>Taxes & GST (Included)</span>
                    <span>₹0.00</span>
                </div>
                <div class="total-row total-grand">
                    <span>Total Amount Paid</span>
                    <span>₹{html.escape(ctx['amount_inr'])} {html.escape(ctx['currency'])}</span>
                </div>
            </div>
        </div>

        <div class="footer">
            <p><strong>KRATOS &apos;26 Organizing Committee</strong> &bull; Dept. of Computer Science &amp; Engineering</p>
            <p style="margin-top: 4px;">This is an authorized, computer-generated receipt &amp; event pass. Verified cryptographically.</p>
            <p style="margin-top: 4px; font-size: 11px; color: #94a3b8;">For questions or assistance, contact support at <strong>kratos.cse@gmail.com</strong></p>
        </div>
    </div>
</body>
</html>
"""


async def ensure_receipt(db: AsyncSession, payment_id: uuid.UUID) -> Receipt:
    """Ensure receipt metadata exists for a PAID payment (HTML/PDF rendered on demand).

    Receipt rows store receipt_number and API URLs only — no generated files are persisted.
    """
    existing = await db.execute(select(Receipt).where(Receipt.payment_id == payment_id))
    receipt = existing.scalar_one_or_none()
    if receipt is not None:
        return receipt

    payment_result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = payment_result.scalar_one_or_none()
    if payment is None:
        raise AppError(NOT_FOUND, "Payment not found", status_code=404)

    if payment.status != PaymentStatus.PAID:
        raise AppError(
            FORBIDDEN,
            "Receipts are only issued for paid payments",
            status_code=403,
        )

    receipt_number = _receipt_number()
    stmt = (
        insert(Receipt)
        .values(
            payment_id=payment_id,
            receipt_number=receipt_number,
            pdf_url=receipt_pdf_url(payment_id),
        )
        .on_conflict_do_nothing(index_elements=["payment_id"])
        .returning(Receipt)
    )
    result = await db.execute(stmt)
    receipt = result.scalar_one_or_none()
    if receipt is not None:
        logger.info("receipt_issued payment_id=%s receipt_number=%s", payment_id, receipt.receipt_number)
        await db.flush()
        return receipt

    # Concurrent request won the insert race — fetch the existing row.
    existing_after = await db.execute(select(Receipt).where(Receipt.payment_id == payment_id))
    receipt = existing_after.scalar_one_or_none()
    if receipt is None:
        raise AppError(NOT_FOUND, "Receipt could not be created", status_code=500)
    return receipt
