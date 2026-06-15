from __future__ import annotations

import email
import imaplib
import os
import re
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from ATC.app.core.config import settings
from ATC.app.core.text import decode_mime_words
from ATC.app.models.email_sync_state import EmailSyncState
from ATC.app.models.message import Message
from ATC.app.models.requester import Requester
from ATC.app.models.ticket import Ticket
from ATC.app.services.ticket_status_service import apply_ticket_status_change


UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def _safe_attachment_filename(
    raw_filename: str | None,
    *,
    content_type: str,
    fallback_prefix: str = "adjunto",
) -> str:
    filename = decode_mime_words(raw_filename) if raw_filename else ""
    filename = (filename or "").strip().strip("\"'")
    if filename:
        filename = filename.replace("\\", "/").split("/")[-1]

    if not filename:
        ext = (content_type.split("/")[-1] or "bin").lower()
        ext = "jpg" if ext == "jpeg" else ext
        filename = f"{fallback_prefix}.{ext}"

    filename = _INVALID_FILENAME_CHARS.sub("_", filename)
    filename = re.sub(r"\s+", " ", filename).strip().rstrip(". ")
    if not filename:
        filename = f"{fallback_prefix}.bin"

    base, ext = os.path.splitext(filename)
    base = base.strip() or fallback_prefix
    if not ext:
        guessed = (content_type.split("/")[-1] or "bin").lower()
        guessed = "jpg" if guessed == "jpeg" else guessed
        ext = f".{guessed}"

    if base.upper() in _WINDOWS_RESERVED_NAMES:
        base = f"file_{base}"

    candidate = f"{base}{ext}"
    counter = 1
    while os.path.exists(os.path.join(UPLOAD_DIR, candidate)):
        candidate = f"{base}_{counter}{ext}"
        counter += 1
    return candidate


def _decode_subject(msg) -> str:
    raw_subject = msg.get("Subject")
    if not raw_subject:
        return "Sin asunto"
    return decode_mime_words(raw_subject) or "Sin asunto"


def _decode_mime_text(value: str | None) -> str:
    return decode_mime_words(value)


