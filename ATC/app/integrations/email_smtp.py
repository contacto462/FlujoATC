import base64
import html
import mimetypes
import re
import smtplib
import ssl
from pathlib import Path
from typing import Iterable
from email.message import EmailMessage
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid, parseaddr

from ATC.app.core.config import settings


def _norm_msgid(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().replace("\r", "").replace("\n", "")
    value = value.strip().strip("<>").strip()
    return value or None


def _as_header_msgid(value: str | None) -> str | None:
    normalized = _norm_msgid(value)
    return f"<{normalized}>" if normalized else None


def _as_header_references(value: str | None) -> str | None:
    if not value:
        return None

    raw = value.replace("\r", " ").replace("\n", " ").strip()
    if not raw:
        return None

    extracted = re.findall(r"<([^>]+)>", raw)
    parts = extracted if extracted else raw.split()

    ordered_unique: list[str] = []
    seen: set[str] = set()
    for part in parts:
        msgid = _norm_msgid(part)
        if not msgid or msgid in seen:
            continue
        seen.add(msgid)
        ordered_unique.append(msgid)

    if not ordered_unique:
        return None
    return " ".join(f"<{item}>" for item in ordered_unique)


def _looks_like_html(value: str) -> bool:
    return bool(re.search(r"<[^>]+>", value or ""))


def _html_to_text(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value or "")
    text = re.sub(r"(?i)</p>|<br\s*/?>|</div>|</li>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or "Mensaje sin contenido."


def _text_to_html(value: str) -> str:
    safe = html.escape(value or "")
    safe = safe.replace("\r\n", "\n").replace("\r", "\n")
    return safe.replace("\n", "<br>")


def _resolve_from_header() -> str:
    from_header = (settings.SMTP_FROM or settings.SMTP_USER or "").strip()
    smtp_user_email = parseaddr(settings.SMTP_USER or "")[1].strip().lower()
    from_email = parseaddr(from_header)[1].strip().lower()

    if not from_email:
        from_email = smtp_user_email

    if not from_email:
        raise ValueError("SMTP_FROM/SMTP_USER invalido: falta direccion de correo.")

    if smtp_user_email and "@" in smtp_user_email and "@" in from_email:
        from_domain = from_email.split("@", 1)[1]
        smtp_domain = smtp_user_email.split("@", 1)[1]
        if from_domain != smtp_domain:
            return settings.SMTP_USER or from_header

    return from_header


def _deliver_message(msg: EmailMessage, envelope_to: list[str] | None = None) -> None:
    host = settings.SMTP_HOST
    port = int(settings.SMTP_PORT or 587)
    timeout_seconds = 25

    if not host:
        raise ValueError("SMTP_HOST no configurado.")

    errors: list[str] = []

    try:
        with smtplib.SMTP(host, port, timeout=timeout_seconds) as server:
            server.ehlo()
            if server.has_extn("starttls"):
                try:
                    server.starttls(context=ssl.create_default_context())
                except ssl.SSLError:
                    server.starttls(context=ssl._create_unverified_context())
                server.ehlo()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg, to_addrs=envelope_to or None)
            return
    except Exception as exc:
        errors.append(f"STARTTLS: {exc}")

    ssl_port = 465 if port != 465 else port
    try:
        with smtplib.SMTP_SSL(host, ssl_port, timeout=timeout_seconds) as server:
            server.ehlo()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg, to_addrs=envelope_to or None)
            return
    except Exception as exc:
        errors.append(f"SMTPS: {exc}")

    raise RuntimeError("No se pudo enviar correo SMTP. " + " | ".join(errors))


def _normalize_recipients(value: str | Iterable[str] | None, *, field_name: str) -> list[str]:
    if value is None:
        return []

    raw_items: list[str]
    if isinstance(value, str):
        raw_items = [value]
    else:
        raw_items = [str(item or "") for item in value]

    recipients: list[str] = []
    seen: set[str] = set()

    for raw in raw_items:
        for token in re.split(r"[;,]", raw):
            token = token.strip()
            if not token:
                continue

            parsed_email = parseaddr(token)[1].strip()
            if not parsed_email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", parsed_email):
                raise ValueError(f"Direccion invalida en {field_name}: {token}")

            lowered = parsed_email.lower()
            if lowered in seen:
                continue

            seen.add(lowered)
            recipients.append(parsed_email)

    return recipients


