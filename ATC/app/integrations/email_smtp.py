import html
import mimetypes
import re
import smtplib
import ssl
from pathlib import Path
from typing import Iterable
from email.message import EmailMessage
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

