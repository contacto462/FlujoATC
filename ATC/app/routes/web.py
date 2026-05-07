from __future__ import annotations

import html
import json
import base64
import binascii
import mimetypes
from decimal import Decimal

import re

import traceback
import unicodedata

from email.utils import parseaddr

from urllib.parse import urlencode
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Form, HTTPException, Query, File, UploadFile

from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from fastapi.templating import Jinja2Templates

from sqlalchemy.orm import Session

from sqlalchemy import (
    and_,
    or_,
    MetaData,
    Table,
    select,
    update,
    text,
    Column,
    BigInteger,
    Integer,
    String,
    DateTime,
    Text as SAText,
    Index,
    func,
    inspect as sa_inspect,
)

from sqlalchemy.orm import joinedload

from jose import JWTError, jwt

from uuid import uuid4

from markupsafe import Markup

from app.core.db import get_db, get_incidencias_db

from app.core.config import settings

from app.core.security import create_access_token, verify_password

from app.core.text import decode_mime_words
from app.services.ticket_status_service import apply_ticket_status_change, mark_first_agent_reply
from app.services.automation_service import RULE_EMAIL_AUTO_REPLY, send_initial_email_auto_reply
from app.services.drive_report_service import (
    create_drive_report_for_odt,
    upload_support_images_for_odt,
    DriveReportError,
)
from app.services.sla_feedback_service import (
    apply_ticket_sla_feedback,
    build_sla_feedback_token,
    build_configured_sla_survey_link,
    build_static_sla_survey_link,
    get_or_create_ticket_sla_feedback,
    verify_sla_feedback_token,
)

from app.models.ticket import Ticket

from app.models.message import Message

from app.models.internal_chat_message import InternalChatMessage

from app.models.internal_chat_read_state import InternalChatReadState

from app.models.ticket_alert_read_state import TicketAlertReadState
from app.models.ticket_message_read_state import TicketMessageReadState
from app.models.ticket_internal_note_read_state import TicketInternalNoteReadState

from app.models.user import User

from app.models.requester import Requester

from app.models.ticket_history import TicketAssignmentHistory
from app.models.automation_log import AutomationLog

from datetime import datetime, timezone, timedelta

router = APIRouter(tags=["web"])

templates = Jinja2Templates(directory="app/templates")

COOKIE_NAME = "access_token"
EMAIL_ATTACHMENT_UPLOAD_ROOT = Path("uploads") / "ticket_replies"
MAX_EMAIL_ATTACHMENTS = 10
MAX_EMAIL_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_EMAIL_TOTAL_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_EMAIL_INLINE_IMAGES = 20
_INLINE_DATA_IMAGE_RE = re.compile(
    r"(<img\b[^>]*?\bsrc\s*=\s*)([\"'])(data:image/[^\"']+)\2",
    flags=re.IGNORECASE | re.DOTALL,
)


def _norm_msgid(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().replace("\r", "").replace("\n", "")
    value = value.strip().strip("<>").strip()
    return value or None


def _build_ticket_email_subject(subject: str | None, ticket_id: int) -> str:
    base = (subject or "Sin asunto").strip() or "Sin asunto"
    if not re.search(rf"(?:Ticket\s*#\s*{ticket_id}|#\s*{ticket_id})", base, re.IGNORECASE):
        base = f"[Ticket #{ticket_id}] {base}"
    if not re.match(r"^\s*re\s*:", base, re.IGNORECASE):
        base = f"Re: {base}"
    return base


def _ticket_support_mailboxes() -> set[str]:
    values = {
        parseaddr(settings.IMAP_USER or "")[1].strip().lower(),
        parseaddr(settings.SMTP_USER or "")[1].strip().lower(),
        parseaddr(settings.SMTP_FROM or "")[1].strip().lower(),
    }
    return {value for value in values if value}


def _strip_ticket_thread_tail_for_display(content: str, *, ticket_id: int) -> str:
    text = (content or "").strip()
    if not text:
        return text

    lowered = text.lower()
    support_mailboxes = _ticket_support_mailboxes()
    has_thread_hint = (ticket_id and f"ticket #{ticket_id}" in lowered) or any(
        mailbox and mailbox in lowered for mailbox in support_mailboxes
    )
    if not has_thread_hint:
        return text

    trimmed = re.sub(
        r"(?is)<div[^>]*class=[\"'][^\"']*gmail_quote[^\"']*[\"'][^>]*>.*$",
        "",
        text,
    ).strip()
    trimmed = re.sub(r"(?is)<blockquote\b.*$", "", trimmed).strip()

    quote_markers = [
        r"(?is)(?:<br\s*/?>|\n|\r)\s*el\s+.{0,500}?escribi(?:o|ó)\s*:",
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

    return trimmed or text


def _parse_recipient_list(raw_value: str | None, *, field_name: str) -> list[str]:
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return []

    recipients: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[;,]", raw_value):
        candidate = token.strip()
        if not candidate:
            continue
        parsed_email = parseaddr(candidate)[1].strip()
        if not parsed_email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", parsed_email):
            raise ValueError(f"Direccion invalida en {field_name}: {candidate}")
        normalized = parsed_email.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        recipients.append(parsed_email)

    return recipients


def _format_size_for_humans(size_bytes: int) -> str:
    size = max(0, int(size_bytes or 0))
    units = ["B", "KB", "MB", "GB"]
    unit_idx = 0
    value = float(size)
    while value >= 1024 and unit_idx < len(units) - 1:
        value /= 1024
        unit_idx += 1
    if unit_idx == 0:
        return f"{int(value)} {units[unit_idx]}"
    return f"{value:.1f} {units[unit_idx]}"


def _sanitize_upload_filename(filename: str) -> str:
    original = Path(filename or "").name.replace("\x00", "")
    original = re.sub(r"\s+", " ", original).strip()
    if not original:
        return "archivo"

    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", original).strip(" .")
    cleaned = cleaned or "archivo"

    stem = Path(cleaned).stem or "archivo"
    suffix = Path(cleaned).suffix[:20]
    stem = stem[:80]
    return f"{stem}{suffix}"


def _save_email_attachments(
    *,
    ticket_id: int,
    uploads: list[UploadFile] | None,
) -> list[dict[str, str | int]]:
    files = [item for item in (uploads or []) if item and (item.filename or "").strip()]
    if not files:
        return []

    if len(files) > MAX_EMAIL_ATTACHMENTS:
        raise ValueError(f"Solo se permiten hasta {MAX_EMAIL_ATTACHMENTS} archivos por envio.")

    ticket_folder = EMAIL_ATTACHMENT_UPLOAD_ROOT / f"T{ticket_id}"
    ticket_folder.mkdir(parents=True, exist_ok=True)

    total_size = 0
    saved_paths: list[Path] = []
    saved_files: list[dict[str, str | int]] = []

    try:
        for upload in files:
            safe_name = _sanitize_upload_filename(upload.filename or "archivo")
            unique_prefix = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            unique_name = f"{unique_prefix}_{uuid4().hex[:10]}_{safe_name}"
            destination = ticket_folder / unique_name

            file_size = 0
            try:
                with destination.open("wb") as out_file:
                    while True:
                        chunk = upload.file.read(1024 * 1024)
                        if not chunk:
                            break
                        file_size += len(chunk)
                        total_size += len(chunk)

                        if file_size > MAX_EMAIL_ATTACHMENT_BYTES:
                            raise ValueError(
                                f"El archivo '{safe_name}' supera el maximo permitido de 25 MB."
                            )
                        if total_size > MAX_EMAIL_TOTAL_ATTACHMENT_BYTES:
                            raise ValueError(
                                "La suma de adjuntos supera 25 MB. Reduce la cantidad o el peso."
                            )

                        out_file.write(chunk)
            except Exception:
                destination.unlink(missing_ok=True)
                raise

            if file_size == 0:
                destination.unlink(missing_ok=True)
                continue

            saved_paths.append(destination)
            content_type = (upload.content_type or "").strip().lower()
            if "/" not in content_type:
                guessed_type, _ = mimetypes.guess_type(safe_name)
                content_type = guessed_type or "application/octet-stream"

            saved_files.append(
                {
                    "path": str(destination),
                    "filename": safe_name,
                    "content_type": content_type,
                    "size": file_size,
                    "public_url": f"/uploads/ticket_replies/T{ticket_id}/{unique_name}",
                }
            )
    except Exception:
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise
    finally:
        for upload in files:
            try:
                upload.file.close()
            except Exception:
                pass

    return saved_files


def _inline_image_extension(content_type: str) -> str:
    normalized = (content_type or "").strip().lower()
    fallback = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
        "image/bmp": "bmp",
        "image/tiff": "tif",
        "image/svg+xml": "svg",
    }
    if normalized in fallback:
        return fallback[normalized]

    guessed = mimetypes.guess_extension(normalized, strict=False) or ""
    guessed = guessed.strip().lstrip(".").lower()
    if guessed:
        return guessed
    if "/" in normalized:
        return re.sub(r"[^a-z0-9]+", "", normalized.split("/", 1)[1].lower()) or "png"
    return "png"


def _extract_inline_data_images(
    *,
    ticket_id: int,
    html_content: str,
    initial_total_bytes: int = 0,
) -> tuple[str, str, list[dict[str, str]], list[Path]]:
    content = (html_content or "").strip()
    if not content or "data:image/" not in content.lower():
        return content, content, [], []

    ticket_folder = EMAIL_ATTACHMENT_UPLOAD_ROOT / f"T{ticket_id}"
    ticket_folder.mkdir(parents=True, exist_ok=True)

    total_bytes = max(0, int(initial_total_bytes or 0))
    inline_count = 0
    saved_paths: list[Path] = []
    inline_images: list[dict[str, str]] = []

    email_parts: list[str] = []
    db_parts: list[str] = []
    last_index = 0

    try:
        for match in _INLINE_DATA_IMAGE_RE.finditer(content):
            email_parts.append(content[last_index:match.start()])
            db_parts.append(content[last_index:match.start()])

            prefix = match.group(1)
            quote = match.group(2)
            data_url = match.group(3)

            replacement_email = f"{prefix}{quote}{data_url}{quote}"
            replacement_db = replacement_email

            parsed = re.match(
                r"^data:([^;]+);base64,(.+)$",
                data_url,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if parsed:
                content_type = (parsed.group(1) or "").strip().lower()
                encoded = re.sub(r"\s+", "", parsed.group(2) or "")
                if content_type.startswith("image/") and encoded:
                    try:
                        image_bytes = base64.b64decode(encoded, validate=False)
                    except (ValueError, binascii.Error) as exc:
                        raise ValueError(
                            "No se pudo procesar una imagen pegada. Pegala nuevamente y reintenta."
                        ) from exc

                    if image_bytes:
                        inline_count += 1
                        if inline_count > MAX_EMAIL_INLINE_IMAGES:
                            raise ValueError(
                                f"Solo se permiten hasta {MAX_EMAIL_INLINE_IMAGES} imagenes pegadas por envio."
                            )

                        image_size = len(image_bytes)
                        if image_size > MAX_EMAIL_ATTACHMENT_BYTES:
                            raise ValueError(
                                "Una imagen pegada supera el maximo permitido de 25 MB."
                            )

                        total_bytes += image_size
                        if total_bytes > MAX_EMAIL_TOTAL_ATTACHMENT_BYTES:
                            raise ValueError(
                                "La suma de imagenes y adjuntos supera 25 MB. Reduce el peso o la cantidad."
                            )

                        ext = _inline_image_extension(content_type)
                        unique_prefix = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                        unique_name = f"{unique_prefix}_{uuid4().hex[:10]}_inline_{inline_count}.{ext}"
                        destination = ticket_folder / unique_name
                        with destination.open("wb") as out_file:
                            out_file.write(image_bytes)
                        saved_paths.append(destination)

                        cid = f"ticket{ticket_id}.inline.{uuid4().hex}@atc.local"
                        public_url = f"/uploads/ticket_replies/T{ticket_id}/{unique_name}"
                        inline_images.append(
                            {
                                "cid": cid,
                                "path": str(destination),
                            }
                        )
                        replacement_email = f"{prefix}{quote}cid:{cid}{quote}"
                        replacement_db = f"{prefix}{quote}{public_url}{quote}"

            email_parts.append(replacement_email)
            db_parts.append(replacement_db)
            last_index = match.end()

        email_parts.append(content[last_index:])
        db_parts.append(content[last_index:])
        return "".join(email_parts), "".join(db_parts), inline_images, saved_paths
    except Exception:
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise


def _build_attachments_html(attachments: list[dict[str, str | int]]) -> str:
    if not attachments:
        return ""

    rows: list[str] = []
    for item in attachments:
        filename = html.escape(str(item.get("filename") or "archivo"))
        public_url = html.escape(str(item.get("public_url") or "#"))
        size_label = _format_size_for_humans(int(item.get("size") or 0))
        rows.append(
            (
                "<li>"
                f"<a href=\"{public_url}\" target=\"_blank\" rel=\"noopener\">{filename}</a>"
                f" <span style=\"color:#64748b;\">({size_label})</span>"
                "</li>"
            )
        )

    return (
        "<div style=\"margin-top:14px;padding:10px 12px;border:1px solid #dbeafe;"
        "border-radius:10px;background:#f8fbff;\">"
        "<div style=\"font-size:12px;font-weight:700;color:#1e40af;margin-bottom:6px;\">"
        "Adjuntos enviados"
        "</div>"
        "<ul style=\"margin:0;padding-left:18px;\">"
        + "".join(rows)
        + "</ul></div>"
    )


def _has_reception_sent(db: Session, ticket_id: int) -> bool:
    # La "recepcion de solicitud" se considera enviada cuando existe
    # log de automatizacion exitoso de la regla email_auto_reply.
    return (
        db.query(AutomationLog)
        .filter(
            AutomationLog.ticket_id == ticket_id,
            AutomationLog.rule_key == RULE_EMAIL_AUTO_REPLY,
            AutomationLog.status == "ok",
        )
        .first()
        is not None
    )

def _get_internal_chat_unread_count(db: Session, user_id: int) -> int:

    read_state = db.get(InternalChatReadState, user_id)

    last_seen_message_id = (

        read_state.last_seen_message_id

        if read_state and read_state.last_seen_message_id

        else 0

    )

    return (

        db.query(InternalChatMessage)

        .filter(

            InternalChatMessage.id > last_seen_message_id,

            or_(

                InternalChatMessage.sender_id.is_(None),

                InternalChatMessage.sender_id != user_id,

            ),

        )

        .count()

    )

def _mark_internal_chat_as_read(

    db: Session,

    user_id: int,

    last_message_id: int | None,

) -> int:

    safe_last_message_id = max(0, int(last_message_id or 0))

    read_state = db.get(InternalChatReadState, user_id)

    if read_state is None:

        read_state = InternalChatReadState(

            user_id=user_id,

            last_seen_message_id=safe_last_message_id,

        )

        db.add(read_state)

    elif safe_last_message_id > (read_state.last_seen_message_id or 0):

        read_state.last_seen_message_id = safe_last_message_id

    db.commit()

    return _get_internal_chat_unread_count(db, user_id)

def _get_latest_active_ticket_id(db: Session) -> int:

    latest_row = (

        db.query(Ticket.id)

        .filter(

            Ticket.is_deleted == False,

            Ticket.is_spam == False,

        )

        .order_by(Ticket.id.desc())

        .first()

    )

    return int(latest_row[0]) if latest_row else 0

def _get_ticket_alert_unread_count(db: Session, user_id: int) -> int:

    read_state = db.get(TicketAlertReadState, user_id)

    if read_state is None:

        # Primer uso: tomar estado actual como "leido" para no mostrar backlog historico.

        read_state = TicketAlertReadState(

            user_id=user_id,

            last_seen_ticket_id=_get_latest_active_ticket_id(db),

        )

        db.add(read_state)

        db.commit()

        return 0

    last_seen_ticket_id = max(0, int(read_state.last_seen_ticket_id or 0))

    return (

        db.query(Ticket)

        .filter(

            Ticket.id > last_seen_ticket_id,

            Ticket.is_deleted == False,

            Ticket.is_spam == False,

        )

        .count()

    )

def _mark_ticket_alerts_as_read(

    db: Session,

    user_id: int,

    last_ticket_id: int | None = None,

) -> int:

    safe_last_ticket_id = max(

        0,

        int(last_ticket_id or _get_latest_active_ticket_id(db)),

    )

    read_state = db.get(TicketAlertReadState, user_id)

    if read_state is None:

        read_state = TicketAlertReadState(

            user_id=user_id,

            last_seen_ticket_id=safe_last_ticket_id,

        )

        db.add(read_state)

    elif safe_last_ticket_id > (read_state.last_seen_ticket_id or 0):

        read_state.last_seen_ticket_id = safe_last_ticket_id

    db.commit()

    return _get_ticket_alert_unread_count(db, user_id)

def assign_ticket_logic(db: Session, ticket: Ticket, new_user_id: int | None, changed_by: User):

    old_user_id = ticket.assigned_to_id

    # Si no cambiÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³ el usuario, no hacemos nada

    if old_user_id == new_user_id:

        return

    # Guardar historial

    history = TicketAssignmentHistory(

        ticket_id=ticket.id,

        from_user_id=old_user_id,

        to_user_id=new_user_id,

        changed_by_id=changed_by.id

    )

    db.add(history)

    # Actualizar asignaciÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³n

    ticket.assigned_to_id = new_user_id


def _send_sla_satisfaction_email(ticket: Ticket) -> None:

    requester = ticket.requester

    requester_email = requester.email if requester else None

    if not requester_email:

        return

    # No enviar encuesta para tickets internos.

    if (ticket.source or "").strip().lower() == "internal":

        return

    requester_name = (requester.name if requester and requester.name else "Cliente").strip() or "Cliente"
    safe_name = html.escape(requester_name)

    ticket_id = ticket.id
    subject = f"Encuesta de satisfaccion SLA - Ticket #{ticket_id}"
    logo_cid = "logo-atc"
    survey_link = build_configured_sla_survey_link(
        ticket_id=ticket_id,
        requester_name=requester_name,
    ) or build_static_sla_survey_link(
        ticket_id=ticket_id,
        requester_name=requester_name,
    )

    body = f"""
    <div style="margin:0;padding:24px;background:#f8fafc;">
      <div style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:24px;overflow:hidden;font-family:Arial,sans-serif;color:#0f172a;">
        <div style="padding:24px 28px 20px;background:linear-gradient(135deg,#0f172a 0%,#1d4ed8 100%);color:#ffffff;">
          <table role="presentation" cellspacing="0" cellpadding="0" width="100%" style="border-collapse:collapse;">
            <tr>
              <td style="vertical-align:top;padding-right:16px;">
                <div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;opacity:.82;">Soporte ATC</div>
                <h1 style="margin:10px 0 0;font-size:28px;line-height:1.2;">Encuesta de satisfaccion</h1>
                <p style="margin:10px 0 0;font-size:15px;line-height:1.6;opacity:.92;">Ticket #{ticket_id}</p>
              </td>
              <td align="right" style="vertical-align:top;">
                <img src="cid:{logo_cid}" alt="ATC" style="display:block;width:110px;max-width:110px;height:auto;">
              </td>
            </tr>
          </table>
        </div>

        <div style="padding:28px;">
          <p style="margin:0 0 16px;font-size:16px;line-height:1.7;">Hola {safe_name},</p>
          <p style="margin:0 0 14px;font-size:16px;line-height:1.7;">Su solicitud ya fue resuelta con exito.</p>
          <p style="margin:0 0 22px;font-size:16px;line-height:1.7;">Para nosotros su opinion es muy valiosa. Queremos seguir entregandole una atencion cercana, agil y de calidad, porque usted es una parte muy importante de ATC.</p>
          <div style="padding:22px;border:1px solid #dbe5f3;border-radius:18px;background:#f8fbff;text-align:center;">
            <p style="margin:0 0 14px;font-size:15px;line-height:1.7;color:#334155;">Responda su encuesta en la pagina corporativa de ATC. Encontrara solo estas 2 preguntas:</p>
            <p style="margin:0 0 6px;font-size:15px;line-height:1.7;"><b>1.</b> La atencion del tecnico fue buena</p>
            <p style="margin:0 0 20px;font-size:15px;line-height:1.7;"><b>2.</b> El tiempo de resolucion le parecio satisfactorio</p>
            <a href="{survey_link}" style="display:inline-block;padding:14px 24px;border-radius:14px;background:#1d4ed8;color:#ffffff;text-decoration:none;font-size:16px;font-weight:700;">Responder Encuesta</a>
          </div>

          <p style="margin:18px 0 0;font-size:13px;line-height:1.7;color:#64748b;">Si el boton no abre automaticamente, copie y pegue este enlace en su navegador:</p>
          <p style="margin:8px 0 0;font-size:12px;line-height:1.6;word-break:break-all;color:#1d4ed8;">{html.escape(survey_link)}</p>

          <p style="margin:22px 0 0;font-size:15px;line-height:1.7;">Gracias por ser parte de ATC.</p>
          <p style="margin:8px 0 0;font-size:15px;line-height:1.7;">Equipo Soporte ATC</p>
        </div>
      </div>
    </div>
    """

    from app.integrations.email_smtp import send_email_reply

    send_email_reply(

        to=requester_email,

        subject=subject,

        body=body,

        ticket_id=ticket.id,

        inline_images=[
            {
                "cid": logo_cid,
                "path": "static/img/logo-atc.png",
            }
        ],

    )


@router.get("/encuesta/{ticket_id}", response_class=HTMLResponse)
def corporate_sla_feedback_page(
    request: Request,
    ticket_id: int,
    token: str = Query(...),
    rating: int | None = Query(None, ge=1, le=5),
    resolved: str | None = Query(None),
    db: Session = Depends(get_db),
):
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    if not verify_sla_feedback_token(token, ticket_id):
        raise HTTPException(status_code=403, detail="Token invalido")

    resolved_value: bool | None = None
    if resolved is not None:
        lowered = resolved.strip().lower()
        if lowered in {"si", "sÃƒÆ’Ã‚Â­", "yes", "true", "1"}:
            resolved_value = True
        elif lowered in {"no", "false", "0"}:
            resolved_value = False
        else:
            raise HTTPException(status_code=400, detail="Respuesta invalida")

    if rating is not None or resolved_value is not None:
        feedback = apply_ticket_sla_feedback(
            db,
            ticket_id=ticket_id,
            rating=rating,
            resolved=resolved_value,
        )
    else:
        feedback = get_or_create_ticket_sla_feedback(db, ticket_id)

    fresh_token = build_sla_feedback_token(ticket_id)

    return templates.TemplateResponse(
        "public_sla_feedback.html",
        {
            "request": request,
            "ticket": ticket,
            "feedback": feedback,
            "rating_links": {
                value: build_sla_feedback_link(ticket_id=ticket_id, token=fresh_token, rating=value)
                for value in range(1, 6)
            },
            "resolved_yes_link": build_sla_feedback_link(ticket_id=ticket_id, token=fresh_token, resolved="si"),
            "resolved_no_link": build_sla_feedback_link(ticket_id=ticket_id, token=fresh_token, resolved="no"),
            "is_complete": (
                feedback.technician_rating is not None
                and feedback.resolution_satisfied is not None
            ),
        },
    )

# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒâ€šÃ‚Â AUTH helpers (cookie-based para HTML)

# ======================================================

def _decode_cookie_token(token: str) -> str:

    try:

        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])

        username = payload.get("sub")

        if not username:

            raise ValueError("Token sin sub")

        return username

    except (JWTError, ValueError):

        raise HTTPException(status_code=401, detail="Token invÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¡lido")

