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
    # pyrefly: ignore [unexpected-keyword]
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
    """Render an elegant, executive-grade KRATOS'26 registration receipt & pass as PDF."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter  # 612 x 792 pt
    margin = 36
    card_w = width - margin * 2  # 540 pt
    card_h = 720
    card_left = margin
    card_top = height - margin  # 756 pt
    card_bottom = card_top - card_h  # 36 pt

    # 1. Page Background
    c.setFillColorRGB(0.06, 0.09, 0.16)  # #0f172a
    c.rect(0, 0, width, height, fill=1, stroke=0)

    # 2. Main White Card Container
    c.setFillColorRGB(1, 1, 1)
    c.roundRect(card_left, card_bottom, card_w, card_h, 12, fill=1, stroke=0)

    # 3. Top Accent Bar
    c.setFillColorRGB(0.73, 0.11, 0.11)  # #b91c1c Crimson
    c.rect(card_left, card_top - 6, card_w, 6, fill=1, stroke=0)

    # 4. Institutional Header Banner
    header_h = 76
    header_y = card_top - 6 - header_h
    c.setFillColorRGB(0.96, 0.97, 0.98)  # #f1f5f9
    c.rect(card_left, header_y, card_w, header_h, fill=1, stroke=0)

    # Institution Logos & Centered Header Text
    from pathlib import Path
    assets_dir = Path(__file__).resolve().parent.parent / "assets" / "kratos"
    lion_logo = assets_dir / "lion-mark-opt.png" if (assets_dir / "lion-mark-opt.png").is_file() else assets_dir / "lion-mark.png"
    cse_logo = assets_dir / "cse-logo-opt.png" if (assets_dir / "cse-logo-opt.png").is_file() else assets_dir / "CSE-logo_black.png"

    # Left Logo: KRATOS Lion Mark
    if lion_logo.is_file():
        c.drawImage(str(lion_logo), card_left + 16, header_y + 16, width=34, height=44, mask="auto")

    # Right Logo: Department of CSE
    if cse_logo.is_file():
        c.drawImage(str(cse_logo), card_left + card_w - 16 - 44, header_y + 17, width=44, height=42, mask="auto")

    # Centered Hierarchy
    c.setFillColorRGB(0.58, 0.10, 0.10)  # Dark Red
    c.setFont("Helvetica-Bold", 13.5)
    c.drawCentredString(width / 2, header_y + 49, "KRATOS'26 TECHNICAL SYMPOSIUM")

    c.setFillColorRGB(0.18, 0.23, 0.31)  # Slate Dark
    c.setFont("Helvetica-Bold", 8.5)
    c.drawCentredString(width / 2, header_y + 33, "DEPARTMENT OF COMPUTER SCIENCE AND ENGINEERING")

    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.39, 0.45, 0.55)
    c.drawCentredString(width / 2, header_y + 19, "Easwari Engineering College (Autonomous), Ramapuram, Chennai - 600089")

    # Thin line below header
    c.setStrokeColorRGB(0.88, 0.91, 0.94)
    c.setLineWidth(1)
    c.line(card_left, header_y, card_left + card_w, header_y)

    # 5. Title & Status Badge Bar
    bar_y = header_y - 34
    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(card_left + 20, bar_y + 10, "OFFICIAL REGISTRATION RECEIPT")

    # Green Confirmed Badge
    badge_w = 124
    badge_h = 20
    badge_x = card_left + card_w - 20 - badge_w
    badge_y = bar_y + 8
    c.setFillColorRGB(0.86, 0.97, 0.91)  # #dcfce7
    c.roundRect(badge_x, badge_y, badge_w, badge_h, 4, fill=1, stroke=0)
    c.setFillColorRGB(0.09, 0.52, 0.28)  # #15803d
    c.setFont("Helvetica-Bold", 8.5)
    c.drawCentredString(badge_x + badge_w / 2, badge_y + 6, "[ CONFIRMED & PAID ]")

    # 6. Metadata Strip (Receipt No, Date, Payment ID)
    meta_box_y = bar_y - 32
    c.setStrokeColorRGB(0.88, 0.91, 0.94)
    c.setFillColorRGB(0.98, 0.98, 0.99)
    c.roundRect(card_left + 20, meta_box_y, card_w - 40, 26, 4, fill=1, stroke=1)

    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.45, 0.50, 0.58)
    c.drawString(card_left + 28, meta_box_y + 9, "RECEIPT NO:")
    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.drawString(card_left + 90, meta_box_y + 9, str(ctx.get("receipt_number", "—")))

    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.45, 0.50, 0.58)
    c.drawString(card_left + 225, meta_box_y + 9, "DATE:")
    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.drawString(card_left + 258, meta_box_y + 9, str(ctx.get("issued_at_formatted", "—"))[:22])

    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.45, 0.50, 0.58)
    c.drawString(card_left + 380, meta_box_y + 9, "PAYMENT ID:")
    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.drawString(card_left + 446, meta_box_y + 9, str(ctx.get("razorpay_payment_id") or ctx.get("payment_id", "—"))[:14])

    # 7. Content Columns: Left (Details) & Right (QR Code Pass)
    content_top = meta_box_y - 16
    left_w = 310
    right_w = 170
    left_x = card_left + 20
    right_x = card_left + card_w - 20 - right_w

    curr_y = content_top

    import textwrap

    def draw_section_box(title: str, items: list[tuple[str, Any]], start_y: float, box_w: float = left_w) -> float:
        c.setFillColorRGB(0.73, 0.11, 0.11)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(left_x, start_y, title.upper())

        c.setStrokeColorRGB(0.73, 0.11, 0.11)
        c.setLineWidth(1)
        c.line(left_x, start_y - 3, left_x + box_w, start_y - 3)

        row_y = start_y - 16
        for label, val in items:
            c.setFont("Helvetica", 8)
            c.setFillColorRGB(0.45, 0.50, 0.58)
            c.drawString(left_x + 4, row_y, f"{label}:")

            c.setFont("Helvetica-Bold", 8.5)
            c.setFillColorRGB(0.06, 0.09, 0.16)
            val_str = str(val or "—")
            lines = textwrap.wrap(val_str, width=34) if len(val_str) > 34 else [val_str]
            for idx, line in enumerate(lines[:2]):
                c.drawString(left_x + 95, row_y, line)
                if idx < len(lines[:2]) - 1:
                    row_y -= 11
            row_y -= 14

        return row_y - 6

    # A. Event Information
    event_items = [
        ("Event Name", ctx.get("event_name", "—")),
        ("Category", ctx.get("event_category", "TECHNICAL")),
        ("Date & Time", ctx.get("event_slot") or "October 15, 2026, 09:30 AM"),
        ("Venue", _default_venue(ctx)),
    ]
    curr_y = draw_section_box("Event & Schedule", event_items, curr_y)

    # B. Payer & Registration Information
    reg_type_str = str(ctx.get("payment_type", "SOLO")).replace("_", " ")
    team_str = str(ctx.get("team_name") or "Individual (Solo)")
    payer_items = [
        ("Participant", ctx.get("payer_name", "—")),
        ("Email", ctx.get("payer_email", "—")),
        ("Contact Phone", ctx.get("payer_phone", "—")),
        ("College / Inst.", ctx.get("payer_college", "—")),
        ("Registration", f"{reg_type_str}  ·  {team_str}"),
    ]
    curr_y = draw_section_box("Participant & Registration", payer_items, curr_y)

    # C. Team Roster (if team)
    members = ctx.get("team_members") or []
    if members:
        c.setFillColorRGB(0.73, 0.11, 0.11)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(left_x, curr_y, "TEAM ROSTER")
        c.setStrokeColorRGB(0.73, 0.11, 0.11)
        c.line(left_x, curr_y - 3, left_x + left_w, curr_y - 3)
        curr_y -= 14

        for m in members[:4]:
            c.setFont("Helvetica", 8)
            c.setFillColorRGB(0.06, 0.09, 0.16)
            c.drawString(left_x + 4, curr_y, f"• {m.get('name', 'Member')} ({m.get('role', 'Member')})")
            curr_y -= 12
        curr_y -= 4

    # 8. Right Side: Official Entry QR Pass Box
    qr_box_h = 240
    qr_box_y = content_top - qr_box_h + 10
    c.setStrokeColorRGB(0.80, 0.84, 0.88)
    c.setFillColorRGB(0.97, 0.98, 0.99)
    c.roundRect(right_x, qr_box_y, right_w, qr_box_h, 8, fill=1, stroke=1)

    # Header inside QR Box
    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(right_x + right_w / 2, qr_box_y + qr_box_h - 18, "OFFICIAL ENTRY PASS")

    c.setFont("Helvetica", 7.5)
    c.setFillColorRGB(0.45, 0.50, 0.58)
    c.drawCentredString(right_x + right_w / 2, qr_box_y + qr_box_h - 30, "PERSONAL CHECK-IN QR")

    # Render QR Code in Image
    qr_url = ctx.get("qr_verify_url") or f"{settings.APP_PUBLIC_BASE_URL.rstrip('/')}/checkin?token={ctx.get('qr_token', '')}"
    qr_png = io.BytesIO()
    qr_img = qrcode.make(qr_url)
    # pyrefly: ignore [unexpected-keyword]
    qr_img.save(qr_png, format="PNG")
    qr_png.seek(0)

    qr_size = 114
    qr_img_x = right_x + (right_w - qr_size) / 2
    qr_img_y = qr_box_y + 60
    c.drawImage(ImageReader(qr_png), qr_img_x, qr_img_y, width=qr_size, height=qr_size, mask="auto")

    # QR Pass ID / Token
    c.setFont("Helvetica-Bold", 8)
    c.setFillColorRGB(0.73, 0.11, 0.11)
    c.drawCentredString(right_x + right_w / 2, qr_box_y + 44, str(ctx.get("qr_token", "KRATOS-PASS"))[:20])

    c.setFont("Helvetica", 7)
    c.setFillColorRGB(0.45, 0.50, 0.58)
    c.drawCentredString(right_x + right_w / 2, qr_box_y + 30, "Show this QR at the venue desk")
    c.drawCentredString(right_x + right_w / 2, qr_box_y + 18, "Valid with student college ID")

    # 9. Payment Summary Card (Full Width Banner)
    pay_banner_h = 44
    pay_banner_y = card_bottom + 52
    c.setFillColorRGB(0.06, 0.09, 0.16)  # Dark slate
    c.roundRect(card_left + 20, pay_banner_y, card_w - 40, pay_banner_h, 6, fill=1, stroke=0)

    c.setFillColorRGB(0.71, 0.76, 0.84)
    c.setFont("Helvetica", 8.5)
    c.drawString(card_left + 34, pay_banner_y + 26, "TOTAL AMOUNT PAID")
    c.setFont("Helvetica", 8)
    c.drawString(card_left + 34, pay_banner_y + 12, "Payment Mode: Online (Razorpay) · Status: Confirmed")

    amount_str = f"INR {ctx.get('amount_inr', '250.00')}"
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(card_left + card_w - 34, pay_banner_y + 15, amount_str)

    # 10. Footer Disclaimer
    c.setFont("Helvetica", 7.5)
    c.setFillColorRGB(0.45, 0.50, 0.58)
    c.drawCentredString(
        width / 2,
        card_bottom + 26,
        "This is an electronically generated official receipt. Association of Computer Engineers (ACE) · CSE Dept.",
    )
    c.drawCentredString(
        width / 2,
        card_bottom + 14,
        "Easwari Engineering College, Chennai · For support contact kratos.cse@gmail.com",
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
    """KRATOS'26 official registration receipt card — ultra-modern responsive web UI."""
    urls = BRAND_IMAGE_URLS
    venue = html.escape(_default_venue(ctx))
    participant_count = _receipt_participant_count(ctx)
    team_label = html.escape(ctx.get("team_name") or "Individual (Solo)")
    reg_type = html.escape(ctx["payment_type"].replace("_", " "))
    generated_at = datetime.now(timezone.utc).strftime("%d %b %Y, %I:%M %p UTC")

    # PDF download link
    payment_id_val = ctx.get("payment_id", "")
    pdf_download_url = f"{settings.APP_PUBLIC_BASE_URL.rstrip('/')}/api/v1/payments/{payment_id_val}/receipt/pdf"
    if ctx.get("receipt_token"):
        pdf_download_url += f"?receipt_token={quote(ctx['receipt_token'], safe='')}"

    roster_rows = ""
    for member in ctx.get("team_members") or []:
        roster_rows += (
            "<tr>"
            f'<td style="padding:10px 14px;border-bottom:1px solid #f1f5f9;font-weight:600;color:#0f172a;">{html.escape(member["name"])}</td>'
            f'<td style="padding:10px 14px;border-bottom:1px solid #f1f5f9;color:#64748b;">{html.escape(member["role"])}</td>'
            f'<td style="padding:10px 14px;border-bottom:1px solid #f1f5f9;text-align:right;">'
            f'<span style="background:#ecfdf5;color:#059669;font-size:11px;font-weight:700;padding:3px 10px;border-radius:9999px;">'
            f'{html.escape(member["status"])}</span></td></tr>'
        )
    roster_block = ""
    if roster_rows:
        roster_block = f"""
        <div style="margin-top:24px;">
          <p style="margin:0 0 10px;font-size:11px;font-weight:700;letter-spacing:0.12em;color:#64748b;text-transform:uppercase;">
            Team Roster</p>
          <table style="width:100%;border-collapse:collapse;font-size:13px;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;background:#fff;">
            <thead><tr style="background:#f8fafc;color:#475569;font-size:11px;text-transform:uppercase;letter-spacing:0.06em;">
              <th style="padding:10px 14px;text-align:left;">Member</th>
              <th style="padding:10px 14px;text-align:left;">Role</th>
              <th style="padding:10px 14px;text-align:right;">Status</th>
            </tr></thead>
            <tbody>{roster_rows}</tbody>
          </table>
        </div>"""

    whatsapp_btn = ""
    if ctx.get("whatsapp_link"):
        whatsapp_btn = (
            f'<p style="margin:16px 0 0;text-align:center;">'
            f'<a href="{html.escape(ctx["whatsapp_link"])}" target="_blank" rel="noopener noreferrer" '
            f'style="display:inline-flex;align-items:center;justify-content:center;gap:8px;background:#25D366;color:#ffffff;'
            f'text-decoration:none;padding:10px 20px;border-radius:8px;font-size:13px;font-weight:700;box-shadow:0 4px 12px rgba(37,211,102,0.25);">'
            f'<span>💬 Join Event WhatsApp Group</span></a></p>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>KRATOS'26 Receipt — {html.escape(ctx['receipt_number'])}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
  <style>
    * {{ box-sizing:border-box; }}
    body {{
      margin:0;
      font-family:'Plus Jakarta Sans','Inter',-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;
      background:#090d16;
      background-image:radial-gradient(at 50% 0%, rgba(185,28,28,0.15) 0px, transparent 60%), radial-gradient(at 100% 100%, rgba(15,23,42,0.8) 0px, transparent 50%);
      color:#0f172a;
      min-height:100vh;
      padding:32px 16px 48px;
    }}
    .wrap {{ max-width:720px; margin:0 auto; }}
    .hero {{ text-align:center; color:#f8fafc; margin-bottom:24px; }}
    .hero h1 {{ margin:0 0 6px; font-size:26px; font-weight:800; letter-spacing:-0.02em; }}
    .hero p {{ margin:0; color:#94a3b8; font-size:14px; }}
    .card {{
      background:#ffffff;
      border-radius:16px;
      overflow:hidden;
      box-shadow:0 25px 60px -15px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.08);
    }}
    .toolbar {{
      background:#0f172a;
      padding:12px 20px;
      display:flex;
      justify-content:space-between;
      align-items:center;
      border-bottom:1px solid #1e293b;
    }}
    .toolbar .badge-num {{
      color:#94a3b8;
      font-size:12px;
      font-weight:600;
      letter-spacing:0.04em;
    }}
    .toolbar-actions {{ display:flex; gap:10px; }}
    .btn-action {{
      cursor:pointer;
      border:none;
      text-decoration:none;
      font-weight:700;
      font-size:12px;
      padding:8px 14px;
      border-radius:6px;
      display:inline-flex;
      align-items:center;
      gap:6px;
      transition:all 0.15s ease;
    }}
    .btn-print {{ background:#b91c1c; color:#ffffff; }}
    .btn-print:hover {{ background:#991b1b; }}
    .btn-pdf {{ background:#1e293b; color:#f8fafc; border:1px solid #334155; }}
    .btn-pdf:hover {{ background:#334155; }}
    
    .brand {{ text-align:center; padding:22px 24px 16px; border-bottom:1px solid #f1f5f9; background:#ffffff; }}
    .brand-logos {{ display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:10px; }}
    .brand-logos .brand-center {{ display:flex; align-items:center; justify-content:center; gap:10px; }}
    .brand-logos img.lion {{ width:38px; height:38px; border-radius:8px; }}
    .brand-logos img.wordmark {{ height:24px; width:auto; }}
    .brand-logos img.side-logo {{ height:32px; width:auto; max-width:110px; object-fit:contain; }}
    .symposium {{ font-size:12px; color:#64748b; letter-spacing:.12em; text-transform:uppercase; font-weight:600; margin-top:4px; }}
    .receipt-label {{
      display:inline-block;
      margin-top:8px;
      font-size:11px;
      font-weight:800;
      letter-spacing:.18em;
      color:#b91c1c;
      background:#fef2f2;
      padding:4px 12px;
      border-radius:9999px;
    }}

    .venue {{
      background:linear-gradient(135deg, #7f1d1d 0%, #b91c1c 50%, #991b1b 100%);
      color:#ffffff;
      text-align:center;
      padding:16px 20px;
    }}
    .venue .tag {{ font-size:10px; letter-spacing:.22em; opacity:.85; font-weight:700; text-transform:uppercase; }}
    .venue .name {{ font-size:15px; font-weight:800; margin-top:2px; }}
    .venue .sub {{ font-size:12px; opacity:.92; margin-top:3px; }}

    .body {{ padding:28px 32px; }}
    .meta-grid {{
      display:grid;
      grid-template-columns:repeat(4, 1fr);
      gap:12px;
      margin-bottom:28px;
      background:#f8fafc;
      padding:16px 18px;
      border-radius:12px;
      border:1px solid #e2e8f0;
    }}
    .meta-grid .item .k {{ font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:.08em; font-weight:700; }}
    .meta-grid .item .v {{ font-size:13px; font-weight:700; color:#0f172a; margin-top:4px; word-break:break-word; }}

    .section-h {{
      font-size:11px;
      font-weight:800;
      letter-spacing:.12em;
      color:#64748b;
      text-transform:uppercase;
      margin:0 0 12px;
      display:flex;
      align-items:center;
      gap:6px;
    }}
    .events-table {{
      width:100%;
      border-collapse:collapse;
      font-size:13px;
      border:1px solid #e2e8f0;
      border-radius:10px;
      overflow:hidden;
      background:#ffffff;
    }}
    .events-table th {{
      background:#f8fafc;
      color:#475569;
      font-size:10px;
      text-transform:uppercase;
      letter-spacing:.08em;
      padding:12px 16px;
      text-align:left;
      font-weight:700;
    }}
    .events-table td {{ padding:14px 16px; border-top:1px solid #f1f5f9; vertical-align:middle; }}

    .total-banner {{
      margin-top:20px;
      background:linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
      color:#f8fafc;
      border-radius:12px;
      padding:16px 22px;
      display:flex;
      justify-content:space-between;
      align-items:center;
      box-shadow:0 10px 25px -5px rgba(15,23,42,0.3);
    }}
    .total-banner .lbl {{ font-size:12px; letter-spacing:.12em; text-transform:uppercase; font-weight:700; color:#94a3b8; }}
    .total-banner .val {{ font-size:22px; font-weight:800; color:#ffffff; }}

    .pass-grid {{
      display:grid;
      grid-template-columns:1fr 200px;
      gap:24px;
      margin-top:28px;
      align-items:start;
    }}
    @media (max-width:640px) {{
      .pass-grid {{ grid-template-columns:1fr; }}
      .meta-grid {{ grid-template-columns:1fr 1fr; }}
      .body {{ padding:20px; }}
    }}
    .participant-card {{
      background:#f8fafc;
      border:1px solid #e2e8f0;
      border-radius:12px;
      padding:16px 20px;
    }}
    .participant-card .row {{
      display:flex;
      justify-content:space-between;
      padding:8px 0;
      border-bottom:1px dashed #e2e8f0;
      font-size:13px;
    }}
    .participant-card .row:last-child {{ border-bottom:none; }}
    .participant-card .row .k {{ color:#64748b; font-weight:500; }}
    .participant-card .row .v {{ font-weight:700; color:#0f172a; text-align:right; max-width:62%; word-break:break-word; }}

    .qr-box {{
      text-align:center;
      border:2px dashed #cbd5e1;
      border-radius:12px;
      padding:16px;
      background:#f8fafc;
    }}
    .qr-box svg {{ width:140px; height:140px; display:inline-block; }}
    .qr-hint {{ font-size:11px; color:#64748b; margin-top:8px; line-height:1.4; font-weight:500; }}

    .card-footer {{
      background:#f8fafc;
      border-top:1px solid #e2e8f0;
      padding:20px 24px;
      text-align:center;
      font-size:12px;
      color:#64748b;
      line-height:1.6;
    }}
    .partners {{ margin-top:14px; display:flex; justify-content:center; align-items:center; gap:16px; }}
    .partners img {{ height:24px; opacity:.9; }}

    .instructions {{
      margin-top:24px;
      background:linear-gradient(135deg, #eff6ff 0%, #e0e7ff 100%);
      border:1px solid #bfdbfe;
      border-radius:14px;
      padding:20px 24px;
    }}
    .instructions h3 {{ margin:0 0 10px; font-size:14px; color:#1e3a8a; font-weight:800; }}
    .instructions ul {{ margin:0; padding-left:18px; color:#334155; font-size:13px; line-height:1.65; }}

    @media print {{
      body {{ background:#ffffff; padding:0; }}
      .toolbar, .hero, .instructions, .partners {{ display:none !important; }}
      .card {{ box-shadow:none; border:none; }}
      .venue, .total-banner {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>Registration Confirmed!</h1>
      <p>Your official receipt and entry pass for KRATOS&apos;26</p>
    </div>

    <div class="card">
      <div class="toolbar">
        <span class="badge-num">Official Receipt · {html.escape(ctx['receipt_number'])}</span>
        <div class="toolbar-actions">
          <button type="button" class="btn-action btn-print" onclick="window.print()">🖨️ Print Receipt</button>
          <a href="{html.escape(pdf_download_url)}" class="btn-action btn-pdf">📥 Download PDF</a>
        </div>
      </div>

      <div class="brand">
        <div class="brand-logos">
          <img class="side-logo" src="{html.escape(urls['eec_white'], quote=True)}" alt="Easwari Engineering College" />
          <div class="brand-center">
            <img class="lion" src="{html.escape(urls['lion_mark'], quote=True)}" alt="KRATOS lion mark" />
            <img class="wordmark" src="{html.escape(urls['kratos26'], quote=True)}" alt="KRATOS'26" />
          </div>
          <img class="side-logo" src="{html.escape(urls['cse_logo'], quote=True)}" alt="Department of CSE" />
        </div>
        <div class="symposium">Department of Computer Science &amp; Engineering · Technical Symposium</div>
        <div class="receipt-label">OFFICIAL REGISTRATION RECEIPT</div>
      </div>

      <div class="venue">
        <div class="tag">VENUE & LOCATION</div>
        <div class="name">{venue}</div>
        <div class="sub">Easwari Engineering College · Chennai, Tamil Nadu</div>
      </div>

      <div class="body">
        <div class="meta-grid">
          <div class="item"><div class="k">Receipt No</div><div class="v">{html.escape(ctx['receipt_number'])}</div></div>
          <div class="item"><div class="k">Date & Time</div><div class="v">{html.escape(ctx['issued_at_formatted'])}</div></div>
          <div class="item"><div class="k">Payment ID</div><div class="v">{html.escape(ctx.get('razorpay_payment_id') or str(ctx.get('payment_id', '')))}</div></div>
          <div class="item"><div class="k">Status</div><div class="v" style="color:#059669;">✔ Confirmed</div></div>
        </div>

        <p class="section-h">Registered Event Details</p>
        <table class="events-table">
          <thead>
            <tr>
              <th>Event Name</th>
              <th>Registration Type</th>
              <th>Participants</th>
              <th style="text-align:right;">Amount</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                <strong style="font-size:14px;color:#0f172a;">{html.escape(ctx['event_name'])}</strong><br />
                <span style="font-size:11px;color:#64748b;">Category: {html.escape(ctx.get('event_category', 'Technical'))}</span>
              </td>
              <td>
                <span style="background:#f1f5f9;color:#334155;font-size:11px;font-weight:700;padding:3px 10px;border-radius:6px;">
                  {team_label} ({reg_type})
                </span>
              </td>
              <td style="font-weight:600;color:#0f172a;">{participant_count}</td>
              <td style="text-align:right;font-weight:800;font-size:15px;color:#0f172a;">₹{html.escape(ctx['amount_inr'])}</td>
            </tr>
          </tbody>
        </table>

        <div class="total-banner">
          <span class="lbl">Total Registration Fee Paid</span>
          <span class="val">₹{html.escape(ctx['amount_inr'])} {html.escape(ctx['currency'])}</span>
        </div>

        <div class="pass-grid">
          <div class="participant-section">
            <p class="section-h">Registered Participant</p>
            <div class="participant-card">
              <div class="row"><span class="k">Participant Name</span><span class="v">{html.escape(ctx['payer_name'])}</span></div>
              <div class="row"><span class="k">Email Address</span><span class="v">{html.escape(ctx['payer_email'])}</span></div>
              <div class="row"><span class="k">Contact Phone</span><span class="v">{html.escape(ctx['payer_phone'] or '—')}</span></div>
              <div class="row"><span class="k">College / Inst.</span><span class="v">{html.escape(ctx['payer_college'] or '—')}</span></div>
              <div class="row"><span class="k">Order Reference</span><span class="v">{html.escape(ctx['razorpay_order_id'])}</span></div>
            </div>
            {roster_block}
          </div>

          <div>
            <p class="section-h">Event Entry Pass</p>
            <div class="qr-box">
              {ctx['qr_svg']}
              <p class="qr-hint">Scan at the event check-in desk.<br />Valid with student college ID.</p>
            </div>
            {whatsapp_btn}
          </div>
        </div>
      </div>

      <div class="card-footer">
        <p style="margin:0 0 6px;font-weight:700;color:#0f172a;">🎉 Thank you for registering for KRATOS&apos;26! 🎉</p>
        <p style="margin:0;font-size:11px;">Keep this official receipt for your records · Electronically generated at {generated_at}</p>
        <div class="partners">
          <img src="{html.escape(urls['eec_white'], quote=True)}" alt="EEC" />
          <img src="{html.escape(urls['ace_white'], quote=True)}" alt="ACE" />
          <img src="{html.escape(urls['cse_logo'], quote=True)}" alt="CSE" />
        </div>
      </div>
    </div>

    <div class="instructions">
      <h3>Important Event Instructions</h3>
      <ul>
        <li>Keep this receipt and QR code handy on your phone for verification at the event entrance.</li>
        <li>Please arrive at least 30 minutes prior to the scheduled event time.</li>
        <li>A valid college identity card is mandatory for venue entry and verification.</li>
        <li>For queries or technical assistance, contact the organizing team at <strong>kratos.cse@gmail.com</strong>.</li>
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
