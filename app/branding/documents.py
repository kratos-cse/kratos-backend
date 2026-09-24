"""KRATOS'26 document and email branding (shared header/footer)."""
import base64
import html
from pathlib import Path

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "kratos"

# Public CDN assets for email + receipt rendering (hosted on Cloudinary).
BRAND_IMAGE_URLS = {
    "lion_mark": "https://res.cloudinary.com/ssaq5evk/image/upload/v1790245970/lion-mark.png",
    "kratos26": "https://res.cloudinary.com/ssaq5evk/image/upload/v1790245971/kratos26.png",
    "eec_white": "https://res.cloudinary.com/ssaq5evk/image/upload/v1790245971/eec-white.png",
    "ace_white": "https://res.cloudinary.com/ssaq5evk/image/upload/v1790245970/ACE-white.png",
    "cse_logo": "https://res.cloudinary.com/ssaq5evk/image/upload/v1790245972/CSE-logo_black.png",
}

_EMAIL_ASSETS = BRAND_IMAGE_URLS


def _asset_data_uri(filename: str, fallback_mime: str = "image/svg+xml") -> str | None:
    path = _ASSETS_DIR / filename
    if not path.is_file():
        return None
    raw = path.read_bytes()
    mime = fallback_mime
    if filename.endswith(".png"):
        mime = "image/png"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _email_img(url: str, *, alt: str = "", width: int | None = None, height: int | None = None, style: str = "") -> str:
    dims = ""
    if width is not None:
        dims += f' width="{width}"'
    if height is not None:
        dims += f' height="{height}"'
    return (
        f'<img src="{html.escape(url, quote=True)}" alt="{html.escape(alt)}"{dims} '
        f'style="display:block;{style}" />'
    )


def _lion_mark_img(width: int = 44, *, for_email: bool = False) -> str:
    if for_email:
        return _email_img(
            _EMAIL_ASSETS["lion_mark"],
            alt="KRATOS lion mark",
            width=width,
            height=width,
            style="border-radius:8px;",
        )
    data_uri = _asset_data_uri("lion-mark.svg") or _asset_data_uri("lion-mark.png")
    if data_uri:
        return (
            f'<img src="{data_uri}" alt="KRATOS lion mark" width="{width}" height="{width}" '
            'style="display:block;border-radius:8px;" />'
        )
    return (
        '<div style="width:44px;height:44px;border-radius:8px;background:#b91c1c;'
        'color:#fff;font-weight:800;font-size:18px;line-height:44px;text-align:center;">K</div>'
    )


def render_document_header_html(*, subtitle: str = "EEC · ACE · CSE") -> str:
    """Receipt/PDF header block."""
    return f"""
    <div class="kratos-doc-header" style="display:flex;align-items:center;gap:16px;margin-bottom:20px;">
        {_lion_mark_img()}
        <div>
            <div style="font-size:28px;font-weight:800;letter-spacing:0.04em;color:#f8fafc;line-height:1.1;">
                KRATOS&apos;26
            </div>
            <div style="font-size:12px;letter-spacing:0.18em;text-transform:uppercase;color:#94a3b8;margin-top:4px;">
                {html.escape(subtitle)}
            </div>
        </div>
    </div>
    """


def render_email_shell_html(
    *,
    title: str,
    body_html: str,
    footer_note: str = "KRATOS'26 · EEC · ACE · CSE · kratos.cse@gmail.com",
) -> str:
    """Wrap transactional email content in the shared KRATOS'26 shell."""
    partner_logos = "".join(
        _email_img(url, alt="", height=22, style="margin:0 8px;opacity:0.9;display:inline-block;")
        for url in (_EMAIL_ASSETS["eec_white"], _EMAIL_ASSETS["ace_white"], _EMAIL_ASSETS["cse_logo"])
    )
    kratos_wordmark = _email_img(
        _EMAIL_ASSETS["kratos26"],
        alt="KRATOS'26",
        height=28,
        style="margin-top:2px;",
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{html.escape(title)}</title>
</head>
<body style="margin:0;padding:0;background:#e2e8f0;font-family:Segoe UI,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#e2e8f0;padding:28px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
             style="max-width:580px;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #cbd5e1;">
        <tr><td style="background:#0f172a;padding:24px 28px;">
          <table role="presentation" width="100%"><tr>
            <td width="52">{_lion_mark_img(40, for_email=True)}</td>
            <td>
              {kratos_wordmark}
              <div style="font-size:11px;letter-spacing:0.2em;text-transform:uppercase;color:#94a3b8;margin-top:6px;">
                EEC · ACE · CSE
              </div>
            </td>
          </tr></table>
          {f'<div style="margin-top:14px;text-align:center;">{partner_logos}</div>' if partner_logos else ''}
        </td></tr>
        <tr><td style="padding:28px 32px 8px;">
          {body_html}
        </td></tr>
        <tr><td style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:18px 28px;text-align:center;">
          <p style="margin:0;font-size:12px;line-height:1.5;color:#64748b;">{html.escape(footer_note)}</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