def get_current_user_web(

    request: Request,

    db: Session = Depends(get_db),

) -> User:

    token = request.cookies.get(COOKIE_NAME)

    if not token:

        raise HTTPException(status_code=401, detail="No autenticado")

    username = _decode_cookie_token(token)

    user = db.query(User).filter(User.username == username).first()

    if not user or not user.is_active:

        raise HTTPException(status_code=401, detail="No autenticado")

    return user

def require_admin_web(current_user: User = Depends(get_current_user_web)) -> User:

    if not current_user.is_admin:

        raise HTTPException(status_code=403, detail="No tienes permisos de administrador")

    return current_user

def redirect_to_login() -> RedirectResponse:

    return RedirectResponse(url="/login", status_code=303)

# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸Ãƒâ€šÃ‚ÂÃƒâ€šÃ‚Â  Home -> login

# ======================================================

@router.get("/", include_in_schema=False)

def home():

    return RedirectResponse(url="/login", status_code=302)

# ======================================================

# ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ LOGIN (HTML)

# ======================================================

@router.get("/login", response_class=HTMLResponse)

def login_page(request: Request, db: Session = Depends(get_db)):

    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            username = _decode_cookie_token(token)
            user = db.query(User).filter(User.username == username).first()
            if user and user.is_active:
                return RedirectResponse(url="/panel", status_code=303)
        except Exception:
            pass

    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@router.post("/web/login")

def web_login(

    request: Request,

    username: str = Form(...),

    password: str = Form(...),

    db: Session = Depends(get_db),

):

    user = db.query(User).filter(User.username == username).first()

    if not user or not user.is_active or not verify_password(password, user.hashed_password):

        # vuelve al form con error

        return templates.TemplateResponse(

            "login.html",

            {"request": request, "error": "Usuario o contraseÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â±a incorrectos"},

            status_code=401,

        )

    token = create_access_token({"sub": user.username})

    # Redirigir segÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Âºn rol

    redirect_to = "/panel"

    resp = RedirectResponse(url=redirect_to, status_code=303)

    # En localhost sin https -> secure=False

    resp.set_cookie(

        key=COOKIE_NAME,

        value=token,

        httponly=True,

        samesite="lax",

        secure=False,

        max_age=settings.JWT_EXPIRES_MIN * 60,

    )

    return resp

@router.get("/logout")

def logout():

    resp = RedirectResponse(url="/login", status_code=303)

    resp.delete_cookie(COOKIE_NAME)

    return resp


@router.get("/panel", response_class=HTMLResponse)
def launcher_panel(
    request: Request,
    current_user: User = Depends(get_current_user_web),
):
    return templates.TemplateResponse(
        "panel_selector.html",
        {
            "request": request,
            "user": current_user,
        },
    )

# ======================================================

# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‹Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‹Å“ DASHBOARD PRINCIPAL (SOLO ADMIN)

# ======================================================