def _build_envelope_recipients(*recipient_groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()

    for group in recipient_groups:
        for recipient in group:
            lowered = recipient.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            merged.append(recipient)

    return merged


def _build_minimal_message(
    *,
    to: str,
    subject: str,
    body: str,
    from_header: str,
    ticket_id: int | None,
) -> EmailMessage:
    fallback = EmailMessage()
    fallback["From"] = from_header
    fallback["To"] = to
    fallback["Subject"] = subject
    fallback["Date"] = formatdate(localtime=True)
    if ticket_id is not None:
        fallback["X-Ticket-ID"] = str(ticket_id)
    fallback.set_content(body or "Mensaje sin contenido.", subtype="plain", charset="utf-8")
    return fallback


def _attach_inline_images(
    msg: EmailMessage,
    inline_images: list[dict[str, str]] | None,
) -> None:
    if not inline_images:
        return

    payload = msg.get_payload()
    if not isinstance(payload, list) or len(payload) < 2:
        return

    html_part = payload[-1]
    for item in inline_images:
        cid = (item.get("cid") or "").strip().strip("<>")
        path_value = (item.get("path") or "").strip()
        if not cid or not path_value:
            continue

        file_path = Path(path_value)
        if not file_path.exists() or not file_path.is_file():
            continue

        mime_type, _ = mimetypes.guess_type(str(file_path))
        maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)

        with file_path.open("rb") as file_obj:
            html_part.add_related(
                file_obj.read(),
                maintype=maintype,
                subtype=subtype,
                cid=f"<{cid}>",
                filename=file_path.name,
                disposition="inline",
            )


def _attach_inline_images_bytes(
    msg: EmailMessage,
    inline_images_bytes: list[dict] | None,
) -> None:
    """Like _attach_inline_images but accepts raw bytes instead of file paths."""
    if not inline_images_bytes:
        return

    payload = msg.get_payload()
    if not isinstance(payload, list) or len(payload) < 2:
        return

    html_part = payload[-1]
    for item in inline_images_bytes:
        cid = (item.get("cid") or "").strip().strip("<>")
        data = item.get("bytes")
        mime_type = str(item.get("mime_type") or "image/png").strip()
        filename = str(item.get("filename") or "imagen.png").strip()
        if not cid or not isinstance(data, (bytes, bytearray)) or not data:
            continue
        maintype, subtype = mime_type.split("/", 1) if "/" in mime_type else ("image", "png")
        html_part.add_related(
            bytes(data),
            maintype=maintype,
            subtype=subtype,
            cid=f"<{cid}>",
            filename=filename,
            disposition="inline",
        )


def _attach_files(
    msg: EmailMessage,
    attachments: list[dict[str, str]] | None,
) -> None:
    if not attachments:
        return

    for item in attachments:
        path_value = str(item.get("path") or "").strip()
        if not path_value:
            continue

        file_path = Path(path_value)
        if not file_path.exists() or not file_path.is_file():
            continue

        declared_name = str(item.get("filename") or "").strip()
        filename = declared_name or file_path.name

        content_type = str(item.get("content_type") or "").strip().lower()
        if "/" not in content_type:
            guessed_mime, _ = mimetypes.guess_type(filename)
            content_type = guessed_mime or "application/octet-stream"

        maintype, subtype = content_type.split("/", 1)
        with file_path.open("rb") as file_obj:
            msg.add_attachment(
                file_obj.read(),
                maintype=maintype,
                subtype=subtype,
                filename=filename,
            )


