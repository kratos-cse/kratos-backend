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

from app.branding.documents import BRAND_IMAGE_URLS
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


def generate_qr_png_bytes(data: str) -> bytes:
    """PNG bytes for email inline embedding (SVG is poorly supported in mail clients)."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    stream = io.BytesIO()
    img.save(stream, format="PNG")
    return stream.getvalue()


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


def _receipt_participant_count(ctx: dict[str, Any]) -> int:
    members = ctx.get("team_members") or []
    return len(members) if members else 1


def _default_venue(ctx: dict[str, Any]) -> str:
    venue = (ctx.get("event_venue") or "").strip()
    if venue:
        return venue
    return "Easwari Engineering College, Chennai, Tamil Nadu"


def render_pdf_receipt(ctx: dict[str, Any]) -> bytes:
    """Render a KRATOS'26 registration receipt card as PDF (in memory only)."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    margin = 54
    card_w = width - margin * 2
    y = height - margin

    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(width / 2, y, "Registration Confirmed!")
    y -= 22
    c.setFont("Helvetica", 10)
    c.setFillColorRGB(0.71, 0.76, 0.84)
    c.drawCentredString(width / 2, y, "Your registration for KRATOS'26 has been successfully processed")
    y -= 28

    card_top = y
    card_h = 420
    c.setFillColorRGB(1, 1, 1)
    c.roundRect(margin, card_top - card_h, card_w, card_h, 10, fill=1, stroke=0)

    ty = card_top - 28
    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width / 2, ty, "KRATOS'26")
    ty -= 14
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(0.45, 0.5, 0.58)
    c.drawCentredString(width / 2, ty, "Technical Symposium")
    ty -= 16
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(width / 2, ty, "REGISTRATION RECEIPT")

    ty -= 22
    c.setFillColorRGB(0.73, 0.11, 0.11)
    c.rect(margin + 12, ty - 34, card_w - 24, 34, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(width / 2, ty - 12, "VENUE")
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(width / 2, ty - 26, _default_venue(ctx)[:60])

    ty -= 52
    c.setFillColorRGB(0.1, 0.1, 0.12)
    c.setFont("Helvetica", 10)
    details = [
        ("Payment ID", ctx.get("razorpay_payment_id") or str(ctx.get("payment_id", ""))[:20]),
        ("Registration Date", ctx["issued_at_formatted"]),
        ("Status", "Confirmed"),
        ("Receipt", ctx["receipt_number"]),
    ]
    for label, value in details:
        c.drawString(margin + 24, ty, f"{label}:")
        c.setFont("Helvetica-Bold", 10)
        c.drawString(margin + 140, ty, str(value)[:48])
        c.setFont("Helvetica", 10)
        ty -= 16

    ty -= 6
    c.setFont("Helvetica-Bold", 9)
    c.drawString(margin + 24, ty, "EVENTS REGISTERED")
    ty -= 14
    team_label = ctx.get("team_name") or "Individual"
    count = _receipt_participant_count(ctx)
    c.setFont("Helvetica", 9)
    c.drawString(margin + 24, ty, f"{ctx['event_name'][:40]}  |  Team: {team_label[:20]}  |  {count} participant(s)")
    ty -= 14
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin + 24, ty, f"TOTAL AMOUNT: ₹{ctx['amount_inr']} {ctx['currency']}")

    qr_png = io.BytesIO()
    qr_img = qrcode.make(ctx["qr_verify_url"])
    qr_img.save(qr_png, format="PNG")
    qr_png.seek(0)
    c.drawImage(ImageReader(qr_png), width - margin - 100, card_top - card_h + 24, width=88, height=88, mask="auto")
    c.setFont("Helvetica", 8)
    c.drawString(width - margin - 100, card_top - card_h + 12, "Entry QR (payer pass)")

    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.45, 0.5, 0.58)
    c.drawCentredString(
        width / 2,
        card_top - card_h + 16,
        "Thank you for registering for KRATOS'26 · kratos.cse@gmail.com",
    )

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
    """KRATOS'26 registration receipt card — hierarchy aligned with legacy Kratos receipt."""
    urls = BRAND_IMAGE_URLS
    venue = html.escape(_default_venue(ctx))
    participant_count = _receipt_participant_count(ctx)
    team_label = html.escape(ctx.get("team_name") or "Individual")
    reg_type = html.escape(ctx["payment_type"].replace("_", " "))
    generated_at = datetime.now(timezone.utc).strftime("%d %b %Y, %I:%M %p UTC")

    roster_rows = ""
    for member in ctx.get("team_members") or []:
        roster_rows += (
            "<tr>"
            f'<td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;">{html.escape(member["name"])}</td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;color:#64748b;">{html.escape(member["role"])}</td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;text-align:right;">'
            f'<span style="background:#ecfdf5;color:#059669;font-size:11px;font-weight:600;padding:2px 8px;border-radius:9999px;">'
            f'{html.escape(member["status"])}</span></td></tr>'
        )
    roster_block = ""
    if roster_rows:
        roster_block = f"""
        <div style="margin-top:20px;">
          <p style="margin:0 0 8px;font-size:11px;font-weight:700;letter-spacing:0.1em;color:#64748b;text-transform:uppercase;">
            Team roster</p>
          <table style="width:100%;border-collapse:collapse;font-size:13px;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
            <thead><tr style="background:#f8fafc;color:#475569;font-size:11px;text-transform:uppercase;">
              <th style="padding:8px 12px;text-align:left;">Member</th>
              <th style="padding:8px 12px;text-align:left;">Role</th>
              <th style="padding:8px 12px;text-align:right;">Status</th>
            </tr></thead>
            <tbody>{roster_rows}</tbody>
          </table>
        </div>"""

    whatsapp_btn = ""
    if ctx.get("whatsapp_link"):
        whatsapp_btn = (
            f'<p style="margin:16px 0 0;text-align:center;">'
            f'<a href="{html.escape(ctx["whatsapp_link"])}" style="display:inline-block;background:#25D366;color:#fff;'
            f'text-decoration:none;padding:10px 18px;border-radius:8px;font-size:13px;font-weight:600;">'
            f"Join event WhatsApp group</a></p>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>KRATOS'26 Receipt — {html.escape(ctx['receipt_number'])}</title>
  <style>
    body {{ margin:0; font-family:Segoe UI,Helvetica,Arial,sans-serif; background:#0f172a; color:#0f172a; }}
    .wrap {{ max-width:680px; margin:0 auto; padding:32px 16px 48px; }}
    .hero {{ text-align:center; color:#f8fafc; margin-bottom:28px; }}
    .hero h1 {{ margin:0 0 8px; font-size:28px; font-weight:800; }}
    .hero p {{ margin:0; color:#94a3b8; font-size:15px; }}
    .card {{ background:#fff; border-radius:12px; overflow:hidden; box-shadow:0 25px 50px -12px rgba(0,0,0,.45); }}
    .toolbar {{ background:#1e293b; padding:10px 16px; display:flex; justify-content:space-between; align-items:center; }}
    .toolbar span {{ color:#94a3b8; font-size:12px; }}
    .btn-print {{ cursor:pointer; border:none; background:#b91c1c; color:#fff; font-weight:600; font-size:13px; padding:8px 14px; border-radius:6px; }}
    .brand {{ text-align:center; padding:28px 24px 20px; border-bottom:1px solid #e2e8f0; }}
    .brand-logos {{ display:flex; align-items:center; justify-content:center; gap:12px; margin-bottom:12px; }}
    .brand-logos img.lion {{ width:48px; height:48px; border-radius:8px; }}
    .brand-logos img.wordmark {{ height:32px; width:auto; }}
    .symposium {{ font-size:12px; color:#64748b; letter-spacing:.12em; text-transform:uppercase; margin-top:4px; }}
    .receipt-label {{ margin-top:14px; font-size:11px; font-weight:700; letter-spacing:.18em; color:#b91c1c; }}
    .venue {{ background:linear-gradient(135deg,#7f1d1d 0%,#b91c1c 50%,#991b1b 100%); color:#fff; text-align:center; padding:18px 20px; }}
    .venue .tag {{ font-size:10px; letter-spacing:.2em; opacity:.85; margin-bottom:6px; }}
    .venue .name {{ font-size:15px; font-weight:700; }}
    .venue .sub {{ font-size:12px; opacity:.9; margin-top:4px; }}
    .body {{ padding:24px 28px 28px; }}
    .meta {{ display:grid; grid-template-columns:1fr 1fr; gap:12px 20px; margin-bottom:24px; }}
    .meta .k {{ font-size:11px; color:#64748b; text-transform:uppercase; letter-spacing:.06em; }}
    .meta .v {{ font-size:14px; font-weight:600; color:#0f172a; margin-top:2px; }}
    .section-h {{ font-size:11px; font-weight:700; letter-spacing:.12em; color:#64748b; text-transform:uppercase; margin:0 0 10px; }}
    .events-table {{ width:100%; border-collapse:collapse; font-size:13px; border:1px solid #e2e8f0; border-radius:8px; overflow:hidden; }}
    .events-table th {{ background:#f8fafc; color:#475569; font-size:10px; text-transform:uppercase; letter-spacing:.06em; padding:10px 12px; text-align:left; }}
    .events-table td {{ padding:12px; border-top:1px solid #f1f5f9; vertical-align:top; }}
    .total {{ margin-top:20px; background:#0f172a; color:#f8fafc; border-radius:8px; padding:16px 20px; display:flex; justify-content:space-between; align-items:center; }}
    .total span:first-child {{ font-size:12px; letter-spacing:.1em; text-transform:uppercase; opacity:.8; }}
    .total span:last-child {{ font-size:22px; font-weight:800; }}
    .pass-grid {{ display:grid; grid-template-columns:1fr 180px; gap:20px; margin-top:24px; align-items:start; }}
    @media (max-width:600px) {{ .pass-grid {{ grid-template-columns:1fr; }} .meta {{ grid-template-columns:1fr; }} }}
    .participant .row {{ display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px dashed #e2e8f0; font-size:13px; }}
    .participant .row .k {{ color:#64748b; }}
    .participant .row .v {{ font-weight:600; text-align:right; max-width:58%; }}
    .qr-box {{ text-align:center; border:2px dashed #cbd5e1; border-radius:10px; padding:14px; background:#f8fafc; }}
    .qr-box svg {{ width:140px; height:140px; }}
    .qr-hint {{ font-size:11px; color:#64748b; margin-top:8px; line-height:1.4; }}
    .card-footer {{ background:#f8fafc; border-top:1px solid #e2e8f0; padding:18px 24px; text-align:center; font-size:12px; color:#64748b; line-height:1.55; }}
    .instructions {{ margin-top:24px; background:linear-gradient(135deg,#eff6ff 0%,#e0e7ff 100%); border:1px solid #bfdbfe; border-radius:12px; padding:20px 24px; }}
    .instructions h3 {{ margin:0 0 12px; font-size:14px; color:#1e3a8a; }}
    .instructions ul {{ margin:0; padding-left:18px; color:#334155; font-size:13px; line-height:1.65; }}
    .partners {{ margin-top:16px; text-align:center; }}
    .partners img {{ height:22px; margin:0 8px; opacity:.9; vertical-align:middle; }}
    @media print {{
      body {{ background:#fff; }}
      .toolbar {{ display:none !important; }}
      .card, .venue, .total {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>Registration Confirmed!</h1>
      <p>Your registration for KRATOS&apos;26 has been successfully processed</p>
    </div>

    <div class="card">
      <div class="toolbar">
        <span>Official registration receipt · {html.escape(ctx['receipt_number'])}</span>
        <button type="button" class="btn-print" onclick="window.print()">Print / Save PDF</button>
      </div>

      <div class="brand">
        <div class="brand-logos">
          <img class="lion" src="{html.escape(urls['lion_mark'], quote=True)}" alt="KRATOS lion mark" />
          <img class="wordmark" src="{html.escape(urls['kratos26'], quote=True)}" alt="KRATOS'26" />
        </div>
        <div class="symposium">Technical Symposium</div>
        <div class="receipt-label">REGISTRATION RECEIPT</div>
      </div>

      <div class="venue">
        <div class="tag">VENUE</div>
        <div class="name">{venue}</div>
        <div class="sub">Easwari Engineering College · Chennai, Tamil Nadu</div>
      </div>

      <div class="body">
        <div class="meta">
          <div><div class="k">Payment ID</div><div class="v">{html.escape(ctx.get('razorpay_payment_id') or str(ctx.get('payment_id', '')))}</div></div>
          <div><div class="k">Registration date</div><div class="v">{html.escape(ctx['issued_at_formatted'])}</div></div>
          <div><div class="k">Receipt number</div><div class="v">{html.escape(ctx['receipt_number'])}</div></div>
          <div><div class="k">Status</div><div class="v" style="color:#059669;">✅ Confirmed</div></div>
        </div>

        <p class="section-h">Events registered</p>
        <table class="events-table">
          <thead>
            <tr>
              <th>Event</th>
              <th>Team</th>
              <th>Participants</th>
              <th style="text-align:right;">Amount</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>{html.escape(ctx['event_name'])}</strong><br />
                <span style="font-size:11px;color:#64748b;">{reg_type}</span></td>
              <td>{team_label}</td>
              <td>{participant_count}</td>
              <td style="text-align:right;font-weight:700;">₹{html.escape(ctx['amount_inr'])}</td>
            </tr>
          </tbody>
        </table>

        <div class="total">
          <span>Total amount</span>
          <span>₹{html.escape(ctx['amount_inr'])} {html.escape(ctx['currency'])}</span>
        </div>

        <div class="pass-grid">
          <div class="participant">
            <p class="section-h">Registered by</p>
            <div class="row"><span class="k">Name</span><span class="v">{html.escape(ctx['payer_name'])}</span></div>
            <div class="row"><span class="k">Email</span><span class="v">{html.escape(ctx['payer_email'])}</span></div>
            <div class="row"><span class="k">College</span><span class="v">{html.escape(ctx['payer_college'] or '—')}</span></div>
            <div class="row"><span class="k">Order ref</span><span class="v">{html.escape(ctx['razorpay_order_id'])}</span></div>
            {roster_block}
          </div>
          <div>
            <p class="section-h">Entry QR pass</p>
            <div class="qr-box">
              {ctx['qr_svg']}
              <p class="qr-hint">Present this QR at event check-in.<br />Each team member receives their own QR by email.</p>
            </div>
            {whatsapp_btn}
          </div>
        </div>
      </div>

      <div class="card-footer">
        <p style="margin:0 0 6px;">🎉 Thank you for registering for KRATOS&apos;26! 🎉</p>
        <p style="margin:0;font-size:11px;">Keep this receipt for your records · Generated {generated_at}</p>
        <div class="partners">
          <img src="{html.escape(urls['eec_white'], quote=True)}" alt="EEC" />
          <img src="{html.escape(urls['ace_white'], quote=True)}" alt="ACE" />
          <img src="{html.escape(urls['cse_logo'], quote=True)}" alt="CSE" />
        </div>
      </div>
    </div>

    <div class="instructions">
      <h3>Important instructions</h3>
      <ul>
        <li>Save this receipt for event entry verification.</li>
        <li>Check your email for your personal QR pass and event details.</li>
        <li>Arrive at least 30 minutes before the event start time.</li>
        <li>Bring a valid college ID for verification at the venue.</li>
        <li>For support, contact <strong>kratos.cse@gmail.com</strong></li>
      </ul>
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