# ======================================================

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
    status: str | None = None,
    q: str | None = None,
    user_filter: str | None = None,
    source: str | None = None,
    priority: str | None = None,
    scope: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
):
    view = request.query_params.get("view")

    allowed_scopes = {"all", "open", "pending", "resolved", "spam", "trash"}
    allowed_sources = {"all", "email", "whatsapp", "internal"}
    allowed_priorities = {"all", "unassigned", "low", "medium", "high", "urgent"}

    date_from_value = (date_from or "").strip()
    date_to_value = (date_to or "").strip()
    users = db.query(User).filter(User.is_active == True).order_by(User.name.asc()).all()
    valid_user_ids = {str(u.id) for u in users}

    # AJUSTE DASHBOARD FILTROS MULTISELECT #
    def _normalize_multi_values(values: list[str], *, allowed: set[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = (value or "").strip().lower()
            if not item:
                continue
            if item in allowed and item not in cleaned:
                cleaned.append(item)
        if not cleaned:
            return ["all"]
        if "all" in cleaned:
            return ["all"]
        return cleaned

    def _normalize_user_values(values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = (value or "").strip()
            if not item:
                continue
            lowered = item.lower()
            if lowered == "all":
                return ["all"]
            if lowered == "unassigned":
                if "unassigned" not in cleaned:
                    cleaned.append("unassigned")
                continue
            if item in valid_user_ids and item not in cleaned:
                cleaned.append(item)
        return cleaned or ["all"]

    raw_scope_filters = [value for value in request.query_params.getlist("scope") if (value or "").strip()]
    if not raw_scope_filters:
        legacy_scope = (scope or "").strip().lower()
        if legacy_scope:
            raw_scope_filters = [legacy_scope]
        elif view == "spam":
            raw_scope_filters = ["spam"]
        elif view == "trash":
            raw_scope_filters = ["trash"]
        elif status in {"open", "pending", "resolved"}:
            raw_scope_filters = [status]
        else:
            raw_scope_filters = ["all"]

    raw_source_filters = [value for value in request.query_params.getlist("source") if (value or "").strip()]
    if not raw_source_filters and source:
        raw_source_filters = [source]

    raw_priority_filters = [value for value in request.query_params.getlist("priority") if (value or "").strip()]
    if not raw_priority_filters and priority:
        raw_priority_filters = [priority]

    raw_user_filters = [value for value in request.query_params.getlist("user_filter") if (value or "").strip()]
    if not raw_user_filters and user_filter:
        raw_user_filters = [user_filter]

    scope_filters = _normalize_multi_values(raw_scope_filters, allowed=allowed_scopes)
    source_filters = _normalize_multi_values(raw_source_filters or ["all"], allowed=allowed_sources)
    priority_filters = _normalize_multi_values(raw_priority_filters or ["all"], allowed=allowed_priorities)
    user_filters = _normalize_user_values(raw_user_filters or ["all"])

    scope_filter = scope_filters[0] if len(scope_filters) == 1 else "all"
    source_filter = source_filters[0] if source_filters != ["all"] and len(source_filters) == 1 else None
    priority_filter = priority_filters[0] if priority_filters != ["all"] and len(priority_filters) == 1 else None
    user_filter_value = user_filters[0] if user_filters != ["all"] and len(user_filters) == 1 else ""

    def _parse_filter_date(value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None

    date_from_dt = _parse_filter_date(date_from_value)
    date_to_dt = _parse_filter_date(date_to_value)

    query = db.query(Ticket)

    # AJUSTE DASHBOARD FILTROS MULTISELECT #
    if scope_filters == ["all"]:
        query = query.filter(
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
        )
    else:
        scope_clauses = []
        selected_statuses: list[str] = []
        if "open" in scope_filters:
            selected_statuses.append("open")
        if "pending" in scope_filters:
            selected_statuses.extend(PENDING_TICKET_STATUSES)
        if "resolved" in scope_filters:
            selected_statuses.append("resolved")
        if selected_statuses:
            scope_clauses.append(
                and_(
                    Ticket.is_deleted == False,
                    Ticket.is_spam == False,
                    Ticket.status.in_(selected_statuses),
                )
            )
        if "spam" in scope_filters:
            scope_clauses.append(
                and_(
                    Ticket.is_spam == True,
                    Ticket.is_deleted == False,
                )
            )
        if "trash" in scope_filters:
            scope_clauses.append(Ticket.is_deleted == True)
        if scope_clauses:
            query = query.filter(or_(*scope_clauses))

    if q:
        query = query.filter(Ticket.subject.ilike(f"%{q}%"))

    if user_filters != ["all"]:
        user_clauses = []
        selected_user_ids = [int(value) for value in user_filters if value.isdigit()]
        if selected_user_ids:
            user_clauses.append(Ticket.assigned_to_id.in_(selected_user_ids))
        if "unassigned" in user_filters:
            user_clauses.append(Ticket.assigned_to_id.is_(None))
        if user_clauses:
            query = query.filter(or_(*user_clauses))

    if source_filters != ["all"]:
        query = query.filter(Ticket.source.in_(source_filters))
    if priority_filters != ["all"]:
        priority_clauses = []
        selected_priorities = [value for value in priority_filters if value in {"low", "medium", "high", "urgent"}]
        if selected_priorities:
            priority_clauses.append(Ticket.priority.in_(selected_priorities))
        if "unassigned" in priority_filters:
            priority_clauses.append(or_(Ticket.priority.is_(None), Ticket.priority == ""))
        if priority_clauses:
            query = query.filter(or_(*priority_clauses))
    if date_from_dt:
        query = query.filter(Ticket.created_at >= date_from_dt)
    if date_to_dt:
        query = query.filter(Ticket.created_at < (date_to_dt + timedelta(days=1)))

    page_size = 30
    safe_page = max(1, page)
    total_filtered = query.count()
    total_pages = max(1, (total_filtered + page_size - 1) // page_size)

    if safe_page > total_pages:
        safe_page = total_pages

    tickets = (
        query.order_by(Ticket.created_at.desc())
        .offset((safe_page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    ticket_has_new_message: dict[int, bool] = {}
    ticket_unseen_message_id: dict[int, int] = {}
    ticket_unseen_message_count: dict[int, int] = {}
    ticket_ids = [t.id for t in tickets]
    if ticket_ids:
        read_rows = (
            db.query(
                TicketMessageReadState.ticket_id,
                TicketMessageReadState.last_seen_message_id,
            )
            .filter(
                TicketMessageReadState.user_id == current_user.id,
                TicketMessageReadState.ticket_id.in_(ticket_ids),
            )
            .all()
        )
        seen_by_ticket = {int(r.ticket_id): int(r.last_seen_message_id or 0) for r in read_rows}

        for ticket_id in ticket_ids:
            last_seen = seen_by_ticket.get(int(ticket_id), 0)
            first_unseen = (
                db.query(Message.id)
                .filter(
                    Message.ticket_id == ticket_id,
                    Message.id > last_seen,
                )
                .order_by(Message.id.asc())
                .first()
            )
            if first_unseen and first_unseen[0]:
                ticket_unseen_message_id[int(ticket_id)] = int(first_unseen[0])
            unseen_count = (
                db.query(func.count(Message.id))
                .filter(
                    Message.ticket_id == ticket_id,
                    Message.is_internal_note == False,
                    Message.id > last_seen,
                    or_(Message.sender_id.is_(None), Message.sender_id != current_user.id),
                )
                .scalar()
                or 0
            )
            if unseen_count > 0:
                ticket_unseen_message_count[int(ticket_id)] = int(unseen_count)
            ticket_has_new_message[int(ticket_id)] = unseen_count > 0

    for t in tickets:
        _normalize_requester_name(t.requester)

    page_start = ((safe_page - 1) * page_size + 1) if total_filtered > 0 else 0
    page_end = min(safe_page * page_size, total_filtered) if total_filtered > 0 else 0

    def build_dashboard_url(
        *,
        scope_values: list[str] | None = None,
        source_values: list[str] | None = None,
        priority_values: list[str] | None = None,
        user_values: list[str] | None = None,
        date_from_value_override: str | None = date_from_value,
        date_to_value_override: str | None = date_to_value,
        page_number: int | None = None,
    ) -> str:
        params: list[tuple[str, str | int]] = []

        active_scope_values = scope_values if scope_values is not None else scope_filters
        active_source_values = source_values if source_values is not None else source_filters
        active_priority_values = priority_values if priority_values is not None else priority_filters
        active_user_values = user_values if user_values is not None else user_filters

        for value in active_scope_values:
            if value and value != "all":
                params.append(("scope", value))
        if q:
            params.append(("q", q))
        for value in active_user_values:
            if value and value != "all":
                params.append(("user_filter", value))
        for value in active_source_values:
            if value and value != "all":
                params.append(("source", value))
        for value in active_priority_values:
            if value and value != "all":
                params.append(("priority", value))
        if date_from_value_override:
            params.append(("date_from", date_from_value_override))
        if date_to_value_override:
            params.append(("date_to", date_to_value_override))
        if page_number is not None:
            params.append(("page", page_number))

        if not params:
            return "/dashboard"
        return f"/dashboard?{urlencode(params)}"

    prev_page_url = build_dashboard_url(page_number=safe_page - 1) if safe_page > 1 else None
    next_page_url = build_dashboard_url(page_number=safe_page + 1) if safe_page < total_pages else None
    first_page_url = build_dashboard_url(page_number=1) if safe_page > 1 else None
    last_page_url = build_dashboard_url(page_number=total_pages) if safe_page < total_pages else None

    counts = {
        "all": db.query(Ticket).filter(
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
        ).count(),
        "open": db.query(Ticket).filter(
            Ticket.status == "open",
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
        ).count(),
        "pending": db.query(Ticket).filter(
            Ticket.status.in_(PENDING_TICKET_STATUSES),
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
        ).count(),
        "resolved": db.query(Ticket).filter(
            Ticket.status == "resolved",
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
        ).count(),
        "spam": db.query(Ticket).filter(
            Ticket.is_spam == True,
            Ticket.is_deleted == False,
        ).count(),
        "trash": db.query(Ticket).filter(
            Ticket.is_deleted == True,
        ).count(),
    }

    internal_chat_unread_count = _get_internal_chat_unread_count(db, current_user.id)
    ticket_alert_unread_count = _mark_ticket_alerts_as_read(db, current_user.id)

    pending_incidencias: list[dict[str, str]] = []
    pending_incidencias_count = 0
    try:
        _inc_table, incidencias_rows = _support_query_incidencias(incidencias_db)
        pending_tokens = {"pendiente"}
        for row in incidencias_rows:
            derivacion_value = _support_text(_support_pick(row, "derivacion")).casefold()
            if derivacion_value not in pending_tokens:
                continue
            pending_incidencias_count += 1
            if len(pending_incidencias) >= 6:
                continue
            pending_incidencias.append(
                {
                    "id": str(_support_pick(row, "id") or ""),
                    "odt": _support_text(_support_pick(row, "odt")) or f"#{_support_pick(row, 'id')}",
                    "sucursal": _support_text(_support_pick(row, "sucursal", "cliente", "puesto")) or "Sin sucursal",
                    "tipo_problema": _support_text(_support_pick(row, "problema", "tipo_incidencia", "descripcion"))
                    or "Sin tipo de problema",
                    "estado": _support_text(_support_pick(row, "estado")) or "Pendiente",
                    "fecha": _support_format_radar_date(_support_pick(row, "fecha", "fecha_registro")),
                }
            )
    except Exception:
        pending_incidencias = []
        pending_incidencias_count = 0

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": current_user,
            "tickets": tickets,
            "ticket_has_new_message": ticket_has_new_message,
            "ticket_unseen_message_id": ticket_unseen_message_id,
            "ticket_unseen_message_count": ticket_unseen_message_count,
            "status": scope_filter if scope_filter in {"open", "pending", "resolved"} else None,
            "scope_filter": scope_filter,
            "scope_filters": scope_filters,
            "q": q or "",
            "counts": counts,
            "users": users,
            "user_filter": user_filter_value,
            "user_filters": user_filters,
            "source_filter": source_filter,
            "source_filters": source_filters,
            "priority_filter": priority_filter,
            "priority_filters": priority_filters,
            "date_from": date_from_value,
            "date_to": date_to_value,
            "internal_chat_unread_count": internal_chat_unread_count,
            "ticket_alert_unread_count": ticket_alert_unread_count,
            "pending_incidencias": pending_incidencias,
            "pending_incidencias_count": pending_incidencias_count,
            "current_page": safe_page,
            "total_pages": total_pages,
            "page_size": page_size,
            "total_filtered": total_filtered,
            "page_start": page_start,
            "page_end": page_end,
            "prev_page_url": prev_page_url,
            "next_page_url": next_page_url,
            "first_page_url": first_page_url,
            "last_page_url": last_page_url,
        },
    )

@router.get("/etapa", response_class=HTMLResponse)
def etapa_board(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
    q: str | None = None,
):
    users = db.query(User).filter(User.is_active == True).order_by(User.name.asc()).all()

    query = db.query(Ticket).options(
        joinedload(Ticket.requester),
        joinedload(Ticket.assigned_to),
    )

    search_value = (q or "").strip()
    if search_value:
        query = query.filter(Ticket.subject.ilike(f"%{search_value}%"))

    tickets = query.order_by(Ticket.updated_at.desc(), Ticket.id.desc()).all()

    stage_order = ["open", "pending", "resolved", "spam", "papelera"]
    stage_labels = {
        "open": "Open",
        "pending": "Pendiente",
        "resolved": "Cerrado",
        "spam": "Spam",
        "papelera": "Papelera",
    }
    stage_tickets: dict[str, list[Ticket]] = {stage: [] for stage in stage_order}

    for ticket in tickets:
        _normalize_requester_name(ticket.requester)
        stage = _ticket_stage(ticket)
        if stage not in stage_tickets:
            stage = "open"
        stage_tickets[stage].append(ticket)

    counts = {stage: len(items) for stage, items in stage_tickets.items()}
    counts["all"] = counts["open"] + counts["pending"] + counts["resolved"]

    return templates.TemplateResponse(
        "etapa.html",
        {
            "request": request,
            "user": current_user,
            "q": search_value,
            "stage_order": stage_order,
            "stage_labels": stage_labels,
            "stage_tickets": stage_tickets,
            "counts": counts,
            "users": users,
            "collapsed_stages": ["spam", "papelera"],
        },
    )


@router.get("/soporte", response_class=HTMLResponse)
def soporte_page(
    request: Request,
    current_user: User = Depends(get_current_user_web),
):
    # Vista importada desde el proyecto de Incidencias.
    return templates.TemplateResponse(
        "soporte.html",
        {
            "request": request,
            "user": current_user,
        },
    )


def _support_incidencias_table(db: Session) -> Table:
    # AJUSTE SOPORTE REGISTRO SQL #
    # Soporte Tecnico debe consumir unicamente la tabla Registro.
    metadata = MetaData()
    inspector = sa_inspect(db.bind)
    table_names = set(inspector.get_table_names())

    for table_name in ("registro", "registros", "Registro", "Registros"):
        if table_name in table_names:
            cols = {col["name"] for col in inspector.get_columns(table_name)}
            if "observacion_soporte" not in cols:
                quoted_table = table_name.replace('"', '""')
                dialect = (db.bind.dialect.name or "").lower()
                try:
                    if dialect == "postgresql":
                        db.execute(
                            text(
                                f'ALTER TABLE "{quoted_table}" ADD COLUMN IF NOT EXISTS observacion_soporte TEXT'
                            )
                        )
                    else:
                        db.execute(
                            text(
                                f'ALTER TABLE "{quoted_table}" ADD COLUMN observacion_soporte TEXT'
                            )
                        )
                    db.commit()
                except Exception:
                    db.rollback()
                    inspector_retry = sa_inspect(db.bind)
                    retry_cols = {col["name"] for col in inspector_retry.get_columns(table_name)}
                    if "observacion_soporte" not in retry_cols:
                        raise
            return Table(table_name, metadata, autoload_with=db.bind)

    raise RuntimeError("# AJUSTE SOPORTE REGISTRO SQL # No se encontro la tabla Registro/registros para soporte.")

def _support_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _support_format_radar_date(value: object) -> str:
    if value is None:
        return "Sin fecha"
    if isinstance(value, datetime):
        return value.strftime("%d-%m-%Y")

    raw_value = _support_text(value)
    if not raw_value:
        return "Sin fecha"

    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        return parsed.strftime("%d-%m-%Y")
    except ValueError:
        pass

    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw_value)
    if match:
        year, month, day = match.groups()
        return f"{day}-{month}-{year}"

    return raw_value.split(".")[0]


def _support_pick(row: dict[str, object], *keys: str) -> str:
    for key in keys:
        if key in row:
            text = _support_text(row.get(key))
            if text:
                return text
    return ""


def _support_person_name(value: object) -> str:
    text = _support_text(value)
    if not text:
        return ""
    normalized = text.casefold()
    if normalized in {"-", "sin asignar", "none", "null", "ninguno", "n/a"}:
        return ""
    return text


def _support_pick_person(row: dict[str, object], *keys: str) -> str:
    for key in keys:
        if key not in row:
            continue
        person = _support_person_name(row.get(key))
        if person:
            return person
    return ""


def _support_safe_odt_path(odt: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", (odt or "").strip())
    return cleaned or "sin_odt"


def _support_ensure_cierre_tables(db: Session) -> None:
    bind = db.get_bind()
    metadata = MetaData()
    id_type = BigInteger().with_variant(Integer, "sqlite")

    cierres = Table(
        "incidencias_cierres",
        metadata,
        Column("id", id_type, primary_key=True, autoincrement=True),
        Column("incidencia_id", BigInteger, nullable=False),
        Column("odt", String(80)),
        Column("observacion", SAText),
        Column("cerrado_por", String(180)),
        Column("cerrado_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    metadata.create_all(bind=bind, tables=[cierres], checkfirst=True)

    inspector = sa_inspect(bind)
    table_names = set(inspector.get_table_names())
    legacy_image_columns = ("imagen_1", "imagen_2", "imagen_3", "foto", "foto_2", "informe")

    if "incidencias_imagenes" in table_names:
        image_cols = {col["name"] for col in inspector.get_columns("incidencias_imagenes")}
        has_new_shape = {"id", "odt", "sucursal", "imagenes"}.issubset(image_cols)
        has_old_shape = bool(
            {"imagen_fallo", "file_url", "incidencia_id", "file_name", "mime_type", "size_bytes"}
            & image_cols
        )
        has_legacy_source_cols = False
        for source_table in ("registro", "registros", "Registro", "Registros"):
            if source_table not in table_names:
                continue
            source_cols = {col["name"] for col in inspector.get_columns(source_table)}
            if any(col in source_cols for col in legacy_image_columns):
                has_legacy_source_cols = True
                break

        if has_new_shape and not has_old_shape and not has_legacy_source_cols:
            db.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_incidencias_imagenes_odt
                    ON incidencias_imagenes (odt)
                    """
                )
            )
            return

    grouped_images: dict[str, dict[str, object]] = {}

    def _add_group_image(odt_value: object, sucursal_value: object, image_value: object) -> None:
        odt_text = _support_text(odt_value)
        image_text = _support_text(image_value)
        if not odt_text or not image_text:
            return
        sucursal_text = _support_text(sucursal_value)
        bucket = grouped_images.setdefault(
            odt_text,
            {"sucursal": sucursal_text, "imagenes": []},
        )
        if sucursal_text and not bucket["sucursal"]:
            bucket["sucursal"] = sucursal_text
        image_list = bucket["imagenes"]
        if image_text not in image_list:
            image_list.append(image_text)

    # 1) Recolecta desde incidencias_imagenes existente (cualquier formato previo).
    if "incidencias_imagenes" in table_names:
        image_cols = {col["name"] for col in inspector.get_columns("incidencias_imagenes")}

        if {"odt", "sucursal", "imagenes"}.issubset(image_cols):
            rows = db.execute(
                text("SELECT odt, sucursal, imagenes FROM incidencias_imagenes")
            ).mappings().all()
            for row in rows:
                for image_value in _support_parse_image_list(row.get("imagenes")):
                    _add_group_image(row.get("odt"), row.get("sucursal"), image_value)

        if {"odt", "sucursal", "imagen_fallo"}.issubset(image_cols):
            rows = db.execute(
                text(
                    """
                    SELECT odt, sucursal, imagen_fallo
                    FROM incidencias_imagenes
                    WHERE COALESCE(TRIM(imagen_fallo), '') <> ''
                    """
                )
            ).mappings().all()
            for row in rows:
                _add_group_image(row.get("odt"), row.get("sucursal"), row.get("imagen_fallo"))

        if {"odt", "file_url"}.issubset(image_cols):
            rows = db.execute(
                text(
                    """
                    SELECT odt, NULL::text AS sucursal, file_url
                    FROM incidencias_imagenes
                    WHERE COALESCE(TRIM(file_url), '') <> ''
                    """
                )
            ).mappings().all()
            for row in rows:
                _add_group_image(row.get("odt"), row.get("sucursal"), row.get("file_url"))

    # AJUSTE SOPORTE REGISTRO SQL #
    # 2) Recolecta desde columnas legacy en registro / incidencias (si existen).
    for source_table in ("registro", "registros", "Registro", "Registros"):
        if source_table not in table_names:
            continue
        source_cols = {col["name"] for col in inspector.get_columns(source_table)}
        present_legacy = [col for col in legacy_image_columns if col in source_cols]
        if not present_legacy:
            continue

        select_odt = "odt" if "odt" in source_cols else "NULL::text"
        if "sucursal" in source_cols:
            select_sucursal = "sucursal"
        elif "cliente" in source_cols:
            select_sucursal = "cliente"
        elif "puesto" in source_cols:
            select_sucursal = "puesto"
        else:
            select_sucursal = "NULL::text"

        select_cols = ", ".join(present_legacy)
        source_rows = db.execute(
            text(
                f"""
                SELECT {select_odt} AS odt, {select_sucursal} AS sucursal, {select_cols}
                FROM {source_table}
                """
            )
        ).mappings().all()

        for source_row in source_rows:
            for legacy_col in present_legacy:
                _add_group_image(
                    source_row.get("odt"),
                    source_row.get("sucursal"),
                    source_row.get(legacy_col),
                )

    # 3) Rebuild: una sola fila por ODT con columna JSON de imagenes.
    if "incidencias_imagenes" in table_names:
        db.execute(text("DROP TABLE incidencias_imagenes"))

    db.execute(
        text(
            """
            CREATE TABLE incidencias_imagenes (
                id BIGSERIAL PRIMARY KEY,
                odt VARCHAR(80) NOT NULL UNIQUE,
                sucursal VARCHAR(255),
                imagenes JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_by VARCHAR(180),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_incidencias_imagenes_odt
            ON incidencias_imagenes (odt)
            """
        )
    )
    db.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_incidencias_imagenes_odt
            ON incidencias_imagenes (odt)
            """
        )
    )

    for odt_key, payload in grouped_images.items():
        image_list = [img for img in payload["imagenes"] if _support_text(img)]
        db.execute(
            text(
                """
                INSERT INTO incidencias_imagenes (odt, sucursal, imagenes, created_by)
                VALUES (:odt, :sucursal, CAST(:imagenes AS JSONB), :created_by)
                """
            ),
            {
                "odt": odt_key,
                "sucursal": _support_text(payload.get("sucursal")) or None,
                "imagenes": json.dumps(image_list, ensure_ascii=False),
                "created_by": "migracion",
            },
        )

    # 4) No alteramos la tabla registro del sistema de Incidencias.
    # Soporte debe consumir ese origen en modo lectura para evitar locks
    # y no romper el flujo principal de ODT.


def _support_ensure_support_images_table(db: Session) -> None:
    try:
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS incidencias_imagenes_odt (
                    id BIGSERIAL PRIMARY KEY,
                    odt VARCHAR(80) NOT NULL UNIQUE,
                    sucursal VARCHAR(255),
                    imagenes JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_by VARCHAR(180),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        db.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_incidencias_imagenes_odt_odt
                ON incidencias_imagenes_odt (odt)
                """
            )
        )
        db.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_incidencias_imagenes_odt_odt
                ON incidencias_imagenes_odt (odt)
                """
            )
        )
        db.execute(text("ALTER TABLE incidencias_imagenes_odt ALTER COLUMN created_at SET DEFAULT NOW()"))
        db.execute(text("ALTER TABLE incidencias_imagenes_odt ALTER COLUMN updated_at SET DEFAULT NOW()"))
        db.execute(text("UPDATE incidencias_imagenes_odt SET created_at = NOW() WHERE created_at IS NULL"))
        db.execute(text("UPDATE incidencias_imagenes_odt SET updated_at = NOW() WHERE updated_at IS NULL"))

        def _table_exists(table_name: str) -> bool:
            return bool(db.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{table_name}"}).scalar())

        legacy_tables = ["incidencias_imagenes_soporte", "incidencias_imagenes_tabla"]
        for legacy in legacy_tables:
            if not _table_exists(legacy):
                continue

            rows = db.execute(
                text(
                    f"""
                    SELECT odt, sucursal, imagenes, created_by
                    FROM {legacy}
                    WHERE COALESCE(TRIM(odt), '') <> ''
                    """
                )
            ).mappings().all()

            for row in rows:
                odt_key = _support_text(row.get("odt"))
                if not odt_key:
                    continue
                old_imgs = _support_parse_image_list(row.get("imagenes"))
                cur = db.execute(
                    text(
                        """
                        SELECT imagenes
                        FROM incidencias_imagenes_odt
                        WHERE odt = :odt
                        LIMIT 1
                        """
                    ),
                    {"odt": odt_key},
                ).mappings().first()
                cur_imgs = _support_parse_image_list(cur.get("imagenes")) if cur else []
                merged: list[str] = []
                for img in [*cur_imgs, *old_imgs]:
                    val = _support_text(img)
                    if val and val not in merged:
                        merged.append(val)
                    if len(merged) >= 3:
                        break

                db.execute(
                    text(
                        """
                        INSERT INTO incidencias_imagenes_odt (odt, sucursal, imagenes, created_by, created_at, updated_at)
                        VALUES (:odt, :sucursal, CAST(:imagenes AS JSONB), :created_by, NOW(), NOW())
                        ON CONFLICT (odt) DO UPDATE SET
                            sucursal = COALESCE(EXCLUDED.sucursal, incidencias_imagenes_odt.sucursal),
                            imagenes = EXCLUDED.imagenes,
                            created_by = COALESCE(EXCLUDED.created_by, incidencias_imagenes_odt.created_by),
                            updated_at = NOW()
                        """
                    ),
                    {
                        "odt": odt_key,
                        "sucursal": _support_text(row.get("sucursal")) or None,
                        "imagenes": json.dumps(merged, ensure_ascii=False),
                        "created_by": _support_text(row.get("created_by")) or None,
                    },
                )

            db.execute(text(f"DROP TABLE {legacy}"))

        db.commit()
    except Exception:
        db.rollback()
        raise


def _support_fetch_support_images_by_odt(db: Session) -> dict[str, list[str]]:
    _support_ensure_support_images_table(db)
    out: dict[str, list[str]] = {}
    rows = db.execute(
        text(
            """
            SELECT odt, imagenes
            FROM incidencias_imagenes_odt
            WHERE COALESCE(TRIM(odt), '') <> ''
            """
        )
    ).mappings().all()
    for row in rows:
        odt_key = _support_text(row.get("odt"))
        if not odt_key:
            continue
        out[odt_key] = _support_parse_image_list(row.get("imagenes"))[:3]
    return out


def _support_append_user_observation(current_text: str, user_label: str, obs_text: str) -> str:
    timestamp = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M")
    line = f"[{user_label} - {timestamp}] {obs_text}"
    if not current_text:
        return line
    return f"{current_text.rstrip()}\n{line}"


def _support_parse_image_list(value: object) -> list[str]:
    parsed_images: list[str] = []
    if isinstance(value, list):
        parsed_images = [_support_text(v) for v in value]
    elif isinstance(value, str):
        raw = value.strip()
        if raw:
            try:
                decoded = json.loads(raw)
                if isinstance(decoded, list):
                    parsed_images = [_support_text(v) for v in decoded]
                else:
                    parsed_images = [_support_text(raw)]
            except Exception:
                parsed_images = [_support_text(raw)]
    else:
        parsed_images = [_support_text(value)]

    unique_images: list[str] = []
    for image_url in parsed_images:
        clean = _support_text(image_url)
        if clean and clean not in unique_images:
            unique_images.append(clean)
    return unique_images


def _support_odt_sort_key(raw_odt: object, raw_id: object) -> tuple[int, int, str]:
    # Orden natural por ODT (menor -> mayor). Si no hay numero, cae al final.
    odt = _support_text(raw_odt)
    numbers = re.findall(r"\d+", odt)
    if numbers:
        return (0, int(numbers[-1]), odt.lower())
    if isinstance(raw_id, int):
        return (1, raw_id, odt.lower())
    return (2, 0, odt.lower())


def _support_next_odt_value(db: Session, table: Table) -> str:
    # Calcula la siguiente ODT numerica basada en los registros existentes.
    if "odt" not in table.c:
        return f"T{int(datetime.now().timestamp())}"

    rows = db.execute(select(table.c.odt)).scalars().all()
    max_number = 0
    seen: set[str] = set()
    for raw_value in rows:
        odt_text = _support_text(raw_value)
        if not odt_text:
            continue
        seen.add(odt_text.casefold())
        numbers = re.findall(r"\d+", odt_text)
        if numbers:
            max_number = max(max_number, int(numbers[-1]))

    candidate_number = max_number + 1 if max_number > 0 else (len(seen) + 1)
    candidate = f"T{candidate_number}"
    while candidate.casefold() in seen:
        candidate_number += 1
        candidate = f"T{candidate_number}"
    return candidate


def _support_find_direccion_by_cliente(db: Session, table: Table, cliente_value: str) -> str:
    # Busca direccion sugerida para una sucursal/cliente existente.
    cliente_clean = _support_text(cliente_value)
    if not cliente_clean:
        return ""
    if "direccion" not in table.c:
        return ""

    lookup_columns = [name for name in ("cliente", "sucursal", "puesto") if name in table.c]
    if not lookup_columns:
        return ""

    wanted = cliente_clean.casefold()
    for col_name in lookup_columns:
        col = table.c[col_name]
        row = db.execute(
            select(table.c.direccion)
            .where(func.lower(func.trim(col)) == wanted)
            .where(func.coalesce(func.trim(table.c.direccion), "") != "")
            .limit(1)
        ).first()
        if row and _support_text(row[0]):
            return _support_text(row[0])
    return ""


def _support_query_incidencias(db: Session) -> tuple[Table, list[dict[str, object]]]:
    # AJUSTE SOPORTE REGISTRO SQL #
    # AJUSTE SOPORTE REGISTRO SQL # Query base de soporte: consume solo la tabla SQL "registro".
    table = _support_incidencias_table(db)

    selected = [table.c.id]
    optional_columns = (
        "odt",
        "fecha_registro",
        "fecha",
        "cliente",
        "puesto",
        "sucursal",
        "tipo_incidencia",
        "problema",
        "derivacion",
        "descripcion",
        "observacion",
        "observacion_soporte",
        "observacion_servicio",
        "tecnico",
        "tecnicos",
        "acompanante",
        "derivado_por",
        "estado",
        "cantidad_dias_ejecucion",
        "cant_dias",
        "dias_ejecucion",
        "fecha_cierre",
        "fecha_derivacion_tecnico",
        "fecha_derivacion",
        "observacion_final",
    )
    for col_name in optional_columns:
        if col_name in table.c:
            selected.append(table.c[col_name])

    stmt = select(*selected)
    rows = db.execute(stmt).mappings().all()
    mapped_rows = [dict(row) for row in rows]

    mapped_rows.sort(
        key=lambda row: _support_odt_sort_key(
            row.get("odt"),
            row.get("id"),
        )
    )
    return table, mapped_rows


def _support_incidencia_id_by_sheet_row(db: Session, fila: int) -> int | None:
    # Compatibilidad con el contrato original (fila de "sheet" iniciando en 2).
    offset = int(fila) - 2
    if offset < 0:
        return None
    _table, incidencias = _support_query_incidencias(db)
    if offset >= len(incidencias):
        return None
    picked = incidencias[offset]
    value = picked.get("id")
    return int(value) if isinstance(value, int) else None


@router.get("/api/registros/tabla")
def soporte_obtener_registros_tabla(
    db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    # AJUSTE SOPORTE REGISTRO SQL #
    # Endpoint de compatibilidad para soporte.html (antes Google Apps Script).
    _ = current_user  # Mantiene autenticacion por cookie.

    _table, incidencias = _support_query_incidencias(db)
    extra_images_by_odt: dict[str, list[str]] = {}
    try:
        extra_images_by_odt = _support_fetch_support_images_by_odt(db)
    except Exception:
        # Si falla la lectura de tabla de soporte, seguimos sin imagenes.
        pass

    rows: list[list[str | int]] = []

    for incidencia in incidencias:
        cliente = _support_pick(incidencia, "cliente", "sucursal", "puesto")
        odt = _support_pick(incidencia, "odt") or f"#{incidencia.get('id')}"
        tecnico_titular = _support_pick_person(incidencia, "tecnico", "tecnicos")
        tecnico_acompanante = _support_pick_person(incidencia, "acompanante")
        odt_images = extra_images_by_odt.get(odt, [])
        extra_images: list[str] = []
        for image_url in odt_images:
            if image_url and image_url not in extra_images:
                extra_images.append(image_url)

        # Estructura compatible con soporte.html:
        # [0..9] columnas + [10..12] reservadas + [13..15] metadatos tecnico + [16] imagenes soporte
        # + [17] observacion soporte + [18] observacion servicio tecnico.
        rows.append(
            [
                odt,  # ODT
                _support_pick(incidencia, "fecha", "fecha_registro"),  # Fecha
                cliente,  # Cliente
                _support_pick(incidencia, "problema", "tipo_incidencia"),  # Problema
                _support_pick(incidencia, "derivacion"),  # Derivacion
                _support_pick(incidencia, "observacion", "descripcion"),  # Observacion
                tecnico_titular,  # Tecnico
                tecnico_acompanante,  # Acompanante
                _support_pick(incidencia, "estado"),  # Estado
                _support_pick(incidencia, "observacion_final"),  # Observacion final
                "",  # Imagen 1 (legacy)
                "",  # Imagen 2 (legacy)
                "",  # Imagen 3 (legacy)
                tecnico_titular,  # Tecnico titular raw
                tecnico_acompanante,  # Acompanante raw
                _support_pick(incidencia, "derivado_por", "tecnicos"),  # Derivado por raw
                extra_images,  # Imagenes soporte (max 3)
                _support_pick(incidencia, "observacion_soporte"),  # Observacion soporte (solo soporte)
                _support_pick(incidencia, "observacion_servicio"),  # Observacion servicio tecnico
            ]
        )

    return rows


@router.get("/api/listas-bbdd")
def soporte_obtener_listas_bbdd(
    db: Session = Depends(get_incidencias_db),
    catalog_db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    # Listas de referencia para autocompletado/filtros del modulo soporte.
    _ = current_user

    _table, incidencias = _support_query_incidencias(db)
    clientes: list[str] = []
    problemas: list[str] = []
    estados: list[str] = []
    derivaciones: list[str] = []
    tecnicos: list[str] = []
    sucursales_map: dict[str, str] = {}

    seen_clientes: set[str] = set()
    seen_problemas: set[str] = set()
    seen_estados: set[str] = set()
    seen_derivaciones: set[str] = set()
    seen_tecnicos: set[str] = set()

    for incidencia in incidencias:
        cliente = _support_pick(incidencia, "cliente", "sucursal", "puesto")
        if cliente and cliente not in seen_clientes:
            seen_clientes.add(cliente)
            clientes.append(cliente)
        if cliente:
            direccion_cliente = _support_pick(incidencia, "direccion")
            if cliente not in sucursales_map or (not sucursales_map.get(cliente) and direccion_cliente):
                sucursales_map[cliente] = direccion_cliente

        problema = _support_pick(incidencia, "problema", "tipo_incidencia")
        if problema and problema not in seen_problemas:
            seen_problemas.add(problema)
            problemas.append(problema)

        estado = _support_pick(incidencia, "estado")
        if estado and estado not in seen_estados:
            seen_estados.add(estado)
            estados.append(estado)

        derivacion = _support_pick(incidencia, "derivacion")
        if derivacion and derivacion not in seen_derivaciones:
            seen_derivaciones.add(derivacion)
            derivaciones.append(derivacion)

        tecnico = _support_pick_person(incidencia, "tecnico", "tecnicos")
        if tecnico and tecnico not in seen_tecnicos:
            seen_tecnicos.add(tecnico)
            tecnicos.append(tecnico)

        acompanante = _support_pick_person(incidencia, "acompanante")
        if acompanante and acompanante not in seen_tecnicos:
            seen_tecnicos.add(acompanante)
            tecnicos.append(acompanante)

    # Catalogo oficial de tecnicos para los selects de derivacion.
    try:
        catalog_rows = catalog_db.execute(
            text(
                """
                SELECT nombre
                FROM incidencias_tecnicos
                WHERE activo = TRUE
                ORDER BY nombre ASC
                """
            )
        ).fetchall()
        for row in catalog_rows:
            tecnico = _support_text(row[0])
            if tecnico and tecnico not in seen_tecnicos:
                seen_tecnicos.add(tecnico)
                tecnicos.append(tecnico)
    except Exception:
        # Si no existe la tabla de catalogo, seguimos con los valores de incidencias.
        pass

    clientes.sort()
    problemas.sort()
    estados.sort()
    derivaciones.sort()
    tecnicos.sort()
    sucursales = [
        {
            "nombre": nombre,
            "direccion": sucursales_map.get(nombre, ""),
        }
        for nombre in sorted(sucursales_map.keys(), key=lambda x: x.casefold())
    ]

    return {
        "clientes": clientes,
        "sucursales": sucursales,
        "problemas": problemas,
        "estados": estados,
        "derivaciones": derivaciones,
        "tecnicos": tecnicos,
    }


@router.get("/api/incidencias/catalogo-ticket")
def ticket_service_catalog(
    db: Session = Depends(get_incidencias_db),
    catalog_db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    # Catalogo para popup de derivacion desde ticket_detail.
    # Fuente solicitada: catalogo_clientes.nombre_sucursal.
    _ = current_user

    clientes_map: dict[str, str] = {}
    tecnicos_set: set[str] = set()

    try:
        rows = db.execute(
            text(
                """
                SELECT nombre_sucursal
                FROM catalogo_clientes
                WHERE COALESCE(TRIM(nombre_sucursal), '') <> ''
                ORDER BY nombre_sucursal ASC
                """
            )
        ).fetchall()
        for row in rows:
            value = re.sub(r"\s+", " ", _support_text(row[0])).strip()
            if not value:
                continue
            key = value.casefold()
            if key not in clientes_map:
                clientes_map[key] = value
    except Exception:
        pass

    # Catalogo oficial de tecnicos.
    try:
        catalog_rows = catalog_db.execute(
            text(
                """
                SELECT nombre
                FROM incidencias_tecnicos
                WHERE activo = TRUE
                ORDER BY nombre ASC
                """
            )
        ).fetchall()
        for row in catalog_rows:
            tecnico = _support_text(row[0])
            if tecnico:
                tecnicos_set.add(tecnico)
    except Exception:
        pass

    clientes = sorted(clientes_map.values(), key=lambda x: x.casefold())
    problemas = [
        "Desconexión",
        "Problema de visual",
        "Problema de Parlante",
        "Problema de Alarma",
        "Hora y/o Fecha Cambiada",
    ]
    tecnicos = sorted(tecnicos_set, key=lambda x: x.casefold())

    # Logica de detalle alineada a incidencias para los dos tipos requeridos.
    visual_options = [
        "Falla de video",
        "Obstruccion",
        "Intermitencia",
        "IVS",
        "Camara sucia",
        "Camara Movida",
        "Bateria Baja",
    ]
    desconexion_options = [
        "Desconocida",
        "Electricidad",
        "Internet",
    ]

    return {
        "clientes": clientes,
        "problemas": problemas,
        "tecnicos": tecnicos,
        "derivaciones": ["Servicio Técnico", "Cliente"],
        "visual_options": visual_options,
        "desconexion_options": desconexion_options,
    }


@router.post("/api/incidencias/actualizar-celda")
def soporte_actualizar_celda(
    payload: dict,
    db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    # Compatibilidad con doble-click de soporte.html.
    fila = int(payload.get("fila") or 0)
    columna = int(payload.get("columna") or 0)
    valor = str(payload.get("valor") or "").strip()
    valor_original = str(payload.get("valor_original") or "").strip()

    table = _support_incidencias_table(db)
    incidencia_id = _support_incidencia_id_by_sheet_row(db, fila)
    if not incidencia_id:
        raise HTTPException(status_code=404, detail="Fila no encontrada")

    # Columnas editables de soporte heredadas de la hoja original.
    values_to_update: dict[str, object] = {}
    if columna == 6:
        if "derivacion" in table.c:
            values_to_update["derivacion"] = valor or None
        if valor.casefold() == "finalizado por soporte":
            obs_final = _support_text(payload.get("observacion_final") or payload.get("observacionFinal"))
            if not obs_final:
                raise HTTPException(
                    status_code=400,
                    detail="Debes indicar la observacion final para finalizar por soporte.",
                )
            if "observacion_final" in table.c:
                user_label = (current_user.name or current_user.username or "Usuario").strip()
                values_to_update["observacion_final"] = _support_append_user_observation(
                    "",
                    user_label,
                    obs_final,
                )
            if "estado" in table.c:
                values_to_update["estado"] = "Terminado"
            if "fecha_cierre" in table.c:
                values_to_update["fecha_cierre"] = datetime.now().astimezone()
    elif columna == 7:
        support_observation_col = "observacion_soporte" if "observacion_soporte" in table.c else None

        if support_observation_col:
            # Comportamiento mixto:
            # - Si agregan texto al final, lo guarda como "[Usuario - Fecha] Obs".
            # - Si mandan solo una nota nueva, tambien la agrega como historial.
            # - Si queda vacio, permite borrar todo.
            current_text = db.execute(
                select(table.c[support_observation_col]).where(table.c.id == incidencia_id)
            ).scalar_one_or_none()
            current_text = _support_text(current_text)

            edited_text = valor
            original_text = valor_original
            if not edited_text:
                # Permite borrar completamente la observacion.
                values_to_update[support_observation_col] = None
            elif not current_text:
                # Primera observacion: se registra con metadata.
                timestamp = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M")
                user_label = (current_user.name or current_user.username or "Usuario").strip()
                values_to_update[support_observation_col] = f"[{user_label} - {timestamp}] {edited_text}"
            elif edited_text == (original_text or current_text):
                # Sin cambios reales.
                pass
            else:
                compare_base = original_text or current_text

                # Si enviaron historial original + nota al final, agrega solo la nota.
                if compare_base and edited_text.startswith(compare_base):
                    new_note = edited_text[len(compare_base) :].lstrip()
                    if new_note:
                        timestamp = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M")
                        user_label = (current_user.name or current_user.username or "Usuario").strip()
                        log_line = f"[{user_label} - {timestamp}] {new_note}"
                        values_to_update[support_observation_col] = f"{current_text.rstrip()}\n{log_line}"
                else:
                    # Si parece una nota corta nueva, la agrega con metadata.
                    # Si no, respeta la edicion/borrado exacto del usuario.
                    looks_like_new_note = (
                        "\n" not in edited_text
                        and "[" not in edited_text
                        and "]" not in edited_text
                        and len(edited_text) <= 300
                    )
                    if looks_like_new_note:
                        timestamp = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M")
                        user_label = (current_user.name or current_user.username or "Usuario").strip()
                        log_line = f"[{user_label} - {timestamp}] {edited_text}"
                        values_to_update[support_observation_col] = f"{current_text.rstrip()}\n{log_line}"
                    else:
                        values_to_update[support_observation_col] = edited_text
    elif columna == 8:
        tecnico_titular = str(payload.get("tecnico") or valor or "").strip()
        tecnico_acompanante = str(payload.get("acompanante") or "").strip()
        user_label = (current_user.name or current_user.username or "Usuario").strip()
        timestamp_dt = datetime.now().astimezone()

        # La fuente de datos (FDW) no acepta NULL en tecnico/acompanante.
        # Normalizamos para evitar HTTP 500 por NotNullViolation.
        if not tecnico_titular and tecnico_acompanante:
            tecnico_titular = tecnico_acompanante
            tecnico_acompanante = "-"
        if not tecnico_titular:
            tecnico_titular = "-"
        if not tecnico_acompanante:
            tecnico_acompanante = "-"
        if (
            tecnico_titular != "-"
            and tecnico_acompanante != "-"
            and tecnico_titular.casefold() == tecnico_acompanante.casefold()
        ):
            tecnico_acompanante = "-"

        if "tecnico" in table.c:
            values_to_update["tecnico"] = tecnico_titular or None
        elif "tecnicos" in table.c:
            # Esquema alternativo donde la columna visible de tecnico se llama "tecnicos".
            values_to_update["tecnicos"] = tecnico_titular or None

        if "acompanante" in table.c:
            values_to_update["acompanante"] = tecnico_acompanante or None
        elif tecnico_acompanante:
            # Fallback: si no existe columna dedicada, lo persistimos junto al tecnico.
            base_tecnico = tecnico_titular or ""
            tecnico_con_acomp = f"{base_tecnico} | {tecnico_acompanante}".strip(" |")
            if "tecnico" in table.c:
                values_to_update["tecnico"] = tecnico_con_acomp
            elif "tecnicos" in table.c:
                values_to_update["tecnicos"] = tecnico_con_acomp

        if "derivado_por" in table.c:
            values_to_update["derivado_por"] = user_label

        if "fecha_derivacion" in table.c:
            values_to_update["fecha_derivacion"] = timestamp_dt
        if "fecha_derivacion_tecnico" in table.c:
            values_to_update["fecha_derivacion_tecnico"] = timestamp_dt

    if values_to_update:
        stmt = update(table).where(table.c.id == incidencia_id).values(**values_to_update)
        db.execute(stmt)

    db.commit()
    return "OK"


@router.post("/api/incidencias/enviar-correo-derivacion-area")
def soporte_enviar_correo_derivacion_area(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    # Placeholder compatible: dejamos OK para no cortar el flujo de soporte.
    _ = (payload, db, current_user)
    return "OK"


@router.post("/api/incidencias/cerrar-odt")
async def soporte_cerrar_odt(
    odt: str = Form(...),
    observacion: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    odt_clean = _support_text(odt)
    obs_clean = _support_text(observacion)
    if not odt_clean:
        raise HTTPException(status_code=400, detail="ODT es obligatoria.")
    if not obs_clean:
        raise HTTPException(status_code=400, detail="Debe ingresar una observacion.")
    if not files:
        raise HTTPException(status_code=400, detail="Debe adjuntar al menos una imagen.")

    table = _support_incidencias_table(db)

    if "odt" in table.c:
        incidencia_row = db.execute(
            select(*table.c).where(table.c.odt == odt_clean).order_by(table.c.id.desc())
        ).mappings().first()
    else:
        incidencia_row = None

    if not incidencia_row and odt_clean.startswith("#") and odt_clean[1:].isdigit():
        incidencia_id = int(odt_clean[1:])
        incidencia_row = db.execute(
            select(*table.c).where(table.c.id == incidencia_id)
        ).mappings().first()

    if not incidencia_row:
        raise HTTPException(status_code=404, detail=f"ODT {odt_clean} no encontrada.")

    incidencia_id = incidencia_row.get("id")
    if not isinstance(incidencia_id, int):
        raise HTTPException(status_code=400, detail="No se pudo resolver el ID de incidencia.")

    _support_ensure_cierre_tables(db)

    user_label = (current_user.name or current_user.username or "Usuario").strip()
    sucursal_value = _support_pick(dict(incidencia_row), "sucursal", "cliente", "puesto")

    folder_safe = _support_safe_odt_path(odt_clean)
    dest_dir = Path("uploads") / "incidencias" / folder_safe
    dest_dir.mkdir(parents=True, exist_ok=True)

    stored_urls: list[str] = []
    for upload in files:
        if not upload or not upload.filename:
            continue
        content = await upload.read()
        if not content:
            continue
        suffix = Path(upload.filename).suffix.lower()
        if not suffix:
            suffix = ".jpg"
        safe_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:10]}{suffix}"
        file_path = dest_dir / safe_name
        file_path.write_bytes(content)
        file_url = f"/uploads/incidencias/{folder_safe}/{safe_name}"
        stored_urls.append(file_url)

    if not stored_urls:
        raise HTTPException(status_code=400, detail="No se pudo guardar ninguna imagen valida.")

    existing_images_row = db.execute(
        text(
            """
            SELECT id, imagenes
            FROM incidencias_imagenes
            WHERE odt = :odt
            LIMIT 1
            """
        ),
        {"odt": odt_clean},
    ).mappings().first()
    existing_images = (
        _support_parse_image_list(existing_images_row.get("imagenes"))
        if existing_images_row
        else []
    )
    merged_images = list(existing_images)
    for image_url in stored_urls:
        if image_url not in merged_images:
            merged_images.append(image_url)

    if existing_images_row:
        db.execute(
            text(
                """
                UPDATE incidencias_imagenes
                SET
                    sucursal = COALESCE(:sucursal, sucursal),
                    imagenes = CAST(:imagenes AS JSONB),
                    created_by = :created_by,
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": existing_images_row.get("id"),
                "sucursal": sucursal_value or None,
                "imagenes": json.dumps(merged_images, ensure_ascii=False),
                "created_by": user_label,
            },
        )
    else:
        db.execute(
            text(
                """
                INSERT INTO incidencias_imagenes (
                    odt, sucursal, imagenes, created_by
                ) VALUES (
                    :odt, :sucursal, CAST(:imagenes AS JSONB), :created_by
                )
                """
            ),
            {
                "odt": odt_clean,
                "sucursal": sucursal_value or None,
                "imagenes": json.dumps(merged_images, ensure_ascii=False),
                "created_by": user_label,
            },
        )

    values_to_update: dict[str, object] = {}
    support_observation_col = "observacion_soporte" if "observacion_soporte" in table.c else None

    if support_observation_col:
        current_observation = _support_text(incidencia_row.get(support_observation_col))
        values_to_update[support_observation_col] = _support_append_user_observation(
            current_observation,
            user_label,
            obs_clean,
        )

    if "observacion_final" in table.c:
        values_to_update["observacion_final"] = _support_append_user_observation(
            "",
            user_label,
            obs_clean,
        )
    if "estado" in table.c:
        values_to_update["estado"] = "Terminado"
    if "fecha_cierre" in table.c:
        values_to_update["fecha_cierre"] = datetime.now().astimezone()

    if values_to_update:
        db.execute(
            update(table).where(table.c.id == incidencia_id).values(**values_to_update)
        )

    db.execute(
        text(
            """
            INSERT INTO incidencias_cierres (incidencia_id, odt, observacion, cerrado_por)
            VALUES (:incidencia_id, :odt, :observacion, :cerrado_por)
            """
        ),
        {
            "incidencia_id": incidencia_id,
            "odt": odt_clean,
            "observacion": obs_clean,
            "cerrado_por": user_label,
        },
    )

    db.commit()
    tecnico_label = _support_text(incidencia_row.get("tecnico"))
    acompanante_label = _support_text(incidencia_row.get("acompanante"))
    if tecnico_label and acompanante_label and tecnico_label.casefold() != acompanante_label.casefold():
        tecnico_para_reporte = f"{tecnico_label} / {acompanante_label}"
    else:
        tecnico_para_reporte = tecnico_label or acompanante_label

    fecha_cierre_label = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M")
    if "fecha_cierre" in table.c and incidencia_row.get("fecha_cierre"):
        fecha_cierre_label = _support_text(incidencia_row.get("fecha_cierre")) or fecha_cierre_label

    drive_payload: dict[str, object] = {
        "drive_ok": False,
        "drive_enabled": bool(settings.GOOGLE_DRIVE_ENABLED),
    }
    if settings.GOOGLE_DRIVE_ENABLED:
        try:
            drive_result = create_drive_report_for_odt(
                odt=odt_clean,
                sucursal=sucursal_value,
                cliente=_support_pick(dict(incidencia_row), "cliente", "sucursal", "puesto"),
                problema=_support_pick(dict(incidencia_row), "problema", "tipo_incidencia", "descripcion"),
                direccion=_support_pick(dict(incidencia_row), "direccion"),
                tecnico=tecnico_para_reporte,
                fecha_cierre=fecha_cierre_label,
                observacion_cierre=obs_clean,
                image_sources=merged_images,
            )
            drive_payload = {"drive_ok": True, "drive_enabled": True, **drive_result}
        except DriveReportError as exc:
            drive_payload = {
                "drive_ok": False,
                "drive_enabled": True,
                "drive_error": str(exc),
            }
        except Exception as exc:
            traceback.print_exc()
            drive_payload = {
                "drive_ok": False,
                "drive_enabled": True,
                "drive_error": f"Error inesperado Drive: {exc}",
            }

    return {
        "ok": True,
        "odt": odt_clean,
        "imagenes_guardadas": len(stored_urls),
        **drive_payload,
    }


@router.post("/api/incidencias/upload-image")
async def soporte_upload_image(
    request: Request,
    db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    _support_ensure_support_images_table(db)
    table = _support_incidencias_table(db)

    content_type = (request.headers.get("content-type") or "").lower()
    odt_clean = ""
    incoming_images: list[dict[str, object]] = []

    if "application/json" in content_type:
        payload = await request.json()
        odt_clean = _support_text(payload.get("odt"))
        base64_data = _support_text(payload.get("base64_data"))
        filename = _support_text(payload.get("filename")) or "captura.png"
        mime_type = "image/png"
        if base64_data.startswith("data:"):
            match = re.match(r"^data:([^;]+);base64,(.+)$", base64_data, flags=re.IGNORECASE | re.DOTALL)
            if not match:
                raise HTTPException(status_code=400, detail="Formato base64 invalido.")
            mime_type = _support_text(match.group(1)) or mime_type
            encoded = match.group(2).strip()
        else:
            encoded = base64_data.strip()
        if not encoded:
            raise HTTPException(status_code=400, detail="No se recibio imagen en base64.")
        try:
            image_bytes = base64.b64decode(encoded, validate=False)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(status_code=400, detail="No se pudo decodificar la imagen base64.") from exc
        if not image_bytes:
            raise HTTPException(status_code=400, detail="La imagen base64 esta vacia.")
        incoming_images.append(
            {
                "filename": filename,
                "mime_type": mime_type if mime_type.startswith("image/") else "image/png",
                "bytes": image_bytes,
            }
        )
    else:
        form = await request.form()
        odt_clean = _support_text(form.get("odt"))
        for key, value in form.multi_items():
            if key not in {"files", "file", "imagenes", "imagen"}:
                continue
            # Starlette puede entregar UploadFile de distintas clases segun entorno.
            if not hasattr(value, "read"):
                continue
            read_result = value.read()
            if hasattr(read_result, "__await__"):
                content = await read_result
            else:
                content = read_result
            if not content:
                continue
            filename = _support_text(getattr(value, "filename", "")) or "imagen.png"
            mime_type = _support_text(getattr(value, "content_type", ""))
            if not mime_type or mime_type == "application/octet-stream":
                guessed, _ = mimetypes.guess_type(filename)
                mime_type = _support_text(guessed) or "image/png"
            if not mime_type.startswith("image/"):
                continue
            incoming_images.append(
                {
                    "filename": filename,
                    "mime_type": mime_type,
                    "bytes": content,
                }
            )

    if not odt_clean:
        raise HTTPException(status_code=400, detail="ODT es obligatoria.")
    if not incoming_images:
        raise HTTPException(status_code=400, detail="Debes adjuntar al menos una imagen valida.")

    if "odt" in table.c:
        incidencia_row = db.execute(
            select(*table.c).where(table.c.odt == odt_clean).order_by(table.c.id.desc())
        ).mappings().first()
    else:
        incidencia_row = None

    if not incidencia_row:
        raise HTTPException(status_code=404, detail=f"ODT {odt_clean} no encontrada.")

    existing_images_row = db.execute(
        text(
            """
            SELECT id, imagenes
            FROM incidencias_imagenes_odt
            WHERE odt = :odt
            LIMIT 1
            """
        ),
        {"odt": odt_clean},
    ).mappings().first()
    existing_images = (
        _support_parse_image_list(existing_images_row.get("imagenes"))[:3]
        if existing_images_row
        else []
    )

    remaining_slots = max(0, 3 - len(existing_images))
    if remaining_slots <= 0:
        raise HTTPException(status_code=400, detail="Esta ODT ya tiene 3 imagenes de soporte.")
    if len(incoming_images) > remaining_slots:
        raise HTTPException(
            status_code=400,
            detail=f"Solo puedes subir {remaining_slots} imagen(es) adicional(es) para esta ODT.",
        )

    try:
        drive_result = upload_support_images_for_odt(
            odt=odt_clean,
            image_payloads=incoming_images,
            root_folder_id=_support_text(settings.GOOGLE_DRIVE_SUPPORT_FOLDER_ID),
            start_index=len(existing_images) + 1,
        )
    except DriveReportError as exc:
        raise HTTPException(status_code=400, detail=f"No se pudo subir a Drive: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error inesperado al subir imagenes: {exc}") from exc

    new_urls = [_support_text(url) for url in (drive_result.get("imagenes") or []) if _support_text(url)]
    if not new_urls:
        raise HTTPException(status_code=400, detail="No se pudo obtener URL publica de las imagenes subidas.")

    merged_images = existing_images[:]
    for url in new_urls:
        if url not in merged_images:
            merged_images.append(url)
    merged_images = merged_images[:3]

    user_label = (current_user.name or current_user.username or "Usuario").strip()
    sucursal_value = _support_pick(dict(incidencia_row), "sucursal", "cliente", "puesto")

    if existing_images_row:
        db.execute(
            text(
                """
                UPDATE incidencias_imagenes_odt
                SET
                    sucursal = COALESCE(:sucursal, sucursal),
                    imagenes = CAST(:imagenes AS JSONB),
                    created_by = :created_by,
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": existing_images_row.get("id"),
                "sucursal": sucursal_value or None,
                "imagenes": json.dumps(merged_images, ensure_ascii=False),
                "created_by": user_label,
            },
        )
    else:
        db.execute(
            text(
                """
                INSERT INTO incidencias_imagenes_odt (
                    odt, sucursal, imagenes, created_by
                ) VALUES (
                    :odt, :sucursal, CAST(:imagenes AS JSONB), :created_by
                )
                """
            ),
            {
                "odt": odt_clean,
                "sucursal": sucursal_value or None,
                "imagenes": json.dumps(merged_images, ensure_ascii=False),
                "created_by": user_label,
            },
        )

    db.commit()
    return {
        "ok": True,
        "odt": odt_clean,
        "imagenes": merged_images,
        "imagenes_guardadas": len(new_urls),
        "total_imagenes": len(merged_images),
        "drive_folder_id": drive_result.get("folder_id", ""),
        "drive_folder_name": drive_result.get("folder_name", ""),
    }


@router.get("/api/usuario-actual")
def soporte_usuario_actual(
    token: str = "",
    current_user: User = Depends(get_current_user_web),
):
    # Mantiene firma con token para compatibilidad con el frontend original.
    _ = token
    return current_user.name or current_user.username

@router.post("/dashboard/tickets/{ticket_id}/stage")
def update_ticket_stage(
    ticket_id: int,
    stage: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    safe_stage = (stage or "").strip().lower()
    allowed_stages = {"open", "pending", "resolved", "spam", "papelera"}
    if safe_stage not in allowed_stages:
        raise HTTPException(status_code=400, detail="Etapa invalida")

    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    ticket_source = (ticket.source or "").strip().lower()
    requires_reception = safe_stage in {"pending", "resolved"}
    if ticket_source == "email" and requires_reception and not _has_reception_sent(db, ticket.id):
        raise HTTPException(
            status_code=400,
            detail="Primero debes enviar 'Recepcion de solicitud' antes de mover a Pending o Resolved.",
        )

    if safe_stage == "spam":
        ticket.is_spam = True
        ticket.is_deleted = False
        apply_ticket_status_change(ticket, "closed")
    elif safe_stage == "papelera":
        ticket.is_deleted = True
        ticket.is_spam = False
        apply_ticket_status_change(ticket, "closed")
    else:
        ticket.is_deleted = False
        ticket.is_spam = False
        _enforce_status_transition_rules(ticket, safe_stage)
        apply_ticket_status_change(ticket, safe_stage)

    db.commit()

    return JSONResponse(
        {
            "ok": True,
            "ticket_id": ticket.id,
            "stage": _ticket_stage(ticket),
            "status": ticket.status,
            "is_spam": bool(ticket.is_spam),
            "is_deleted": bool(ticket.is_deleted),
            "updated_by": current_user.name or current_user.username,
        }
    )

@router.post("/dashboard/tickets/{ticket_id}/priority-json")
def update_priority_json(
    ticket_id: int,
    priority: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id,
        Ticket.is_deleted == False,
    ).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    safe_priority = (priority or "").strip().lower()
    allowed_priorities = ["low", "medium", "high", "urgent"]
    if safe_priority not in allowed_priorities:
        raise HTTPException(status_code=400, detail="Prioridad invalida")

    ticket.priority = safe_priority
    db.commit()

    return JSONResponse(
        {
            "ok": True,
            "ticket_id": ticket.id,
            "priority": ticket.priority,
            "updated_by": current_user.name or current_user.username,
        }
    )

@router.post("/dashboard/tickets/{ticket_id}/assign-json")
def assign_ticket_json(
    ticket_id: int,
    user_id: int | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    safe_user_id = user_id
    assigned_user = None
    if safe_user_id is not None:
        assigned_user = db.query(User).filter(User.id == safe_user_id, User.is_active == True).first()
        if not assigned_user:
            raise HTTPException(status_code=400, detail="Usuario invalido")

    assign_ticket_logic(db, ticket, safe_user_id, current_user)
    db.commit()

    return JSONResponse(
        {
            "ok": True,
            "ticket_id": ticket.id,
            "assigned_to_id": ticket.assigned_to_id,
            "assigned_to_name": assigned_user.name if assigned_user else None,
            "updated_by": current_user.name or current_user.username,
        }
    )

@router.get("/internal-chat/messages")

def get_internal_chat_messages(

    limit: int = 80,

    mark_read: int = 0,

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    safe_limit = max(1, min(limit, 200))

    rows = (

        db.query(InternalChatMessage)

        .options(joinedload(InternalChatMessage.sender))

        .order_by(InternalChatMessage.created_at.desc())

        .limit(safe_limit)

        .all()

    )

    rows.reverse()

    unread_count = _get_internal_chat_unread_count(db, current_user.id)

    if mark_read:

        latest_message_id = rows[-1].id if rows else 0

        unread_count = _mark_internal_chat_as_read(

            db=db,

            user_id=current_user.id,

            last_message_id=latest_message_id,

        )

    return JSONResponse(

        {

            "messages": [_serialize_internal_chat_message(row) for row in rows],

            "unread_count": unread_count,

            "current_user_id": current_user.id,

        }

    )

@router.get("/internal-chat/unread-count")

def get_internal_chat_unread_count(

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    return JSONResponse(

        {

            "unread_count": _get_internal_chat_unread_count(db, current_user.id),

        }

    )

@router.post("/internal-chat/messages")

def post_internal_chat_message(

    content: str = Form(""),

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    message_text = content.strip()

    if not message_text:

        raise HTTPException(status_code=400, detail="Mensaje vacio")

    if len(message_text) > 2000:

        raise HTTPException(status_code=400, detail="Mensaje demasiado largo")

    row = InternalChatMessage(

        sender_id=current_user.id,

        content=message_text,

    )

    db.add(row)

    db.commit()

    db.refresh(row)

    unread_count = _get_internal_chat_unread_count(db, current_user.id)

    return JSONResponse(

        {

            "ok": True,

            "message": _serialize_internal_chat_message(row),

            "unread_count": unread_count,

        }

    )

@router.get("/ticket-alerts/unread-count")

def get_ticket_alerts_unread_count(

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    return JSONResponse(

        {

            "unread_count": _get_ticket_alert_unread_count(db, current_user.id),

            "latest_ticket_id": _get_latest_active_ticket_id(db),

        }

    )

@router.get("/ticket-alerts/latest")

def get_ticket_alerts_latest(

    limit: int = 10,

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    safe_limit = max(1, min(limit, 20))

    # Inicializa estado de lectura en primer uso para evitar backlog historico.
    _get_ticket_alert_unread_count(db, current_user.id)

    read_state = db.get(TicketAlertReadState, current_user.id)

    last_seen_ticket_id = max(

        0,

        int(read_state.last_seen_ticket_id or 0),

    ) if read_state else 0

    rows = (

        db.query(Ticket)

        .filter(

            Ticket.is_deleted == False,

            Ticket.is_spam == False,

        )

        .order_by(Ticket.created_at.desc(), Ticket.id.desc())

        .limit(safe_limit)

        .all()

    )

    alerts: list[dict[str, str | int | bool]] = []

    for row in rows:

        created_at_display = "-"

        created_at = row.created_at

        if created_at:

            try:

                created_at_display = created_at.astimezone().strftime("%d-%m-%Y %H:%M")

            except Exception:

                created_at_display = created_at.strftime("%d-%m-%Y %H:%M")

        alerts.append(

            {

                "ticket_id": row.id,

                "subject": row.subject or "Sin asunto",

                "status": row.status or "open",

                "source": (row.source or "email").strip().lower(),

                "created_at_display": created_at_display,

                "url": f"/dashboard/tickets/{row.id}",

                "unread": row.id > last_seen_ticket_id,

            }

        )

    return JSONResponse(

        {

            "alerts": alerts,

            "last_seen_ticket_id": last_seen_ticket_id,

        }

    )

@router.post("/ticket-alerts/mark-read")

def mark_ticket_alerts_as_read(

    last_ticket_id: int | None = Form(None),

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    unread_count = _mark_ticket_alerts_as_read(

        db=db,

        user_id=current_user.id,

        last_ticket_id=last_ticket_id,

    )

    return JSONResponse(

        {

            "ok": True,

            "unread_count": unread_count,

        }

    )

# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‹Å“Ãƒâ€šÃ‚Â¨ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚ÂÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢Ãƒâ€šÃ‚Â» TICKETS (AGENTE: solo asignados | ADMIN: todos)

# ======================================================

@router.get("/tickets", response_class=HTMLResponse)

def tickets_view(

    request: Request,

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

    status: str | None = None,

    q: str | None = None,

):

    query = db.query(Ticket)

    # ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ RestricciÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³n por rol

    if not current_user.is_admin:

        query = query.filter(Ticket.assigned_to_id == current_user.id)

    if status:

        query = query.filter(Ticket.status == status)

    if q:

        query = query.filter(or_(Ticket.subject.ilike(f"%{q}%")))

    tickets = query.order_by(Ticket.created_at.desc()).all()

    for t in tickets:

        _normalize_requester_name(t.requester)

    return templates.TemplateResponse(

        "tickets.html",

        {

            "request": request,

            "user": current_user,

            "tickets": tickets,

            "status": status,

            "q": q or "",

        },

    )

# ======================================================

# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒâ€¦Ã‚Â½ DETALLE DE TICKET (admin o dueÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â±o asignado)

# ======================================================

# ======================================================

def _format_note_datetime(raw_value: str | None) -> str:

    if not raw_value:

        return "Sin fecha"

    value = raw_value.strip()

    if not value:

        return "Sin fecha"

    try:

        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

    except ValueError:

        return value

    if parsed.tzinfo is None:

        return parsed.strftime("%d-%m-%Y %H:%M")

    return parsed.astimezone().strftime("%d-%m-%Y %H:%M")

def _parse_requester_notes(raw_notes: str | None) -> list[dict[str, str | int]]:
    if not raw_notes:
        return []

    notes_text = raw_notes.strip()
    if not notes_text:
        return []

    try:
        parsed = json.loads(notes_text)
    except json.JSONDecodeError:
        parsed = None

    notes: list[dict[str, str]] = []

    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue

            text = str(item.get("text", "")).strip()
            if not text:
                continue

            author = str(item.get("author", "")).strip() or "Agente"
            created_at_raw = str(item.get("created_at", "")).strip()
            notes.append(
                {
                    "text": text,
                    "author": author,
                    "author_id": int(item.get("author_id") or 0),
                    "created_at": created_at_raw,
                    "created_at_display": _format_note_datetime(created_at_raw),
                }
            )
        return notes

    return [
        {
            "text": notes_text,
            "author": "Nota previa",
            "created_at": "",
            "created_at_display": "Sin fecha",
        }
    ]


def _serialize_requester_notes(notes: list[dict[str, str | int]]) -> str:
    payload: list[dict[str, str | int]] = []
    for note in notes:
        text = str(note.get("text", "")).strip()
        if not text:
            continue

        payload.append(
            {
                "text": text,
                "author": str(note.get("author", "")).strip() or "Agente",
                "author_id": int(note.get("author_id") or 0),
                "created_at": str(note.get("created_at", "")).strip(),
            }
        )

    return json.dumps(payload, ensure_ascii=False)


def _normalize_requester_name(requester: Requester | None) -> None:
    if not requester or not requester.name:
        return
    decoded = decode_mime_words(requester.name)
    if decoded:
        requester.name = decoded


PENDING_TICKET_STATUSES = ("pending", "pending_service", "pending_client")
RESOLVED_TICKET_STATUSES = ("resolved", "resolved_service", "resolved_client")
TICKET_STATUS_LABELS = {
    "open": "Open",
    "pending": "Pendiente",
    "pending_service": "Pendiente Servicio",
    "pending_client": "Pendiente Cliente",
    "resolved": "Resuelto",
    "resolved_service": "Resuelto Servicio",
    "resolved_client": "Resuelto Cliente",
    "closed": "Cerrado",
}


def _normalize_ticket_status_code(status: str | None) -> str:
    raw = re.sub(r"\s+", " ", (status or "").strip())
    key = raw.casefold()
    key = unicodedata.normalize("NFD", key)
    key = "".join(ch for ch in key if unicodedata.category(ch) != "Mn")
    key = key.replace(" ", "_").replace("-", "_")
    aliases = {
        "open": "open",
        "abierto": "open",
        "pending": "pending",
        "pendiente": "pending",
        "pending_service": "pending_service",
        "pendiente_servicio": "pending_service",
        "pending_client": "pending_client",
        "pendiente_cliente": "pending_client",
        "resolved": "resolved",
        "resuelto": "resolved",
        "resolved_service": "resolved_service",
        "resuelto_servicio": "resolved_service",
        "resuleto_servicio": "resolved_service",
        "resolved_client": "resolved_client",
        "resuelto_cliente": "resolved_client",
        "cerrado": "closed",
        "closed": "closed",
    }
    return aliases.get(key, key or "open")


def _ticket_status_label(status: str | None) -> str:
    code = _normalize_ticket_status_code(status)
    return TICKET_STATUS_LABELS.get(code, (status or "Open").strip() or "Open")


def _ticket_status_css(status: str | None) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", _normalize_ticket_status_code(status)).strip("-") or "open"


def _ticket_status_group(status: str | None) -> str:
    code = _normalize_ticket_status_code(status)
    if code in PENDING_TICKET_STATUSES:
        return "pending"
    if code in RESOLVED_TICKET_STATUSES or code == "closed":
        return "resolved"
    return code or "open"


templates.env.globals["ticket_status_label"] = _ticket_status_label
templates.env.globals["ticket_status_css"] = _ticket_status_css
templates.env.globals["ticket_status_group"] = _ticket_status_group


def _ticket_stage(ticket: Ticket) -> str:
    # Mapea el ticket al "estado visual" del tablero Etapa.
    if ticket.is_deleted:
        return "papelera"
    if ticket.is_spam:
        return "spam"
    return _ticket_status_group(ticket.status)


def _ticket_is_locked(ticket: Ticket | None) -> bool:
    if not ticket:
        return False
    status_code = _normalize_ticket_status_code(ticket.status)
    return status_code in RESOLVED_TICKET_STATUSES or status_code == "closed"


def _enforce_status_transition_rules(ticket: Ticket, new_status: str) -> None:
    # Regla de negocio: no permitimos Open -> Resolved directo.
    # Debe pasar por Pending para asegurar contacto previo con cliente.
    old_status = _normalize_ticket_status_code(ticket.status)
    target_status = _normalize_ticket_status_code(new_status)
    if old_status == "open" and (target_status in RESOLVED_TICKET_STATUSES or target_status == "closed"):
        raise HTTPException(
            status_code=400,
            detail="No se puede mover de Open a Resuelto directamente. Primero debe pasar por Pendiente.",
        )


def _serialize_internal_chat_message(msg: InternalChatMessage) -> dict[str, str | int | None]:
    created_at = msg.created_at
    created_at_display = "-"
    created_at_iso = ""
    if created_at is not None:
        if created_at.tzinfo is None:
            created_at_display = created_at.strftime("%d-%m-%Y %H:%M:%S")
        else:
            created_at_display = created_at.astimezone().strftime("%d-%m-%Y %H:%M:%S")
        created_at_iso = created_at.isoformat()

    return {
        "id": msg.id,
        "sender_id": msg.sender_id,
        "sender_name": msg.sender.name if msg.sender and msg.sender.name else "Usuario",
        "content": msg.content,
        "created_at": created_at_iso,
        "created_at_display": created_at_display,
    }


@router.get("/dashboard/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_detail(
    request: Request,
    ticket_id: int,
    focus_message_id: int | None = None,
    db: Session = Depends(get_db),
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    # Ticket actual
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id
    ).first()

    if not ticket:
        return HTMLResponse("Ticket no encontrado", status_code=404)

    _normalize_requester_name(ticket.requester)

    # Ticket anterior
    previous_ticket = (
        db.query(Ticket)
        .filter(
            Ticket.id < ticket_id,
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
        )
        .order_by(Ticket.id.desc())
        .first()
    )

    # Ticket siguiente
    next_ticket = (
        db.query(Ticket)
        .filter(
            Ticket.id > ticket_id,
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
        )
        .order_by(Ticket.id.asc())
        .first()
    )

    # Loop inteligente
    if not previous_ticket:
        previous_ticket = (
            db.query(Ticket)
            .filter(
                Ticket.is_deleted == False,
                Ticket.is_spam == False,
            )
            .order_by(Ticket.id.desc())
            .first()
        )

    if not next_ticket:
        next_ticket = (
            db.query(Ticket)
            .filter(
                Ticket.is_deleted == False,
                Ticket.is_spam == False,
            )
            .order_by(Ticket.id.asc())
            .first()
        )

    # Mensajes
    messages = (
        db.query(Message)
        .options(joinedload(Message.sender))
        .filter(
            Message.ticket_id == ticket_id,
            Message.is_internal_note == False,
        )
        .order_by(Message.created_at.asc())
        .all()
    )

    latest_message_id = messages[-1].id if messages else 0
    ticket_read_state = (
        db.query(TicketMessageReadState)
        .filter(
            TicketMessageReadState.user_id == current_user.id,
            TicketMessageReadState.ticket_id == ticket_id,
        )
        .first()
    )
    previous_last_seen_message_id = int(ticket_read_state.last_seen_message_id or 0) if ticket_read_state else 0
    unseen_reply_message_ids = [
        int(m.id)
        for m in messages
        if int(m.id or 0) > previous_last_seen_message_id
        and (m.sender_id is None or int(m.sender_id) != int(current_user.id))
    ]
    if ticket_read_state is None:
        ticket_read_state = TicketMessageReadState(
            user_id=current_user.id,
            ticket_id=ticket_id,
            last_seen_message_id=latest_message_id,
        )
        db.add(ticket_read_state)
        db.commit()
    elif latest_message_id > (ticket_read_state.last_seen_message_id or 0):
        ticket_read_state.last_seen_message_id = latest_message_id
        db.commit()

    for m in messages:
        if not m.content:
            continue

        content = m.content.strip()
        if (m.channel or "").strip().lower() == "email":
            content = _strip_ticket_thread_tail_for_display(content, ticket_id=ticket.id)

        # Si ya es HTML real -> no tocar.
        if "<html" in content or "<div" in content or "<table" in content or "<a " in content or "<img" in content:
            m.content = Markup(content)
            continue

        # Eliminar patrones tipo [image: ...]
        content = re.sub(r"\[image:\s*.*?\]", "", content)

        # Convertir URLs en links
        def make_link(match):
            url = match.group(0)
            return f'<a href="{url}" target="_blank" style="color:#2563eb;font-weight:600;">{url}</a>'

        content = re.sub(r"https?://[^\s]+", make_link, content)

        # Saltos de linea
        content = content.replace("\n", "<br>")

        m.content = Markup(content)

    requester_notes = _parse_requester_notes(ticket.requester.notes if ticket.requester else None)
    total_internal_notes = len(requester_notes)
    internal_note_state = (
        db.query(TicketInternalNoteReadState)
        .filter(
            TicketInternalNoteReadState.user_id == current_user.id,
            TicketInternalNoteReadState.ticket_id == ticket.id,
        )
        .first()
    )
    seen_internal_notes = int(internal_note_state.last_seen_note_count or 0) if internal_note_state else 0
    unseen_internal_notes = 0
    if total_internal_notes > seen_internal_notes:
        for idx, note in enumerate(requester_notes, start=1):
            if idx <= seen_internal_notes:
                continue
            note_author_id = int(note.get("author_id") or 0) if isinstance(note, dict) else 0
            if note_author_id and note_author_id == current_user.id:
                continue
            unseen_internal_notes += 1

    message_read_state = (
        db.query(TicketMessageReadState)
        .filter(
            TicketMessageReadState.user_id == current_user.id,
            TicketMessageReadState.ticket_id == ticket.id,
        )
        .first()
    )
    last_seen_message_id = int(message_read_state.last_seen_message_id or 0) if message_read_state else 0
    unseen_reply_count = (
        db.query(func.count(Message.id))
        .filter(
            Message.ticket_id == ticket.id,
            Message.is_internal_note == False,
            Message.id > last_seen_message_id,
            or_(Message.sender_id.is_(None), Message.sender_id != current_user.id),
        )
        .scalar()
        or 0
    )
    unseen_total_count = int(unseen_internal_notes) + int(unseen_reply_count)

    requester_tickets = (
        db.query(Ticket)
        .filter(Ticket.requester_id == ticket.requester_id)
        .order_by(Ticket.created_at.desc())
        .all()
    )

    status_counts = {
        "open": 0,
        "pending": 0,
        "pending_service": 0,
        "pending_client": 0,
        "resolved": 0,
    }
    for requester_ticket in requester_tickets:
        status_code = _normalize_ticket_status_code(requester_ticket.status)
        if status_code in status_counts:
            status_counts[status_code] += 1

    requester_display_name = ticket.requester.display_name if ticket.requester else ""
    requester_info = {
        "id": ticket.requester.id if ticket.requester else None,
        "name": ticket.requester.name if ticket.requester else "",
        "internal_name": ticket.requester.internal_name if ticket.requester else "",
        "display_name": requester_display_name,
        "email": ticket.requester.email if ticket.requester else "",
        "total_tickets": len(requester_tickets),
        "first_ticket_at": requester_tickets[-1].created_at if requester_tickets else None,
        "last_ticket_at": requester_tickets[0].created_at if requester_tickets else None,
        "status_counts": status_counts,
        "tickets": requester_tickets,
    }
    requester_name_catalog: list[str] = []
    try:
        catalog_rows = incidencias_db.execute(
            text(
                """
                SELECT nombre_sucursal
                FROM catalogo_clientes
                WHERE COALESCE(TRIM(nombre_sucursal), '') <> ''
                ORDER BY nombre_sucursal ASC
                """
            )
        ).fetchall()
        seen_names: set[str] = set()
        for row in catalog_rows:
            value = re.sub(r"\s+", " ", _support_text(row[0])).strip()
            if not value:
                continue
            key = value.casefold()
            if key in seen_names:
                continue
            seen_names.add(key)
            requester_name_catalog.append(value)
    except Exception:
        requester_name_catalog = []

    linked_odt: dict | None = None
    odt_derivacion_tipo: str | None = None
    correos_enviados: list[dict] = []
    correos_count: int = 0
    try:
        # Extraer número de ODT y tipo de derivación desde las notas de auditoría.
        # El formato de la nota es: "Derivado a {destino}. ODT: {odt}."
        odt_value_linked: str | None = None
        audit_msgs = (
            db.query(Message)
            .filter(
                Message.ticket_id == ticket_id,
                Message.is_internal_note == True,
            )
            .order_by(Message.id.desc())
            .all()
        )
        for _msg in audit_msgs:
            _content = _msg.content or ""
            if not odt_value_linked:
                _m_odt = re.search(r"ODT:\s*([\w\-]+)", _content)
                if _m_odt:
                    odt_value_linked = _m_odt.group(1).strip()
            if not odt_derivacion_tipo:
                _m_deriv = re.search(r"Derivado a ([^.]+)\.", _content)
                if _m_deriv:
                    odt_derivacion_tipo = _m_deriv.group(1).strip()
            if odt_value_linked and odt_derivacion_tipo:
                break

        if odt_value_linked:
            odt_table = _support_incidencias_table(incidencias_db)
            if "odt" in odt_table.c:
                odt_row = incidencias_db.execute(
                    select(*odt_table.c).where(odt_table.c.odt == odt_value_linked)
                ).mappings().first()
                if odt_row:
                    linked_odt = dict(odt_row)

        if odt_derivacion_tipo:
            _outgoing = (
                db.query(Message)
                .filter(
                    Message.ticket_id == ticket_id,
                    Message.channel == "email",
                    Message.sender_type == "agent",
                    Message.is_internal_note == False,
                )
                .order_by(Message.created_at.asc())
                .all()
            )
            correos_count = len(_outgoing)
            _requester_addr = (ticket.requester.email if ticket.requester else "") or ""
            _subject_label = _build_ticket_email_subject(ticket.subject, ticket.id)
            for _em in _outgoing:
                correos_enviados.append({
                    "fecha": _em.created_at,
                    "destinatario": _requester_addr,
                    "asunto": _subject_label,
                    "contenido": _em.content or "",
                })
    except Exception:
        linked_odt = None
        odt_derivacion_tipo = None
        correos_enviados = []
        correos_count = 0

    assignable_users = (
        db.query(User)
        .filter(User.is_active == True)
        .order_by(User.name.asc())
        .all()
    )
    internal_chat_unread_count = _get_internal_chat_unread_count(db, current_user.id)
    ticket_alert_unread_count = _get_ticket_alert_unread_count(db, current_user.id)
    raw_send_error = request.query_params.get("send_error")
    send_error = None
    if raw_send_error:
        send_error = re.sub(
            r"\s+",
            " ",
            raw_send_error.replace("\r", " ").replace("\n", " "),
        ).strip()
        if send_error:
            send_error = send_error[:280]
    raw_service_success = request.query_params.get("service_success")
    service_success = None
    if raw_service_success:
        service_success = re.sub(
            r"\s+",
            " ",
            raw_service_success.replace("\r", " ").replace("\n", " "),
        ).strip()
        if service_success:
            service_success = service_success[:280]
    raw_service_error = request.query_params.get("service_error")
    service_error = None
    if raw_service_error:
        service_error = re.sub(
            r"\s+",
            " ",
            raw_service_error.replace("\r", " ").replace("\n", " "),
        ).strip()
        if service_error:
            service_error = service_error[:280]

    requires_reception = (ticket.source or "").strip().lower() == "email"
    reception_sent = _has_reception_sent(db, ticket.id) if requires_reception else True
    ticket_locked = _ticket_is_locked(ticket)

    return templates.TemplateResponse(
        "ticket_detail.html",
        {
            "request": request,
            "user": current_user,
            "ticket": ticket,
            "messages": messages,
            "requester_notes": requester_notes,
            "unseen_internal_notes": unseen_internal_notes,
            "unseen_reply_count": unseen_reply_count,
            "unseen_total_count": unseen_total_count,
            "requester_info": requester_info,
            "requester_name_catalog": requester_name_catalog,
            "assignable_users": assignable_users,
            "internal_chat_unread_count": internal_chat_unread_count,
            "ticket_alert_unread_count": ticket_alert_unread_count,
            "previous_ticket_id": previous_ticket.id if previous_ticket else None,
            "next_ticket_id": next_ticket.id if next_ticket else None,
            "send_error": send_error,
            "service_success": service_success,
            "service_error": service_error,
            "requires_reception": requires_reception,
            "reception_sent": reception_sent,
            "can_reply": reception_sent,
            "ticket_locked": ticket_locked,
            "focus_message_id": focus_message_id,
            "unseen_reply_message_ids": unseen_reply_message_ids,
            "linked_odt": linked_odt,
            "odt_derivacion_tipo": odt_derivacion_tipo,
            "correos_enviados": correos_enviados,
            "correos_count": correos_count,
        },
    )


@router.post("/tickets/requesters/{requester_id}/notes")

def add_requester_internal_note(

    requester_id: int,

    ticket_id: int = Form(...),

    note: str = Form(""),

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    requester = db.query(Requester).filter(Requester.id == requester_id).first()

    if not requester:

        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()

    if not ticket:

        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    if ticket.requester_id != requester_id:

        raise HTTPException(status_code=400, detail="Ticket y cliente no coinciden")

    note_text = note.strip()

    if note_text:

        notes = _parse_requester_notes(requester.notes)

        notes.append(

            {

                "text": note_text,

                "author": current_user.name or current_user.username,
                "author_id": current_user.id,

                "created_at": datetime.now(timezone.utc).isoformat(),

            }

        )

        requester.notes = _serialize_requester_notes(notes)

        db.commit()

    return RedirectResponse(

        url=f"/dashboard/tickets/{ticket_id}",

        status_code=303,

    )


@router.post("/dashboard/tickets/{ticket_id}/internal-notes/mark-read")
def mark_internal_notes_as_read(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    notes = _parse_requester_notes(ticket.requester.notes if ticket.requester else None)
    note_count = len(notes)

    read_state = (
        db.query(TicketInternalNoteReadState)
        .filter(
            TicketInternalNoteReadState.user_id == current_user.id,
            TicketInternalNoteReadState.ticket_id == ticket_id,
        )
        .first()
    )
    if read_state is None:
        read_state = TicketInternalNoteReadState(
            user_id=current_user.id,
            ticket_id=ticket_id,
            last_seen_note_count=note_count,
        )
        db.add(read_state)
    elif note_count > (read_state.last_seen_note_count or 0):
        read_state.last_seen_note_count = note_count

    latest_reply_message = (
        db.query(Message.id)
        .filter(
            Message.ticket_id == ticket_id,
            Message.is_internal_note == False,
        )
        .order_by(Message.id.desc())
        .first()
    )
    latest_reply_message_id = int(latest_reply_message[0]) if latest_reply_message and latest_reply_message[0] else 0
    message_read_state = (
        db.query(TicketMessageReadState)
        .filter(
            TicketMessageReadState.user_id == current_user.id,
            TicketMessageReadState.ticket_id == ticket_id,
        )
        .first()
    )
    if message_read_state is None:
        message_read_state = TicketMessageReadState(
            user_id=current_user.id,
            ticket_id=ticket_id,
            last_seen_message_id=latest_reply_message_id,
        )
        db.add(message_read_state)
    elif latest_reply_message_id > (message_read_state.last_seen_message_id or 0):
        message_read_state.last_seen_message_id = latest_reply_message_id

    db.commit()
    return JSONResponse({"ok": True, "unseen_internal_notes": 0, "unseen_reply_count": 0, "unseen_total_count": 0})


@router.post("/tickets/requesters/{requester_id}/internal-name")
def update_requester_internal_name(
    requester_id: int,
    ticket_id: int = Form(...),
    internal_name: str = Form(""),
    db: Session = Depends(get_db),
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    # Guarda alias interno del cliente para todo el equipo.
    requester = db.query(Requester).filter(Requester.id == requester_id).first()
    if not requester:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    if ticket.requester_id != requester_id:
        raise HTTPException(status_code=400, detail="Ticket y cliente no coinciden")

    sanitized_alias = re.sub(r"\s+", " ", (internal_name or "")).strip()[:120]
    if sanitized_alias:
        row = incidencias_db.execute(
            text(
                """
                SELECT nombre_sucursal
                FROM catalogo_clientes
                WHERE LOWER(TRIM(nombre_sucursal)) = LOWER(TRIM(:alias))
                LIMIT 1
                """
            ),
            {"alias": sanitized_alias},
        ).fetchone()
        if not row or not row[0]:
            raise HTTPException(
                status_code=400,
                detail="El nombre debe existir en catalogo_clientes.",
            )
        requester.internal_name = re.sub(r"\s+", " ", _support_text(row[0])).strip()[:120]
    else:
        requester.internal_name = None
    db.commit()

    return RedirectResponse(
        url=f"/dashboard/tickets/{ticket_id}",
        status_code=303,
    )

# ======================================================

# ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ ACTUALIZAR ESTADO (admin o asignado)

# ======================================================

@router.post("/dashboard/tickets/{ticket_id}/status")

def update_status(

    ticket_id: int,

    status: str = Form(...),

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    ticket = db.query(Ticket).filter(

        Ticket.id == ticket_id,

        Ticket.is_deleted == False

    ).first()

    if not ticket:

        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    status = _normalize_ticket_status_code(status)
    _enforce_status_transition_rules(ticket, status)
    change = apply_ticket_status_change(ticket, status)

    became_resolved = bool(change["became_resolved"])

    # ==============================

    # ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ RESOLUCIÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œN

    # ==============================

    # La transicion completa vive en ticket_status_service.

    # ==============================

    # ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ REAPERTURA

    # ==============================

    # La transicion completa vive en ticket_status_service.

    # ==============================

    # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢Ãƒâ€šÃ‚Â¾ GUARDAR SIEMPRE

    # ==============================

    db.commit()

    if became_resolved:

        try:

            _send_sla_satisfaction_email(ticket)

        except Exception as exc:

            print("Error enviando encuesta SLA:", exc)

            print(traceback.format_exc())

    return RedirectResponse(

        url=f"/dashboard/tickets/{ticket_id}",

        status_code=303

    )

# ======================================================

# ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ ACTUALIZAR PRIORIDAD (admin o asignado)

# ======================================================

@router.post("/dashboard/tickets/{ticket_id}/assign-me")

def assign_to_me(

    ticket_id: int,

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    ticket = db.get(Ticket, ticket_id)

    if not ticket:

        return HTMLResponse("Ticket no encontrado", status_code=404)

    assign_ticket_logic(db, ticket, current_user.id, current_user)

    db.commit()

    return RedirectResponse(

        url=f"/dashboard/tickets/{ticket_id}",

        status_code=303,

    )

@router.post("/dashboard/tickets/{ticket_id}/assign")

def assign_ticket(

    ticket_id: int,

    user_id: int | None = Form(None),

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    ticket = db.get(Ticket, ticket_id)

    if not ticket:

        return HTMLResponse("Ticket no encontrado", status_code=404)

    if _ticket_is_locked(ticket):
        return RedirectResponse(
            url=f"/dashboard/tickets/{ticket_id}?service_error=El+ticket+ya+esta+resuelto+y+no+permite+cambios.",
            status_code=303,
        )

    assign_ticket_logic(db, ticket, user_id, current_user)

    db.commit()

    return RedirectResponse(

        url=f"/dashboard/tickets/{ticket_id}",

        status_code=303,

    )

# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾ ACTUALIZAR PRIORIDAD

# ======================================================

@router.post("/dashboard/tickets/{ticket_id}/priority")

def update_priority(

    ticket_id: int,

    priority: str = Form(...),

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    ticket = db.query(Ticket).filter(

        Ticket.id == ticket_id,

        Ticket.is_deleted == False

    ).first()

    if not ticket:

        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    if _ticket_is_locked(ticket):
        return RedirectResponse(
            url=f"/dashboard/tickets/{ticket_id}?service_error=El+ticket+ya+esta+resuelto+y+no+permite+cambios.",
            status_code=303,
        )

    # ValidaciÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³n bÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¡sica

    allowed_priorities = ["low", "medium", "high", "urgent"]

    if priority not in allowed_priorities:

        raise HTTPException(status_code=400, detail="Prioridad invÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¡lida")

    ticket.priority = priority

    db.commit()

    return RedirectResponse(

        url=f"/dashboard/tickets/{ticket_id}",

        status_code=303

    )

@router.post("/dashboard/tickets/{ticket_id}/quick-actions")

def update_quick_actions(

    ticket_id: int,

    status: str = Form(...),

    priority: str = Form(...),

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    ticket = db.query(Ticket).filter(

        Ticket.id == ticket_id,

        Ticket.is_deleted == False

    ).first()

    if not ticket:

        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    if _ticket_is_locked(ticket):
        return RedirectResponse(
            url=f"/dashboard/tickets/{ticket_id}?service_error=El+ticket+ya+esta+resuelto+y+no+permite+cambios.",
            status_code=303,
        )

    status = _normalize_ticket_status_code(status)
    allowed_status = [
        "open",
        "pending",
        "pending_service",
        "pending_client",
        "resolved",
        "resolved_service",
        "resolved_client",
    ]

    if status not in allowed_status:

        raise HTTPException(status_code=400, detail="Estado invalido")

    allowed_priorities = ["low", "medium", "high", "urgent"]

    if priority not in allowed_priorities:

        raise HTTPException(status_code=400, detail="Prioridad invalida")

    _enforce_status_transition_rules(ticket, status)
    change = apply_ticket_status_change(ticket, status)

    became_resolved = bool(change["became_resolved"])

    ticket.priority = priority

    # La transicion completa vive en ticket_status_service.

    db.commit()

    if became_resolved:

        try:

            _send_sla_satisfaction_email(ticket)

        except Exception as exc:

            print("Error enviando encuesta SLA:", exc)

            print(traceback.format_exc())

    return RedirectResponse(

        url=f"/dashboard/tickets/{ticket_id}",

        status_code=303

    )

@router.post("/dashboard/tickets/{ticket_id}/send-to-service")
def send_ticket_to_service(
    ticket_id: int,
    cliente: str = Form(""),
    problema: str = Form(""),
    problema_detalle: str = Form(""),
    direccion: str = Form(""),
    observacion: str = Form(""),
    tecnico: str = Form(""),
    estado: str = Form("Pendiente"),
    derivacion: str = Form("Servicio Técnico"),
    db: Session = Depends(get_db),
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        return HTMLResponse("Ticket no encontrado", status_code=404)

    if _ticket_is_locked(ticket):
        query = urlencode({"service_error": "El ticket ya esta resuelto y no permite cambios."})
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    if ticket.is_deleted:
        query = urlencode({"service_error": "No se puede derivar un ticket en papelera."})
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    if ticket.is_spam:
        query = urlencode({"service_error": "No se puede derivar un ticket marcado como spam."})
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    requester_name = ""
    if ticket.requester and ticket.requester.display_name:
        requester_name = ticket.requester.display_name.strip()
    elif ticket.requester and ticket.requester.name:
        requester_name = ticket.requester.name.strip()

    derivacion_normalized = re.sub(r"\s+", " ", (derivacion or "").strip()).casefold()
    if derivacion_normalized in {"servicio tecnico", "servicio técnico"}:
        derivacion_clean = "Servicio Técnico"
    elif derivacion_normalized == "cliente":
        derivacion_clean = "Cliente"
    else:
        query = urlencode({"service_error": "Derivacion invalida. Usa Servicio Técnico o Cliente."})
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    cliente_clean = re.sub(r"\s+", " ", (cliente or "").strip())
    if not cliente_clean and derivacion_clean == "Servicio Técnico":
        cliente_clean = requester_name

    def normalize_problem_key(value: str) -> str:
        normalized = unicodedata.normalize("NFD", value or "")
        normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        normalized = re.sub(r"\s+", " ", normalized).strip().lower()
        return normalized

    problem_map = {
        "desconexion": "Desconexión",
        "problema de visual": "Problema de visual",
        "problema de parlante": "Problema de Parlante",
        "problema de alarma": "Problema de Alarma",
        "hora y/o fecha cambiada": "Hora y/o Fecha Cambiada",
    }
    disconnection_detail_map = {
        "desconocida": "Desconocida",
        "electricidad": "Electricidad",
        "internet": "Internet",
    }
    visual_detail_map = {
        "falla de video": "Falla de video",
        "obstruccion": "Obstruccion",
        "intermitencia": "Intermitencia",
        "ivs": "IVS",
        "camara sucia": "Camara sucia",
        "camara movida": "Camara Movida",
        "bateria baja": "Bateria Baja",
    }

    problema_base_raw = re.sub(r"\s+", " ", (problema or "").strip())
    problema_key = normalize_problem_key(problema_base_raw)
    problema_clean = problem_map.get(problema_key, "")
    if not problema_clean:
        query = urlencode({"service_error": "Tipo de problema invalido."})
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    problema_detalle_raw = re.sub(r"\s+", " ", (problema_detalle or "").strip())
    problema_detalle_key = normalize_problem_key(problema_detalle_raw)
    problema_detalle_clean = ""
    requires_detail = problema_clean in {"Desconexión", "Problema de visual"}
    if requires_detail:
        if problema_clean == "Desconexión":
            problema_detalle_clean = disconnection_detail_map.get(problema_detalle_key, "")
        else:
            problema_detalle_clean = visual_detail_map.get(problema_detalle_key, "")
        if not problema_detalle_clean:
            query = urlencode({"service_error": "Debes seleccionar el detalle del problema."})
            return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    direccion_clean = re.sub(r"\s+", " ", (direccion or "").strip())
    tecnico_clean = re.sub(r"\s+", " ", (tecnico or "").strip())
    estado_clean = "Pendiente"
    # Evita que el historial se parta en multiples bloques por saltos de linea.
    observacion_clean = re.sub(r"\s+", " ", (observacion or "").strip())

    if not cliente_clean:
        query = urlencode({"service_error": "Debes indicar el cliente para crear la incidencia."})
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    if not problema_clean:
        query = urlencode({"service_error": "Debes indicar el problema para crear la incidencia."})
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    observation_prefix = ""
    if problema_clean == "Desconexión":
        observation_prefix = f"Debido a {problema_detalle_clean.lower()} de: "
    elif problema_clean == "Problema de visual":
        observation_prefix = f"Problema de visual: {problema_detalle_clean}. "
    if observation_prefix and not observacion_clean.startswith(observation_prefix):
        observacion_clean = f"{observation_prefix}{observacion_clean.lstrip()}"

    user_label = (current_user.name or current_user.username or "Soporte").strip() or "Soporte"
    observacion_payload = (
        _support_append_user_observation("", user_label, observacion_clean)
        if observacion_clean
        else ""
    )

    now_local = datetime.now().astimezone()
    now_label = now_local.strftime("%d-%m-%Y %H:%M")

    try:
        table = _support_incidencias_table(incidencias_db)
        odt_value = _support_next_odt_value(incidencias_db, table)
        table_columns = set(table.c.keys())
        values_to_insert: dict[str, object] = {}

        def set_first(keys: tuple[str, ...], value: object) -> None:
            if value is None:
                return
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    return
            for key in keys:
                if key in table_columns and key not in values_to_insert:
                    values_to_insert[key] = value
                    return

        if derivacion_clean == "Cliente" and not direccion_clean:
            direccion_clean = _support_find_direccion_by_cliente(incidencias_db, table, cliente_clean)

        set_first(("odt",), odt_value)
        set_first(("fecha", "fecha_registro"), now_label)
        set_first(("cliente", "sucursal", "puesto"), cliente_clean)
        set_first(("problema", "tipo_incidencia"), problema_clean)
        set_first(("detalle_problema",), problema_detalle_clean)
        set_first(("derivacion",), derivacion_clean)
        # No inyectamos texto automatico en Registro Operaciones.
        # Gestion Soporte queda en observacion_soporte con firma de usuario/fecha.
        set_first(("direccion",), direccion_clean)
        set_first(("estado",), estado_clean)
        set_first(("fecha_derivacion_area", "fecha_derivacion_tecnico", "fecha_derivacion"), now_label)
        set_first(("derivado_por",), current_user.name or current_user.username or "Soporte")
        set_first(("observacion_soporte",), observacion_payload)
        set_first(("source_file",), "tickets")
        set_first(("source_row",), ticket.id)

        if not values_to_insert:
            raise RuntimeError("No se detectaron columnas compatibles para crear la incidencia.")

        incidencias_db.execute(table.insert().values(**values_to_insert))
        incidencias_db.commit()
    except Exception as exc:
        incidencias_db.rollback()
        error_text = re.sub(
            r"\s+",
            " ",
            str(exc or "No se pudo crear la incidencia").replace("\r", " ").replace("\n", " "),
        ).strip()
        query = urlencode({"service_error": f"No se pudo crear la incidencia: {error_text[:200]}"})
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    correo_info = ""
    try:
        ticket_status = "pending_client" if derivacion_clean == "Cliente" else "pending_service"
        apply_ticket_status_change(ticket, ticket_status)
        audit_note = Message(
            ticket_id=ticket.id,
            sender_type="agent",
            sender_id=current_user.id,
            channel="internal",
            content=f"Derivado a {derivacion_clean}. ODT: {odt_value}.",
            is_internal_note=True,
        )
        db.add(audit_note)
        requester_email = parseaddr(ticket.requester.email if ticket.requester else "")[1].strip()
        if (ticket.source or "").strip().lower() == "email" and requester_email:
            try:
                from app.integrations.email_smtp import send_email_reply

                requester_name = (
                    ((ticket.requester.name if ticket.requester else "") or "Cliente").strip() or "Cliente"
                )
                area_label = "Servicio Técnico" if derivacion_clean == "Servicio Técnico" else "Coordinación con cliente"
                logo_cid = "logo-atc-derivation"
                detalle_derivacion = f"""
                <div style="margin:0;padding:24px;background:#f8fafc;">
                  <div style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:24px;overflow:hidden;font-family:Arial,sans-serif;color:#0f172a;">
                    <div style="padding:24px 28px;background:linear-gradient(135deg,#0f172a 0%,#1d4ed8 100%);color:#ffffff;">
                      <table role="presentation" cellspacing="0" cellpadding="0" width="100%" style="border-collapse:collapse;">
                        <tr>
                          <td style="vertical-align:top;padding-right:16px;">
                            <div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;opacity:.82;">Soporte ATC</div>
                            <h1 style="margin:10px 0 0;font-size:27px;line-height:1.2;">Actualización de su solicitud</h1>
                            <p style="margin:10px 0 0;font-size:15px;line-height:1.6;opacity:.92;">Ticket #{ticket.id}</p>
                          </td>
                          <td align="right" style="vertical-align:top;">
                            <img src="cid:{logo_cid}" alt="ATC" style="display:block;width:110px;max-width:110px;height:auto;">
                          </td>
                        </tr>
                      </table>
                    </div>
                    <div style="padding:28px;">
                      <p style="margin:0 0 16px;font-size:16px;line-height:1.7;">Hola {html.escape(requester_name)},</p>
                      <p style="margin:0 0 14px;font-size:16px;line-height:1.7;">Le informamos que su solicitud fue derivada al área de <strong>{html.escape(area_label)}</strong> para continuar su gestión.</p>
                      <p style="margin:0 0 14px;font-size:16px;line-height:1.7;">Nuestro equipo continuará el seguimiento de su caso por este mismo ticket y le mantendremos informado ante cualquier actualización.</p>
                      <p style="margin:22px 0 0;font-size:15px;line-height:1.7;">Gracias por contactar con el Soporte de Alguien te cuida.</p>
                    </div>
                  </div>
                </div>
                """
                send_email_reply(
                    to=requester_email,
                    subject=_build_ticket_email_subject(ticket.subject, ticket.id),
                    body=detalle_derivacion,
                    ticket_id=ticket.id,
                    inline_images=[
                        {
                            "cid": logo_cid,
                            "path": "static/img/logo-atc.png",
                        }
                    ],
                )
                db.add(
                    Message(
                        ticket_id=ticket.id,
                        sender_type="agent",
                        sender_id=current_user.id,
                        channel="email",
                        content=detalle_derivacion,
                        is_internal_note=False,
                    )
                )
                correo_info = " Correo enviado al solicitante."
            except Exception as exc:
                error_text = re.sub(r"\s+", " ", str(exc or "error desconocido")).strip()
                db.add(
                    Message(
                        ticket_id=ticket.id,
                        sender_type="system",
                        channel="internal",
                        content=(
                            "No se pudo enviar el correo automatico de derivacion. "
                            f"Detalle: {html.escape(error_text[:220])}"
                        ),
                        is_internal_note=True,
                    )
                )
                correo_info = " No se pudo enviar el correo automatico."
        elif (ticket.source or "").strip().lower() == "email":
            correo_info = " No se envio correo: el solicitante no tiene email registrado."
        db.commit()
    except Exception:
        db.rollback()

    query = urlencode({"service_success": f"Incidencia creada y enviada a {derivacion_clean} (ODT: {odt_value}).{correo_info}"})
    return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

# ======================================================

# ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ RESPONDER TICKET (admin o asignado)

# ======================================================

@router.post("/dashboard/tickets/{ticket_id}/send-reception")
def send_reception_notice(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        return HTMLResponse("Ticket no encontrado", status_code=404)

    if _ticket_is_locked(ticket):
        query = urlencode({"send_error": "El ticket ya esta resuelto y no permite cambios."})
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    if (ticket.source or "").strip().lower() != "email":
        query = urlencode({"send_error": "La recepcion de solicitud solo aplica a tickets por email."})
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    if _has_reception_sent(db, ticket_id):
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}", status_code=303)

    allowed_priorities = {"low", "medium", "high", "urgent"}
    priority_value = (ticket.priority or "").strip().lower()
    if priority_value not in allowed_priorities:
        query = urlencode({"send_error": "Debes seleccionar la prioridad antes de enviar la recepcion de solicitud."})
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    latest_requester_email = (
        db.query(Message)
        .filter(
            Message.ticket_id == ticket_id,
            Message.sender_type == "requester",
            Message.channel == "email",
            Message.external_id.isnot(None),
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .first()
    )

    try:
        sent = send_initial_email_auto_reply(
            db,
            ticket=ticket,
            requester=ticket.requester,
            in_reply_to_external_id=latest_requester_email.external_id if latest_requester_email else None,
            event_name="manual_reception",
        )
        if not sent:
            query = urlencode({"send_error": "No se pudo enviar la recepcion de solicitud."})
            return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)
        db.commit()
    except Exception as exc:
        db.rollback()
        error_detail = re.sub(r"\s+", " ", str(exc).replace("\r", " ").replace("\n", " ")).strip()
        error_detail = (error_detail or "error desconocido")[:220]
        query = urlencode({"send_error": f"No se pudo enviar la recepcion: {error_detail}"})
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}", status_code=303)


@router.post("/dashboard/tickets/{ticket_id}/reply")
def reply_ticket(
    ticket_id: int,
    content: str = Form(""),
    cc: str = Form(""),
    bcc: str = Form(""),
    attachments: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        return HTMLResponse("Ticket no encontrado", status_code=404)

    if _ticket_is_locked(ticket):
        query = urlencode({"send_error": "El ticket ya esta resuelto y no permite cambios."})
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    content = (content or "").strip()
    uploaded_count = len([f for f in (attachments or []) if f and (f.filename or "").strip()])
    saved_attachments: list[dict[str, str | int]] = []
    inline_images_for_email: list[dict[str, str]] = []
    saved_inline_image_paths: list[Path] = []
    email_body_for_send = content
    content_for_db = content

    def _redirect_with_error(message: str) -> RedirectResponse:
        query = urlencode({"send_error": message})
        return RedirectResponse(
            url=f"/dashboard/tickets/{ticket_id}?{query}",
            status_code=303,
        )

    def _cleanup_saved_files() -> None:
        for item in saved_attachments:
            path_value = str(item.get("path") or "").strip()
            if path_value:
                Path(path_value).unlink(missing_ok=True)
        for path in saved_inline_image_paths:
            path.unlink(missing_ok=True)

    ticket_source = (ticket.source or "").strip().lower()
    if ticket_source == "email" and not _has_reception_sent(db, ticket_id):
        return _redirect_with_error("Primero debes enviar 'Recepcion de solicitud' antes de responder.")

    if ticket_source != "email" and uploaded_count:
        return _redirect_with_error("Los adjuntos solo estan disponibles para respuestas por correo.")

    to_recipients: list[str] = []
    cc_recipients: list[str] = []
    bcc_recipients: list[str] = []
    resolved_subject = _build_ticket_email_subject(ticket.subject, ticket.id)

    if ticket_source == "email":
        requester_email = (ticket.requester.email if ticket.requester and ticket.requester.email else "").strip()
        if not requester_email:
            return _redirect_with_error("El ticket no tiene correo del solicitante para responder.")

        try:
            to_recipients = _parse_recipient_list(requester_email, field_name="correo del cliente")
            cc_recipients = _parse_recipient_list(cc, field_name="cc")
            bcc_recipients = _parse_recipient_list(bcc, field_name="bcc")
        except ValueError as exc:
            return _redirect_with_error(str(exc))

        if not to_recipients:
            return _redirect_with_error("El correo del solicitante no es valido para responder.")

        try:
            saved_attachments = _save_email_attachments(ticket_id=ticket_id, uploads=attachments)
        except ValueError as exc:
            return _redirect_with_error(str(exc))
        except Exception:
            return _redirect_with_error("No se pudieron procesar los adjuntos.")

        if content:
            attachments_total_bytes = sum(int(item.get("size") or 0) for item in saved_attachments)
            try:
                (
                    email_body_for_send,
                    content_for_db,
                    inline_images_for_email,
                    saved_inline_image_paths,
                ) = _extract_inline_data_images(
                    ticket_id=ticket_id,
                    html_content=content,
                    initial_total_bytes=attachments_total_bytes,
                )
            except ValueError as exc:
                _cleanup_saved_files()
                return _redirect_with_error(str(exc))
            except Exception:
                _cleanup_saved_files()
                return _redirect_with_error("No se pudieron procesar las imagenes pegadas.")

    if not content and not saved_attachments:
        return _redirect_with_error("Escribe un mensaje o adjunta al menos un archivo.")

    message_channel = (
        ticket_source
        if ticket_source in ("email", "whatsapp", "internal")
        else (ticket.source or "internal")
    )

    out_message_id_db: str | None = None
    in_reply_to_db: str | None = None
    references_db: str | None = None

    if ticket_source == "email":
        email_thread_ids = [
            _norm_msgid(row.external_id)
            for row in (
                db.query(Message)
                .filter(
                    Message.ticket_id == ticket_id,
                    Message.channel == "email",
                    Message.external_id.isnot(None),
                )
                .order_by(Message.created_at.asc(), Message.id.asc())
                .all()
            )
        ]
        email_thread_ids = [item for item in email_thread_ids if item]

        if email_thread_ids:
            in_reply_to_db = email_thread_ids[-1]
            references_db = " ".join(email_thread_ids)

        from_email = parseaddr(settings.SMTP_FROM or settings.SMTP_USER or "")[1].strip()
        if "@" not in from_email:
            from_email = parseaddr(settings.SMTP_USER or "")[1].strip()
        message_domain = from_email.split("@", 1)[1] if "@" in from_email else "localhost"
        out_message_id_db = f"{uuid4()}@{message_domain}"

    if ticket_source == "email" and saved_attachments:
        attachments_html = _build_attachments_html(saved_attachments)
        if content_for_db:
            content_for_db = f"{content_for_db}\n\n{attachments_html}"
        else:
            content_for_db = attachments_html

    msg = Message(
        ticket_id=ticket_id,
        sender_type="agent",
        sender_id=current_user.id,
        channel=message_channel,
        content=content_for_db,
        is_internal_note=False,
        external_id=out_message_id_db,
    )
    db.add(msg)

    ticket.assigned_to_id = current_user.id

    # Toda respuesta del agente deja el ticket en pending
    # usando la misma regla centralizada de estado.
    apply_ticket_status_change(ticket, "pending")
    mark_first_agent_reply(ticket)

    try:
        if ticket_source == "email":
            from app.integrations.email_smtp import send_email_reply

            email_body = email_body_for_send or ("Se adjuntan archivos solicitados." if saved_attachments else "")

            send_email_reply(
                to=to_recipients,
                cc=cc_recipients,
                bcc=bcc_recipients,
                subject=resolved_subject,
                body=email_body,
                message_id=out_message_id_db,
                in_reply_to=in_reply_to_db,
                references=references_db,
                ticket_id=ticket.id,
                inline_images=inline_images_for_email,
                attachments=saved_attachments,
            )

        elif ticket_source == "whatsapp":
            from app.integrations.whatsapp_cloud import send_whatsapp_message

            send_whatsapp_message(
                to_phone=ticket.requester.phone,
                body=content,
            )

        db.commit()

    except Exception as e:
        db.rollback()
        _cleanup_saved_files()
        print(f"Error enviando respuesta ticket #{ticket_id}: {e}")
        print(traceback.format_exc())
        error_detail = str(e).strip() or "error desconocido"
        error_detail = re.sub(r"\s+", " ", error_detail.replace("\r", " ").replace("\n", " "))
        error_detail = error_detail[:220]
        query = urlencode({"send_error": f"No se pudo enviar la respuesta: {error_detail}"})
        return RedirectResponse(
            url=f"/dashboard/tickets/{ticket_id}?{query}",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/dashboard/tickets/{ticket_id}",
        status_code=303,
    )


# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸Ãƒâ€¦Ã‚Â¡Ãƒâ€šÃ‚Â« MARCAR COMO SPAM (cualquiera)

# ======================================================

@router.post("/dashboard/tickets/{ticket_id}/spam")

def mark_spam(

    ticket_id: int,

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    ticket = db.get(Ticket, ticket_id)

    if not ticket:

        return HTMLResponse("Ticket no encontrado", status_code=404)

    if _ticket_is_locked(ticket):
        query = urlencode({"service_error": "El ticket ya esta resuelto y no permite cambios."})
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    ticket.is_spam = True
    ticket.is_deleted = False
    apply_ticket_status_change(ticket, "closed")

    db.commit()

    return RedirectResponse("/dashboard", status_code=303)

# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾ RESTAURAR DESDE SPAM

# ======================================================

@router.post("/dashboard/tickets/{ticket_id}/restore-spam")

def restore_from_spam(

    ticket_id: int,

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    ticket = db.get(Ticket, ticket_id)

    if not ticket:

        raise HTTPException(status_code=404)

    ticket.is_spam = False

    db.commit()

    return RedirectResponse("/dashboard?view=spam", status_code=303)

# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬ÂÃƒÂ¢Ã¢â€šÂ¬Ã‹Å“ ELIMINAR TICKET (SOLO ADMIN)

# ======================================================

@router.post("/dashboard/tickets/{ticket_id}/delete")

def delete_ticket(

    ticket_id: int,

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    if not current_user.is_admin:

        raise HTTPException(status_code=403, detail="Solo administradores")

    ticket = db.get(Ticket, ticket_id)

    if not ticket:

        return HTMLResponse("Ticket no encontrado", status_code=404)

    if _ticket_is_locked(ticket):
        query = urlencode({"service_error": "El ticket ya esta resuelto y no permite cambios."})
        return RedirectResponse(url=f"/dashboard/tickets/{ticket_id}?{query}", status_code=303)

    ticket.is_deleted = True
    ticket.is_spam = False
    apply_ticket_status_change(ticket, "closed")

    db.commit()

    return RedirectResponse("/dashboard", status_code=303)

# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾ RESTAURAR DESDE PAPELERA

# ======================================================

@router.post("/dashboard/tickets/{ticket_id}/restore-trash")

def restore_from_trash(

    ticket_id: int,

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    if not current_user.is_admin:

        raise HTTPException(status_code=403)

    ticket = db.get(Ticket, ticket_id)

    if not ticket:

        raise HTTPException(status_code=404)

    ticket.is_deleted = False

    db.commit()

    return RedirectResponse("/dashboard?view=trash", status_code=303)

@router.get("/panel-indicadores", response_class=HTMLResponse)

def panel_indicadores(

    request: Request,

    date_from: str | None = Query(default=None),

    date_to: str | None = Query(default=None),

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    from app.services.analytics_service import (

        get_overview_kpis,

        get_sla_summary,

        get_ticket_volume_30d,

        get_tickets_by_priority,

        get_tickets_by_agent,

        get_ticket_aging,

        get_ticket_status_breakdown

    )

    def _parse_iso_date(raw_value: str | None):
        # Convierte YYYY-MM-DD a date; si viene invalida, ignora sin romper el panel.
        if not raw_value:
            return None
        try:
            return datetime.strptime(raw_value, "%Y-%m-%d").date()
        except ValueError:
            return None

    def _normalize_range(date_start, date_end):
        # Si vienen invertidas, las corrige para evitar errores de usuario.
        if date_start and date_end and date_start > date_end:
            return date_end, date_start
        return date_start, date_end

    def _to_utc_bounds(date_start, date_end):
        # Convierte rango date -> datetime UTC inclusivo.
        start_dt = (
            datetime(
                date_start.year,
                date_start.month,
                date_start.day,
                0,
                0,
                0,
                tzinfo=timezone.utc
            )
            if date_start else None
        )
        end_dt = (
            datetime(
                date_end.year,
                date_end.month,
                date_end.day,
                23,
                59,
                59,
                999999,
                tzinfo=timezone.utc
            )
            if date_end else None
        )
        return start_dt, end_dt

    def _resolve_prefixed_range(prefix: str, fallback_from, fallback_to):
        # Lee date_from/date_to por prefijo y hereda del filtro global cuando no existe.
        start_obj = _parse_iso_date(request.query_params.get(f"{prefix}_date_from")) or fallback_from
        end_obj = _parse_iso_date(request.query_params.get(f"{prefix}_date_to")) or fallback_to
        start_obj, end_obj = _normalize_range(start_obj, end_obj)
        start_dt, end_dt = _to_utc_bounds(start_obj, end_obj)
        return start_obj, end_obj, start_dt, end_dt

    def _json_safe(value):
        # Convierte Decimal (y estructuras anidadas) a tipos serializables para tojson.
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, dict):
            return {k: _json_safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_json_safe(v) for v in value]
        if isinstance(value, tuple):
            return tuple(_json_safe(v) for v in value)
        return value

    from_date_obj = _parse_iso_date(date_from)
    to_date_obj = _parse_iso_date(date_to)
    from_date_obj, to_date_obj = _normalize_range(from_date_obj, to_date_obj)

    # Filtros independientes por grafico (si no vienen, heredan el global).
    volume_from_obj, volume_to_obj, volume_from_dt, volume_to_dt = _resolve_prefixed_range(
        "volume",
        from_date_obj,
        to_date_obj,
    )
    priority_from_obj, priority_to_obj, priority_from_dt, priority_to_dt = _resolve_prefixed_range(
        "priority",
        from_date_obj,
        to_date_obj,
    )
    agent_from_obj, agent_to_obj, agent_from_dt, agent_to_dt = _resolve_prefixed_range(
        "agent",
        from_date_obj,
        to_date_obj,
    )
    aging_from_obj, aging_to_obj, aging_from_dt, aging_to_dt = _resolve_prefixed_range(
        "aging",
        from_date_obj,
        to_date_obj,
    )

    summary_status_from_obj, summary_status_to_obj, summary_status_from_dt, summary_status_to_dt = _resolve_prefixed_range(
        "summary_status",
        from_date_obj,
        to_date_obj,
    )
    summary_rates_from_obj, summary_rates_to_obj, summary_rates_from_dt, summary_rates_to_dt = _resolve_prefixed_range(
        "summary_rates",
        from_date_obj,
        to_date_obj,
    )
    summary_times_from_obj, summary_times_to_obj, summary_times_from_dt, summary_times_to_dt = _resolve_prefixed_range(
        "summary_times",
        from_date_obj,
        to_date_obj,
    )
    summary_quality_from_obj, summary_quality_to_obj, summary_quality_from_dt, summary_quality_to_dt = _resolve_prefixed_range(
        "summary_quality",
        from_date_obj,
        to_date_obj,
    )

    date_from_dt, date_to_dt = _to_utc_bounds(from_date_obj, to_date_obj)

    kpis = get_overview_kpis(db, date_from=date_from_dt, date_to=date_to_dt)

    summary_status_kpis = get_overview_kpis(db, date_from=summary_status_from_dt, date_to=summary_status_to_dt)
    summary_rates_kpis = get_overview_kpis(db, date_from=summary_rates_from_dt, date_to=summary_rates_to_dt)
    summary_times_kpis = get_overview_kpis(db, date_from=summary_times_from_dt, date_to=summary_times_to_dt)
    summary_quality_kpis = get_overview_kpis(db, date_from=summary_quality_from_dt, date_to=summary_quality_to_dt)

    summary_status_kpis = _json_safe(summary_status_kpis)
    summary_rates_kpis = _json_safe(summary_rates_kpis)
    summary_times_kpis = _json_safe(summary_times_kpis)
    summary_quality_kpis = _json_safe(summary_quality_kpis)

    sla = get_sla_summary(db, date_from=date_from_dt, date_to=date_to_dt)

    volume = get_ticket_volume_30d(db, date_from=volume_from_dt, date_to=volume_to_dt)

    status_breakdown = get_ticket_status_breakdown(db, date_from=summary_status_from_dt, date_to=summary_status_to_dt)

    priorities = get_tickets_by_priority(db, date_from=priority_from_dt, date_to=priority_to_dt)

    agents = get_tickets_by_agent(db, date_from=agent_from_dt, date_to=agent_to_dt)

    aging = get_ticket_aging(db, date_from=aging_from_dt, date_to=aging_to_dt)

    return templates.TemplateResponse(

        "panel_indicadores.html",

        {

            "request": request,

            "user": current_user,

            "kpis": kpis,
            "summary_status_kpis": summary_status_kpis,
            "summary_rates_kpis": summary_rates_kpis,
            "summary_times_kpis": summary_times_kpis,
            "summary_quality_kpis": summary_quality_kpis,

            "sla": sla,

            "volume": volume,

            "status_breakdown": status_breakdown,

            "priorities": priorities,

            "agents": agents,

            "aging": aging,

            "date_from": from_date_obj.isoformat() if from_date_obj else "",

            "date_to": to_date_obj.isoformat() if to_date_obj else "",

            "has_date_filter": bool(from_date_obj or to_date_obj),

            "volume_date_from": volume_from_obj.isoformat() if volume_from_obj else "",
            "volume_date_to": volume_to_obj.isoformat() if volume_to_obj else "",
            "priority_date_from": priority_from_obj.isoformat() if priority_from_obj else "",
            "priority_date_to": priority_to_obj.isoformat() if priority_to_obj else "",
            "agent_date_from": agent_from_obj.isoformat() if agent_from_obj else "",
            "agent_date_to": agent_to_obj.isoformat() if agent_to_obj else "",
            "aging_date_from": aging_from_obj.isoformat() if aging_from_obj else "",
            "aging_date_to": aging_to_obj.isoformat() if aging_to_obj else "",

            "summary_status_date_from": summary_status_from_obj.isoformat() if summary_status_from_obj else "",
            "summary_status_date_to": summary_status_to_obj.isoformat() if summary_status_to_obj else "",
            "summary_rates_date_from": summary_rates_from_obj.isoformat() if summary_rates_from_obj else "",
            "summary_rates_date_to": summary_rates_to_obj.isoformat() if summary_rates_to_obj else "",
            "summary_times_date_from": summary_times_from_obj.isoformat() if summary_times_from_obj else "",
            "summary_times_date_to": summary_times_to_obj.isoformat() if summary_times_to_obj else "",
            "summary_quality_date_from": summary_quality_from_obj.isoformat() if summary_quality_from_obj else "",
            "summary_quality_date_to": summary_quality_to_obj.isoformat() if summary_quality_to_obj else "",

        }

    )

@router.get("/dashboard/tickets/new")

def new_ticket_form(

    request: Request,

    current_user: User = Depends(get_current_user_web),

    db: Session = Depends(get_db)

):

    users = db.query(User).all()

    return templates.TemplateResponse(

        "new_ticket.html",

        {

            "request": request,

            "users": users,

            "user": current_user

        }

    )

@router.post("/dashboard/tickets/create")

def create_ticket(

    subject: str = Form(...),                 # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œÃƒâ€šÃ‚Â Asunto del ticket (obligatorio)

    content: str = Form(...),                 # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢Ãƒâ€šÃ‚Â¬ Mensaje inicial del ticket (obligatorio)

    priority: str = Form(""),

    assigned_to_id: int | None = Form(None),  # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‹Å“Ãƒâ€šÃ‚Â¤ Usuario asignado (opcional)

    current_user: User = Depends(get_current_user_web),  # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒâ€šÃ‚Â Usuario autenticado

    db: Session = Depends(get_db)             # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬ÂÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾ SesiÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³n de base de datos

):

    # =====================================

    # 1ÃƒÆ’Ã‚Â¯Ãƒâ€šÃ‚Â¸Ãƒâ€šÃ‚ÂÃƒÆ’Ã‚Â¢Ãƒâ€ Ã¢â‚¬â„¢Ãƒâ€šÃ‚Â£ Buscar o crear Requester interno

    # =====================================

    # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒâ€¦Ã‚Â½ ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¿QuÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â© es esto?

    # El modelo Ticket exige un requester_id (FK a requesters).

    # Como este ticket es interno, necesitamos que el usuario

    # tambiÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©n exista como "Requester".

    # Buscamos si ya existe uno con el mismo nombre.

    requester = db.query(Requester).filter(

        Requester.name == current_user.name

    ).first()

    # ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã‚Â¾ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Si no existe, lo creamos

    # Esto evita errores de clave forÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¡nea.

    if not requester:

        requester = Requester(

        name=current_user.name  # Nombre del usuario interno

        )

        db.add(requester)          # Lo agregamos a la sesiÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³n

        db.commit()                # Guardamos en base de datos

        db.refresh(requester)      # Refrescamos para obtener el ID generado

    # =====================================

    # Si no se asigna, se auto-asigna

    # =====================================

    # Si el usuario no selecciona un responsable en el modal,

    # el ticket se asigna automÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¡ticamente al creador.

    allowed_priorities = {"low", "medium", "high", "urgent"}
    priority = (priority or "").strip().lower()
    if priority not in allowed_priorities:
        raise HTTPException(status_code=400, detail="Debes seleccionar una prioridad")

    if not assigned_to_id:

        assigned_to_id = current_user.id

    # =====================================

    # 3ÃƒÆ’Ã‚Â¯Ãƒâ€šÃ‚Â¸Ãƒâ€šÃ‚ÂÃƒÆ’Ã‚Â¢Ãƒâ€ Ã¢â‚¬â„¢Ãƒâ€šÃ‚Â£ Crear Ticket

    # =====================================

    # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸Ãƒâ€¦Ã‚Â½Ãƒâ€šÃ‚Â« Creamos el registro principal del ticket.

    # source="internal" permite diferenciarlo

    # de tickets por email o whatsapp.

    ticket = Ticket(

        subject=subject,

        requester_id=requester.id,    # ID del requester interno

        assigned_to_id=assigned_to_id,

        priority=priority,

        status="open",

        source="internal"

    )

    db.add(ticket)

    db.commit()

    db.refresh(ticket)

    # =====================================

    # 4ÃƒÆ’Ã‚Â¯Ãƒâ€šÃ‚Â¸Ãƒâ€šÃ‚ÂÃƒÆ’Ã‚Â¢Ãƒâ€ Ã¢â‚¬â„¢Ãƒâ€šÃ‚Â£ Crear mensaje inicial

    # =====================================

    # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢Ãƒâ€šÃ‚Â¬ Todo ticket debe tener al menos un mensaje.

    # AquÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â­ creamos el mensaje inicial del agente.

    message = Message(

        ticket_id=ticket.id,

        sender_id=current_user.id,

        sender_type="agent",

        channel="internal",          # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‹Å“Ãƒâ€¹Ã¢â‚¬Â  obligatorio

        content=content,

        is_internal_note=False       # opcional pero recomendable

    )

    db.add(message)

    db.commit()

    # =====================================

    # 5ÃƒÆ’Ã‚Â¯Ãƒâ€šÃ‚Â¸Ãƒâ€šÃ‚ÂÃƒÆ’Ã‚Â¢Ãƒâ€ Ã¢â‚¬â„¢Ãƒâ€šÃ‚Â£ Redirigir al dashboard

    # =====================================

    # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒâ€šÃ‚Â Volvemos al inbox despuÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©s de crear el ticket.

    return RedirectResponse("/dashboard", status_code=303)