def send_email_reply(
    to: str | list[str],
    subject: str,
    body: str,
    *,
    cc: str | list[str] | None = None,
    bcc: str | list[str] | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    message_id: str | None = None,
    ticket_id: int | None = None,
    inline_images: list[dict[str, str]] | None = None,
    inline_images_bytes: list[dict] | None = None,
    attachments: list[dict[str, str]] | None = None,
) -> str:
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        raise ValueError("SMTP_USER/SMTP_PASSWORD no configurados.")

    subject = (subject or "").replace("\r", " ").replace("\n", " ").strip() or "Sin asunto"

    to_recipients = _normalize_recipients(to, field_name="para")
    if not to_recipients:
        raise ValueError("Destinatario vacio.")
    cc_recipients = _normalize_recipients(cc, field_name="cc")
    bcc_recipients = _normalize_recipients(bcc, field_name="bcc")
    envelope_recipients = _build_envelope_recipients(to_recipients, cc_recipients, bcc_recipients)
    if not envelope_recipients:
        raise ValueError("Destinatario vacio.")

    from_header = _resolve_from_header()
    from_email = parseaddr(from_header)[1].strip().lower()
    msgid_domain = from_email.split("@", 1)[1] if "@" in from_email else None
    msgid = _as_header_msgid(message_id) or make_msgid(domain=msgid_domain)

    plain_body = _html_to_text(body) if _looks_like_html(body) else (body or "").strip()
    plain_body = plain_body or "Mensaje sin contenido."
    html_body = body if _looks_like_html(body) else _text_to_html(plain_body)

    msg = EmailMessage()
    msg["From"] = from_header
    msg["To"] = ", ".join(to_recipients)
    if cc_recipients:
        msg["Cc"] = ", ".join(cc_recipients)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = msgid

    if ticket_id is not None:
        msg["X-Ticket-ID"] = str(ticket_id)

    irt = _as_header_msgid(in_reply_to)
    try:
        if irt:
            msg["In-Reply-To"] = irt
    except Exception:
        irt = None

    try:
        ref = _as_header_references(references)
        if ref:
            msg["References"] = ref
        elif irt:
            msg["References"] = irt
    except Exception:
        pass

    msg.set_content(plain_body, subtype="plain", charset="utf-8")
    msg.add_alternative(html_body, subtype="html", charset="utf-8")
    _attach_inline_images(msg, inline_images)
    _attach_inline_images_bytes(msg, inline_images_bytes)
    _attach_files(msg, attachments)

    try:
        _deliver_message(msg, envelope_to=envelope_recipients)
    except Exception as first_error:
        minimal = _build_minimal_message(
            to=", ".join(_build_envelope_recipients(to_recipients, cc_recipients)),
            subject=subject,
            body=plain_body,
            from_header=from_header,
            ticket_id=ticket_id,
        )
        try:
            _deliver_message(minimal, envelope_to=envelope_recipients)
        except Exception as second_error:
            raise RuntimeError(
                f"Fallo envio normal ({first_error}) y fallback ({second_error})"
            ) from second_error

    return _norm_msgid(msgid) or _norm_msgid(message_id) or ""


_LOGO_PATH = Path(__file__).resolve().parents[2] / "static" / "img" / "logo-atc.png"