def _norm_msgid(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().replace("\r", "").replace("\n", "")
    value = value.strip().strip("<>").strip()
    return value or None


def _extract_reference_ids(raw: str | None) -> list[str]:
    if not raw:
        return []

    raw = raw.replace("\r", " ").replace("\n", " ").strip()
    ids = re.findall(r"<([^>]+)>", raw)

    out: list[str] = []
    parts = ids if ids else raw.split()
    for part in parts:
        msgid = _norm_msgid(part)
        if msgid:
            out.append(msgid)
    return out


def _extract_ticket_id_from_headers(msg) -> int | None:
    raw = msg.get("X-Ticket-ID")
    if not raw:
        return None
    raw = raw.strip()
    return int(raw) if raw.isdigit() else None


def _extract_ticket_id_from_subject(subject: str) -> int | None:
    if not subject:
        return None

    match = re.search(r"(?:Ticket\s*#\s*(\d+)|#\s*(\d+))", subject, re.IGNORECASE)
    if not match:
        return None

    value = match.group(1) or match.group(2)
    return int(value) if value and value.isdigit() else None


def _is_same_requester(ticket: Ticket, from_email: str) -> bool:
    sender = _normalize_email_address(from_email)
    requester_email = _normalize_email_address(getattr(getattr(ticket, "requester", None), "email", None))
    return bool(sender and requester_email and sender == requester_email)


def _has_reply_headers(msg) -> bool:
    if _norm_msgid(msg.get("In-Reply-To")):
        return True
    if _extract_reference_ids(msg.get("References")):
        return True
    return False


def _is_safe_subject_reticket_match(
    *,
    msg,
    body: str,
    support_mailboxes: set[str],
    ticket: Ticket,
    from_email: str,
) -> bool:
    # Candado fuerte: asunto con Ticket #N por sÃ­ solo NO basta.
    # Requiere remitente del solicitante + evidencia real de hilo.
    if not _is_same_requester(ticket, from_email):
        return False
    if _has_reply_headers(msg):
        return True
    if _content_mentions_ticket_thread(
        body or "",
        support_mailboxes=support_mailboxes,
        ticket_id=int(getattr(ticket, "id", 0) or 0),
    ):
        return True
    return False


def _extract_html_and_save_images(msg) -> str | None:
    html_body = None
    cid_map: dict[str, str] = {}

    for part in msg.walk():
        content_type = part.get_content_type()
        content_disposition = str(part.get("Content-Disposition") or "")

        if content_type == "text/html" and "attachment" not in content_disposition.lower():
            payload = part.get_payload(decode=True)
            if payload:
                html_body = payload.decode(errors="ignore")

        if not content_type.startswith("image/"):
            continue

        content_id = part.get("Content-ID")
        if not content_id:
            continue

        content_id = content_id.strip("<>")
        filename = _safe_attachment_filename(
            part.get_filename() or f"{content_id}",
            content_type=content_type,
            fallback_prefix=(content_id or "inline_image"),
        )

        payload = part.get_payload(decode=True)
        if not payload:
            continue

        filepath = os.path.join(UPLOAD_DIR, filename)
        with open(filepath, "wb") as file_obj:
            file_obj.write(payload)
        cid_map[content_id] = filename

    if html_body and cid_map:
        for cid, filename in cid_map.items():
            html_body = html_body.replace(f"cid:{cid}", f"/uploads/{filename}")

    return html_body


def _extract_body_text(msg) -> str:
    body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition") or "")
            if content_type == "text/plain" and "attachment" not in content_disposition.lower():
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode(errors="ignore")
                break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(errors="ignore")

    return body


def _extract_raw_email(msg_data) -> bytes | None:
    if not msg_data:
        return None

    for item in msg_data:
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def _normalize_email_address(value: str | None) -> str:
    return (parseaddr(value or "")[1] or "").strip().lower()


def _support_mailboxes() -> set[str]:
    mailboxes = {
        _normalize_email_address(settings.IMAP_USER),
        _normalize_email_address(settings.IMAP2_USER),
        _normalize_email_address(settings.SMTP_USER),
        _normalize_email_address(settings.SMTP_FROM),
    }
    return {item for item in mailboxes if item}


def _mailbox_key(user: str | None = None, folder: str | None = None) -> str:
    u = _normalize_email_address(user or settings.IMAP_USER)
    f = (folder or settings.IMAP_FOLDER or "INBOX").strip()
    return f"{u}:{f}"


def _parse_uid_validity(mail: imaplib.IMAP4_SSL) -> str | None:
    try:
        response = mail.response("UIDVALIDITY")
    except Exception:
        return None

    if not response or len(response) < 2:
        return None

    data = response[1]
    if not data:
        return None

    raw = data[0]
    if isinstance(raw, bytes):
        return raw.decode(errors="ignore").strip() or None
    if isinstance(raw, str):
        return raw.strip() or None
    return None


def _get_or_create_sync_state(db: Session, mailbox_key: str) -> EmailSyncState:
    state = db.get(EmailSyncState, mailbox_key)
    if state:
        return state

    state = EmailSyncState(mailbox_key=mailbox_key, last_uid=0)
    db.add(state)
    db.flush()
    return state


def _message_datetime(msg) -> datetime:
    raw_date = msg.get("Date")
    if raw_date:
        try:
            dt = parsedate_to_datetime(raw_date)
            if dt is not None:
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def _resolve_requester(db: Session, from_name: str, from_email: str) -> Requester:
    requester = (
        db.query(Requester)
        .filter(func.lower(Requester.email) == from_email)
        .first()
    )
    if requester:
        existing_name = requester.name or ""
        decoded_existing = _decode_mime_text(existing_name)
        if decoded_existing and decoded_existing != existing_name:
            requester.name = decoded_existing
        if from_name and (not requester.name or (requester.name or "").startswith("=?")):
            requester.name = from_name
        return requester

    requester = Requester(
        email=from_email,
        name=(from_name or from_email.split("@")[0]),
    )
    db.add(requester)
    db.flush()
    return requester


def _content_mentions_ticket_thread(
    content: str,
    *,
    support_mailboxes: set[str],
    ticket_id: int,
) -> bool:
    lowered = (content or "").lower()
    if not lowered:
        return False
    if ticket_id and f"ticket #{ticket_id}" in lowered:
        return True
    return any(mailbox and mailbox in lowered for mailbox in support_mailboxes)


def _strip_ticket_thread_tail(
    content: str,
    *,
    support_mailboxes: set[str],
    ticket_id: int,
) -> str:
    if not content:
        return content

    if not _content_mentions_ticket_thread(
        content,
        support_mailboxes=support_mailboxes,
        ticket_id=ticket_id,
    ):
        return content

    trimmed = content.strip()

    # Gmail/Webmail quoted blocks.
    trimmed = re.sub(
        r"(?is)<div[^>]*class=[\"'][^\"']*gmail_quote[^\"']*[\"'][^>]*>.*$",
        "",
        trimmed,
    ).strip()
    trimmed = re.sub(r"(?is)<blockquote\b.*$", "", trimmed).strip()

    quote_markers = [
        r"(?is)(?:<br\s*/?>|\n|\r)\s*el\s+.{0,500}?escribi(?:o|Ã³)\s*:",
        r"(?is)(?:<br\s*/?>|\n|\r)\s*on\s+.{0,500}?wrote\s*:",
        r"(?is)(?:<br\s*/?>|\n|\r)\s*from\s*:\s*.+",
        r"(?is)(?:<br\s*/?>|\n|\r)\s*-{2,}\s*(mensaje original|original message)\s*-{2,}",
    ]

    cut_index: int | None = None
    for pattern in quote_markers:
        match = re.search(pattern, trimmed)
        if not match:
            continue
        marker_index = match.start()
        if marker_index < 20:
            continue
        if cut_index is None or marker_index < cut_index:
            cut_index = marker_index

    if cut_index is not None:
        candidate = trimmed[:cut_index].strip()
        if candidate:
            trimmed = candidate

    return trimmed or content


def _resolve_ticket(
    db: Session,
    *,
    msg,
    subject: str,
    body: str,
    from_email: str,
    support_mailboxes: set[str],
    requester_id: int,
    message_dt: datetime,
) -> tuple[Ticket, bool]:
    ticket: Ticket | None = None

    ticket_id = _extract_ticket_id_from_headers(msg)
    if ticket_id:
        candidate = db.query(Ticket).filter(Ticket.id == ticket_id).first()
        if candidate and _is_same_requester(candidate, from_email):
            ticket = candidate

    if not ticket:
        possible_ids: list[str] = []
        in_reply_to = _norm_msgid(msg.get("In-Reply-To"))
        if in_reply_to:
            possible_ids.append(in_reply_to)
        possible_ids.extend(_extract_reference_ids(msg.get("References")))

        possible_ids = [item for item in possible_ids if item]
        if possible_ids:
            parent_msg = (
                db.query(Message)
                .filter(Message.external_id.in_(possible_ids))
                .order_by(Message.created_at.desc())
                .first()
            )
            if parent_msg:
                ticket = parent_msg.ticket

    if not ticket:
        ticket_id = _extract_ticket_id_from_subject(subject)
        if ticket_id:
            candidate = db.query(Ticket).filter(Ticket.id == ticket_id).first()
            if candidate and _is_safe_subject_reticket_match(
                msg=msg,
                body=body,
                support_mailboxes=support_mailboxes,
                ticket=candidate,
                from_email=from_email,
            ):
                ticket = candidate

    if ticket:
        if getattr(ticket, "status", None) == "resolved":
            # Reutilizamos la misma regla de reapertura
            # usada por el resto del sistema.
            apply_ticket_status_change(ticket, "open")
        return ticket, True

    ticket = Ticket(
        subject=subject or "Sin asunto",
        source="email",
        priority="",
        requester_id=requester_id,
        status="open",
        created_at=message_dt,
    )
    db.add(ticket)
    db.flush()
    return ticket, False


def fetch_emails_and_create_tickets(
    db: Session,
    limit: int = 100,
    *,
    imap_host: str | None = None,
    imap_port: int | None = None,
    imap_user: str | None = None,
    imap_password: str | None = None,
    imap_folder: str | None = None,
) -> dict:
    host     = imap_host     or settings.IMAP_HOST
    port     = imap_port     or settings.IMAP_PORT
    user     = imap_user     or settings.IMAP_USER
    password = imap_password or settings.IMAP_PASSWORD
    folder   = imap_folder   or settings.IMAP_FOLDER

    mail = imaplib.IMAP4_SSL(host, port)
    mail.login(user, password)

    status, _ = mail.select(folder)
    if status != "OK":
        mail.logout()
        return {"count": 0}

    mailbox_key = _mailbox_key(user, folder)
    is_new_mailbox = db.get(EmailSyncState, mailbox_key) is None
    sync_state = _get_or_create_sync_state(db, mailbox_key)
    uid_validity = _parse_uid_validity(mail)

    # Primera conexiÃ³n a este buzÃ³n: saltar historial, arrancar desde ahora
    if is_new_mailbox:
        _, uidnext_data = mail.status(folder, "(UIDNEXT)")
        try:
            raw = uidnext_data[0].decode() if isinstance(uidnext_data[0], bytes) else str(uidnext_data[0])
            import re as _re
            m = _re.search(r"UIDNEXT\s+(\d+)", raw)
            uidnext = int(m.group(1)) if m else 1
        except Exception:
            uidnext = 1
        sync_state.last_uid = max(uidnext - 1, 0)
        if uid_validity:
            sync_state.uid_validity = uid_validity
        db.commit()
        mail.logout()
        return {"count": 0, "skipped_initial_sync": True}

    if uid_validity and sync_state.uid_validity and sync_state.uid_validity != uid_validity:
        sync_state.last_uid = 0

    if uid_validity:
        sync_state.uid_validity = uid_validity
        db.commit()

    start_uid = max(int(sync_state.last_uid or 0) + 1, 1)
    search_status, data = mail.uid("search", None, f"UID {start_uid}:*")
    if search_status != "OK":
        mail.logout()
        return {"count": 0}

    raw_uid_list = data[0].split() if data and data[0] else []
    uid_values = sorted(
        {
            int(item.decode() if isinstance(item, bytes) else item)
            for item in raw_uid_list
            if str(item.decode() if isinstance(item, bytes) else item).strip()
        }
    )

    if limit > 0:
        uid_values = uid_values[:limit]

    processed = 0
    support_mailboxes = _support_mailboxes()

    for uid in uid_values:
        try:
            fetch_status, msg_data = mail.uid("fetch", str(uid), "(RFC822)")
            if fetch_status != "OK":
                raise RuntimeError(f"No se pudo descargar UID {uid}")

            raw_email = _extract_raw_email(msg_data)
            if not raw_email:
                raise RuntimeError(f"UID {uid} sin contenido RFC822")

            msg = email.message_from_bytes(raw_email)
            message_dt = _message_datetime(msg)
            message_id = _norm_msgid(msg.get("Message-ID"))

            if message_id:
                exists = db.query(Message).filter(Message.external_id == message_id).first()
                if exists:
                    sync_state.last_uid = uid
                    db.commit()
                    continue

            from_name, from_email = parseaddr(msg.get("From"))
            from_name = _decode_mime_text(from_name)
            from_email = (from_email or "").strip().lower()
            if not from_email:
                raise ValueError("Email sin remitente valido")

            if from_email in support_mailboxes:
                sync_state.last_uid = uid
                db.commit()
                continue

            subject = _decode_subject(msg)
            html_body = _extract_html_and_save_images(msg)
            if html_body:
                body = html_body
            else:
                body = _extract_body_text(msg).strip().replace("\n", "<br>")

            requester = _resolve_requester(db, from_name, from_email)
            ticket, ticket_exists = _resolve_ticket(
                db,
                msg=msg,
                subject=subject,
                body=body,
                from_email=from_email,
                support_mailboxes=support_mailboxes,
                requester_id=requester.id,
                message_dt=message_dt,
            )

            if ticket_exists and body:
                body = _strip_ticket_thread_tail(
                    body,
                    support_mailboxes=support_mailboxes,
                    ticket_id=ticket.id,
                )

            db.add(
                Message(
                    ticket_id=ticket.id,
                    sender_type="requester",
                    channel="email",
                    content=body,
                    external_id=message_id,
                    is_internal_note=False,
                    sender_name=(from_name or from_email.split("@")[0]).strip() or None,
                    sender_email=from_email,
                    created_at=message_dt,
                )
            )

            sync_state.last_uid = uid
            if uid_validity:
                sync_state.uid_validity = uid_validity

            db.commit()
            processed += 1
            print(f"Email procesado -> Ticket #{ticket.id} (UID {uid})")

        except Exception as exc:
            db.rollback()
            print(f"Error importando email UID {uid}: {exc}")
            try:
                sync_state = _get_or_create_sync_state(db, mailbox_key)
                sync_state.last_uid = uid
                if uid_validity:
                    sync_state.uid_validity = uid_validity
                db.commit()
            except Exception:
                db.rollback()
            continue

    mail.logout()
    return {"count": processed, "last_uid": sync_state.last_uid}