def send_corporate_image_email(
    *,
    to: str,
    subject: str,
    titulo: str,
    subtitulo: str,
    cuerpo_html: str,
    images: list[dict],
) -> str:
    """
    Envía un correo corporativo con imágenes inline usando estructura MIME explícita.
    `images` = [{"bytes": b"...", "mime_type": "image/png", "filename": "foto.png"}]
    """
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        raise ValueError("SMTP_USER/SMTP_PASSWORD no configurados.")

    from_header = _resolve_from_header()
    from_email = parseaddr(from_header)[1].strip().lower()
    msgid_domain = from_email.split("@", 1)[1] if "@" in from_email else "atc.cl"
    msgid = make_msgid(domain=msgid_domain)

    logo_cid = "logo_atc_corp"
    has_logo = _LOGO_PATH.exists()

    img_tags = ""
    for i, img in enumerate(images):
        cid = f"adjunto_{i + 1}"
        img_tags += (
            f'<tr><td style="padding:0 0 16px 0;">'
            f'<img src="cid:{cid}" alt="Vista de cámaras {i + 1}" '
            f'style="display:block;max-width:100%;width:100%;height:auto;'
            f'border-radius:6px;border:1px solid #dde3ea;">'
            f'</td></tr>'
        )

    logo_tag = (
        f'<img src="cid:{logo_cid}" alt="Alguien Te Cuida" height="40" '
        f'style="display:block;border:0;" border="0">'
        if has_logo else
        '<span style="font-family:Arial,sans-serif;font-size:18px;font-weight:700;'
        'color:#ffffff;letter-spacing:-0.02em;">Alguien Te Cuida</span>'
    )

    from datetime import datetime as _dt
    fecha_str = _dt.now().strftime("%-d de %B de %Y, %H:%M hrs").capitalize()

    html_body = f"""<!DOCTYPE html>
<html lang="es" xmlns="http://www.w3.org/1999/xhtml"
      style="color-scheme:light;-webkit-color-scheme:light;">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="color-scheme" content="light">
  <meta name="supported-color-schemes" content="light">
  <title>{subject}</title>
  <style>
    :root {{ color-scheme: light; }}
    @media (prefers-color-scheme: dark) {{
      .email-outer   {{ background-color:#f0f4f8 !important; }}
      .email-wrap    {{ background-color:#f0f4f8 !important; }}
      .email-card    {{ background-color:#ffffff !important; border-color:#dde3ea !important; }}
      .email-body-td {{ background-color:#ffffff !important; }}
      .email-footer  {{ background-color:#f8fafc !important; }}
      .email-meta    {{ background-color:#f8fafc !important; border-color:#e5e7eb !important; }}
      .body-text     {{ color:#374151 !important; }}
      .meta-text     {{ color:#6b7280 !important; }}
      .meta-label    {{ color:#374151 !important; }}
      .footer-name   {{ color:#374151 !important; }}
      .footer-disc   {{ color:#9ca3af !important; }}
      .sep-line      {{ border-color:#e5e7eb !important; }}
      .img-label     {{ color:#6b7280 !important; }}
    }}
  </style>
</head>
<body class="email-outer"
      style="margin:0;padding:0;background-color:#f0f4f8;
             -webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;
             color-scheme:light;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       class="email-wrap" style="background-color:#f0f4f8;min-width:320px;">
  <tr>
    <td align="center" style="padding:40px 16px;">

      <!-- CONTENEDOR -->
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
             class="email-card"
             style="max-width:600px;width:100%;background-color:#ffffff;
                    border-radius:8px;overflow:hidden;border:1px solid #dde3ea;">

        <!-- HEADER -->
        <tr>
          <td style="background-color:#0d1f2d;padding:22px 36px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="vertical-align:middle;">{logo_tag}</td>
                <td align="right" style="vertical-align:middle;">
                  <span style="font-family:Arial,sans-serif;font-size:10px;font-weight:600;
                               color:#7a9bb5;letter-spacing:0.1em;text-transform:uppercase;">
                    Notificación interna
                  </span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- FRANJA ODS -->
        <tr>
          <td style="background-color:#1a5c38;padding:18px 36px;">
            <p style="margin:0;font-family:Arial,sans-serif;font-size:11px;font-weight:600;
                      color:#6ee7a8;letter-spacing:0.1em;text-transform:uppercase;">
              Vista de Cámaras
            </p>
            <p style="margin:5px 0 0;font-family:Arial,sans-serif;font-size:20px;font-weight:700;
                      color:#ffffff;letter-spacing:-0.01em;line-height:1.3;">
              {titulo}
            </p>
            <p style="margin:3px 0 0;font-family:Arial,sans-serif;font-size:13px;
                      color:#a7f3d0;line-height:1.4;">
              {subtitulo}
            </p>
          </td>
        </tr>

        <!-- CUERPO -->
        <tr>
          <td class="email-body-td" style="padding:30px 36px 8px;background-color:#ffffff;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td class="body-text"
                    style="font-family:Arial,sans-serif;font-size:14px;line-height:1.6;
                           color:#374151;padding-bottom:24px;">
                  {cuerpo_html}
                </td>
              </tr>

              <tr>
                <td style="padding-bottom:20px;">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                      <td class="sep-line"
                          style="border-top:1px solid #e5e7eb;font-size:0;line-height:0;">&nbsp;</td>
                    </tr>
                  </table>
                </td>
              </tr>

              <tr>
                <td style="padding-bottom:12px;">
                  <p class="img-label"
                     style="margin:0;font-family:Arial,sans-serif;font-size:11px;font-weight:700;
                            color:#6b7280;letter-spacing:0.08em;text-transform:uppercase;">
                    Imagen{('s' if len(images) > 1 else '')} adjunta{('s' if len(images) > 1 else '')}
                  </p>
                </td>
              </tr>

              {img_tags}

            </table>
          </td>
        </tr>

        <!-- META -->
        <tr>
          <td class="email-body-td" style="padding:0 36px 28px;background-color:#ffffff;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0"
                   class="email-meta"
                   style="background-color:#f8fafc;border-radius:6px;border:1px solid #e5e7eb;
                          width:100%;">
              <tr>
                <td style="padding:12px 18px;">
                  <p class="meta-text"
                     style="margin:0;font-family:Arial,sans-serif;font-size:12px;color:#6b7280;">
                    <strong class="meta-label" style="color:#374151;">Fecha:</strong>
                    &nbsp;{fecha_str}
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td class="email-footer"
              style="background-color:#f8fafc;border-top:1px solid #e5e7eb;
                     padding:18px 36px;border-radius:0 0 8px 8px;">
            <p class="footer-name"
               style="margin:0;font-family:Arial,sans-serif;font-size:12px;
                      font-weight:600;color:#374151;">
              Alguien Te Cuida SpA
            </p>
            <p class="footer-disc"
               style="margin:3px 0 0;font-family:Arial,sans-serif;font-size:11px;color:#9ca3af;">
              Este mensaje es de uso interno y fue generado automáticamente.
            </p>
          </td>
        </tr>

      </table>

    </td>
  </tr>
</table>
</body>
</html>"""

    plain_body = (
        f"{titulo}\n"
        f"{subtitulo}\n"
        f"Fecha: {fecha_str}\n\n"
        f"{re.sub(r'<[^>]+>', ' ', cuerpo_html).strip()}\n\n"
        f"---\nAlguien Te Cuida SpA\n"
        f"Este mensaje es confidencial y fue generado automáticamente."
    )

    # Estructura MIME: mixed > alternative > (plain | related > (html + imágenes)) + logo
    msg_outer = MIMEMultipart("mixed")
    msg_outer["From"] = from_header
    msg_outer["To"] = to
    msg_outer["Subject"] = subject
    msg_outer["Date"] = formatdate(localtime=True)
    msg_outer["Message-ID"] = msgid

    msg_alternative = MIMEMultipart("alternative")
    msg_outer.attach(msg_alternative)

    msg_alternative.attach(MIMEText(plain_body, "plain", "utf-8"))

    msg_related = MIMEMultipart("related")
    msg_alternative.attach(msg_related)

    msg_related.attach(MIMEText(html_body, "html", "utf-8"))

    # Logo
    if has_logo:
        logo_part = MIMEImage(_LOGO_PATH.read_bytes(), _subtype="png")
        logo_part["Content-ID"] = f"<{logo_cid}>"
        logo_part.add_header("Content-Disposition", "inline", filename="logo-atc.png")
        msg_related.attach(logo_part)

    # Imágenes adjuntas inline
    for i, img in enumerate(images):
        cid = f"adjunto_{i + 1}"
        raw = img.get("bytes")
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            continue
        mime_type = str(img.get("mime_type") or "image/png")
        subtype = mime_type.split("/", 1)[1] if "/" in mime_type else "png"
        filename = str(img.get("filename") or f"imagen_{i + 1}.{subtype}")
        img_part = MIMEImage(bytes(raw), _subtype=subtype)
        img_part["Content-ID"] = f"<{cid}>"
        img_part.add_header("Content-Disposition", "inline", filename=filename)
        msg_related.attach(img_part)

    _deliver_message(msg_outer, envelope_to=[parseaddr(to)[1] or to])
    return _norm_msgid(msgid) or ""
