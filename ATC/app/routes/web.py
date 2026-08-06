from __future__ import annotations

import html
import json
import base64
import bcrypt
import binascii
import mimetypes
import secrets
from decimal import Decimal

import re

import traceback
import unicodedata

from email.utils import parseaddr

from urllib.parse import parse_qsl, urlencode, urlsplit
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Request, Form, HTTPException, Query, File, UploadFile
from pydantic import BaseModel

from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from fastapi.templating import Jinja2Templates
from jinja2 import Environment as _Jinja2Env, FileSystemLoader as _FSLoader

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

from ATC.app.core.db import get_db, get_incidencias_db

from ATC.app.core.config import settings
from ATC.app.core.db_compat import add_column, sql_null_text

from ATC.app.core.security import create_access_token, hash_password, verify_password
from ATC.app.core.session_policy import expiracion_sesion, max_age_cookie_segundos

from ATC.app.core.text import decode_mime_words
from ATC.app.core.signatures import signature_html_for_user
from ATC.app.services.ticket_status_service import apply_ticket_status_change, mark_first_agent_reply
from ATC.app.services.user_service import UserService
from ATC.app.services.automation_service import RULE_EMAIL_AUTO_REPLY, send_initial_email_auto_reply
from ATC.app.services.drive_report_service import (
    create_drive_report_for_odt,
    DriveReportError,
)
from ATC.app.services.sla_feedback_service import (
    build_configured_sla_survey_link,
    build_static_sla_survey_link,
)
from ATC.app.services.incidencias_service import IncidenciasService
from ATC.app.routes.bitacora_access import can_access_bitacora

from ATC.app.models.ticket import Ticket

from ATC.app.models.message import Message

from ATC.app.models.ticket_alert_read_state import TicketAlertReadState
from ATC.app.models.ticket_message_read_state import TicketMessageReadState
from ATC.app.models.ticket_internal_note_read_state import TicketInternalNoteReadState
from ATC.app.models.ticket_manual_unread import TicketManualUnread
from ATC.app.models.requester_internal_note_read_state import RequesterInternalNoteReadState

from ATC.app.models.user import User
from ATC.app.models.incidencias import AdministracionODT, LoginSession, Registro, ServicioTecnicoVentaODT, VentaODS

from ATC.app.models.requester import Requester

from ATC.app.models.ticket_history import TicketAssignmentHistory
from ATC.app.models.automation_log import AutomationLog

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

_TZ_LOCAL = ZoneInfo("America/Santiago")

router = APIRouter(tags=["web"])

_ATC_APP_DIR = Path(__file__).resolve().parents[1]
_jinja_env = _Jinja2Env(loader=_FSLoader([str(_ATC_APP_DIR / "templates")]), autoescape=True)

# Ruta absoluta al logo para imagenes inline (cid:) en correos: una ruta
# relativa como "static/img/logo-atc.png" depende del cwd del proceso y
# fallaba en produccion (Path.exists() daba False -> imagen rota en el
# correo, sin ningun error visible porque _attach_inline_images ignora
# en silencio los paths que no existen).
_LOGO_ATC_PATH = str(_ATC_APP_DIR.parent / "static" / "img" / "logo-atc.png")

def _jinja_localdt(dt: datetime | None, fmt: str = "%d-%m-%Y %H:%M") -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TZ_LOCAL).strftime(fmt)

_jinja_env.filters["localdt"] = _jinja_localdt
templates = Jinja2Templates(env=_jinja_env)

COOKIE_NAME = "access_token"
_UPLOADS_ROOT = _ATC_APP_DIR.parent / "uploads"
EMAIL_ATTACHMENT_UPLOAD_ROOT = _UPLOADS_ROOT / "ticket_replies"
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
        parseaddr(settings.IMAP2_USER or "")[1].strip().lower(),
        parseaddr(settings.SMTP_USER or "")[1].strip().lower(),
        parseaddr(settings.smtp2_username or "")[1].strip().lower(),
        parseaddr(settings.smtp2_from_email or "")[1].strip().lower(),
        parseaddr(settings.SMTP_FROM or "")[1].strip().lower(),
    }
    return {value for value in values if value}


RESTRICTED_SUPPORT_MAILBOX = "soporte@alguientecuida.cl"
RONALD_MONTILLA_RUTS = {"26332060-3", "26.332.060-3", "263320603"}
# Pedido explicito, jul 2026: Fernando tambien debe ver TODOS los tickets del
# buzon restringido soporte@alguientecuida.cl, igual que Ronald Montilla.
FERNANDO_LUBIANO_RUTS = {"21134285-4", "21.134.285-4", "211342854"}


def _is_ronald_montilla_user(user: User | None) -> bool:
    if not user:
        return False
    raw_values = [
        getattr(user, "username", None),
        getattr(user, "name", None),
        getattr(user, "email", None),
    ]
    joined = " ".join(str(value or "") for value in raw_values).strip().casefold()
    digits = re.sub(r"[^0-9kK]", "", joined).casefold()
    return (
        "ronald" in joined
        and "montilla" in joined
    ) or any(rut.replace(".", "").replace("-", "").casefold() in digits for rut in RONALD_MONTILLA_RUTS)


def _is_fernando_lubiano_user(user: User | None) -> bool:
    if not user:
        return False
    raw_values = [
        getattr(user, "username", None),
        getattr(user, "name", None),
        getattr(user, "email", None),
    ]
    joined = " ".join(str(value or "") for value in raw_values).strip().casefold()
    digits = re.sub(r"[^0-9kK]", "", joined).casefold()
    return (
        "fernando" in joined
        and "lubiano" in joined
    ) or any(rut.replace(".", "").replace("-", "").casefold() in digits for rut in FERNANDO_LUBIANO_RUTS)


def _has_unrestricted_support_mailbox_access(user: User | None) -> bool:
    return _is_ronald_montilla_user(user) or _is_fernando_lubiano_user(user)


def _apply_ticket_visibility_for_user(query, user: User):
    if _has_unrestricted_support_mailbox_access(user):
        return query
    restricted = RESTRICTED_SUPPORT_MAILBOX.casefold()
    return query.filter(
        or_(
            Ticket.inbound_mailbox.is_(None),
            func.lower(Ticket.inbound_mailbox) != restricted,
            Ticket.assigned_to_id == user.id,
        )
    )


def _can_view_ticket(ticket: Ticket | None, user: User) -> bool:
    if not ticket:
        return False
    mailbox = str(getattr(ticket, "inbound_mailbox", "") or "").strip().casefold()
    if mailbox != RESTRICTED_SUPPORT_MAILBOX:
        return True
    return _has_unrestricted_support_mailbox_access(user) or int(ticket.assigned_to_id or 0) == int(user.id or 0)


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


def _merge_recipient_list(*groups: list[str]) -> list[str]:
    recipients: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for email_value in group or []:
            parsed_email = parseaddr(str(email_value or "").strip())[1].strip()
            if not parsed_email:
                continue
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", parsed_email):
                continue
            normalized = parsed_email.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            recipients.append(parsed_email)
    return recipients


def _safe_parse_recipient_list(raw_value: str | None) -> list[str]:
    try:
        return _parse_recipient_list(raw_value, field_name="correo guardado")
    except ValueError:
        return []


def _extract_saved_email_copy_recipients(ticket: Ticket) -> list[str]:
    copied: list[str] = []
    for message in getattr(ticket, "messages", []) or []:
        if getattr(message, "channel", "") != "email":
            continue
        content = str(getattr(message, "content", "") or "")
        for match in re.finditer(r"<!--\s*ATC_EMAIL_RECIPIENTS\s+(\{.*?\})\s*-->", content, re.S):
            try:
                payload = json.loads(match.group(1))
            except Exception:
                continue
            copied.extend([str(item) for item in payload.get("cc", []) or []])
    return copied


def _resolve_ticket_email_recipients(
    ticket: Ticket,
    *,
    to: str | None = "",
    cc: str | None = "",
    bcc: str | None = "",
) -> tuple[list[str], list[str], list[str]]:
    to_override = (to or "").strip()
    if to_override:
        # El agente cambio el destinatario a mano para este envio puntual —
        # no toca el email guardado del solicitante (pedido explicito, jul 2026).
        to_recipients = _parse_recipient_list(to_override, field_name="destinatario")
    else:
        requester_email = (ticket.requester.email if ticket.requester and ticket.requester.email else "").strip()
        if not requester_email:
            raise ValueError("El ticket no tiene correo del solicitante para responder.")
        to_recipients = _parse_recipient_list(requester_email, field_name="correo del cliente")

    if not to_recipients:
        raise ValueError("El correo del destinatario no es valido para responder.")

    thread_requester_emails: list[str] = []
    for message in getattr(ticket, "messages", []) or []:
        if getattr(message, "sender_type", "") != "requester":
            continue
        if getattr(message, "channel", "") != "email":
            continue
        thread_requester_emails.extend(_safe_parse_recipient_list(getattr(message, "sender_email", "") or ""))

    saved_copy_recipients = _extract_saved_email_copy_recipients(ticket)
    manual_cc_recipients = _parse_recipient_list(cc, field_name="cc")
    bcc_recipients = _parse_recipient_list(bcc, field_name="bcc")

    to_recipients = _merge_recipient_list(to_recipients)
    excluded = {email.lower() for email in to_recipients}
    cc_recipients = []
    for email_value in _merge_recipient_list(thread_requester_emails, saved_copy_recipients, manual_cc_recipients):
        if email_value.lower() in excluded:
            continue
        excluded.add(email_value.lower())
        cc_recipients.append(email_value)

    clean_bcc_recipients = []
    for email_value in _merge_recipient_list(bcc_recipients):
        if email_value.lower() in excluded:
            continue
        excluded.add(email_value.lower())
        clean_bcc_recipients.append(email_value)

    return to_recipients, cc_recipients, clean_bcc_recipients


def _email_recipient_summary_html(
    *,
    to_recipients: list[str],
    cc_recipients: list[str],
    bcc_recipients: list[str],
) -> str:
    payload = {
        "to": to_recipients,
        "cc": cc_recipients,
        "bcc": bcc_recipients,
    }
    marker = f"<!-- ATC_EMAIL_RECIPIENTS {json.dumps(payload, ensure_ascii=False)} -->"

    def _line(label: str, recipients: list[str]) -> str:
        if not recipients:
            return ""
        safe_recipients = html.escape(", ".join(recipients))
        return (
            '<div style="margin:2px 0;">'
            f'<strong style="color:#475569;">{html.escape(label)}:</strong> '
            f'<span>{safe_recipients}</span>'
            '</div>'
        )

    lines = "".join(
        [
            _line("Para", to_recipients),
            _line("CC", cc_recipients),
            _line("CCO", bcc_recipients),
        ]
    )
    if not lines:
        return marker

    return (
        marker +
        '<div style="margin:0 0 12px;padding:10px 12px;border:1px solid #cbd5e1;'
        'border-left:4px solid #f97316;border-radius:8px;background:#f8fafc;'
        'font-size:12px;line-height:1.45;color:#0f172a;">'
        '<div style="margin:0 0 4px;font-weight:800;color:#0f172a;">Correo enviado</div>'
        f"{lines}"
        "</div>"
    )


def _prepend_email_recipient_summary(
    content: str,
    *,
    to_recipients: list[str],
    cc_recipients: list[str],
    bcc_recipients: list[str],
) -> str:
    summary = _email_recipient_summary_html(
        to_recipients=to_recipients,
        cc_recipients=cc_recipients,
        bcc_recipients=bcc_recipients,
    )
    clean_content = (content or "").strip()
    return f"{summary}\n{clean_content}" if clean_content else summary


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

def _get_latest_active_ticket_id(db: Session, user: User | None = None) -> int:
    query = db.query(Ticket.id)
    if user is not None:
        query = _apply_ticket_visibility_for_user(query, user)

    latest_row = (

        query

        .filter(

            Ticket.is_deleted == False,

            Ticket.is_spam == False,

        )

        .order_by(Ticket.id.desc())

        .first()

    )

    return int(latest_row[0]) if latest_row else 0

def _get_ticket_alert_unread_count(db: Session, user_id: int) -> int:
    user = db.get(User, user_id)

    read_state = db.get(TicketAlertReadState, user_id)

    if read_state is None:

        # Primer uso: tomar estado actual como "leido" para no mostrar backlog historico.

        read_state = TicketAlertReadState(

            user_id=user_id,

            last_seen_ticket_id=_get_latest_active_ticket_id(db, user),

        )

        db.add(read_state)

        db.commit()

        return 0

    last_seen_ticket_id = max(0, int(read_state.last_seen_ticket_id or 0))
    query = db.query(Ticket)
    if user is not None:
        query = _apply_ticket_visibility_for_user(query, user)

    return (

        query

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
    user = db.get(User, user_id)

    safe_last_ticket_id = max(

        0,

        int(last_ticket_id or _get_latest_active_ticket_id(db, user)),

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


def _auto_assign_current_user(db: Session, ticket: Ticket, current_user: User) -> None:
    """Si el ticket no tiene responsable, se asigna automaticamente a quien
    responde o gestiona algo en el. Evita que queden tickets (incluso ya
    finalizados) sin asignar solo porque nadie los tomo explicitamente."""
    if ticket.assigned_to_id is None:
        assign_ticket_logic(db, ticket, current_user.id, current_user)


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
                <div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#ffffff;opacity:.82;">Soporte ATC</div>
                <h1 style="margin:10px 0 0;font-size:28px;line-height:1.2;color:#ffffff;">Encuesta de satisfaccion</h1>
                <p style="margin:10px 0 0;font-size:15px;line-height:1.6;color:#ffffff;opacity:.92;">Ticket #{ticket_id}</p>
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

    from ATC.app.integrations.email_smtp import send_email_reply

    send_email_reply(

        to=requester_email,

        subject=subject,

        body=body,

        ticket_id=ticket.id,

        inline_images=[
            {
                "cid": logo_cid,
                "path": _LOGO_ATC_PATH,
            }
        ],

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

        raise HTTPException(status_code=401, detail="Token inválido")


def _resolver_usuario_generico(request: Request, db: Session, token: str = "") -> User | None:
    """Resuelve el usuario logueado sin importar si llego por token= (SSO/incidencias)
    o por cookie de sesion web. Usado para funciones transversales como el cambio
    de clave inicial, disponible en todos los seleccion_panel_*.html."""
    token_limpio = (token or "").strip()
    if token_limpio:
        sesion = db.get(LoginSession, token_limpio)
        if sesion and sesion.user_id and sesion.expires_at and sesion.expires_at > datetime.utcnow():
            user = db.get(User, int(sesion.user_id))
            if user and user.is_active:
                return user
    try:
        cookie = request.cookies.get(COOKIE_NAME, "")
        if cookie:
            login = _decode_cookie_token(cookie)
            user = UserService.find_by_login(db, login)
            if user and user.is_active:
                return user
    except Exception:
        pass
    return None


def _verificar_clave_usuario(user: User, clave: str) -> bool:
    """Misma logica multi-formato que IncidenciasService._password_usuario_ok
    (soporta hashes bcrypt, passlib y el legado 'plain:<clave>')."""
    stored = str(user.hashed_password or "")
    incoming = str(clave or "")
    if stored.startswith("plain:"):
        return secrets.compare_digest(stored.removeprefix("plain:"), incoming)
    if stored.startswith("$2"):
        try:
            return bcrypt.checkpw(incoming.encode("utf-8"), stored.encode("utf-8"))
        except Exception:
            pass
    try:
        return verify_password(incoming, stored)
    except Exception:
        return secrets.compare_digest(stored, incoming)


class CambiarClaveInicialRequest(BaseModel):
    clave_actual_1: str
    clave_actual_2: str
    clave_nueva_1: str
    clave_nueva_2: str


class VerificarClaveActualRequest(BaseModel):
    clave_actual_1: str
    clave_actual_2: str


@router.post("/api/usuario/verificar-clave-actual")
def verificar_clave_actual(
    request: Request,
    payload: VerificarClaveActualRequest,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
):
    user = _resolver_usuario_generico(request, db, token)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if user.password_changed:
        raise HTTPException(status_code=400, detail="Ya se resolvió el aviso de cambio de contraseña")
    actual_1 = payload.clave_actual_1 or ""
    actual_2 = payload.clave_actual_2 or ""
    if actual_1 != actual_2 or not _verificar_clave_usuario(user, actual_1):
        raise HTTPException(status_code=400, detail="Contraseña incorrecta o no coinciden")
    return {"ok": True}


@router.get("/api/usuario/estado-clave-inicial")
def estado_clave_inicial(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
):
    user = _resolver_usuario_generico(request, db, token)
    if not user:
        return {"autenticado": False, "mostrar": False}
    return {"autenticado": True, "mostrar": not bool(user.password_changed)}


@router.post("/api/usuario/clave-prompt/omitir")
def omitir_prompt_clave(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
):
    user = _resolver_usuario_generico(request, db, token)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    user.password_changed = True
    db.commit()
    return {"ok": True}


@router.post("/api/usuario/cambiar-clave-inicial")
def cambiar_clave_inicial(
    request: Request,
    payload: CambiarClaveInicialRequest,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
):
    user = _resolver_usuario_generico(request, db, token)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if user.password_changed:
        raise HTTPException(status_code=400, detail="Ya se resolvió el aviso de cambio de contraseña")

    actual_1 = payload.clave_actual_1 or ""
    actual_2 = payload.clave_actual_2 or ""
    if actual_1 != actual_2 or not _verificar_clave_usuario(user, actual_1):
        raise HTTPException(status_code=400, detail="Contraseña incorrecta o no coinciden")

    nueva_1 = payload.clave_nueva_1 or ""
    nueva_2 = payload.clave_nueva_2 or ""
    if nueva_1 != nueva_2:
        raise HTTPException(status_code=400, detail="Las contraseñas nuevas no coinciden entre sí")
    if len(nueva_1.strip()) < 4:
        raise HTTPException(status_code=400, detail="La nueva contraseña debe tener al menos 4 caracteres")

    user.hashed_password = hash_password(nueva_1)
    user.password_changed = True
    db.commit()
    return {"ok": True}


DEPARTMENT_AREA_MAP = {
    "soporte": [("soporte", "Soporte", "Soporte")],
    "materiales": [("materiales", "Materiales", "Materiales")],
    "control de compras": [("compras_control", "Control de Compras", "Control de Compras")],
    "solicitud de compra": [("compras_solicitud", "Solicitar Compra", "Solicitud de Compra")],
    "servicio tecnico": [("servicio_tecnico", "Servicio Tecnico", "Servicio Tecnico")],
    "servicio técnico": [("servicio_tecnico", "Servicio Tecnico", "Servicio Tecnico")],
    "tecnicos": [("tecnicos", "Tecnicos", "Tecnicos")],
    "técnicos": [("tecnicos", "Tecnicos", "Tecnicos")],
    "operador": [("incidencias", "Operadores", "Operador")],
    "coordinacion": [("coordinacion", "Coordinacion", "Coordinacion")],
    "coordinación": [("coordinacion", "Coordinacion", "Coordinacion")],
    "comercial": [("venta", "Venta", "Comercial")],
    "finanzas": [("finanzas", "Finanzas", "Finanzas")],
    "administracion": [("administracion", "Administracion", "Administracion")],
    "administración": [("administracion", "Administracion", "Administracion")],
    "operaciones": [("operaciones", "Operaciones", "Operaciones")],
    "guardia": [("guardia", "Guardia", "Guardia")],
    "supervisores": [("supervisores", "Supervisores", "Supervisores")],
    "rrhh": [("rrhh", "RRHH", "RRHH")],
    "prevencion": [("prevencion", "Prevención", "Prevención")],
    "prevención": [("prevencion", "Prevención", "Prevención")],
    "bitacora": [("bitacora", "Bitácora", "Bitacora")],
    "televigilante": [("bitacora", "Bitácora", "Bitacora")],
}

ADMIN_SELECTOR_AREAS = [
    ("soporte", "Soporte", "Soporte"),
    ("materiales", "Materiales", "Materiales"),
    ("servicio_tecnico", "Servicio Tecnico", "Servicio Tecnico"),
    ("tecnicos", "Tecnicos", "Tecnicos"),
    ("incidencias", "Operadores", "Operador"),
    ("coordinacion", "Coordinacion", "Operador"),
    ("venta", "Venta", "Comercial"),
    ("finanzas", "Finanzas", "Finanzas"),
    ("administracion", "Administracion", "Administracion"),
    ("operaciones", "Operaciones", "Operaciones"),
    ("guardia", "Guardia", "Guardia"),
    ("supervisores", "Supervisores", "Supervisores"),
    ("rrhh", "RRHH", "RRHH"),
    ("prevencion", "Prevención", "Prevención"),
    ("bitacora", "Bitácora", "Bitacora"),
]


def _split_user_departments(value: str | None) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    parts = re.split(r"[;,|]+", raw)
    return [p.strip() for p in parts if p.strip()]


def _areas_from_departments(departments: list[str]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for dept in departments:
        key = dept.strip().casefold()
        for code, name, department in DEPARTMENT_AREA_MAP.get(key, []):
            if code in seen:
                continue
            seen.add(code)
            out.append(
                {
                    "area_code": code,
                    "area_name": name,
                    "department": department,
                    "is_primary": len(out) == 0,
                }
            )
    return out


def _primary_user_area(db: Session, user_id: int) -> dict[str, object] | None:
    areas = _active_user_areas(db, user_id)
    return areas[0] if areas else None

def _active_user_areas(db: Session, user_id: int) -> list[dict[str, object]]:
    user = db.get(User, user_id)
    if not user:
        return []
    if getattr(user, "is_super_admin", False):
        return [
            {
                "area_code": code,
                "area_name": name,
                "department": department,
                "is_primary": idx == 0,
            }
            for idx, (code, name, department) in enumerate(ADMIN_SELECTOR_AREAS)
        ]
    return _areas_from_departments(_split_user_departments(user.department))

def _area_card_options(areas: list[dict[str, object]]) -> list[dict[str, str]]:
    labels = {
        "soporte": ("Soporte Técnico", "Tickets, incidencias de soporte y cierres operativos.", "S"),
        "materiales": ("Materiales", "Control de materiales presupuestados versus entregados.", "M"),
        "compras_control": ("Control de Compras", "Revisión y aprobación de solicitudes de compra.", "CC"),
        "compras_solicitud": ("Solicitar Compra", "Ingresa una nueva solicitud de compra con ítems, destino y presupuestos.", "SC"),
        "servicio_tecnico": ("Servicio Técnico", "Panel de servicio, coordinacion y seguimiento tecnico.", "ST"),
        "tecnicos": ("Tecnicos", "Cola de trabajo, rutas, evidencias y rendiciones.", "T"),
        "incidencias": ("Operadores", "Operador", "OP"),
        "coordinacion": ("Coordinación", "Gestion operativa y control de derivaciones.", "C"),
        "venta": ("Comercial", "Clientes, sucursales y ordenes de servicio.", "C"),
        "finanzas": ("Finanzas", "Estados financieros y seguimiento de ODS.", "F"),
        "administracion": ("Administración", "Control administrativo de ordenes y procesos.", "A"),
        "operaciones": ("Operaciones", "Coordinacion interna y seguimiento operacional.", "O"),
        "guardia": ("Guardia", "Inicio de turno y QR por recinto.", "G"),
        "supervisores": ("Supervisores", "Tablas de supervisor y base de guardias.", "SV"),
        "rrhh": ("Recursos Humanos", "Asistencia, recursos humanos y solicitudes de compra.", "RH"),
        "prevencion": ("Prevención", "Estatus de gestión documental, capacitaciones y protocolos de prevención.", "PR"),
        "bitacora": ("Bitácora", "Registro de novedades y comunicados.", "B"),
    }
    out: list[dict[str, str]] = []
    for area in areas:
        code = str(area.get("area_code") or "").strip()
        if code == "protocolos":
            continue
        fallback_name = str(area.get("area_name") or code or "Area").strip()
        title, description, initials = labels.get(
            code,
            (fallback_name, str(area.get("department") or "Acceso habilitado").strip(), fallback_name[:2].upper()),
        )
        out.append(
            {
                "code": code,
                "title": title,
                "description": description,
                "initials": initials,
                "primary": "true" if area.get("is_primary") else "false",
            }
        )
    return out

def _department_has_area(department_value: str | None, area_code: str) -> bool:
    target = (area_code or "").strip()
    if not target:
        return False
    for area in _areas_from_departments(_split_user_departments(department_value)):
        if str(area.get("area_code") or "").strip() == target:
            return True
    return False

def _require_area_access(db: Session, user: User, area_code: str) -> None:
    _ = db
    if getattr(user, "is_super_admin", False):
        return
    if not _department_has_area(user.department, area_code):
        raise HTTPException(status_code=403, detail="No tienes acceso a esta area.")


def _active_users_in_area(db: Session, area_code: str) -> list[User]:
    users = db.query(User).filter(User.is_active == 1).order_by(User.name.asc()).all()
    return [u for u in users if _department_has_area(u.department, area_code)]


_SUPPORT_VISIBLE_FIRST_NAMES = {"ronald", "felipe", "stephan", "sthefan", "antonio", "julissa"}


def _support_visible_name_key(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").strip())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = re.sub(r"\s+", " ", normalized).strip().casefold()
    return normalized.split(" ", 1)[0] if normalized else ""


def _visible_support_users(users: list[User]) -> list[User]:
    return [u for u in users if _support_visible_name_key(getattr(u, "name", None)) in _SUPPORT_VISIBLE_FIRST_NAMES]


def _is_visible_support_user(user: User | None) -> bool:
    return _support_visible_name_key(getattr(user, "name", None)) in _SUPPORT_VISIBLE_FIRST_NAMES


@router.post("/api/resumen-equipos-tecnicos/mover")
def resumen_equipos_tecnicos_mover(
    payload: dict = Body(...),
    db: Session = Depends(get_incidencias_db),
):
    token = str(payload.get("token") or "").strip()
    service = IncidenciasService(db)
    if not service.usuario_autorizado_para_resumen_equipos(token):
        raise HTTPException(status_code=401, detail="No autenticado.")
    codigo = str(payload.get("odt") or payload.get("codigo") or "").strip()
    origen = str(payload.get("origen") or "").strip().casefold()
    tecnico = re.sub(r"\s+", " ", str(payload.get("tecnico") or "").strip())
    acompanante = re.sub(r"\s+", " ", str(payload.get("acompanante") or "").strip())
    destino_prioritario = bool(payload.get("prioritaria"))
    if not codigo:
        raise HTTPException(status_code=400, detail="ODT/ODS requerida.")
    if origen not in {"registro", "venta_ods"}:
        raise HTTPException(status_code=400, detail="Origen invalido.")
    if not destino_prioritario and not tecnico and not acompanante:
        raise HTTPException(status_code=400, detail="Debes seleccionar un equipo destino.")
    if tecnico and acompanante and tecnico.casefold() == acompanante.casefold():
        acompanante = ""

    now = datetime.now(_TZ_LOCAL).replace(tzinfo=None)
    row = (
        db.query(Registro)
        .filter(func.lower(func.trim(Registro.odt)) == codigo.lower())
        .first()
    )
    ods = (
        db.query(VentaODS)
        .filter(func.lower(func.trim(VentaODS.codigo)) == codigo.lower())
        .first()
    )
    if not row and not ods:
        raise HTTPException(status_code=404, detail=f"No se encontro la ODT/ODS {codigo}.")

    if destino_prioritario:
        if not service.usuario_admin_para_resumen_equipos(token):
            raise HTTPException(status_code=403, detail="Solo administradores pueden usar Pendientes Prioritarios.")
        if origen == "registro":
            if not row:
                raise HTTPException(status_code=404, detail=f"No se encontro la ODT {codigo}.")
            row.tecnicos = None
            row.acompanante = None
            row.fecha_derivacion_tecnico = None
            row.derivacion = "Pendiente"
            row.estado = "Pendiente"
            db.commit()
            return {
                "ok": True,
                "tipo": "ODT",
                "codigo": codigo,
                "tecnico": "",
                "acompanante": "",
                "prioritaria": True,
                "sincronizado_registro": True,
                "sincronizado_venta": False,
            }
        if not ods:
            raise HTTPException(status_code=404, detail=f"No se encontro la ODS {codigo}.")
        if str(ods.estado or "").strip().casefold() == "anulada":
            raise HTTPException(status_code=400, detail="La ODS esta anulada.")
        st = (
            db.query(ServicioTecnicoVentaODT)
            .filter(func.lower(func.trim(ServicioTecnicoVentaODT.odt)) == codigo.lower())
            .first()
        )
        if st:
            st.tecnico_a_cargo = None
            st.acompanante = None
            st.updated_at = now
        adm = (
            db.query(AdministracionODT)
            .filter(func.lower(func.trim(AdministracionODT.odt)) == codigo.lower())
            .first()
        )
        if adm:
            adm.tecnico = None
            adm.acompanante = None
            adm.fecha_derivacion = None
        db.commit()
        return {
            "ok": True,
            "tipo": "ODS",
            "codigo": codigo,
            "tecnico": "",
            "acompanante": "",
            "prioritaria": True,
            "sincronizado_registro": False,
            "sincronizado_venta": True,
        }

    sincronizado_registro = False
    sincronizado_venta = False

    if row:
        row.tecnicos = tecnico or None
        row.acompanante = acompanante or None
        row.fecha_derivacion_tecnico = now
        row.derivacion = "Servicio Técnico" if tecnico else "Pendiente"
        row.estado = "En Proceso" if tecnico else "Pendiente"
        sincronizado_registro = True

    if ods:
        if str(ods.estado or "").strip().casefold() == "anulada":
            if not row:
                raise HTTPException(status_code=400, detail="La ODS esta anulada.")
        else:
            st = (
                db.query(ServicioTecnicoVentaODT)
                .filter(func.lower(func.trim(ServicioTecnicoVentaODT.odt)) == codigo.lower())
                .first()
            )
            if not st:
                st = ServicioTecnicoVentaODT(odt=codigo)
                db.add(st)
                db.flush()
            st.tecnico_a_cargo = tecnico or None
            st.acompanante = acompanante or None
            st.updated_at = now
            adm = (
                db.query(AdministracionODT)
                .filter(func.lower(func.trim(AdministracionODT.odt)) == codigo.lower())
                .first()
            )
            if not adm:
                adm = AdministracionODT(odt=codigo)
                db.add(adm)
                db.flush()
            adm.tecnico = tecnico or None
            adm.acompanante = acompanante or None
            adm.fecha_derivacion = now
            sincronizado_venta = True

    db.commit()
    return {
        "ok": True,
        "tipo": "ODT/ODS" if sincronizado_registro and sincronizado_venta else ("ODS" if sincronizado_venta else "ODT"),
        "codigo": codigo,
        "tecnico": tecnico,
        "acompanante": acompanante,
        "sincronizado_registro": sincronizado_registro,
        "sincronizado_venta": sincronizado_venta,
    }


def _redirect_for_authenticated_user(db: Session, user: User) -> RedirectResponse:
    if getattr(user, "is_super_admin", False):
        return RedirectResponse(url="/gerencia", status_code=303)
    if getattr(user, "cliente_rut", None):
        return RedirectResponse(url="/portal-cliente", status_code=303)
    areas = _active_user_areas(db, user.id)
    if len(areas) > 1:
        return RedirectResponse(url="/seleccionar-area", status_code=303)
    area_info = areas[0] if areas else None
    session_token = _create_unified_login_session(db, user, area_info)
    return RedirectResponse(
        url=_redirect_for_user_area(area_info.get("area_code") if area_info else None, session_token),
        status_code=303,
    )

def _user_has_area(db: Session, user_id: int, area_code: str) -> bool:
    return any(str(area.get("area_code") or "") == area_code for area in _active_user_areas(db, user_id))

def _create_unified_login_session(db: Session, user: User, area_info: dict[str, object] | None) -> str:
    token = str(uuid4())
    expires_at = expiracion_sesion(
        user.id,
        datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRES_MIN),
    )
    db.merge(
        LoginSession(
            token=token,
            usuario=user.name or user.username,
            user_id=user.id,
            area_code=area_info.get("area_code") if area_info else None,
            department=area_info.get("department") if area_info else None,
            created_at=datetime.now(timezone.utc),
            expires_at=expires_at,
        )
    )
    db.commit()
    return token

def _incidencias_base_url() -> str:
    return (settings.INCIDENCIAS_PUBLIC_BASE_URL or "").strip().rstrip("/")

def _redirect_for_user_area(area_code: str | None, session_token: str) -> str:
    base = _incidencias_base_url()
    prefix = base if base else ""
    area = (area_code or "").strip()
    if area == "soporte":
        return "/panel?area=soporte"
    if area == "materiales":
        return "/materiales"
    if area == "compras_control":
        return "/compras/panel-control"
    if area == "compras_solicitud":
        return "/compras/solicitud"
    if area == "servicio_tecnico":
        return f"{prefix}/?form=panelSelectorServicio&token={session_token}&next=panelSelectorServicio"
    if area == "tecnicos":
        return f"{prefix}/?form=tecnicos&token={session_token}&next=tecnicos"
    if area in {"coordinacion", "protocolos"}:
        return f"{prefix}/?form=panelSelectorCoordinacion&token={session_token}&next=panelSelectorCoordinacion"
    if area == "venta":
        return f"{prefix}/venta/panel-selector?token={session_token}&next=panelSelectorVenta"
    if area == "finanzas":
        return f"{prefix}/venta/finanzas?token={session_token}&next=panelSelectorFinanzas"
    if area == "administracion":
        return f"{prefix}/venta/administracion?token={session_token}&next=panelSelectorAdministracion"
    if area == "operaciones":
        return f"{prefix}/venta/operaciones?token={session_token}&next=panelSelectorOperaciones"
    if area == "guardia":
        return f"{prefix}/guardia?token={session_token}&next=panelSelectorGuardia"
    if area == "supervisores":
        return f"{prefix}/supervisores?token={session_token}&next=panelSelectorSupervisores"
    if area == "rrhh":
        return f"{prefix}/rrhh?token={session_token}&next=panelSelectorRRHH"
    if area == "prevencion":
        return f"{prefix}/prevencion?token={session_token}&next=panelSelectorPrevencion"
    if area == "bitacora":
        return f"{prefix}/bitacora"
    if area == "incidencias":
        return f"{prefix}/?form=panelSelector&token={session_token}&next=panelSelector"
    return "/panel"


def _append_token(url: str, token: str | None) -> str:
    token = str(token or "").strip()
    if not token or "token=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}token={token}"


def _selector_url(token: str | None = "") -> str:
    return _append_token("/seleccionar-area", token)


def _area_panel_url(area_code: str | None, token: str | None = "") -> str:
    area = str(area_code or "").strip()
    panel_map = {
        "soporte": "/panel?area=soporte",
        "incidencias": "/?form=panelSelector",
        "servicio_tecnico": "/?form=panelSelectorServicio",
        "coordinacion": "/?form=panelSelectorCoordinacion",
        "venta": "/venta/panel-selector",
        "finanzas": "/venta/finanzas",
        "administracion": "/venta/administracion",
        "operaciones": "/venta/operaciones",
        "guardia": "/guardia",
        "supervisores": "/supervisores",
        "rrhh": "/rrhh",
        "prevencion": "/prevencion",
        "tecnicos": "/?form=tecnicos",
        "compras_control": "/compras/panel-control",
        "compras_solicitud": "/compras/solicitud",
        "materiales": "/materiales",
        "bitacora": "/bitacora",
    }
    return _append_token(panel_map.get(area, "/seleccionar-area"), token)


def _session_context_for_nav(
    request: Request,
    db: Session,
    token: str | None,
) -> tuple[User | None, str, str]:
    """Devuelve usuario, token de LoginSession y area activa si existen."""
    token_limpio = str(token or "").strip()
    if token_limpio:
        ses = (
            db.query(LoginSession)
            .filter(LoginSession.token == token_limpio)
            .first()
        )
        if ses and ses.user_id:
            expires_at = ses.expires_at
            now = datetime.now(timezone.utc)
            if expires_at and getattr(expires_at, "tzinfo", None) is None:
                now = now.replace(tzinfo=None)
            if expires_at and expires_at <= now:
                ses = None
        if ses and ses.user_id:
            user = db.get(User, int(ses.user_id))
            if user and user.is_active:
                return user, token_limpio, str(ses.area_code or "").strip()

    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token:
        try:
            username = _decode_cookie_token(cookie_token)
            user = UserService.find_by_login(db, username)
            if user and user.is_active:
                return user, token_limpio, ""
        except Exception:
            pass
    return None, token_limpio, ""


def _ticketera_parent_url(params: dict[str, str]) -> str:
    keep = {
        "scope",
        "source",
        "priority",
        "user_filter",
        "q",
        "date_from",
        "date_to",
        "page",
        "view",
    }
    filtered = {k: v for k, v in params.items() if k in keep and v}
    return "/ticketera" + (f"?{urlencode(filtered)}" if filtered else "")


def _infer_area_from_path(path: str, form: str, active_area: str) -> str:
    if active_area:
        return active_area
    if path.startswith(("/ticketera", "/soporte", "/tabla-soporte", "/panel-indicadores")):
        return "soporte"
    if path.startswith("/venta/administracion"):
        return "administracion"
    if path.startswith("/venta/finanzas"):
        return "finanzas"
    if path.startswith("/venta/operaciones"):
        return "operaciones"
    if path.startswith("/venta"):
        return "venta"
    if path.startswith("/guardia"):
        return "supervisores" if "tabla-supervisor" in path else "guardia"
    if path.startswith("/supervisores"):
        return "supervisores"
    if path.startswith("/rrhh"):
        return "rrhh"
    if path.startswith("/prevencion"):
        return "prevencion"
    if path.startswith("/servicio") or path.startswith("/resumen-equipos-tecnicos") or path.startswith("/tecnico-externo"):
        return "servicio_tecnico"
    if form in {"panelSelector", "incidencias", "controlProtocolos", "cierreAperturaClientes", "pruebasSonido"}:
        return "incidencias"
    if form in {"panelSelectorServicio", "servicioTecnico", "stVentas", "TablaServicioTecnico", "tablaServicioTecnico", "rendiciones"}:
        return "servicio_tecnico"
    if form in {"panelSelectorCoordinacion", "coordinacion", "tablaProtocolos", "envioProtocolosSemanales"}:
        return "coordinacion"
    if form in {"tecnicos", "rendicionesTecnico", "formularioViatico"}:
        return "tecnicos"
    return ""


def _nav_back_destination(
    *,
    path: str,
    params: dict[str, str],
    token: str,
    active_area: str,
    area_count: int,
) -> tuple[str, bool]:
    form = str(params.get("form") or "").strip()
    area = _infer_area_from_path(path, form, active_area)

    if path.startswith("/ticketera/tickets/") or re.match(r"^/ticketera/\d+", path):
        return _ticketera_parent_url(params), True

    if path == "/panel-indicadores":
        # Dashboard Soporte tiene 2 padres de nivel 2: Seleccion Panel
        # Operaciones y Seleccion Panel Soporte. Sin "origen" no hay forma de
        # saber por cual se entro, asi que por defecto vuelve a Soporte.
        if params.get("origen") == "operaciones":
            return _area_panel_url("operaciones", token), True
        return _area_panel_url("soporte", token), True

    if path == "/servicio/indicadores":
        # Mismo caso: Dashboard Servicio tiene padres Operaciones y Servicio.
        if params.get("origen") == "operaciones":
            return _area_panel_url("operaciones", token), True
        return _area_panel_url("servicio_tecnico", token), True

    if path == "/ticketera" or path in {"/soporte", "/tabla-soporte"}:
        return _area_panel_url("soporte", token), True

    if path == "/" and form in {"incidencias", "controlProtocolos", "cierreAperturaClientes", "pruebasSonido"}:
        return _area_panel_url("incidencias", token), True
    if path == "/" and form in {"servicioTecnico", "stVentas", "TablaServicioTecnico", "tablaServicioTecnico", "rendiciones"}:
        return _area_panel_url("servicio_tecnico", token), True
    if path == "/" and form in {"coordinacion", "tablaProtocolos", "envioProtocolosSemanales"}:
        return _area_panel_url("coordinacion", token), True
    if path == "/" and form in {"formularioViatico", "rendicionesTecnico"}:
        return _area_panel_url("tecnicos", token), True

    if path.startswith("/venta/administracion/"):
        return _area_panel_url("administracion", token), True
    if path.startswith("/venta/finanzas/"):
        if params.get("from") == "servicio":
            return _area_panel_url("servicio_tecnico", token), True
        return _area_panel_url("finanzas", token), True
    if path.startswith("/venta/operaciones/"):
        return _area_panel_url("operaciones", token), True
    if path.startswith("/venta/") and path != "/venta/panel-selector":
        return _area_panel_url("venta", token), True

    if path.startswith("/prevencion/"):
        return _area_panel_url("prevencion", token), True
    if path.startswith("/rrhh/"):
        return _area_panel_url("rrhh", token), True

    if path.startswith("/guardia/") or path.startswith("/inicio-turno"):
        if params.get("origen") == "guardia":
            return _area_panel_url("guardia", token), True
        if params.get("origen") == "rrhh":
            return _area_panel_url("rrhh", token), True
        if params.get("origen") == "supervisores" or area == "supervisores":
            return _area_panel_url("supervisores", token), True
        return _area_panel_url("guardia", token), True

    if path.startswith("/compras/"):
        # /compras/solicitud es un accion compartida enlazada desde ~8
        # seleccion_panel_*.html distintos; su propia ruta consume y limpia
        # el token de la URL (compras.py._consume_session_token) sin volver a
        # renderizarlo en la pagina, asi que active_area/token quedan sin
        # forma confiable de saber de que panel se vino. "origen" (agregado a
        # cada uno de esos links) es la unica senal fiable en ese caso.
        origen = str(params.get("origen") or "").strip()
        if origen:
            return _area_panel_url(origen, token), True
        return _area_panel_url(area, token) if area else _selector_url(token), True

    if path in {
        "/panel",
        "/venta/panel-selector",
        "/venta/administracion",
        "/venta/finanzas",
        "/venta/operaciones",
        "/guardia",
        "/supervisores",
        "/rrhh",
        "/prevencion",
    } or (path == "/" and form.startswith("panelSelector")):
        return _selector_url(token), area_count > 1

    if path == "/bitacora":
        return _selector_url(token), area_count > 1

    if area:
        return _area_panel_url(area, token), True
    return _selector_url(token), area_count > 1


def _nav_home_destination(db: Session, user: User | None, session_token: str) -> str:
    if not user:
        return "/login"
    if getattr(user, "is_super_admin", False):
        return "/gerencia"
    if getattr(user, "cliente_rut", None):
        return "/portal-cliente"

    areas = _active_user_areas(db, user.id)
    if len(areas) > 1:
        return _selector_url(session_token)

    area_info = areas[0] if areas else None
    token = session_token
    if not token:
        token = _create_unified_login_session(db, user, area_info)
    return _redirect_for_user_area(area_info.get("area_code") if area_info else None, token)

def _set_web_cookie(resp: RedirectResponse, token: str, user_id: int | None = None) -> RedirectResponse:
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=max_age_cookie_segundos(user_id, settings.JWT_EXPIRES_MIN * 60),
    )
    return resp

def get_current_user_web(

    request: Request,

    db: Session = Depends(get_db),

) -> User:

    token = request.cookies.get(COOKIE_NAME)

    if not token:

        raise HTTPException(status_code=401, detail="No autenticado")

    username = _decode_cookie_token(token)

    user = UserService.find_by_login(db, username)

    if not user or not user.is_active:

        raise HTTPException(status_code=401, detail="No autenticado")

    return user

def require_admin_web(current_user: User = Depends(get_current_user_web)) -> User:

    if not current_user.is_super_admin:

        raise HTTPException(status_code=403, detail="No tienes permisos de administrador")

    return current_user

def redirect_to_login() -> RedirectResponse:

    return RedirectResponse(url="/login", status_code=303)

# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸Ãƒâ€šÃ‚ÂÃƒâ€šÃ‚Â  Home -> login

# ======================================================

# ======================================================

# ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ LOGIN (HTML)

# ======================================================

@router.get("/login", response_class=HTMLResponse)

def login_page(request: Request, db: Session = Depends(get_db)):
    # Usuario ya autenticado: directo a su area
    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            username = _decode_cookie_token(token)
            user = UserService.find_by_login(db, username)
            if user and user.is_active:
                return _redirect_for_authenticated_user(db, user)
        except Exception:
            pass

    # Login unico: siempre el login de Incidencias (/?form=login)
    central_login = _incidencias_base_url()
    prefix = central_login if central_login else ""
    return RedirectResponse(url=f"{prefix}/?form=login&next=auto", status_code=303)

_SSO_NEXT_ALLOWED_PREFIXES = (
    "/soporte",
    "/tickets",
    "/dashboard",
    "/ticketera",
    "/tabla-soporte",
    "/materiales",
    "/bitacora",
    "/supervisores",
    "/seleccionar-area",
    "/gerencia",
    "/seleccion-panel-gerencia",
    "/panel",
    "/panel-indicadores",
    "/compras",
    "/portal-cliente",
)


def _sso_safe_next(raw: str | None, fallback_token: str = "") -> str | None:
    candidate = (raw or "").strip()
    if not candidate:
        return None
    # Rechaza rutas absolutas o con protocolo (anti open-redirect)
    if not candidate.startswith("/") or candidate.startswith("//"):
        return None
    base = candidate.split("?", 1)[0]
    if not any(base == p or base.startswith(p + "/") or base.startswith(p + "?") for p in _SSO_NEXT_ALLOWED_PREFIXES):
        return None
    # Si el destino no traía token y tenemos uno, lo agregamos para preservar el flujo
    if fallback_token and base != "/bitacora" and "token=" not in candidate:
        sep = "&" if "?" in candidate else "?"
        candidate = f"{candidate}{sep}token={fallback_token}"
    return candidate


@router.get("/sso/login")
def sso_login(
    token: str = Query(default=""),
    next: str = Query(default=""),
    db: Session = Depends(get_db),
):
    token_limpio = (token or "").strip()
    if not token_limpio:
        return RedirectResponse(url="/login", status_code=303)

    username_row = (
        db.query(User.username)
        .join(LoginSession, User.id == LoginSession.user_id)
        .filter(
            LoginSession.token == token_limpio,
            LoginSession.expires_at > datetime.now(timezone.utc),
            User.is_active == 1,
        )
        .first()
    )
    if not username_row:
        return RedirectResponse(url="/login", status_code=303)

    username = username_row[0]
    user = UserService.find_by_login(db, username)
    if not user or not user.is_active:
        return RedirectResponse(url="/login", status_code=303)

    web_token = create_access_token({"sub": username})
    safe_next = _sso_safe_next(next, fallback_token=token_limpio)
    if safe_next:
        return _set_web_cookie(RedirectResponse(url=safe_next, status_code=303), web_token, user.id)
    return _set_web_cookie(_redirect_for_authenticated_user(db, user), web_token, user.id)

@router.get("/logout")

def logout():

    resp = RedirectResponse(url="/login", status_code=303)

    resp.delete_cookie(COOKIE_NAME)

    return resp


@router.get("/dashboard", include_in_schema=False)
def legacy_dashboard_root(request: Request):
    query = request.url.query
    target = "/ticketera"
    if query:
        target = f"{target}?{query}"
    return RedirectResponse(url=target, status_code=303)


@router.api_route("/dashboard/{rest_of_path:path}", methods=["GET", "POST"], include_in_schema=False)
def legacy_dashboard_path(request: Request, rest_of_path: str):
    query = request.url.query
    target = f"/ticketera/{rest_of_path.lstrip('/')}"
    if query:
        target = f"{target}?{query}"
    return RedirectResponse(url=target, status_code=303)


@router.get("/seleccionar-area", response_class=HTMLResponse)
def seleccionar_area_page(
    request: Request,
    db: Session = Depends(get_db),
):
    # Tolerante a cookie ausente: si no hay sesion, redirige al login (no 401 JSON)
    cookie_token = request.cookies.get(COOKIE_NAME)
    current_user: User | None = None
    if cookie_token:
        try:
            username = _decode_cookie_token(cookie_token)
            user = UserService.find_by_login(db, username)
            if user and user.is_active:
                current_user = user
        except Exception:
            current_user = None
    if current_user is None:
        # Si vino con ?token=... de incidencias, pasamos por el SSO bridge
        token_qs = (request.query_params.get("token") or "").strip()
        if token_qs:
            return RedirectResponse(url=f"/sso/login?token={token_qs}&next=/seleccionar-area", status_code=303)
        return RedirectResponse(url="/login", status_code=303)
    if getattr(current_user, "is_super_admin", False):
        return RedirectResponse(url="/gerencia", status_code=303)
    areas = _active_user_areas(db, current_user.id)
    if len(areas) <= 1:
        return _redirect_for_authenticated_user(db, current_user)
    return templates.TemplateResponse(
        request,
        "seleccion_area.html",
        {
            "request": request,
            "user": current_user,
            "areas": _area_card_options(areas),
            "bitacora_enabled": can_access_bitacora(current_user),
        },
    )


@router.get("/gerencia", response_class=HTMLResponse)
@router.get("/seleccion-panel-gerencia", response_class=HTMLResponse)
def seleccion_panel_gerencia_page(
    request: Request,
    db: Session = Depends(get_db),
):
    cookie_token = request.cookies.get(COOKIE_NAME)
    current_user: User | None = None
    if cookie_token:
        try:
            username = _decode_cookie_token(cookie_token)
            user = UserService.find_by_login(db, username)
            if user and user.is_active:
                current_user = user
        except Exception:
            current_user = None
    if current_user is None:
        token_qs = (request.query_params.get("token") or "").strip()
        if token_qs:
            return RedirectResponse(url=f"/sso/login?token={token_qs}&next=/gerencia", status_code=303)
        return RedirectResponse(url="/login", status_code=303)
    if not getattr(current_user, "is_super_admin", False):
        return _redirect_for_authenticated_user(db, current_user)

    area_info = {"area_code": "gerencia", "department": "Gerencia"}
    session_token = _create_unified_login_session(db, current_user, area_info)
    areas = _area_card_options(_active_user_areas(db, current_user.id))
    return templates.TemplateResponse(
        request,
        "seleccion_panel_gerencia.html",
        {
            "request": request,
            "user": current_user,
            "token": session_token,
            "areas": areas,
        },
    )


@router.post("/seleccionar-area")
def seleccionar_area(
    area_code: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    selected = None
    for area in _active_user_areas(db, current_user.id):
        if str(area.get("area_code") or "") == str(area_code or "").strip():
            selected = area
            break
    if not selected:
        raise HTTPException(status_code=403, detail="No tienes acceso a esa area")
    session_token = _create_unified_login_session(db, current_user, selected)
    return RedirectResponse(
        url=_redirect_for_user_area(str(selected.get("area_code") or ""), session_token),
        status_code=303,
    )


@router.get("/api/navigation/volver")
def navigation_volver(
    request: Request,
    current: str = Query(default=""),
    token: str = Query(default=""),
    db: Session = Depends(get_db),
):
    parsed = urlsplit(current or str(request.headers.get("referer") or "/"))
    path = parsed.path or "/"
    params = {k: v for k, v in parse_qsl(parsed.query, keep_blank_values=False)}
    token_limpio = (token or params.get("token") or "").strip()
    user, session_token, active_area = _session_context_for_nav(request, db, token_limpio)
    area_count = len(_active_user_areas(db, user.id)) if user else 0
    destino, visible = _nav_back_destination(
        path=path,
        params=params,
        token=session_token,
        active_area=active_area,
        area_count=area_count,
    )
    return {"ok": True, "href": destino, "visible": bool(visible)}


@router.get("/api/navigation/home")
def navigation_home(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
):
    user, session_token, _active_area = _session_context_for_nav(request, db, token)
    destino = _nav_home_destination(db, user, session_token)
    return {"ok": True, "href": destino}


@router.get("/panel", response_class=HTMLResponse)
def launcher_panel(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    # current_user ya viene autenticado por la cookie de sesion (JWT), asi que
    # un ?token= en la URL de esta pagina no aporta nada para el acceso — solo
    # quedaba expuesto en la barra de direcciones. Si llega uno, lo limpiamos
    # antes de renderizar (el token para los links salientes se sigue armando
    # mas abajo, pero ya no en la URL visible de /panel).
    if request.query_params.get("token"):
        clean_url = str(request.url.remove_query_params(["token"]))
        return RedirectResponse(url=clean_url, status_code=303)

    areas = _active_user_areas(db, current_user.id)
    if len(areas) > 1 and request.query_params.get("area") != "soporte":
        return RedirectResponse(url="/seleccionar-area", status_code=303)
    # Token vivo para que los enlaces del panel pasen por el SSO bridge
    # y refresquen la cookie. Si vino uno en la URL lo reusamos, si no
    # creamos uno nuevo apuntando al area de soporte.
    token = (request.query_params.get("token") or "").strip()
    if not token:
        area_info = next((a for a in areas if a.get("area_code") == "soporte"), None) or (areas[0] if areas else None)
        token = _create_unified_login_session(db, current_user, area_info)

    # Igual que incidencias_soporte.html: el filtro por defecto de esa tabla
    # es "Derivacion = Pendiente" (aun no se ha derivado a ningun area), no
    # el estado de la incidencia.
    odt_incidencia = func.upper(func.trim(func.coalesce(Registro.odt, "")))
    pendiente_incidencias = (
        db.query(Registro.id)
        .filter(
            func.lower(func.trim(func.coalesce(Registro.derivacion, ""))) == "pendiente",
            ~odt_incidencia.like("V%"),
            ~odt_incidencia.like("S%"),
        )
        .count()
    )

    _ventas_rows = db.execute(text("""
        SELECT
            v.codigo, v.tipo_servicio,
            COALESCE(s.instalacion_finalizada, 0),
            COALESCE(i.fecha_cierre, s.fecha_instalacion_finalizada) AS fecha_instalacion_finalizada_real,
            COALESCE(sp.terminado, 0)
        FROM venta_comercial v
        LEFT JOIN venta_servicio_tecnico s
            ON LOWER(TRIM(s.odt)) = LOWER(TRIM(v.codigo))
        LEFT JOIN venta_soporte_tecnico sp
            ON LOWER(TRIM(sp.odt)) = LOWER(TRIM(v.codigo))
        LEFT JOIN (
            SELECT LOWER(TRIM(odt)) AS odt_key, MAX(fecha_cierre) AS fecha_cierre
            FROM incidencias
            WHERE fecha_cierre IS NOT NULL
            GROUP BY LOWER(TRIM(odt))
        ) i
            ON i.odt_key = LOWER(TRIM(v.codigo))
        WHERE (
            LOWER(v.tipo_servicio) LIKE '%televigilancia%'
            OR LOWER(v.tipo_servicio) LIKE '%alarma%'
            OR LOWER(v.tipo_servicio) LIKE '%instalaci%'
            OR LOWER(v.tipo_servicio) LIKE '%servicio t%'
            OR LOWER(v.tipo_servicio) LIKE '%upgrade%'
            OR LOWER(v.tipo_servicio) LIKE '%downgrade%'
            OR LOWER(v.tipo_servicio) LIKE '%monitoreo adicional%'
        )
    """)).fetchall()
    pendiente_ventas = 0
    for _codigo, _tipo_servicio, _instalacion_finalizada, _fecha_instalacion, _terminado in _ventas_rows:
        # Misma logica que /api/soporte-tecnico/ods-filas: la instalacion
        # cuenta como resuelta si esta finalizada, tiene fecha, o "no aplica"
        # (solo televigilancia). Si la instalacion en si sigue pendiente, no
        # se contabiliza aqui (es otro paso del flujo, no de este panel).
        instalacion_resuelta = bool(_fecha_instalacion) or bool(_instalacion_finalizada)
        no_aplica = _st_es_solo_televigilancia(_tipo_servicio)
        if not (instalacion_resuelta or no_aplica):
            continue
        if not _terminado:
            pendiente_ventas += 1

    _ticket_estados_activos = ("open", "pending", "pending_service", "pending_client")
    pendiente_ticketera = (
        _apply_ticket_visibility_for_user(db.query(Ticket.id), current_user)
        .filter(
            Ticket.status.in_(_ticket_estados_activos),
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
            Ticket.is_no_ticket == False,
        )
        .count()
    )

    return templates.TemplateResponse(
        request,
        "seleccion_panel_soporte.html",
        {
            "request": request,
            "user": current_user,
            "show_back_button": len(areas) > 1 or str(getattr(current_user, "role", "") or "").strip().lower() == "superadmin",
            "token": token,
            "pendiente_incidencias": pendiente_incidencias,
            "pendiente_ventas": pendiente_ventas,
            "pendiente_ticketera": pendiente_ticketera,
        },
    )

# ======================================================

# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‹Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‹Å“ DASHBOARD PRINCIPAL (SOLO ADMIN)

# ======================================================

# ======================================================


def _apply_ticket_search(query, term: str):
    """Filtra tickets por asunto, cliente (nombre/alias/correo) o ID."""
    term = (term or "").strip()
    if not term:
        return query

    like = f"%{term}%"
    clauses = [
        Ticket.subject.ilike(like),
        Ticket.requester.has(Requester.name.ilike(like)),
        Ticket.requester.has(Requester.internal_name.ilike(like)),
        Ticket.requester.has(Requester.email.ilike(like)),
    ]

    # Permite buscar por ID con o sin "#" (p.ej. "360" o "#360").
    ticket_id_term = term.lstrip("#").strip()
    if ticket_id_term.isdigit():
        clauses.append(Ticket.id == int(ticket_id_term))

    return query.filter(or_(*clauses))


_TICKETERA_FILTER_PARAM_NAMES = (
    "scope", "source", "priority", "user_filter", "q", "date_from", "date_to", "view", "status",
)


def _ticketera_filter_querystring(request: Request) -> str:
    """Serializa los filtros activos de /ticketera (scope/source/priority/
    user_filter/q/fechas) para poder propagarlos al abrir un ticket y a los
    botones Anterior/Siguiente, de forma que la navegacion se quede dentro
    del filtro elegido en la lista (pedido explicito, jul 2026)."""
    pairs: list[tuple[str, str]] = []
    for name in _TICKETERA_FILTER_PARAM_NAMES:
        for value in request.query_params.getlist(name):
            if (value or "").strip():
                pairs.append((name, value))
    return urlencode(pairs)


def _ticketera_build_filtered_query(
    request: Request,
    db: Session,
    current_user: User,
    *,
    status: str | None = None,
    q: str | None = None,
    user_filter: str | None = None,
    source: str | None = None,
    priority: str | None = None,
    scope: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """Arma el Query de Ticket filtrado segun los mismos criterios que usa la
    lista de /ticketera (scope/source/priority/user_filter/q/fechas, mas la
    visibilidad del usuario). Reusado por ticket_detail() para que Anterior/
    Siguiente respeten el filtro activo en vez de recorrer todos los tickets."""
    view = request.query_params.get("view")

    allowed_scopes = {"all", "open", "pending", "resolved", "spam", "trash", "no_ticket"}
    allowed_sources = {"all", "email", "whatsapp", "internal"}
    allowed_priorities = {"all", "unassigned", "low", "medium", "high", "urgent"}

    date_from_value = (date_from or "").strip()
    date_to_value = (date_to or "").strip()
    support_users_all = _active_users_in_area(db, "soporte")
    users = _visible_support_users(support_users_all)
    valid_user_ids = {str(u.id) for u in users}

    # AJUSTE TICKETING FILTROS MULTISELECT #
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
        elif view == "no_ticket":
            raw_scope_filters = ["no_ticket"]
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

    query = _apply_ticket_visibility_for_user(db.query(Ticket), current_user)

    # AJUSTE TICKETING FILTROS MULTISELECT #
    if scope_filters == ["all"]:
        query = query.filter(
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
            Ticket.is_no_ticket == False,
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
                    Ticket.is_no_ticket == False,
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
        if "no_ticket" in scope_filters:
            scope_clauses.append(
                and_(
                    Ticket.is_no_ticket == True,
                    Ticket.is_deleted == False,
                )
            )
        if scope_clauses:
            query = query.filter(or_(*scope_clauses))

    if q:
        query = _apply_ticket_search(query, q)

    if user_filters != ["all"]:
        user_clauses = []
        selected_user_ids = [int(value) for value in user_filters if value.isdigit()]
        if selected_user_ids:
            user_clauses.append(Ticket.assigned_to_id.in_(selected_user_ids))
        if "unassigned" in user_filters:
            user_clauses.append(Ticket.assigned_to_id.is_(None))
        # Ronald Montilla sigue viendo los tickets que pasaron por
        # "Asignar a todo el equipo", aunque ya se los haya tomado otro
        # agente y el filtro no lo incluya a el (pedido explicito, jul 2026).
        if user_clauses and _is_ronald_montilla_user(current_user):
            user_clauses.append(Ticket.team_broadcast_at.isnot(None))
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

    filter_state = {
        "scope_filters": scope_filters,
        "source_filters": source_filters,
        "priority_filters": priority_filters,
        "user_filters": user_filters,
        "scope_filter": scope_filter,
        "source_filter": source_filter,
        "priority_filter": priority_filter,
        "user_filter_value": user_filter_value,
        "date_from_value": date_from_value,
        "date_to_value": date_to_value,
        "users": users,
    }
    return query, filter_state


@router.get("/ticketera", response_class=HTMLResponse)
@router.get("/ticketera/pagina/{page}", response_class=HTMLResponse)
def ticketera(
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
    _require_area_access(db, current_user, "soporte")

    query, _filter_state = _ticketera_build_filtered_query(
        request, db, current_user,
        status=status, q=q, user_filter=user_filter, source=source,
        priority=priority, scope=scope, date_from=date_from, date_to=date_to,
    )
    scope_filters = _filter_state["scope_filters"]
    source_filters = _filter_state["source_filters"]
    priority_filters = _filter_state["priority_filters"]
    user_filters = _filter_state["user_filters"]
    scope_filter = _filter_state["scope_filter"]
    source_filter = _filter_state["source_filter"]
    priority_filter = _filter_state["priority_filter"]
    user_filter_value = _filter_state["user_filter_value"]
    date_from_value = _filter_state["date_from_value"]
    date_to_value = _filter_state["date_to_value"]
    users = _filter_state["users"]

    # Orden por ultima actividad (como Gmail): si un tercero responde un
    # ticket ya creado, ese ticket sube arriba de la lista en vez de quedarse
    # clavado en el orden de creacion original — pedido explicito, jul 2026.
    latest_activity_subq = (
        db.query(Message.ticket_id, func.max(Message.created_at).label("last_activity"))
        .group_by(Message.ticket_id)
        .subquery()
    )
    query = query.outerjoin(
        latest_activity_subq, latest_activity_subq.c.ticket_id == Ticket.id
    )

    page_size = 30
    safe_page = max(1, page)
    total_filtered = query.count()
    total_pages = max(1, (total_filtered + page_size - 1) // page_size)

    if safe_page > total_pages:
        safe_page = total_pages

    tickets = (
        query.order_by(
            func.coalesce(latest_activity_subq.c.last_activity, Ticket.created_at).desc(),
            Ticket.id.desc(),
        )
        .offset((safe_page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    ticket_has_new_message: dict[int, bool] = {}
    ticket_unseen_message_id: dict[int, int] = {}
    ticket_unseen_message_count: dict[int, int] = {}
    ticket_manually_unread: dict[int, bool] = {}
    ticket_ids = [t.id for t in tickets]
    if ticket_ids:
        manual_rows = (
            db.query(TicketManualUnread.ticket_id)
            .filter(
                TicketManualUnread.user_id == current_user.id,
                TicketManualUnread.ticket_id.in_(ticket_ids),
            )
            .all()
        )
        for row in manual_rows:
            ticket_manually_unread[int(row.ticket_id)] = True

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

    def build_ticketera_url(
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

        base = f"/ticketera/pagina/{page_number}" if page_number and page_number > 1 else "/ticketera"

        if not params:
            return base
        return f"{base}?{urlencode(params)}"

    prev_page_url = build_ticketera_url(page_number=safe_page - 1) if safe_page > 1 else None
    next_page_url = build_ticketera_url(page_number=safe_page + 1) if safe_page < total_pages else None
    first_page_url = build_ticketera_url(page_number=1) if safe_page > 1 else None
    last_page_url = build_ticketera_url(page_number=total_pages) if safe_page < total_pages else None

    # Los conteos deben reflejar lo que este usuario puede ver realmente en
    # la lista (mismo filtro de visibilidad por buzon restringido que la
    # query de tickets), no el total global de la BBDD (bug reportado: el
    # modal mostraba "Open (1)" para un ticket que el usuario no podia ver
    # porque pertenecia al buzon restringido y no le estaba asignado).
    counts = {
        "all": _apply_ticket_visibility_for_user(db.query(Ticket), current_user).filter(
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
            Ticket.is_no_ticket == False,
        ).count(),
        "open": _apply_ticket_visibility_for_user(db.query(Ticket), current_user).filter(
            Ticket.status == "open",
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
            Ticket.is_no_ticket == False,
        ).count(),
        "pending": _apply_ticket_visibility_for_user(db.query(Ticket), current_user).filter(
            Ticket.status.in_(PENDING_TICKET_STATUSES),
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
            Ticket.is_no_ticket == False,
        ).count(),
        "resolved": _apply_ticket_visibility_for_user(db.query(Ticket), current_user).filter(
            Ticket.status == "resolved",
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
            Ticket.is_no_ticket == False,
        ).count(),
        "spam": _apply_ticket_visibility_for_user(db.query(Ticket), current_user).filter(
            Ticket.is_spam == True,
            Ticket.is_deleted == False,
        ).count(),
        "trash": _apply_ticket_visibility_for_user(db.query(Ticket), current_user).filter(
            Ticket.is_deleted == True,
        ).count(),
        "no_ticket": _apply_ticket_visibility_for_user(db.query(Ticket), current_user).filter(
            Ticket.is_no_ticket == True,
            Ticket.is_deleted == False,
        ).count(),
    }

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
        request,
        "ticketera.html",
        {
            "request": request,
            "user": current_user,
            "user_signature": signature_html_for_user(current_user),
            "tickets": tickets,
            "ticket_has_new_message": ticket_has_new_message,
            "ticket_unseen_message_id": ticket_unseen_message_id,
            "ticket_unseen_message_count": ticket_unseen_message_count,
            "ticket_manually_unread": ticket_manually_unread,
            "filter_qs": _ticketera_filter_querystring(request),
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
    _require_area_access(db, current_user, "soporte")
    users = _visible_support_users(_active_users_in_area(db, "soporte"))

    query = _apply_ticket_visibility_for_user(db.query(Ticket), current_user).options(
        joinedload(Ticket.requester),
        joinedload(Ticket.assigned_to),
    )
    # Los tickets marcados "no_ticket" no cuentan como Open/Pendiente/Cerrado
    # real (mismo criterio que ticketera, que los excluye de esos conteos) —
    # solo se muestran aqui si ademas cayeron en Spam o Papelera.
    query = query.filter(
        or_(Ticket.is_no_ticket == False, Ticket.is_deleted == True, Ticket.is_spam == True)
    )

    search_value = (q or "").strip()
    if search_value:
        query = _apply_ticket_search(query, search_value)

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
        request,
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    _require_area_access(db, current_user, "soporte")
    # Vista importada desde el proyecto de Incidencias.
    return templates.TemplateResponse(
        request,
        "incidencias_soporte.html",
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

    for table_name in ("registro", "registros", "Registro", "Registros", "incidencias"):
        if table_name in table_names:
            cols = {col["name"] for col in inspector.get_columns(table_name)}
            if "observacion_soporte" not in cols:
                try:
                    add_column(db, table_name, "observacion_soporte", "TEXT")
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


_SUPPORT_NON_TECHNICIAN_NAMES = {
    "fernando lubiano",
    "gianpiero lubiano",
}


def _support_person_key(value: object) -> str:
    normalized = unicodedata.normalize("NFD", _support_text(value))
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _support_is_assignable_technician(value: object) -> bool:
    key = _support_person_key(value)
    return bool(key) and not any(non_tech in key for non_tech in _SUPPORT_NON_TECHNICIAN_NAMES)


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


def _support_is_mantencion_odt(odt: str) -> bool:
    return (odt or "").strip().upper().startswith("M")


def _support_normalize_sucursal_key(value: str) -> str:
    txt = str(value or "").strip().lower()
    if not txt:
        return ""
    txt = unicodedata.normalize("NFD", txt)
    txt = "".join(ch for ch in txt if unicodedata.category(ch) != "Mn")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


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


def _support_ensure_cierre_tables_UNUSED_MIGRATION(db: Session) -> None:
    # Código de migración de incidencias_imagenes desactivado.
    # La tabla incidencias_imagenes fue eliminada del esquema.
    bind = db.get_bind()
    inspector = sa_inspect(bind)
    table_names = set(inspector.get_table_names())
    legacy_image_columns = ("imagen_1", "imagen_2", "imagen_3", "foto", "foto_2", "informe")

    if "incidencias_imagenes" in table_names:
        image_cols = {col["name"] for col in inspector.get_columns("incidencias_imagenes")}
        null_text = sql_null_text((db.bind.dialect.name or "").lower())
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
                    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='uq_incidencias_imagenes_odt' AND object_id=OBJECT_ID('incidencias_imagenes'))
                    CREATE UNIQUE INDEX uq_incidencias_imagenes_odt ON incidencias_imagenes (odt)
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
                    f"""
                    SELECT odt, {null_text} AS sucursal, file_url
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

        select_odt = "odt" if "odt" in source_cols else null_text
        if "sucursal" in source_cols:
            select_sucursal = "sucursal"
        elif "cliente" in source_cols:
            select_sucursal = "cliente"
        elif "puesto" in source_cols:
            select_sucursal = "puesto"
        else:
            select_sucursal = null_text

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
            IF OBJECT_ID('incidencias_imagenes', 'U') IS NULL
            BEGIN
            CREATE TABLE incidencias_imagenes (
                id BIGINT IDENTITY(1,1) PRIMARY KEY,
                odt VARCHAR(80) NOT NULL UNIQUE,
                sucursal VARCHAR(255),
                imagenes NVARCHAR(MAX) NOT NULL DEFAULT '[]',
                created_by VARCHAR(180),
                created_at DATETIMEOFFSET NOT NULL DEFAULT GETDATE(),
                updated_at DATETIMEOFFSET NOT NULL DEFAULT GETDATE()
            )
            END
            """
        )
    )
    db.execute(
        text(
            """
            IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='idx_incidencias_imagenes_odt' AND object_id=OBJECT_ID('incidencias_imagenes'))
            CREATE INDEX idx_incidencias_imagenes_odt ON incidencias_imagenes (odt)
            """
        )
    )
    db.execute(
        text(
            """
            IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='uq_incidencias_imagenes_odt' AND object_id=OBJECT_ID('incidencias_imagenes'))
            CREATE UNIQUE INDEX uq_incidencias_imagenes_odt ON incidencias_imagenes (odt)
            """
        )
    )

    for odt_key, payload in grouped_images.items():
        image_list = [img for img in payload["imagenes"] if _support_text(img)]
        db.execute(
            text(
                """
                INSERT INTO incidencias_imagenes (odt, sucursal, imagenes, created_by)
                VALUES (:odt, :sucursal, :imagenes, :created_by)
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
                IF OBJECT_ID('incidencias_imagenes_odt', 'U') IS NULL
                BEGIN
                CREATE TABLE incidencias_imagenes_odt (
                    id BIGINT IDENTITY(1,1) PRIMARY KEY,
                    odt VARCHAR(80) NOT NULL UNIQUE,
                    sucursal VARCHAR(255),
                    imagenes NVARCHAR(MAX) NOT NULL DEFAULT '[]',
                    created_by VARCHAR(180),
                    created_at DATETIMEOFFSET NOT NULL DEFAULT GETDATE(),
                    updated_at DATETIMEOFFSET NOT NULL DEFAULT GETDATE()
                )
                END
                """
            )
        )
        db.execute(
            text(
                """
                IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='uq_incidencias_imagenes_odt_odt' AND object_id=OBJECT_ID('incidencias_imagenes_odt'))
                CREATE UNIQUE INDEX uq_incidencias_imagenes_odt_odt ON incidencias_imagenes_odt (odt)
                """
            )
        )
        db.execute(
            text(
                """
                IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='idx_incidencias_imagenes_odt_odt' AND object_id=OBJECT_ID('incidencias_imagenes_odt'))
                CREATE INDEX idx_incidencias_imagenes_odt_odt ON incidencias_imagenes_odt (odt)
                """
            )
        )
        db.execute(text("IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_img_odt_created_at' AND parent_object_id=OBJECT_ID('incidencias_imagenes_odt')) ALTER TABLE incidencias_imagenes_odt ADD CONSTRAINT DF_img_odt_created_at DEFAULT GETDATE() FOR created_at"))
        db.execute(text("IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_img_odt_updated_at' AND parent_object_id=OBJECT_ID('incidencias_imagenes_odt')) ALTER TABLE incidencias_imagenes_odt ADD CONSTRAINT DF_img_odt_updated_at DEFAULT GETDATE() FOR updated_at"))
        db.execute(text("UPDATE incidencias_imagenes_odt SET created_at = GETDATE() WHERE created_at IS NULL"))
        db.execute(text("UPDATE incidencias_imagenes_odt SET updated_at = GETDATE() WHERE updated_at IS NULL"))

        def _table_exists(table_name: str) -> bool:
            return bool(db.execute(text("SELECT OBJECT_ID(:name)"), {"name": f"dbo.{table_name}"}).scalar() is not None)

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
                        ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
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
                        MERGE INTO incidencias_imagenes_odt AS target
                        USING (SELECT :odt AS odt, :sucursal AS sucursal, :imagenes AS imagenes, :created_by AS created_by) AS source
                        ON target.odt = source.odt
                        WHEN MATCHED THEN UPDATE SET
                            target.sucursal   = COALESCE(source.sucursal, target.sucursal),
                            target.imagenes   = source.imagenes,
                            target.created_by = COALESCE(source.created_by, target.created_by),
                            target.updated_at = GETDATE()
                        WHEN NOT MATCHED THEN INSERT (odt, sucursal, imagenes, created_by, created_at, updated_at)
                            VALUES (source.odt, source.sucursal, source.imagenes, source.created_by, GETDATE(), GETDATE());
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


def _support_ensure_mantenciones_images_table(db: Session) -> None:
    try:
        db.execute(
            text(
                """
                IF OBJECT_ID('mantenciones_imagenes_sucursal', 'U') IS NULL
                BEGIN
                CREATE TABLE mantenciones_imagenes_sucursal (
                    id BIGINT IDENTITY(1,1) PRIMARY KEY,
                    sucursal_key VARCHAR(255) NOT NULL UNIQUE,
                    sucursal VARCHAR(255) NOT NULL,
                    imagenes NVARCHAR(MAX) NOT NULL DEFAULT '[]',
                    created_by VARCHAR(180),
                    created_at DATETIMEOFFSET NOT NULL DEFAULT GETDATE(),
                    updated_at DATETIMEOFFSET NOT NULL DEFAULT GETDATE()
                )
                END
                """
            )
        )
        db.execute(
            text(
                """
                IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='uq_mantenciones_imagenes_sucursal_key' AND object_id=OBJECT_ID('mantenciones_imagenes_sucursal'))
                CREATE UNIQUE INDEX uq_mantenciones_imagenes_sucursal_key ON mantenciones_imagenes_sucursal (sucursal_key)
                """
            )
        )
        db.execute(
            text(
                """
                IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='idx_mantenciones_imagenes_sucursal' AND object_id=OBJECT_ID('mantenciones_imagenes_sucursal'))
                CREATE INDEX idx_mantenciones_imagenes_sucursal ON mantenciones_imagenes_sucursal (sucursal)
                """
            )
        )
        db.execute(text("IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_mant_img_created_at' AND parent_object_id=OBJECT_ID('mantenciones_imagenes_sucursal')) ALTER TABLE mantenciones_imagenes_sucursal ADD CONSTRAINT DF_mant_img_created_at DEFAULT GETDATE() FOR created_at"))
        db.execute(text("IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_mant_img_updated_at' AND parent_object_id=OBJECT_ID('mantenciones_imagenes_sucursal')) ALTER TABLE mantenciones_imagenes_sucursal ADD CONSTRAINT DF_mant_img_updated_at DEFAULT GETDATE() FOR updated_at"))
        db.execute(text("UPDATE mantenciones_imagenes_sucursal SET created_at = GETDATE() WHERE created_at IS NULL"))
        db.execute(text("UPDATE mantenciones_imagenes_sucursal SET updated_at = GETDATE() WHERE updated_at IS NULL"))

        # Migración liviana: si existían imágenes por ODT para Mantenciones (ODT 'M%'),
        # consolidamos por sucursal para que queden "para siempre" en la tabla nueva.
        try:
            legacy_rows = db.execute(
                text(
                    """
                    SELECT odt, sucursal, imagenes, created_by
                    FROM incidencias_imagenes_odt
                    WHERE UPPER(COALESCE(TRIM(odt), '')) LIKE 'M%'
                      AND COALESCE(TRIM(sucursal), '') <> ''
                    """
                )
            ).mappings().all()
        except Exception:
            legacy_rows = []

        migrated_any = False
        for row in legacy_rows:
            sucursal = _support_text(row.get("sucursal"))
            key = _support_normalize_sucursal_key(sucursal)
            if not key:
                continue
            imgs = _support_parse_image_list(row.get("imagenes"))[:3]
            if not imgs:
                continue
            db.execute(
                text(
                    """
                    MERGE INTO mantenciones_imagenes_sucursal AS target
                    USING (SELECT :key AS sucursal_key, :sucursal AS sucursal, :imagenes AS imagenes, :created_by AS created_by) AS source
                    ON target.sucursal_key = source.sucursal_key
                    WHEN MATCHED THEN UPDATE SET
                        target.sucursal   = source.sucursal,
                        target.imagenes   = source.imagenes,
                        target.created_by = COALESCE(source.created_by, target.created_by),
                        target.updated_at = GETDATE()
                    WHEN NOT MATCHED THEN INSERT (sucursal_key, sucursal, imagenes, created_by, created_at, updated_at)
                        VALUES (source.sucursal_key, source.sucursal, source.imagenes, source.created_by, GETDATE(), GETDATE());
                    """
                ),
                {
                    "key": key,
                    "sucursal": sucursal,
                    "imagenes": json.dumps(imgs, ensure_ascii=False),
                    "created_by": _support_text(row.get("created_by")) or "migracion_mantencion",
                },
            )
            migrated_any = True

        # Si migramos algo, limpiamos las filas 'M%' para evitar duplicidad y forzar el nuevo origen.
        if migrated_any:
            db.execute(
                text(
                    """
                    DELETE FROM incidencias_imagenes_odt
                    WHERE UPPER(COALESCE(TRIM(odt), '')) LIKE 'M%'
                    """
                )
            )

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


def _support_fetch_mantencion_images_by_sucursal_key(db: Session) -> dict[str, list[str]]:
    _support_ensure_mantenciones_images_table(db)
    out: dict[str, list[str]] = {}
    rows = db.execute(
        text(
            """
            SELECT sucursal_key, imagenes
            FROM mantenciones_imagenes_sucursal
            WHERE COALESCE(TRIM(sucursal_key), '') <> ''
            """
        )
    ).mappings().all()
    for row in rows:
        key = _support_text(row.get("sucursal_key"))
        if not key:
            continue
        out[key] = _support_parse_image_list(row.get("imagenes"))[:3]
    return out


def _support_append_user_observation(current_text: str, user_label: str, obs_text: str) -> str:
    timestamp = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M")
    line = f"[{user_label} - {timestamp}] {obs_text}"
    if not current_text:
        return line
    return f"{current_text.rstrip()}\n{line}"


# Edicion de la ultima nota de observacion, con ventana de tiempo tipo
# "editar mensaje de WhatsApp" (pedido explicito, jul 2026): solo el mismo
# usuario que escribio la ultima linea puede modificarla, y solo dentro de
# los OBSERVACION_EDIT_WINDOW_MINUTES desde que la escribio.
OBSERVACION_EDIT_WINDOW_MINUTES = 15
_OBS_ENTRY_RE = re.compile(
    r"^\[(?P<user>.+) - (?P<fecha>\d{2}/\d{2}/\d{4} \d{2}:\d{2})\]\s*(?:\(editado\)\s*)?(?P<texto>.*)$"
)


def _support_last_observation_entry(text: str) -> dict | None:
    lines = (text or "").splitlines()
    start_idx = None
    match = None
    for idx in range(len(lines) - 1, -1, -1):
        m = _OBS_ENTRY_RE.match(lines[idx].strip())
        if m:
            start_idx = idx
            match = m
            break
    if start_idx is None or match is None:
        return None
    try:
        fecha_dt = datetime.strptime(match.group("fecha"), "%d/%m/%Y %H:%M")
    except ValueError:
        return None
    return {
        "start_idx": start_idx,
        "user": match.group("user").strip(),
        "fecha_str": match.group("fecha"),
        "fecha_dt": fecha_dt,
    }


def _support_can_edit_observation_entry(entry: dict | None, user_label: str) -> bool:
    if not entry:
        return False
    if entry["user"].strip().casefold() != (user_label or "").strip().casefold():
        return False
    ahora = datetime.now().astimezone().replace(tzinfo=None)
    elapsed = ahora - entry["fecha_dt"]
    return timedelta(0) <= elapsed <= timedelta(minutes=OBSERVACION_EDIT_WINDOW_MINUTES)


def _support_edit_last_observation_line(current_text: str, entry: dict, user_label: str, nuevo_texto: str) -> str:
    lines = current_text.splitlines()
    nueva_linea = f"[{user_label} - {entry['fecha_str']}] (editado) {nuevo_texto}"
    nuevas_lineas = lines[: entry["start_idx"]] + [nueva_linea]
    return "\n".join(nuevas_lineas).strip()


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
    mapped_rows = [
        dict(row)
        for row in rows
        if not str(row.get("odt") or "").strip().upper().startswith(("V", "S"))
    ]

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
    # Endpoint de compatibilidad para incidencias_soporte.html (antes Google Apps Script).
    _ = current_user  # Mantiene autenticacion por cookie.

    _table, incidencias = _support_query_incidencias(db)
    extra_images_by_odt: dict[str, list[str]] = {}
    mantencion_images_by_sucursal_key: dict[str, list[str]] = {}
    try:
        extra_images_by_odt = _support_fetch_support_images_by_odt(db)
    except Exception:
        # Si falla la lectura de tabla de soporte, seguimos sin imagenes.
        pass
    try:
        mantencion_images_by_sucursal_key = _support_fetch_mantencion_images_by_sucursal_key(db)
    except Exception:
        pass

    rows: list[list[str | int]] = []

    for incidencia in incidencias:
        cliente = _support_pick(incidencia, "cliente", "sucursal", "puesto")
        odt = _support_pick(incidencia, "odt") or f"#{incidencia.get('id')}"
        tecnico_titular = _support_pick_person(incidencia, "tecnico", "tecnicos")
        tecnico_acompanante = _support_pick_person(incidencia, "acompanante")
        if _support_is_mantencion_odt(odt):
            key = _support_normalize_sucursal_key(cliente)
            odt_images = mantencion_images_by_sucursal_key.get(key, []) if key else []
        else:
            odt_images = extra_images_by_odt.get(odt, [])
        extra_images: list[str] = []
        for image_url in odt_images:
            if image_url and image_url not in extra_images:
                extra_images.append(image_url)

        # Estructura compatible con incidencias_soporte.html:
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

    def add_tecnico_option(value: object) -> None:
        tecnico = _support_text(value)
        key = _support_person_key(tecnico)
        if not tecnico or not _support_is_assignable_technician(tecnico) or key in seen_tecnicos:
            return
        seen_tecnicos.add(key)
        tecnicos.append(tecnico)

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

    # Fuente unificada del Windows Server: usuarios activos con departamento/area Tecnicos.
    for user in _active_users_in_area(catalog_db, "tecnicos"):
        add_tecnico_option(getattr(user, "name", ""))

    # Catalogo legacy/oficial de tecnicos, si existe.
    try:
        catalog_rows = catalog_db.execute(
            text(
                """
                SELECT nombre
                FROM incidencias_tecnicos
                WHERE activo = 1
                ORDER BY nombre ASC
                """
            )
        ).fetchall()
        for row in catalog_rows:
            add_tecnico_option(row[0])
    except Exception:
        # Si no existe la tabla de catalogo, seguimos con usuarios activos del area Tecnicos.
        catalog_db.rollback()

    if not tecnicos:
        for incidencia in incidencias:
            add_tecnico_option(_support_pick_person(incidencia, "tecnico", "tecnicos"))
            add_tecnico_option(_support_pick_person(incidencia, "acompanante"))

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
    # Fuente: bbdd_sucursales.nombre_sucursal (unificada; antes catalogo_clientes).
    _ = current_user

    clientes_map: dict[str, str] = {}
    tecnicos_map: dict[str, str] = {}

    def add_ticket_tecnico(value: object) -> None:
        tecnico = _support_text(value)
        key = _support_person_key(tecnico)
        if tecnico and _support_is_assignable_technician(tecnico) and key not in tecnicos_map:
            tecnicos_map[key] = tecnico

    try:
        rows = db.execute(
            text(
                """
                SELECT nombre_sucursal
                FROM bbdd_sucursales
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
        db.rollback()

    # Fuente unificada del Windows Server: usuarios activos con departamento/area Tecnicos.
    for user in _active_users_in_area(db, "tecnicos"):
        add_ticket_tecnico(getattr(user, "name", ""))

    # Catalogo legacy/oficial de tecnicos, si existe.
    try:
        catalog_rows = catalog_db.execute(
            text(
                """
                SELECT nombre
                FROM incidencias_tecnicos
                WHERE activo = 1
                ORDER BY nombre ASC
                """
            )
        ).fetchall()
        for row in catalog_rows:
            add_ticket_tecnico(row[0])
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
    tecnicos = sorted(tecnicos_map.values(), key=lambda x: x.casefold())

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
    # Compatibilidad con doble-click de incidencias_soporte.html.
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
        elif valor.casefold() == "repetida":
            repetida_ref = _support_text(payload.get("repetidaOdtRef") or payload.get("repetida_odt_ref"))
            if not repetida_ref:
                raise HTTPException(status_code=400, detail="Debes indicar la ODT con la que se repite.")

            current_row = db.execute(
                select(table).where(table.c.id == incidencia_id).limit(1)
            ).mappings().first()
            if not current_row:
                raise HTTPException(status_code=404, detail="Fila no encontrada")

            current_odt = _support_text(current_row.get("odt"))
            if repetida_ref == current_odt:
                raise HTTPException(status_code=400, detail="La ODT repetida no puede ser la misma ODT actual.")

            ref_row = None
            if "odt" in table.c:
                ref_row = db.execute(
                    select(table)
                    .where(func.lower(func.trim(table.c.odt)) == repetida_ref.lower())
                    .limit(1)
                ).mappings().first()
            if not ref_row:
                raise HTTPException(status_code=400, detail=f"No se encontro la ODT de referencia {repetida_ref}.")

            ref_estado = _support_text(ref_row.get("estado")).casefold()
            if "pend" not in ref_estado and "proceso" not in ref_estado:
                raise HTTPException(status_code=400, detail="La ODT de referencia debe estar Pendiente o En Proceso.")

            current_cliente = _support_text(current_row.get("cliente") or current_row.get("sucursal"))
            ref_cliente = _support_text(ref_row.get("cliente") or ref_row.get("sucursal"))
            if _support_normalize_sucursal_key(current_cliente) != _support_normalize_sucursal_key(ref_cliente):
                raise HTTPException(status_code=400, detail="La ODT de referencia debe ser de la misma sucursal.")

            current_problema = _support_text(current_row.get("problema") or current_row.get("tipo_incidencia"))
            ref_problema = _support_text(ref_row.get("problema") or ref_row.get("tipo_incidencia"))
            if _support_normalize_sucursal_key(current_problema) != _support_normalize_sucursal_key(ref_problema):
                raise HTTPException(status_code=400, detail="Solo puedes marcar como repetida con una ODT del mismo problema.")

            if "estado" in table.c:
                values_to_update["estado"] = "Repetida"
            if "fecha_cierre" in table.c:
                values_to_update["fecha_cierre"] = datetime.now().astimezone()
            obs_servicio_col = "observacion_servicio" if "observacion_servicio" in table.c else None
            if obs_servicio_col:
                values_to_update[obs_servicio_col] = f"ODT con la que se repite {repetida_ref}"
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
            user_label = (current_user.name or current_user.username or "Usuario").strip()

            if bool(payload.get("editar_ultima")):
                # Edicion de la ultima nota (ventana de 15 min, solo el autor) —
                # pedido explicito, jul 2026.
                entry = _support_last_observation_entry(current_text)
                if not _support_can_edit_observation_entry(entry, user_label):
                    raise HTTPException(
                        status_code=400,
                        detail="Ya no puedes editar esta observacion (limite de 15 minutos).",
                    )
                nuevo_texto = valor.strip()
                if not nuevo_texto:
                    raise HTTPException(status_code=400, detail="La observacion no puede quedar vacia.")
                values_to_update[support_observation_col] = _support_edit_last_observation_line(
                    current_text, entry, user_label, nuevo_texto
                )
            else:
                edited_text = valor
                original_text = valor_original
                if not edited_text:
                    # Permite borrar completamente la observacion.
                    values_to_update[support_observation_col] = None
                elif not current_text:
                    # Primera observacion: se registra con metadata.
                    timestamp = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M")
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

        tiene_tecnico = tecnico_titular and tecnico_titular != "-"
        if "estado" in table.c:
            values_to_update["estado"] = "En Proceso" if tiene_tecnico else "Pendiente"
        if "derivacion" in table.c:
            values_to_update["derivacion"] = "Servicio Técnico" if tiene_tecnico else "Pendiente"

    if values_to_update:
        stmt = update(table).where(table.c.id == incidencia_id).values(**values_to_update)
        db.execute(stmt)

    db.commit()
    response: dict[str, object] = {"ok": True}
    if "estado" in values_to_update:
        response["estado"] = values_to_update["estado"]
    if "derivacion" in values_to_update:
        response["derivacion"] = values_to_update["derivacion"]
    return response


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
    dest_dir = _UPLOADS_ROOT / "incidencias" / folder_safe
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
            ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
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
                    imagenes = :imagenes,
                    created_by = :created_by,
                    updated_at = GETDATE()
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
                    :odt, :sucursal, :imagenes, :created_by
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

    user_label = (current_user.name or current_user.username or "Usuario").strip()
    sucursal_value = _support_pick(dict(incidencia_row), "sucursal", "cliente", "puesto")
    is_mantencion = _support_is_mantencion_odt(odt_clean)

    if is_mantencion:
        _support_ensure_mantenciones_images_table(db)
        sucursal_key = _support_normalize_sucursal_key(sucursal_value)
        if not sucursal_key:
            raise HTTPException(status_code=400, detail="No se pudo determinar la sucursal para guardar imágenes de Mantención.")
        existing_images_row = db.execute(
            text(
                """
                SELECT id, imagenes
                FROM mantenciones_imagenes_sucursal
                WHERE sucursal_key = :key
                ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
                """
            ),
            {"key": sucursal_key},
        ).mappings().first()
    else:
        existing_images_row = db.execute(
            text(
                """
                SELECT id, imagenes
                FROM incidencias_imagenes_odt
                WHERE odt = :odt
                ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
                """
            ),
            {"odt": odt_clean},
        ).mappings().first()

    if is_mantencion:
        existing_images = _support_parse_image_list(existing_images_row.get("imagenes"))[:3] if existing_images_row else []
    else:
        inc = dict(incidencia_row)
        unified_images = _support_parse_image_list(existing_images_row.get("imagenes")) if existing_images_row else []
        registro_images = [
            _support_text(inc.get("foto_1")),
            _support_text(inc.get("foto_2")),
            _support_text(inc.get("foto_3")),
        ]
        existing_images = []
        for image_url in [*unified_images, *registro_images]:
            clean_url = _support_text(image_url)
            if clean_url and clean_url not in existing_images:
                existing_images.append(clean_url)
            if len(existing_images) >= 3:
                break

    remaining_slots = max(0, 3 - len(existing_images))
    if remaining_slots <= 0:
        if is_mantencion:
            raise HTTPException(status_code=400, detail="Esta sucursal ya tiene 3 imagenes de mantención guardadas.")
        raise HTTPException(status_code=400, detail="Esta ODT ya tiene 3 imagenes de soporte.")
    if len(incoming_images) > remaining_slots:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Solo puedes subir {remaining_slots} imagen(es) adicional(es) para esta sucursal."
                if is_mantencion
                else f"Solo puedes subir {remaining_slots} imagen(es) adicional(es) para esta ODT."
            ),
        )

    new_data_uris = []
    for img in incoming_images:
        raw = img["bytes"]
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            continue
        encoded = base64.b64encode(raw).decode("ascii")
        new_data_uris.append(f"data:{img['mime_type']};base64,{encoded}")

    if not new_data_uris:
        raise HTTPException(status_code=400, detail="No se pudo procesar ninguna imagen.")

    merged_images = existing_images + new_data_uris
    merged_images = merged_images[:3]

    if is_mantencion:
        sucursal_key = _support_normalize_sucursal_key(sucursal_value)
        db.execute(
            text(
                """
                MERGE INTO mantenciones_imagenes_sucursal AS target
                USING (SELECT :key AS sucursal_key, :sucursal AS sucursal, :imagenes AS imagenes, :created_by AS created_by) AS source
                ON target.sucursal_key = source.sucursal_key
                WHEN MATCHED THEN UPDATE SET
                    target.sucursal   = source.sucursal,
                    target.imagenes   = source.imagenes,
                    target.created_by = COALESCE(source.created_by, target.created_by),
                    target.updated_at = GETDATE()
                WHEN NOT MATCHED THEN INSERT (sucursal_key, sucursal, imagenes, created_by, created_at, updated_at)
                    VALUES (source.sucursal_key, source.sucursal, source.imagenes, source.created_by, GETDATE(), GETDATE());
                """
            ),
            {
                "key": sucursal_key,
                "sucursal": sucursal_value or sucursal_key,
                "imagenes": json.dumps(merged_images, ensure_ascii=False),
                "created_by": user_label,
            },
        )
    else:
        foto_slots = ["foto_1", "foto_2", "foto_3"]
        inc = dict(incidencia_row)
        updates: dict[str, str] = {}
        uri_queue = list(new_data_uris)
        for slot in foto_slots:
            if not uri_queue:
                break
            if not _support_text(inc.get(slot)):
                updates[slot] = uri_queue.pop(0)

        if updates:
            set_clause = ", ".join(f'"{col}" = :{col}' for col in updates)
            params = {**updates, "odt": odt_clean}
            db.execute(text(f"UPDATE incidencias SET {set_clause} WHERE odt = :odt"), params)

        db.execute(
            text(
                """
                MERGE INTO incidencias_imagenes_odt AS target
                USING (
                    SELECT
                        :odt AS odt,
                        :sucursal AS sucursal,
                        :imagenes AS imagenes,
                        :created_by AS created_by
                ) AS source
                ON target.odt = source.odt
                WHEN MATCHED THEN UPDATE SET
                    target.sucursal = source.sucursal,
                    target.imagenes = source.imagenes,
                    target.created_by = source.created_by,
                    target.updated_at = GETDATE()
                WHEN NOT MATCHED THEN INSERT
                    (odt, sucursal, imagenes, created_by, created_at, updated_at)
                    VALUES
                    (source.odt, source.sucursal, source.imagenes, source.created_by, GETDATE(), GETDATE());
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
        "sucursal": sucursal_value,
        "scope": "sucursal" if is_mantencion else "odt",
        "imagenes": merged_images,
        "imagenes_guardadas": len(new_data_uris),
        "total_imagenes": len(merged_images),
    }


@router.post("/ticketera/tickets/{ticket_id}/stage")
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
    if not _can_view_ticket(ticket, current_user):
        raise HTTPException(status_code=403, detail="Ticket no autorizado")

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

@router.post("/ticketera/tickets/{ticket_id}/priority-json")
def update_priority_json(
    ticket_id: int,
    priority: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    _require_area_access(db, current_user, "soporte")
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id,
        Ticket.is_deleted == False,
    ).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    if not _can_view_ticket(ticket, current_user):
        raise HTTPException(status_code=403, detail="Ticket no autorizado")

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

@router.post("/ticketera/tickets/{ticket_id}/assign-json")
def assign_ticket_json(
    ticket_id: int,
    user_id: int | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    _require_area_access(db, current_user, "soporte")
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    support_users = _visible_support_users(_active_users_in_area(db, "soporte"))
    support_user_ids = {u.id for u in support_users}

    safe_user_id = user_id
    assigned_user = None
    if safe_user_id is not None:
        if safe_user_id not in support_user_ids:
            raise HTTPException(status_code=400, detail="Usuario invalido")
        assigned_user = next((u for u in support_users if u.id == safe_user_id), None)

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

@router.get("/ticket-alerts/unread-count")

def get_ticket_alerts_unread_count(

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    return JSONResponse(

        {

            "unread_count": _get_ticket_alert_unread_count(db, current_user.id),

            "latest_ticket_id": _get_latest_active_ticket_id(db, current_user),

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

        _apply_ticket_visibility_for_user(db.query(Ticket), current_user)

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

                "url": f"/ticketera/tickets/{row.id}",

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

@router.get("/tickets")
def tickets_view(request: Request):
    # tickets.html fue eliminado; la ticketera es la vista oficial de tickets.
    qs = request.url.query
    target = f"/ticketera?{qs}" if qs else "/ticketera"
    return RedirectResponse(url=target, status_code=303)

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


def _client_notes_key(value: str | None) -> str:
    text_value = decode_mime_words(value or "") or (value or "")
    text_value = re.sub(r"\s+", " ", text_value).strip().casefold()
    text_value = unicodedata.normalize("NFD", text_value)
    text_value = "".join(ch for ch in text_value if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text_value).strip()


def _requester_client_note_keys(requester: Requester | None) -> set[str]:
    if not requester:
        return set()
    values = [
        requester.display_name,
        requester.internal_name or "",
        requester.name or "",
        requester.email or "",
    ]
    return {key for key in (_client_notes_key(value) for value in values) if key}


def _find_requesters_for_client_notes(
    db: Session,
    client_name: str,
    preferred: Requester | None = None,
) -> list[Requester]:
    target_key = _client_notes_key(client_name)
    matches: list[Requester] = []
    seen_ids: set[int] = set()

    if preferred is not None:
        matches.append(preferred)
        seen_ids.add(int(preferred.id))
        if not target_key:
            return matches

    if not target_key:
        return matches

    requesters = db.query(Requester).order_by(Requester.id.asc()).all()
    for requester in requesters:
        requester_id = int(requester.id)
        if requester_id in seen_ids:
            continue
        if target_key in _requester_client_note_keys(requester):
            matches.append(requester)
            seen_ids.add(requester_id)

    return matches


def _note_sort_key(note: dict[str, str | int], fallback_index: int) -> tuple[str, int]:
    created_at = str(note.get("created_at", "") or "")
    return (created_at, fallback_index)


def _collect_client_internal_notes(
    db: Session,
    client_name: str,
    preferred: Requester | None = None,
) -> list[dict[str, str | int]]:
    notes: list[dict[str, str | int]] = []
    seen: set[tuple[str, str, str]] = set()
    fallback_index = 0
    for requester in _find_requesters_for_client_notes(db, client_name, preferred=preferred):
        for note in _parse_requester_notes(requester.notes):
            text = str(note.get("text", "") or "").strip()
            if not text:
                continue
            note_key = (
                text,
                str(note.get("author", "") or ""),
                str(note.get("created_at", "") or ""),
            )
            if note_key in seen:
                continue
            seen.add(note_key)
            fallback_index += 1
            enriched = dict(note)
            enriched["source_requester_id"] = int(requester.id)
            enriched["_fallback_index"] = fallback_index
            notes.append(enriched)

    notes.sort(key=lambda item: _note_sort_key(item, int(item.get("_fallback_index") or 0)))
    for note in notes:
        note.pop("_fallback_index", None)
    return notes


def _get_or_create_requester_for_client_notes(db: Session, client_name: str) -> Requester:
    matches = _find_requesters_for_client_notes(db, client_name)
    if matches:
        return matches[0]

    clean_name = re.sub(r"\s+", " ", (client_name or "").strip())[:100] or "Cliente"
    requester = Requester(name=clean_name, email=None)
    db.add(requester)
    db.flush()
    return requester


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


def _ticket_status_label(status: str | None, is_no_ticket: bool = False) -> str:
    if is_no_ticket:
        return TICKET_STATUS_LABELS["closed"]
    code = _normalize_ticket_status_code(status)
    return TICKET_STATUS_LABELS.get(code, (status or "Open").strip() or "Open")


def _ticket_status_css(status: str | None, is_no_ticket: bool = False) -> str:
    if is_no_ticket:
        return "closed"
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


@router.get("/ticketera/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_detail(
    request: Request,
    ticket_id: int,
    focus_message_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    user_filter: str | None = None,
    source: str | None = None,
    priority: str | None = None,
    scope: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    _require_area_access(db, current_user, "soporte")
    # Ticket actual
    ticket = db.query(Ticket).filter(
        Ticket.id == ticket_id
    ).first()

    if not ticket:
        return HTMLResponse("Ticket no encontrado", status_code=404)
    if not _can_view_ticket(ticket, current_user):
        return HTMLResponse("Ticket no autorizado", status_code=403)

    _normalize_requester_name(ticket.requester)

    # Anterior/Siguiente respetan el filtro que estaba activo en la lista de
    # /ticketera (scope/source/priority/user_filter/q/fechas), no el listado
    # general completo (pedido explicito, jul 2026).
    filter_qs = _ticketera_filter_querystring(request)
    filtered_query, _ = _ticketera_build_filtered_query(
        request, db, current_user,
        status=status, q=q, user_filter=user_filter, source=source,
        priority=priority, scope=scope, date_from=date_from, date_to=date_to,
    )

    # Ticket anterior
    previous_ticket = (
        filtered_query
        .filter(Ticket.id < ticket_id)
        .order_by(Ticket.id.desc())
        .first()
    )

    # Ticket siguiente
    next_ticket = (
        filtered_query
        .filter(Ticket.id > ticket_id)
        .order_by(Ticket.id.asc())
        .first()
    )

    # Loop inteligente (dentro del mismo filtro)
    if not previous_ticket:
        previous_ticket = filtered_query.order_by(Ticket.id.desc()).first()

    if not next_ticket:
        next_ticket = filtered_query.order_by(Ticket.id.asc()).first()

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

    # Se exige un minimo de 2 mensajes (no solo 1 "de agente") antes de poder
    # marcar Resuelto: en tickets internos el primer mensaje ya se crea con
    # sender_type="agent" (lo escribe quien crea el ticket), asi que exigir
    # solo "algun mensaje de agente" no detectaba que nunca hubo una
    # respuesta real en Mesa Tecnica.
    has_agent_reply = len(messages) >= 2

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

    manual_unread_row = db.get(TicketManualUnread, (current_user.id, ticket_id))
    if manual_unread_row is not None:
        db.delete(manual_unread_row)
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

    requester_note_client_name = ticket.requester.display_name if ticket.requester else ""
    requester_notes = _collect_client_internal_notes(
        db,
        requester_note_client_name,
        preferred=ticket.requester,
    )
    total_internal_notes = len(requester_notes)
    requester_id = int(ticket.requester_id or 0)
    internal_note_state = None
    if requester_id:
        internal_note_state = (
            db.query(RequesterInternalNoteReadState)
            .filter(
                RequesterInternalNoteReadState.user_id == current_user.id,
                RequesterInternalNoteReadState.requester_id == requester_id,
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
        _apply_ticket_visibility_for_user(db.query(Ticket), current_user)
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
                FROM bbdd_sucursales
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
        incidencias_db.rollback()
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
        db.rollback()
        linked_odt = None
        odt_derivacion_tipo = None
        correos_enviados = []
        correos_count = 0

    assignable_users = _visible_support_users(_active_users_in_area(db, "soporte"))
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

    # Modo "No es un ticket": omitir gate de recepción si la flag está activa
    direct_reply_mode = ticket.is_no_ticket or request.query_params.get("direct_reply") == "1"
    if direct_reply_mode and not ticket_locked:
        requires_reception = False
        reception_sent = True

    return templates.TemplateResponse(
        request,
        "detalle_ticket.html",
        {
            "request": request,
            "user": current_user,
            "ticket": ticket,
            "messages": messages,
            "has_agent_reply": has_agent_reply,
            "requester_notes": requester_notes,
            "unseen_internal_notes": unseen_internal_notes,
            "unseen_reply_count": unseen_reply_count,
            "unseen_total_count": unseen_total_count,
            "requester_info": requester_info,
            "requester_name_catalog": requester_name_catalog,
            "assignable_users": assignable_users,
            "ticket_alert_unread_count": ticket_alert_unread_count,
            "previous_ticket_id": previous_ticket.id if previous_ticket else None,
            "next_ticket_id": next_ticket.id if next_ticket else None,
            "filter_qs": filter_qs,
            "send_error": send_error,
            "service_success": service_success,
            "service_error": service_error,
            "requires_reception": requires_reception,
            "reception_sent": reception_sent,
            "can_reply": reception_sent,
            "ticket_locked": ticket_locked,
            "direct_reply_mode": direct_reply_mode,
            "focus_message_id": focus_message_id,
            "unseen_reply_message_ids": unseen_reply_message_ids,
            "linked_odt": linked_odt,
            "odt_derivacion_tipo": odt_derivacion_tipo,
            "correos_enviados": correos_enviados,
            "correos_count": correos_count,
            "user_signature": signature_html_for_user(current_user),
        },
    )


# Nota: sin @router — la ruta /api/client-notes vive en modules/client_notes.py,
# que despacha a esta implementación cuando hay cookie de Helpdesk.
def api_get_client_internal_notes(
    client: str = Query(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    client_name = re.sub(r"\s+", " ", (client or "").strip())
    if not client_name:
        return JSONResponse({"ok": True, "client": "", "notes": [], "count": 0})

    notes = _collect_client_internal_notes(db, client_name)
    return JSONResponse(
        {
            "ok": True,
            "client": client_name,
            "notes": notes,
            "count": len(notes),
        }
    )


def api_add_client_internal_note(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    client_name = re.sub(r"\s+", " ", str(payload.get("client", "") or "").strip())
    note_text = str(payload.get("note", "") or "").strip()
    if not client_name:
        raise HTTPException(status_code=400, detail="Debes indicar el cliente.")
    if not note_text:
        raise HTTPException(status_code=400, detail="Debes escribir una nota.")

    requester_id_raw = payload.get("requester_id")
    requester: Requester | None = None
    if requester_id_raw:
        try:
            requester = db.get(Requester, int(requester_id_raw))
        except (TypeError, ValueError):
            requester = None
    if requester is None:
        requester = _get_or_create_requester_for_client_notes(db, client_name)

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
    db.refresh(requester)

    merged_notes = _collect_client_internal_notes(db, client_name, preferred=requester)
    return JSONResponse(
        {
            "ok": True,
            "client": client_name,
            "requester_id": requester.id,
            "notes": merged_notes,
            "count": len(merged_notes),
        }
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

        url=f"/ticketera/tickets/{ticket_id}",

        status_code=303,

    )


@router.post("/ticketera/tickets/{ticket_id}/internal-notes/mark-read")
def mark_internal_notes_as_read(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    _require_area_access(db, current_user, "soporte")
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    notes = _collect_client_internal_notes(
        db,
        ticket.requester.display_name if ticket.requester else "",
        preferred=ticket.requester,
    )
    note_count = len(notes)

    requester_id = int(ticket.requester_id or 0)
    if not requester_id:
        raise HTTPException(status_code=400, detail="Ticket sin cliente asociado.")
    read_state = (
        db.query(RequesterInternalNoteReadState)
        .filter(
            RequesterInternalNoteReadState.user_id == current_user.id,
            RequesterInternalNoteReadState.requester_id == requester_id,
        )
        .first()
    )
    if read_state is None:
        read_state = RequesterInternalNoteReadState(
            user_id=current_user.id,
            requester_id=requester_id,
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
    # Solo el administrador de soporte puede cambiar el nombre del cliente.
    if not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="Solo el administrador puede cambiar el nombre del cliente.")

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
                FROM bbdd_sucursales
                WHERE LOWER(TRIM(nombre_sucursal)) = LOWER(TRIM(:alias))
                ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
                """
            ),
            {"alias": sanitized_alias},
        ).fetchone()
        if not row or not row[0]:
            raise HTTPException(
                status_code=400,
                detail="El nombre debe existir en bbdd_sucursales.",
            )
        requester.internal_name = re.sub(r"\s+", " ", _support_text(row[0])).strip()[:120]
    else:
        requester.internal_name = None
    db.commit()

    return RedirectResponse(
        url=f"/ticketera/tickets/{ticket_id}",
        status_code=303,
    )

# ======================================================

# ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ ACTUALIZAR ESTADO (admin o asignado)

# ======================================================

@router.post("/ticketera/tickets/{ticket_id}/status")

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

        url=f"/ticketera/tickets/{ticket_id}",

        status_code=303

    )

# ======================================================

# ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ ACTUALIZAR PRIORIDAD (admin o asignado)

# ======================================================

@router.post("/ticketera/tickets/{ticket_id}/assign-me")

def assign_to_me(

    ticket_id: int,

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):
    _require_area_access(db, current_user, "soporte")

    ticket = db.get(Ticket, ticket_id)

    if not ticket:

        return HTMLResponse("Ticket no encontrado", status_code=404)
    if not _can_view_ticket(ticket, current_user):
        return HTMLResponse("Ticket no autorizado", status_code=403)

    if not _is_visible_support_user(current_user):
        return RedirectResponse(
            url=f"/ticketera/tickets/{ticket_id}?service_error=Usuario+no+disponible+para+asignacion",
            status_code=303,
        )

    assign_ticket_logic(db, ticket, current_user.id, current_user)

    db.commit()

    return RedirectResponse(

        url=f"/ticketera/tickets/{ticket_id}",

        status_code=303,

    )


@router.post("/ticketera/tickets/{ticket_id}/assign-team")
def assign_ticket_to_team(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    """Difunde el ticket a todo el equipo de soporte (queda sin asignar,
    visible para todos); en cuanto alguien lo tome via 'Tomar'/'Asignar a',
    deja de estar disponible para el resto. Se guarda una marca permanente
    para que Ronald Montilla lo siga viendo aunque otro agente ya lo haya
    tomado (pedido explicito, jul 2026)."""
    _require_area_access(db, current_user, "soporte")
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        return HTMLResponse("Ticket no encontrado", status_code=404)
    if not _can_view_ticket(ticket, current_user):
        return HTMLResponse("Ticket no autorizado", status_code=403)
    if _ticket_is_locked(ticket):
        return RedirectResponse(
            url=f"/ticketera/tickets/{ticket_id}?service_error=El+ticket+ya+esta+resuelto+y+no+permite+cambios.",
            status_code=303,
        )

    assign_ticket_logic(db, ticket, None, current_user)
    ticket.team_broadcast_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}", status_code=303)


@router.post("/ticketera/tickets/{ticket_id}/assign")

def assign_ticket(

    ticket_id: int,

    user_id: int | None = Form(None),

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):
    _require_area_access(db, current_user, "soporte")

    ticket = db.get(Ticket, ticket_id)

    if not ticket:

        return HTMLResponse("Ticket no encontrado", status_code=404)
    if not _can_view_ticket(ticket, current_user):
        return HTMLResponse("Ticket no autorizado", status_code=403)

    if _ticket_is_locked(ticket):
        return RedirectResponse(
            url=f"/ticketera/tickets/{ticket_id}?service_error=El+ticket+ya+esta+resuelto+y+no+permite+cambios.",
            status_code=303,
        )

    support_user_ids = {u.id for u in _visible_support_users(_active_users_in_area(db, "soporte"))}
    if user_id is not None and int(user_id) not in support_user_ids:
        return RedirectResponse(
            url=f"/ticketera/tickets/{ticket_id}?service_error=Usuario+invalido",
            status_code=303,
        )

    assign_ticket_logic(db, ticket, user_id, current_user)

    db.commit()

    return RedirectResponse(

        url=f"/ticketera/tickets/{ticket_id}",

        status_code=303,

    )


@router.post("/ticketera/tickets/{ticket_id}/mark-unread")
def mark_ticket_unread(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    _require_area_access(db, current_user, "soporte")

    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        return JSONResponse({"detail": "Ticket no encontrado"}, status_code=404)

    existing = db.get(TicketManualUnread, (current_user.id, ticket_id))
    if not existing:
        db.add(TicketManualUnread(user_id=current_user.id, ticket_id=ticket_id))
        db.commit()

    return JSONResponse({"ok": True, "ticket_id": ticket_id})


@router.post("/ticketera/tickets/bulk-mark-read")
def bulk_mark_tickets_read(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    """Marca como leidos varios tickets seleccionados en la lista, con la
    misma logica que se aplica al abrir un ticket individual (pedido
    explicito, jul 2026)."""
    _require_area_access(db, current_user, "soporte")

    raw_ids = payload.get("ticket_ids") if isinstance(payload, dict) else None
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="Debes indicar al menos un ticket.")
    try:
        ticket_ids = sorted({int(value) for value in raw_ids})
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="ticket_ids invalido.")

    tickets = (
        _apply_ticket_visibility_for_user(db.query(Ticket), current_user)
        .filter(Ticket.id.in_(ticket_ids))
        .all()
    )
    if not tickets:
        return JSONResponse({"ok": True, "updated": 0})

    latest_message_by_ticket = dict(
        db.query(Message.ticket_id, func.max(Message.id))
        .filter(Message.ticket_id.in_(ticket_ids), Message.is_internal_note == False)
        .group_by(Message.ticket_id)
        .all()
    )

    existing_states = {
        state.ticket_id: state
        for state in db.query(TicketMessageReadState).filter(
            TicketMessageReadState.user_id == current_user.id,
            TicketMessageReadState.ticket_id.in_(ticket_ids),
        )
    }

    updated = 0
    for ticket in tickets:
        latest_message_id = int(latest_message_by_ticket.get(ticket.id) or 0)
        state = existing_states.get(ticket.id)
        if state is None:
            db.add(TicketMessageReadState(
                user_id=current_user.id,
                ticket_id=ticket.id,
                last_seen_message_id=latest_message_id,
            ))
            updated += 1
        elif latest_message_id > (state.last_seen_message_id or 0):
            state.last_seen_message_id = latest_message_id
            updated += 1

        manual_unread_row = db.get(TicketManualUnread, (current_user.id, ticket.id))
        if manual_unread_row is not None:
            db.delete(manual_unread_row)
            updated += 1

    db.commit()
    return JSONResponse({"ok": True, "updated": updated, "ticket_ids": [t.id for t in tickets]})


@router.post("/ticketera/tickets/bulk-mark-unread")
def bulk_mark_tickets_unread(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    """Marca como no leidos varios tickets seleccionados en la lista. La UI
    solo ofrece esta accion cuando TODOS los tickets seleccionados ya estaban
    leidos; si la seleccion mezcla leidos y no leidos, predomina "Marcar como
    leidos" (pedido explicito, jul 2026)."""
    _require_area_access(db, current_user, "soporte")

    raw_ids = payload.get("ticket_ids") if isinstance(payload, dict) else None
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="Debes indicar al menos un ticket.")
    try:
        ticket_ids = sorted({int(value) for value in raw_ids})
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="ticket_ids invalido.")

    tickets = (
        _apply_ticket_visibility_for_user(db.query(Ticket), current_user)
        .filter(Ticket.id.in_(ticket_ids))
        .all()
    )
    if not tickets:
        return JSONResponse({"ok": True, "updated": 0})

    existing_manual_unread_ids = {
        row.ticket_id
        for row in db.query(TicketManualUnread.ticket_id).filter(
            TicketManualUnread.user_id == current_user.id,
            TicketManualUnread.ticket_id.in_(ticket_ids),
        )
    }

    updated = 0
    for ticket in tickets:
        if ticket.id not in existing_manual_unread_ids:
            db.add(TicketManualUnread(user_id=current_user.id, ticket_id=ticket.id))
            updated += 1

    db.commit()
    return JSONResponse({"ok": True, "updated": updated, "ticket_ids": [t.id for t in tickets]})

# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾ ACTUALIZAR PRIORIDAD

# ======================================================

@router.post("/ticketera/tickets/{ticket_id}/priority")

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
            url=f"/ticketera/tickets/{ticket_id}?service_error=El+ticket+ya+esta+resuelto+y+no+permite+cambios.",
            status_code=303,
        )

    # ValidaciÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³n bÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¡sica

    allowed_priorities = ["low", "medium", "high", "urgent"]

    if priority not in allowed_priorities:

        raise HTTPException(status_code=400, detail="Prioridad inválida")

    ticket.priority = priority

    db.commit()

    return RedirectResponse(

        url=f"/ticketera/tickets/{ticket_id}",

        status_code=303

    )

@router.post("/ticketera/tickets/{ticket_id}/quick-actions")

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
            url=f"/ticketera/tickets/{ticket_id}?service_error=El+ticket+ya+esta+resuelto+y+no+permite+cambios.",
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
        return RedirectResponse(
            url=f"/ticketera/tickets/{ticket_id}?service_error=Estado+invalido.",
            status_code=303,
        )

    allowed_priorities = ["low", "medium", "high", "urgent"]

    if priority not in allowed_priorities:
        return RedirectResponse(
            url=f"/ticketera/tickets/{ticket_id}?service_error=Prioridad+invalida.",
            status_code=303,
        )

    resolved_statuses = {"resolved", "resolved_service", "resolved_client"}
    if status in resolved_statuses:
        # Minimo 2 mensajes (no solo "algun mensaje de agente"): en tickets
        # internos el primer mensaje ya lo crea el agente al abrir el ticket,
        # asi que exigir solo eso no garantiza que hubo una respuesta real
        # en Mesa Tecnica.
        cantidad_mensajes = db.query(Message).filter(
            Message.ticket_id == ticket_id,
            Message.is_internal_note == False,
        ).count()
        if cantidad_mensajes < 2:
            return RedirectResponse(
                url=f"/ticketera/tickets/{ticket_id}?service_error=Debes+enviar+una+respuesta+al+cliente+antes+de+marcar+el+ticket+como+resuelto.",
                status_code=303,
            )

    try:
        _enforce_status_transition_rules(ticket, status)
    except HTTPException as exc:
        query = urlencode({"service_error": str(exc.detail)})
        return RedirectResponse(
            url=f"/ticketera/tickets/{ticket_id}?{query}",
            status_code=303,
        )

    _auto_assign_current_user(db, ticket, current_user)

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

        url=f"/ticketera/tickets/{ticket_id}",

        status_code=303

    )

@router.post("/ticketera/tickets/{ticket_id}/send-to-service")
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
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

    if ticket.is_deleted:
        query = urlencode({"service_error": "No se puede derivar un ticket en papelera."})
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

    if ticket.is_spam:
        query = urlencode({"service_error": "No se puede derivar un ticket marcado como spam."})
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

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
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

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
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

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
            return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

    direccion_clean = re.sub(r"\s+", " ", (direccion or "").strip())
    tecnico_clean = re.sub(r"\s+", " ", (tecnico or "").strip())
    estado_clean = "Pendiente"
    # Evita que el historial se parta en multiples bloques por saltos de linea.
    observacion_clean = re.sub(r"\s+", " ", (observacion or "").strip())

    if not cliente_clean:
        query = urlencode({"service_error": "Debes indicar el cliente para crear la incidencia."})
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

    if not problema_clean:
        query = urlencode({"service_error": "Debes indicar el problema para crear la incidencia."})
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

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
    now_db = now_local.replace(tzinfo=None)

    try:
        table = _support_incidencias_table(incidencias_db)
        odt_value = _support_next_odt_value(incidencias_db, table)
        table_columns = set(table.c.keys())
        values_to_insert: dict[str, object] = {}

        def is_datetime_column(key: str) -> bool:
            column = table.c.get(key)
            return bool(column is not None and isinstance(column.type, DateTime))

        def set_first(
            keys: tuple[str, ...],
            value: object,
            datetime_value: datetime | None = None,
        ) -> None:
            if value is None:
                return
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    return
            for key in keys:
                if key in table_columns and key not in values_to_insert:
                    values_to_insert[key] = (
                        datetime_value
                        if datetime_value is not None and is_datetime_column(key)
                        else value
                    )
                    return

        if derivacion_clean == "Cliente" and not direccion_clean:
            direccion_clean = _support_find_direccion_by_cliente(incidencias_db, table, cliente_clean)

        set_first(("odt",), odt_value)
        set_first(("fecha", "fecha_registro"), now_label, datetime_value=now_db)
        set_first(("cliente", "sucursal", "puesto"), cliente_clean)
        set_first(("problema", "tipo_incidencia"), problema_clean)
        set_first(("detalle_problema",), problema_detalle_clean)
        set_first(("derivacion",), derivacion_clean)
        # No inyectamos texto automatico en Registro Operaciones.
        # Gestion Soporte queda en observacion_soporte con firma de usuario/fecha.
        set_first(("direccion",), direccion_clean)
        set_first(("estado",), estado_clean)
        set_first(
            ("fecha_derivacion_area", "fecha_derivacion_tecnico", "fecha_derivacion"),
            now_label,
            datetime_value=now_db,
        )
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
        import logging
        logging.getLogger(__name__).exception(
            "No se pudo crear la incidencia al derivar ticket %s. Valores: %s",
            ticket_id, values_to_insert,
        )
        error_text = re.sub(
            r"\s+",
            " ",
            str(exc or "No se pudo crear la incidencia").replace("\r", " ").replace("\n", " "),
        ).strip()
        query = urlencode({"service_error": f"No se pudo crear la incidencia: {error_text[:200]}"})
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

    correo_info = ""
    try:
        _auto_assign_current_user(db, ticket, current_user)
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
                from ATC.app.integrations.email_smtp import send_email_reply

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
                            <div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#ffffff;opacity:.82;">Soporte ATC</div>
                            <h1 style="margin:10px 0 0;font-size:27px;line-height:1.2;color:#ffffff;">Actualización de su solicitud</h1>
                            <p style="margin:10px 0 0;font-size:15px;line-height:1.6;color:#ffffff;opacity:.92;">Ticket #{ticket.id}</p>
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
                            "path": _LOGO_ATC_PATH,
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
    return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)


@router.post("/ticketera/tickets/{ticket_id}/derivacion-administrativa")
def derivacion_administrativa(
    ticket_id: int,
    emails: str = Form(...),
    mensaje: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    """Deriva/reenvia el ticket a uno o mas correos libres, SIN crear una
    incidencia (distinto de 'Derivar Ticket', que si crea una) — pedido
    explicito, jul 2026. Es un "Reenviar" estilo Gmail: sin asunto editable,
    solo un comentario opcional antes del correo original."""
    _require_area_access(db, current_user, "soporte")
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        return HTMLResponse("Ticket no encontrado", status_code=404)
    if not _can_view_ticket(ticket, current_user):
        return HTMLResponse("Ticket no autorizado", status_code=403)
    if _ticket_is_locked(ticket):
        query = urlencode({"send_error": "El ticket ya esta resuelto y no permite cambios."})
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

    try:
        destinatarios = _parse_recipient_list(emails, field_name="correos de destino")
    except ValueError as exc:
        query = urlencode({"send_error": str(exc)})
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

    if not destinatarios:
        query = urlencode({"send_error": "Debes indicar al menos un correo de destino."})
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

    mensaje_limpio = (mensaje or "").strip()

    # "Derivacion administrativa" reenvia el correo original recibido (como
    # un "Reenviar" de cliente de correo), no un mensaje nuevo redactado a
    # mano — pedido explicito, jul 2026. Se busca el primer correo del
    # solicitante que origino el ticket para armar el bloque reenviado.
    original_message = (
        db.query(Message)
        .filter(
            Message.ticket_id == ticket.id,
            Message.sender_type == "requester",
            Message.channel == "email",
        )
        .order_by(Message.created_at.asc(), Message.id.asc())
        .first()
    )

    if original_message is None:
        query = urlencode({"send_error": "Este ticket no tiene un correo recibido para reenviar."})
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

    asunto_limpio = f"Fwd: {(ticket.subject or 'Sin asunto').strip()}"

    original_from_name = (original_message.sender_name or "").strip() or (
        ticket.requester.name.strip() if ticket.requester and ticket.requester.name else ""
    )
    original_from_email = (original_message.sender_email or "").strip() or (
        ticket.requester.email.strip() if ticket.requester and ticket.requester.email else ""
    )
    original_from = f"{original_from_name} <{original_from_email}>".strip() if original_from_email else (original_from_name or "Desconocido")
    original_to = (ticket.inbound_mailbox or "").strip() or "-"
    original_date = _jinja_localdt(original_message.created_at)

    base_url = (settings.PUBLIC_BASE_URL or "https://soporteatc.cl").strip().rstrip("/")
    original_html = original_message.content or ""
    original_html = original_html.replace('src="/uploads/', f'src="{base_url}/uploads/')
    original_html = original_html.replace("src='/uploads/", f"src='{base_url}/uploads/")
    original_html = original_html.replace('href="/uploads/', f'href="{base_url}/uploads/')
    original_html = original_html.replace("href='/uploads/", f"href='{base_url}/uploads/")

    forward_header = (
        '<div style="margin:16px 0 12px;padding-top:10px;border-top:1px solid #ccc;'
        'color:#555;font-size:13px;">'
        "---------- Mensaje reenviado ---------<br>"
        f"<b>De:</b> {html.escape(original_from)}<br>"
        f"<b>Fecha:</b> {html.escape(original_date)}<br>"
        f"<b>Asunto:</b> {html.escape(ticket.subject or 'Sin asunto')}<br>"
        f"<b>Para:</b> {html.escape(original_to)}"
        "</div>"
    )
    body_parts = []
    if mensaje_limpio:
        body_parts.append(html.escape(mensaje_limpio).replace("\n", "<br>"))
    body_parts.append(forward_header)
    body_parts.append(original_html)
    email_body = "<br>".join(body_parts)

    from ATC.app.integrations.email_smtp import send_email_reply
    try:
        send_email_reply(
            to=destinatarios,
            subject=asunto_limpio,
            body=email_body,
        )
    except Exception as exc:
        query = urlencode({"send_error": f"No se pudo reenviar el correo: {exc}"})
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

    audit_note = Message(
        ticket_id=ticket.id,
        sender_type="agent",
        sender_id=current_user.id,
        channel="email",
        # Mismo estilo visual que una respuesta normal ("Correo enviado" con
        # Para/CC), en vez del texto plano "Derivacion administrativa... /
        # Nota:" — pedido explicito, jul 2026.
        content=_prepend_email_recipient_summary(
            html.escape(mensaje_limpio).replace("\n", "<br>") if mensaje_limpio else "",
            to_recipients=destinatarios,
            cc_recipients=[],
            bcc_recipients=[],
        ),
        # Visible en el chat principal (is_internal_note=False): la ticketera
        # necesita ver quien reenvio, que se reenvio y a quien — pedido
        # explicito, jul 2026. Antes quedaba como nota interna y la query de
        # ticket_detail() excluye is_internal_note=True del chat, asi que
        # nunca se veia.
        is_internal_note=False,
    )
    db.add(audit_note)
    db.commit()

    query = urlencode({"service_success": f"Reenviado a {', '.join(destinatarios)}."})
    return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

# ======================================================

# ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ RESPONDER TICKET (admin o asignado)

# ======================================================

@router.post("/ticketera/tickets/{ticket_id}/send-reception")
def send_reception_notice(
    ticket_id: int,
    cc: str = Form(default=""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        return HTMLResponse("Ticket no encontrado", status_code=404)

    if _ticket_is_locked(ticket):
        query = urlencode({"send_error": "El ticket ya esta resuelto y no permite cambios."})
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

    if (ticket.source or "").strip().lower() != "email":
        query = urlencode({"send_error": "La recepcion de solicitud solo aplica a tickets por email."})
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

    if _has_reception_sent(db, ticket_id):
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}", status_code=303)

    allowed_priorities = {"low", "medium", "high", "urgent"}
    priority_value = (ticket.priority or "").strip().lower()
    if priority_value not in allowed_priorities:
        query = urlencode({"send_error": "Debes seleccionar la prioridad antes de enviar la recepcion de solicitud."})
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

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

    cc_limpio = ", ".join(
        addr.strip() for addr in (cc or "").split(",") if addr.strip()
    )

    try:
        sent = send_initial_email_auto_reply(
            db,
            ticket=ticket,
            requester=ticket.requester,
            in_reply_to_external_id=latest_requester_email.external_id if latest_requester_email else None,
            event_name="manual_reception",
            cc=cc_limpio or None,
        )
        if not sent:
            query = urlencode({"send_error": "No se pudo enviar la recepcion de solicitud."})
            return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)
        if current_user and current_user.id:
            ticket.assigned_to_id = current_user.id
        db.commit()
    except Exception as exc:
        db.rollback()
        error_detail = re.sub(r"\s+", " ", str(exc).replace("\r", " ").replace("\n", " ")).strip()
        error_detail = (error_detail or "error desconocido")[:220]
        query = urlencode({"send_error": f"No se pudo enviar la recepcion: {error_detail}"})
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

    return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}", status_code=303)


def _extract_inline_images_for_compose(body: str) -> tuple[str, list[dict]]:
    """Extrae data:image base64 del body y los convierte a CID in-memory.
    Devuelve (html_con_cid, inline_images_bytes) para send_email_reply."""
    if not body or "data:image/" not in body.lower():
        return body, []

    inline_bytes: list[dict] = []
    count = 0

    def _replacer(m: re.Match) -> str:
        nonlocal count
        prefix, quote, data_url = m.group(1), m.group(2), m.group(3)
        parsed = re.match(r"^data:([^;]+);base64,(.+)$", data_url, re.IGNORECASE | re.DOTALL)
        if not parsed:
            return m.group(0)
        mime = parsed.group(1).strip().lower()
        if not mime.startswith("image/"):
            return m.group(0)
        try:
            img_bytes = base64.b64decode(re.sub(r"\s+", "", parsed.group(2)), validate=False)
        except Exception:
            return m.group(0)
        if not img_bytes:
            return m.group(0)
        count += 1
        ext = mime.split("/")[-1].replace("jpeg", "jpg")[:8]
        fname = f"compose_inline_{count}.{ext}"
        cid = f"compose.inline.{uuid4().hex}@atc.local"
        inline_bytes.append({"cid": cid, "bytes": img_bytes, "mime_type": mime, "filename": fname})
        return f"{prefix}{quote}cid:{cid}{quote}"

    result = _INLINE_DATA_IMAGE_RE.sub(_replacer, body)
    return result, inline_bytes


_VALID_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def _save_compose_emails_to_db(db: Session, *address_fields: str, flush_only: bool = False) -> None:
    """Registra en Requester solo los emails válidos y recién enviados (To/CC/CCO).
    Solo se llama tras un envío SMTP exitoso, así se garantiza que el correo
    llegó a destino (o al menos fue aceptado por el servidor)."""
    for field in address_fields:
        for raw in (field or "").split(","):
            email = raw.strip().lower()
            if not email or not _VALID_EMAIL_RE.match(email):
                continue
            exists = db.query(Requester).filter(Requester.email == email).first()
            if not exists:
                db.add(Requester(name=email, email=email))
    if flush_only:
        db.flush()
        return
    try:
        db.commit()
    except Exception:
        db.rollback()


@router.post("/ticketera/compose/send")
async def ticketera_compose_send(
    to: str = Form(""),
    cc: str = Form(""),
    bcc: str = Form(""),
    subject: str = Form(""),
    body: str = Form(""),
    attachments: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    from ATC.app.integrations.email_smtp import send_email_reply

    to = (to or "").strip()
    subject = (subject or "").strip() or "Sin asunto"
    body = (body or "").strip()

    if not to:
        return JSONResponse({"ok": False, "error": "Destinatario requerido."}, status_code=400)
    if not body:
        return JSONResponse({"ok": False, "error": "El cuerpo del mensaje está vacío."}, status_code=400)

    # Convertir base64 inline images a CID attachments (Gmail bloquea data: URIs)
    body, inline_images_bytes = _extract_inline_images_for_compose(body)

    saved: list[dict] = []
    try:
        uploads_dir = Path("uploads") / "compose_tmp"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        for upload in (attachments or []):
            fname = (upload.filename or "").strip()
            if not fname:
                continue
            content = await upload.read()
            if not content:
                continue
            safe_name = re.sub(r"[^\w.\-]", "_", fname)[:120]
            dest = uploads_dir / f"{uuid4().hex[:8]}_{safe_name}"
            dest.write_bytes(content)
            saved.append({"path": str(dest), "filename": fname,
                          "content_type": upload.content_type or "application/octet-stream"})

        send_email_reply(
            to=to,
            cc=cc or None,
            bcc=bcc or None,
            subject=subject,
            body=body,
            inline_images_bytes=inline_images_bytes or None,
            attachments=saved or None,
        )
    except Exception as exc:
        for item in saved:
            Path(item["path"]).unlink(missing_ok=True)
        err = re.sub(r"\s+", " ", str(exc).replace("\n", " ")).strip()[:220]
        return JSONResponse({"ok": False, "error": err}, status_code=500)

    for item in saved:
        Path(item["path"]).unlink(missing_ok=True)
    _save_compose_emails_to_db(db, to, cc, bcc)
    return JSONResponse({"ok": True})


@router.post("/ticketera/compose/send-as-ticket")
async def ticketera_compose_send_as_ticket(
    to: str = Form(""),
    cc: str = Form(""),
    bcc: str = Form(""),
    subject: str = Form(""),
    body: str = Form(""),
    priority: str = Form(""),
    assigned_to_id: int | None = Form(None),
    attachments: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    from ATC.app.integrations.email_smtp import send_email_reply

    to = (to or "").strip()
    subject = (subject or "").strip() or "Sin asunto"
    body = (body or "").strip()
    priority = (priority or "").strip().lower()

    if not to:
        return JSONResponse({"ok": False, "error": "Destinatario requerido."}, status_code=400)
    if not body:
        return JSONResponse({"ok": False, "error": "El cuerpo del mensaje está vacío."}, status_code=400)
    if priority not in {"low", "medium", "high", "urgent"}:
        return JSONResponse({"ok": False, "error": "Seleccioná una prioridad."}, status_code=400)

    body, inline_images_bytes = _extract_inline_images_for_compose(body)

    saved: list[dict] = []
    try:
        uploads_dir = Path("uploads") / "compose_tmp"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        for upload in (attachments or []):
            fname = (upload.filename or "").strip()
            if not fname:
                continue
            content_bytes = await upload.read()
            if not content_bytes:
                continue
            safe_name = re.sub(r"[^\w.\-]", "_", fname)[:120]
            dest = uploads_dir / f"{uuid4().hex[:8]}_{safe_name}"
            dest.write_bytes(content_bytes)
            saved.append({"path": str(dest), "filename": fname,
                          "content_type": upload.content_type or "application/octet-stream"})

        # Enviar el correo
        send_email_reply(
            to=to, cc=cc or None, bcc=bcc or None,
            subject=subject, body=body,
            inline_images_bytes=inline_images_bytes or None,
            attachments=saved or None,
        )
    except Exception as exc:
        for item in saved:
            Path(item["path"]).unlink(missing_ok=True)
        err = re.sub(r"\s+", " ", str(exc).replace("\n", " ")).strip()[:220]
        return JSONResponse({"ok": False, "error": f"Error al enviar correo: {err}"}, status_code=500)

    for item in saved:
        Path(item["path"]).unlink(missing_ok=True)

    # Guardar todos los emails en Requester (To / CC / CCO) sin commit aún
    _save_compose_emails_to_db(db, to, cc, bcc, flush_only=True)

    # Obtener o crear el requester principal (primer email de To)
    to_email = to.split(",")[0].strip().lower()
    requester = db.query(Requester).filter(Requester.email == to_email).first()
    if not requester:
        requester = db.query(Requester).filter(Requester.name == to_email).first()
    if not requester:
        requester = Requester(name=to_email, email=to_email)
        db.add(requester)
        db.flush()

    # Validar asignado
    support_user_ids = {u.id for u in _visible_support_users(_active_users_in_area(db, "soporte"))}
    if assigned_to_id and int(assigned_to_id) not in support_user_ids:
        assigned_to_id = None
    if not assigned_to_id and _is_visible_support_user(current_user):
        assigned_to_id = current_user.id

    ticket = Ticket(
        subject=subject,
        requester_id=requester.id,
        assigned_to_id=assigned_to_id,
        priority=priority,
        status="open",
        source="email",
    )
    db.add(ticket)
    db.flush()

    from ATC.app.models.message import Message
    msg = Message(
        ticket_id=ticket.id,
        sender_type="agent",
        sender_id=current_user.id,
        channel="email",
        content=body,
        is_internal_note=False,
    )
    db.add(msg)
    db.commit()

    return JSONResponse({"ok": True, "ticket_id": ticket.id, "ticket_url": f"/ticketera/tickets/{ticket.id}"})


@router.get("/ticketera/compose/contacts")
def ticketera_compose_contacts(
    q: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    q = (q or "").strip()
    if len(q) < 1:
        return JSONResponse([])
    like = f"%{q}%"
    rows = (
        db.query(Requester)
        .filter(
            Requester.email.isnot(None),
            Requester.email != "",
            (Requester.name.ilike(like) | Requester.email.ilike(like) |
             Requester.internal_name.ilike(like))
        )
        .order_by(Requester.name.asc())
        .limit(10)
        .all()
    )
    return JSONResponse([
        {"name": r.display_name, "email": (r.email or "").strip()}
        for r in rows
        if (r.email or "").strip()
    ])


@router.get("/ticketera/compose/agents")
def ticketera_compose_agents(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    users = _visible_support_users(_active_users_in_area(db, "soporte"))
    return JSONResponse([{"id": u.id, "name": u.name} for u in users])


@router.post("/ticketera/tickets/{ticket_id}/reply")
def reply_ticket(
    ticket_id: int,
    content: str = Form(""),
    to: str = Form(""),
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
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

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
            url=f"/ticketera/tickets/{ticket_id}?{query}",
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

    if ticket_source != "email" and uploaded_count:
        return _redirect_with_error("Los adjuntos solo estan disponibles para respuestas por correo.")

    to_recipients: list[str] = []
    cc_recipients: list[str] = []
    bcc_recipients: list[str] = []
    resolved_subject = _build_ticket_email_subject(ticket.subject, ticket.id)

    if ticket_source == "email":
        requester_email = (ticket.requester.email if ticket.requester and ticket.requester.email else "").strip()
        if not requester_email and not (to or "").strip():
            return _redirect_with_error("El ticket no tiene correo del solicitante para responder.")

        try:
            to_recipients, cc_recipients, bcc_recipients = _resolve_ticket_email_recipients(
                ticket,
                to=to,
                cc=cc,
                bcc=bcc,
            )
        except ValueError as exc:
            return _redirect_with_error(str(exc))

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

    if ticket_source == "email":
        content_for_db = _prepend_email_recipient_summary(
            content_for_db,
            to_recipients=to_recipients,
            cc_recipients=cc_recipients,
            bcc_recipients=bcc_recipients,
        )

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

    if current_user and current_user.id:
        assign_ticket_logic(db, ticket, current_user.id, current_user)

    # Toda respuesta del agente deja el ticket en pending
    # usando la misma regla centralizada de estado.
    apply_ticket_status_change(ticket, "pending")
    mark_first_agent_reply(ticket)

    try:
        if ticket_source == "email":
            from ATC.app.integrations.email_smtp import send_email_reply

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
            from ATC.app.integrations.whatsapp_cloud import send_whatsapp_message

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
            url=f"/ticketera/tickets/{ticket_id}?{query}",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/ticketera/tickets/{ticket_id}",
        status_code=303,
    )


# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸Ãƒâ€¦Ã‚Â¡Ãƒâ€šÃ‚Â« MARCAR COMO SPAM (cualquiera)

# ======================================================

@router.post("/ticketera/tickets/{ticket_id}/spam")

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
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

    ticket.is_spam = True
    ticket.is_deleted = False
    apply_ticket_status_change(ticket, "closed")

    db.commit()

    return RedirectResponse("/ticketera", status_code=303)

# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾ RESTAURAR DESDE SPAM

# ======================================================

@router.post("/ticketera/tickets/{ticket_id}/restore-spam")

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

    return RedirectResponse("/ticketera?view=spam", status_code=303)

# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬ÂÃƒÂ¢Ã¢â€šÂ¬Ã‹Å“ ELIMINAR TICKET (SOLO ADMIN)

# ======================================================


# ======================================================
# NO ES UN TICKET
# ======================================================

@router.post("/ticketera/tickets/{ticket_id}/no-ticket")
def mark_no_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    """Marca el ticket como 'no ticket' — habilita respuesta directa sin recepción."""
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        return HTMLResponse("Ticket no encontrado", status_code=404)

    ticket.is_no_ticket = True
    db.commit()

    return RedirectResponse(f"/ticketera/tickets/{ticket_id}", status_code=303)


@router.post("/ticketera/tickets/{ticket_id}/reply-direct")
async def reply_direct(
    ticket_id: int,
    content: str = Form(""),
    to: str = Form(""),
    cc: str = Form(""),
    bcc: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    """Envía email de respuesta sin recepción de solicitud ni cambio de estado/prioridad."""
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404)

    body_html = (content or "").strip()
    if body_html and ticket.source == "email":
        from ATC.app.integrations.email_smtp import send_email_reply
        try:
            to_recipients, cc_recipients, bcc_recipients = _resolve_ticket_email_recipients(
                ticket,
                to=to,
                cc=cc,
                bcc=bcc,
            )
        except ValueError as exc:
            query = urlencode({"send_error": str(exc)})
            return RedirectResponse(
                url=f"/ticketera/tickets/{ticket_id}?{query}",
                status_code=303,
            )

        if to_recipients:
            ticket_msgs = sorted(ticket.messages, key=lambda m: m.created_at)
            last_msg = next((m for m in reversed(ticket_msgs) if getattr(m, "external_id", None)), None)
            in_reply_to = getattr(last_msg, "external_id", None) if last_msg else None

            email_body, content_for_db, inline_images, _ = _extract_inline_data_images(
                ticket_id=ticket_id,
                html_content=body_html,
            )
            content_for_db = _prepend_email_recipient_summary(
                content_for_db,
                to_recipients=to_recipients,
                cc_recipients=cc_recipients,
                bcc_recipients=bcc_recipients,
            )

            send_email_reply(
                to=to_recipients,
                cc=cc_recipients,
                bcc=bcc_recipients,
                subject=f"Re: {ticket.subject}",
                body=email_body,
                in_reply_to=in_reply_to,
                ticket_id=ticket.id,
                inline_images=inline_images,
                attachments=[],
            )
            db.add(Message(
                ticket_id=ticket.id,
                sender_type="agent",
                sender_id=current_user.id,
                sender_name=getattr(current_user, "name", None) or "Soporte",
                channel="email",
                content=content_for_db,
                is_internal_note=False,
            ))
            assign_ticket_logic(db, ticket, current_user.id, current_user)
            db.commit()

    return RedirectResponse(f"/ticketera/tickets/{ticket_id}", status_code=303)


@router.post("/ticketera/tickets/{ticket_id}/restore-no-ticket")
def restore_from_no_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404)

    ticket.is_no_ticket = False

    db.commit()

    return RedirectResponse(f"/ticketera/tickets/{ticket_id}", status_code=303)


@router.post("/ticketera/tickets/{ticket_id}/delete")

def delete_ticket(

    ticket_id: int,

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    ticket = db.get(Ticket, ticket_id)

    if not ticket:

        return HTMLResponse("Ticket no encontrado", status_code=404)

    if _ticket_is_locked(ticket):
        query = urlencode({"service_error": "El ticket ya esta resuelto y no permite cambios."})
        return RedirectResponse(url=f"/ticketera/tickets/{ticket_id}?{query}", status_code=303)

    ticket.is_deleted = True
    ticket.is_spam = False
    apply_ticket_status_change(ticket, "closed")

    db.commit()

    return RedirectResponse("/ticketera", status_code=303)

# ======================================================

# ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾ RESTAURAR DESDE PAPELERA

# ======================================================

@router.post("/ticketera/tickets/{ticket_id}/restore-trash")

def restore_from_trash(

    ticket_id: int,

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):

    ticket = db.get(Ticket, ticket_id)

    if not ticket:

        raise HTTPException(status_code=404)

    ticket.is_deleted = False

    db.commit()

    return RedirectResponse("/ticketera?view=trash", status_code=303)

@router.get("/panel-indicadores", response_class=HTMLResponse)

def panel_indicadores(

    request: Request,

    date_from: str | None = Query(default=None),

    date_to: str | None = Query(default=None),

    db: Session = Depends(get_db),

    current_user: User = Depends(get_current_user_web),

):
    _require_area_access(db, current_user, "soporte")

    from ATC.app.services.analytics_service import (

        get_overview_kpis,

        get_sla_summary,

        get_ticket_volume_monthly,

        get_tickets_priority_detail,

        get_response_resolution_history,

        get_response_resolution_by_agent,

        get_tickets_by_agent,

        get_ticket_detail_by_agent,

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
    frt_res_from_obj, frt_res_to_obj, frt_res_from_dt, frt_res_to_dt = _resolve_prefixed_range(
        "frt_res",
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

    volume = get_ticket_volume_monthly(db, date_from=volume_from_dt, date_to=volume_to_dt)
    volume_tickets = get_tickets_priority_detail(db)

    if volume_from_obj or volume_to_obj:
        volume_title = "Cantidad de tickets desde {} hasta {}".format(
            volume_from_obj.strftime("%d/%m/%Y") if volume_from_obj else "…",
            volume_to_obj.strftime("%d/%m/%Y") if volume_to_obj else "…",
        )
    else:
        volume_title = "Cantidad de tickets por mes"

    status_breakdown = get_ticket_status_breakdown(db, date_from=summary_status_from_dt, date_to=summary_status_to_dt)

    def _support_indicator_name(value: str | None) -> str:
        normalized = unicodedata.normalize("NFKD", str(value or "").strip())
        return "".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold()

    # Tecnicos a mostrar en el indicador; se matchea por tokens contra el
    # nombre completo en BBDD (ej. "antonio bahamondes" calza con
    # "Antonio Alexis Bahamondes Hernandez").
    support_real_technicians = [
        ("antonio", "bahamondes"),
        ("julissa", "mella"),
        ("sthefan", "leal"),
        ("felipe", "mora"),
        ("ronald", "montilla"),
    ]

    def _es_tecnico_indicador(nombre: str | None) -> bool:
        tokens = set(_support_indicator_name(nombre).split())
        return any(nom in tokens and ape in tokens for nom, ape in support_real_technicians)

    support_user_ids = {
        u.id
        for u in _active_users_in_area(db, "soporte")
        if _es_tecnico_indicador(getattr(u, "name", None))
    }
    # "Desempeño por tecnico" siempre muestra el historial completo, sin
    # importar el filtro Desde/Hasta de la pagina (a diferencia de los demas
    # graficos); el detalle filtrable vive en su popup "+ Info".
    agents = get_tickets_by_agent(db, date_from=None, date_to=None, allowed_user_ids=support_user_ids)
    agents.sort(key=lambda a: (a.get("tickets") or 0), reverse=True)
    agent_ticket_detail = get_ticket_detail_by_agent(db, allowed_user_ids=support_user_ids)

    response_resolution_history = get_response_resolution_history(db, date_from=frt_res_from_dt, date_to=frt_res_to_dt)
    response_resolution_by_agent = get_response_resolution_by_agent(
        db, date_from=frt_res_from_dt, date_to=frt_res_to_dt, allowed_user_ids=support_user_ids
    )

    if frt_res_from_obj or frt_res_to_obj:
        frt_res_title = "Primera respuesta V/S tiempo de Resolución desde {} hasta {}".format(
            frt_res_from_obj.strftime("%d/%m/%Y") if frt_res_from_obj else "…",
            frt_res_to_obj.strftime("%d/%m/%Y") if frt_res_to_obj else "…",
        )
    else:
        frt_res_title = "Primera respuesta V/S tiempo de Resolución"

    aging = get_ticket_aging(db, date_from=aging_from_dt, date_to=aging_to_dt)

    return templates.TemplateResponse(
        request,
        "dashboard_soporte.html",

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
            "volume_tickets": volume_tickets,
            "volume_title": volume_title,

            "status_breakdown": status_breakdown,

            "response_resolution_history": response_resolution_history,
            "response_resolution_by_agent": response_resolution_by_agent,
            "frt_res_title": frt_res_title,

            "agents": agents,
            "agent_ticket_detail": agent_ticket_detail,

            "aging": aging,

            "date_from": from_date_obj.isoformat() if from_date_obj else "",

            "date_to": to_date_obj.isoformat() if to_date_obj else "",

            "has_date_filter": bool(from_date_obj or to_date_obj),

            "volume_date_from": volume_from_obj.isoformat() if volume_from_obj else "",
            "volume_date_to": volume_to_obj.isoformat() if volume_to_obj else "",
            "frt_res_date_from": frt_res_from_obj.isoformat() if frt_res_from_obj else "",
            "frt_res_date_to": frt_res_to_obj.isoformat() if frt_res_to_obj else "",
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


@router.get("/panel-indicadores/informe")
def panel_indicadores_informe(
    request: Request,
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    import io

    from fastapi.responses import StreamingResponse

    from ATC.app.services.analytics_service import generar_informe_soporte_pdf

    _require_area_access(db, current_user, "soporte")

    def _parse_iso_date(raw_value: str | None):
        if not raw_value:
            return None
        try:
            return datetime.strptime(raw_value, "%Y-%m-%d").date()
        except ValueError:
            return None

    from_date_obj = _parse_iso_date(date_from)
    to_date_obj = _parse_iso_date(date_to)
    if from_date_obj and to_date_obj and from_date_obj > to_date_obj:
        from_date_obj, to_date_obj = to_date_obj, from_date_obj

    from_dt = (
        datetime(from_date_obj.year, from_date_obj.month, from_date_obj.day, 0, 0, 0, tzinfo=timezone.utc)
        if from_date_obj else None
    )
    to_dt = (
        datetime(to_date_obj.year, to_date_obj.month, to_date_obj.day, 23, 59, 59, 999999, tzinfo=timezone.utc)
        if to_date_obj else None
    )

    def _support_indicator_name(value: str | None) -> str:
        normalized = unicodedata.normalize("NFKD", str(value or "").strip())
        return "".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold()

    support_real_technicians = [
        ("antonio", "bahamondes"),
        ("julissa", "mella"),
        ("sthefan", "leal"),
        ("felipe", "mora"),
        ("ronald", "montilla"),
    ]

    def _es_tecnico_indicador(nombre: str | None) -> bool:
        tokens = set(_support_indicator_name(nombre).split())
        return any(nom in tokens and ape in tokens for nom, ape in support_real_technicians)

    support_user_ids = {
        u.id
        for u in _active_users_in_area(db, "soporte")
        if _es_tecnico_indicador(getattr(u, "name", None))
    }

    pdf_bytes = generar_informe_soporte_pdf(
        db, date_from=from_dt, date_to=to_dt, support_user_ids=support_user_ids
    )
    sufijo = f"_{date_from}_a_{date_to}" if (date_from and date_to) else ""
    nombre = f"Informe_Soporte_Helpdesk{sufijo}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{nombre}"'},
    )


@router.get("/panel-indicadores/informe-tecnico")
def panel_indicadores_informe_tecnico(
    request: Request,
    agent_id: int = Query(...),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    import io

    from fastapi.responses import StreamingResponse

    from ATC.app.services.analytics_service import generar_informe_tecnico_pdf

    _require_area_access(db, current_user, "soporte")

    agente = db.get(User, agent_id)
    if not agente:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")

    def _parse_iso_date(raw_value: str | None):
        if not raw_value:
            return None
        try:
            return datetime.strptime(raw_value, "%Y-%m-%d").date()
        except ValueError:
            return None

    from_date_obj = _parse_iso_date(date_from)
    to_date_obj = _parse_iso_date(date_to)
    if from_date_obj and to_date_obj and from_date_obj > to_date_obj:
        from_date_obj, to_date_obj = to_date_obj, from_date_obj

    from_dt = (
        datetime(from_date_obj.year, from_date_obj.month, from_date_obj.day, 0, 0, 0, tzinfo=timezone.utc)
        if from_date_obj else None
    )
    to_dt = (
        datetime(to_date_obj.year, to_date_obj.month, to_date_obj.day, 23, 59, 59, 999999, tzinfo=timezone.utc)
        if to_date_obj else None
    )

    pdf_bytes = generar_informe_tecnico_pdf(
        db, agent_user_id=agent_id, agent_name=agente.name or "Técnico",
        date_from=from_dt, date_to=to_dt,
    )
    sufijo = f"_{date_from}_a_{date_to}" if (date_from and date_to) else ""
    nombre_seguro = "".join(ch if ch.isalnum() else "_" for ch in (agente.name or "tecnico"))
    nombre = f"Informe_Tecnico_{nombre_seguro}{sufijo}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{nombre}"'},
    )


@router.get("/panel-indicadores/ticket-historial/{ticket_id}")
def panel_indicadores_ticket_historial(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    from ATC.app.services.analytics_service import get_ticket_timeline

    _require_area_access(db, current_user, "soporte")

    timeline = get_ticket_timeline(db, ticket_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    return timeline


@router.get("/ticketera/tickets/new")

def new_ticket_form(

    request: Request,

    current_user: User = Depends(get_current_user_web),

    db: Session = Depends(get_db)

):
    _require_area_access(db, current_user, "soporte")
    users = _visible_support_users(_active_users_in_area(db, "soporte"))

    return templates.TemplateResponse(
        request,
        "new_ticket.html",

        {

            "request": request,

            "users": users,

            "user": current_user

        }

    )

@router.post("/ticketera/tickets/create")

def create_ticket(

    subject: str = Form(...),                 # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œÃƒâ€šÃ‚Â Asunto del ticket (obligatorio)

    content: str = Form(...),                 # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢Ãƒâ€šÃ‚Â¬ Mensaje inicial del ticket (obligatorio)

    priority: str = Form(""),

    assigned_to_id: int | None = Form(None),  # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‹Å“Ãƒâ€šÃ‚Â¤ Usuario asignado (opcional)

    current_user: User = Depends(get_current_user_web),  # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒâ€šÃ‚Â Usuario autenticado

    db: Session = Depends(get_db)             # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬ÂÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾ SesiÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³n de base de datos

):
    _require_area_access(db, current_user, "soporte")

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
        assigned_to_id = current_user.id if _is_visible_support_user(current_user) else None

    support_user_ids = {u.id for u in _visible_support_users(_active_users_in_area(db, "soporte"))}
    if assigned_to_id is not None and int(assigned_to_id) not in support_user_ids:
        raise HTTPException(status_code=400, detail="Usuario asignado invalido")

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

    # Redirigir a la ticketera

    # =====================================

    # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒâ€šÃ‚Â Volvemos al inbox despuÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©s de crear el ticket.

    return RedirectResponse("/ticketera", status_code=303)


# ============================================================
# TABLA SOPORTE TECNICO ODS - INSTALACIONES
# ============================================================

_SOPORTE_BOOL_FIELDS = {
    "configuracion_camaras", "posicionamiento_imagen", "enlace_servidor",
    "configuracion_ivs", "plan_grabacion", "configuracion_cliente", "vb_final_servicio",
    "terminado",
}
_SERVICIO_BOOL_FIELDS = {"instalacion_finalizada", "finalizado"}
_ST_BOOL_FIELDS = _SOPORTE_BOOL_FIELDS | _SERVICIO_BOOL_FIELDS

_SOPORTE_TEXT_FIELDS = {"requiere_puesto_nuevo", "numero_central_asignado"}
_SERVICIO_TEXT_FIELDS = {"materiales_bodega"}
_ST_TEXT_FIELDS = _SOPORTE_TEXT_FIELDS | _SERVICIO_TEXT_FIELDS

_SOPORTE_BOOL_DATE: dict[str, str] = {
    "configuracion_camaras": "fecha_configuracion_camaras",
    "posicionamiento_imagen": "fecha_posicionamiento_imagen",
    "enlace_servidor": "fecha_enlace_servidor",
    "configuracion_ivs": "fecha_configuracion_ivs",
    "plan_grabacion": "fecha_plan_grabacion",
    "configuracion_cliente": "fecha_configuracion_cliente",
    "vb_final_servicio": "fecha_vb_final_servicio",
    "terminado": "fecha_terminado",
}
_SERVICIO_BOOL_DATE: dict[str, str] = {
    "instalacion_finalizada": "fecha_instalacion_finalizada",
    "finalizado": "fecha_cierre",
}
_ST_BOOL_DATE: dict[str, str] = {**_SOPORTE_BOOL_DATE, **_SERVICIO_BOOL_DATE}
_st_campos_ok = False
_soporte_campos_ok = False


def _ensure_st_campos(db: Session) -> None:
    global _st_campos_ok
    if _st_campos_ok:
        return
    extras = [
        ("materiales_bodega", "TEXT"),
        ("fecha_inicio_trabajo", "DATETIME2"),
        ("fecha_fin_trabajo", "DATETIME2"),
    ]
    try:
        for col, ctype in extras:
            db.execute(text(
                f"IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='{col}' AND object_id=OBJECT_ID('venta_servicio_tecnico')) ALTER TABLE venta_servicio_tecnico ADD {col} {ctype}"
            ))
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass

    for col in ("recepcion_solicitud_instalacion", "instalacion_finalizada", "finalizado"):
        try:
            db.execute(text(
                f"IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE parent_object_id=OBJECT_ID('venta_servicio_tecnico') AND COL_NAME(parent_object_id,parent_column_id)='{col}') ALTER TABLE venta_servicio_tecnico ADD CONSTRAINT DF_vst_{col} DEFAULT 0 FOR {col}"
            ))
            db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
    _st_campos_ok = True


def _ensure_soporte_campos(db: Session) -> None:
    global _soporte_campos_ok
    if _soporte_campos_ok:
        return
    try:
        db.execute(text("""
            IF OBJECT_ID('venta_soporte_tecnico', 'U') IS NULL
            BEGIN
            CREATE TABLE venta_soporte_tecnico (
                id INT IDENTITY(1,1) PRIMARY KEY,
                odt VARCHAR(30) NOT NULL UNIQUE REFERENCES venta_comercial(codigo) ON DELETE CASCADE,
                configuracion_camaras BIT NOT NULL DEFAULT 0,
                fecha_configuracion_camaras DATETIME2,
                posicionamiento_imagen BIT NOT NULL DEFAULT 0,
                fecha_posicionamiento_imagen DATETIME2,
                enlace_servidor BIT NOT NULL DEFAULT 0,
                fecha_enlace_servidor DATETIME2,
                configuracion_ivs BIT NOT NULL DEFAULT 0,
                fecha_configuracion_ivs DATETIME2,
                plan_grabacion BIT NOT NULL DEFAULT 0,
                fecha_plan_grabacion DATETIME2,
                configuracion_cliente BIT NOT NULL DEFAULT 0,
                fecha_configuracion_cliente DATETIME2,
                vb_final_servicio BIT NOT NULL DEFAULT 0,
                fecha_vb_final_servicio DATETIME2,
                requiere_puesto_nuevo VARCHAR(20),
                numero_central_asignado VARCHAR(40),
                camaras_registradas NVARCHAR(MAX),
                imagenes_ejecutivo_envios NVARCHAR(MAX),
                updated_at DATETIME2 DEFAULT GETDATE()
            )
            END
        """))
        db.execute(text(
            "IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='imagenes_ejecutivo_envios' AND object_id=OBJECT_ID('venta_soporte_tecnico')) ALTER TABLE venta_soporte_tecnico ADD imagenes_ejecutivo_envios NVARCHAR(MAX)"
        ))
        db.execute(text(
            "IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='terminado' AND object_id=OBJECT_ID('venta_soporte_tecnico')) ALTER TABLE venta_soporte_tecnico ADD terminado BIT NOT NULL DEFAULT 0"
        ))
        db.execute(text(
            "IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='fecha_terminado' AND object_id=OBJECT_ID('venta_soporte_tecnico')) ALTER TABLE venta_soporte_tecnico ADD fecha_terminado DATETIME2"
        ))
        db.execute(text(
            "IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='ix_venta_soporte_tecnico_odt' AND object_id=OBJECT_ID('venta_soporte_tecnico')) CREATE INDEX ix_venta_soporte_tecnico_odt ON venta_soporte_tecnico (odt)"
        ))
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    _soporte_campos_ok = True

    _st_campos_ok = True


def _st_bool_val(v: object) -> str:
    if v is None or v is False:
        return "Pendiente"
    if v is True:
        return "Completado"
    return "Pendiente" if str(v).strip().lower() in ("", "false", "0", "pendiente") else "Completado"


def _st_date_val(v: object) -> str:
    if not v:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y")
    return str(v or "")


def _st_normalize_service_part(value: object) -> str:
    normalized = unicodedata.normalize("NFD", str(value or "").strip())
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", normalized).casefold()


def _st_es_solo_televigilancia(tipo_servicio: object) -> bool:
    partes = [
        _st_normalize_service_part(part)
        for part in re.split(r"[|,;/]+", str(tipo_servicio or ""))
    ]
    partes = [part for part in partes if part]
    return len(partes) == 1 and partes[0] == "televigilancia"


def _st_parse_json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    raw = str(value or "").strip()
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except Exception:
        return []
    return decoded if isinstance(decoded, list) else []


def _st_envio_vista_label(envio: object) -> str:
    if not isinstance(envio, dict):
        return ""
    fecha = str(envio.get("fecha") or "").strip()
    total = int(envio.get("imagenes") or 0)
    email_sent = bool(envio.get("email_sent"))
    estado = "Correo enviado" if email_sent else "Guardado sin correo"
    partes = [p for p in [fecha, f"{total} imagen(es)" if total else "", estado] if p]
    return " · ".join(partes)


@router.get("/materiales", response_class=HTMLResponse)
def materiales_page(
    request: Request,
    db: Session = Depends(get_db),
    token: str = Query(default=""),
):
    current_user: User | None = None
    token_limpio = (token or "").strip()
    if token_limpio:
        return RedirectResponse(url=f"/sso/login?token={token_limpio}", status_code=303)

    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token:
        try:
            username = _decode_cookie_token(cookie_token)
            current_user = UserService.find_by_login(db, username)
        except Exception:
            current_user = None

    if not current_user or not current_user.is_active:
        return RedirectResponse(url="/login", status_code=303)

    _require_area_access(db, current_user, "materiales")
    return templates.TemplateResponse(
        request,
        "materiales.html",
        {"request": request, "user": current_user},
    )


@router.get("/api/soporte-tecnico/ods-filas")
def st_ods_filas(
    db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    _ = current_user
    _ensure_st_campos(db)
    _ensure_soporte_campos(db)
    rows = db.execute(text("""
        SELECT
            v.codigo, v.created_at,
            COALESCE(NULLIF(TRIM(u.name), ''), v.creado_por) AS ejecutivo_nombre,
            v.rut_cliente,
            COALESCE(NULLIF(TRIM(v.nombre_sucursal), ''), v.razon_social) AS sucursal_label,
            v.direccion_sucursal, v.tipo_servicio, v.tipo_plan, v.estado,
            COALESCE(s.instalacion_finalizada, 0),
            COALESCE(i.fecha_cierre, s.fecha_instalacion_finalizada) AS fecha_instalacion_finalizada_real,
            COALESCE(sp.configuracion_camaras, 0),
            COALESCE(sp.posicionamiento_imagen, 0),
            COALESCE(sp.enlace_servidor, 0),
            COALESCE(sp.configuracion_ivs, 0),
            COALESCE(sp.plan_grabacion, 0),
            COALESCE(sp.requiere_puesto_nuevo, ''),
            COALESCE(sp.numero_central_asignado, ''),
            COALESCE(sp.configuracion_cliente, 0),
            COALESCE(sp.vb_final_servicio, 0),
            COALESCE(sp.terminado, 0)
        FROM venta_comercial v
        LEFT JOIN users u
            ON LOWER(TRIM(u.email)) = LOWER(TRIM(v.creado_por))
        LEFT JOIN venta_servicio_tecnico s
            ON LOWER(TRIM(s.odt)) = LOWER(TRIM(v.codigo))
        LEFT JOIN venta_soporte_tecnico sp
            ON LOWER(TRIM(sp.odt)) = LOWER(TRIM(v.codigo))
        LEFT JOIN (
            SELECT LOWER(TRIM(odt)) AS odt_key, MAX(fecha_cierre) AS fecha_cierre
            FROM incidencias
            WHERE fecha_cierre IS NOT NULL
            GROUP BY LOWER(TRIM(odt))
        ) i
            ON i.odt_key = LOWER(TRIM(v.codigo))
        WHERE (
            LOWER(v.tipo_servicio) LIKE '%televigilancia%'
            OR LOWER(v.tipo_servicio) LIKE '%alarma%'
            OR LOWER(v.tipo_servicio) LIKE '%instalaci%'
            OR LOWER(v.tipo_servicio) LIKE '%servicio t%'
            OR LOWER(v.tipo_servicio) LIKE '%upgrade%'
            OR LOWER(v.tipo_servicio) LIKE '%downgrade%'
            OR LOWER(v.tipo_servicio) LIKE '%monitoreo adicional%'
        )
        ORDER BY v.created_at DESC, v.id DESC
    """)).fetchall()
    result: list[list] = []
    for r in rows:
        try:
            fecha = r[1].strftime("%d/%m/%Y") if r[1] else ""
        except Exception:
            fecha = str(r[1] or "")
        instalacion_estado = _st_date_val(r[10]) or ("Finalizado" if r[9] else "Pendiente")
        if _st_es_solo_televigilancia(r[6]):
            instalacion_estado = "No aplica"
        result.append([
            r[0] or "",
            fecha,
            r[2] or "",
            r[3] or "",
            r[4] or "",
            r[5] or "",
            r[6] or "",
            r[7] or "",
            instalacion_estado,
            _st_bool_val(r[11]),
            _st_bool_val(r[12]),
            _st_bool_val(r[13]),
            _st_bool_val(r[14]),
            _st_bool_val(r[15]),
            r[16] or "",
            r[17] or "",
            _st_bool_val(r[18]),
            _st_bool_val(r[19]),
            _st_bool_val(r[20]),
            r[8] or "",
        ])
    return result


@router.get("/api/soporte-tecnico/ods/{codigo}/detalle")
def st_ods_detalle(
    codigo: str,
    db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    _ = current_user
    _ensure_st_campos(db)
    row = db.execute(text("""
        SELECT numero_camaras_instalar, numero_camaras_vigilar, dias_grabacion,
               observacion, rut_cliente, nombre_sucursal, razon_social,
               direccion_sucursal
        FROM venta_comercial
        WHERE LOWER(TRIM(codigo)) = LOWER(TRIM(:c))
        ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
    """), {"c": codigo}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="ODS no encontrada")
    layout_url = db.execute(text("""
        SELECT ruta_archivo FROM venta_ods_archivos
        WHERE LOWER(TRIM(codigo_ods)) = LOWER(TRIM(:c))
          AND LOWER(COALESCE(tipo_documento,'')) LIKE '%layout%'
        ORDER BY id DESC OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
    """), {"c": codigo}).scalar_one_or_none()
    contacto = db.execute(text("""
        SELECT c.nombre, c.telefono
        FROM sucursal_contactos_emergencia c
        JOIN bbdd_sucursales s ON s.id = c.sucursal_id
        WHERE LOWER(TRIM(s.direccion_sucursal)) = LOWER(TRIM(:d))
        ORDER BY c.id ASC OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
    """), {"d": str(row.get("direccion_sucursal") or "")}).mappings().first()
    materiales_st = db.execute(text("""
        SELECT solicitud_materiales FROM venta_servicio_tecnico
        WHERE LOWER(TRIM(odt)) = LOWER(TRIM(:c))
        ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
    """), {"c": codigo}).scalar_one_or_none()
    materiales_bodega_raw = db.execute(text("""
        SELECT materiales_bodega FROM venta_servicio_tecnico
        WHERE LOWER(TRIM(odt)) = LOWER(TRIM(:c))
        ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
    """), {"c": codigo}).scalar_one_or_none()
    try:
        materiales_bodega = json.loads(materiales_bodega_raw) if materiales_bodega_raw else None
    except Exception:
        materiales_bodega = None
    return {
        "camarasInstalar": str(row.get("numero_camaras_instalar") or ""),
        "camarasVigilar": str(row.get("numero_camaras_vigilar") or ""),
        "diasGrabacion": str(row.get("dias_grabacion") or ""),
        "materiales": str(materiales_st or ""),
        "observacion": str(row.get("observacion") or ""),
        "idCliente": str(row.get("rut_cliente") or ""),
        "idSucursal": str(row.get("nombre_sucursal") or ""),
        "razonSocial": str(row.get("razon_social") or ""),
        "sucursal": str(row.get("nombre_sucursal") or row.get("razon_social") or ""),
        "direccion": str(row.get("direccion_sucursal") or ""),
        "layout": layout_url or "",
        "contactoNombre": str(contacto.get("nombre") or "") if contacto else "",
        "contactoTelefono": str(contacto.get("telefono") or "") if contacto else "",
        "materialesBodega": materiales_bodega,
    }


@router.get("/api/soporte-tecnico/ods/{codigo}/camaras")
def st_ods_camaras(
    codigo: str,
    db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    _ = current_user
    _ensure_soporte_campos(db)
    raw = db.execute(text("""
        SELECT TOP 1 camaras_registradas FROM venta_soporte_tecnico
        WHERE LOWER(TRIM(odt)) = LOWER(TRIM(:c))
    """), {"c": codigo}).scalar_one_or_none()
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


@router.get("/api/soporte-tecnico/ods/{codigo}/vistas-ejecutivo")
def st_ods_vistas_ejecutivo(
    codigo: str,
    db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    _ = current_user
    _ensure_soporte_campos(db)
    raw = db.execute(text("""
        SELECT TOP 1 imagenes_ejecutivo_envios FROM venta_soporte_tecnico
        WHERE LOWER(TRIM(odt)) = LOWER(TRIM(:c))
    """), {"c": codigo}).scalar_one_or_none()
    envios = _st_parse_json_list(raw)[:2]
    salida: list[dict[str, object]] = []
    for envio in envios:
        if not isinstance(envio, dict):
            continue
        archivos: list[dict[str, str]] = []
        for archivo in _st_parse_json_list(envio.get("archivos")):
            if not isinstance(archivo, dict):
                continue
            data = str(archivo.get("data") or "").strip()
            if not data:
                continue
            archivos.append({
                "nombre": str(archivo.get("nombre") or "Vista de cámara").strip(),
                "mime_type": str(archivo.get("mime_type") or "").strip(),
                "data": data,
            })
        salida.append({
            "fecha": str(envio.get("fecha") or "").strip(),
            "imagenes": int(envio.get("imagenes") or 0),
            "archivos": archivos,
            "email_sent": bool(envio.get("email_sent")),
            "email_to": str(envio.get("email_to") or "").strip(),
            "email_error": str(envio.get("email_error") or "").strip(),
        })
    return {"odt": codigo, "envios": salida, "max_envios": 2, "restantes": max(0, 2 - len(salida))}


def _st_parse_camera_total(value: object) -> int:
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except Exception:
        match = re.search(r"\d+", str(value or ""))
        return max(0, int(match.group(0))) if match else 0


def _st_camaras_registradas_completas(db: Session, codigo: str) -> bool:
    row = db.execute(text("""
        SELECT v.tipo_servicio, v.numero_camaras_vigilar, sp.camaras_registradas
        FROM venta_comercial v
        LEFT JOIN venta_soporte_tecnico sp
            ON LOWER(TRIM(sp.odt)) = LOWER(TRIM(v.codigo))
        WHERE LOWER(TRIM(v.codigo)) = LOWER(TRIM(:c))
        ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
    """), {"c": codigo}).mappings().first()
    if not row:
        return False
    if "televigilancia" not in _st_normalize_service_part(row.get("tipo_servicio")):
        return True
    total = _st_parse_camera_total(row.get("numero_camaras_vigilar"))
    if total <= 0:
        return True
    registradas = [
        str(value or "").strip()
        for value in _st_parse_json_list(row.get("camaras_registradas"))
    ]
    return len(registradas) >= total and all(registradas[idx] for idx in range(total))


@router.post("/api/soporte-tecnico/ods/actualizar-estado")
def st_ods_actualizar_estado(
    payload: dict,
    db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    _ = current_user
    _ensure_st_campos(db)
    _ensure_soporte_campos(db)
    codigo = str(payload.get("codigo") or "").strip()
    campo = str(payload.get("campo") or "").strip()
    valor = bool(payload.get("valor"))
    if not codigo or campo not in _ST_BOOL_FIELDS:
        raise HTTPException(status_code=400, detail="Parametros invalidos")
    if campo == "finalizado" and valor and not _st_camaras_registradas_completas(db, codigo):
        raise HTTPException(status_code=400, detail="Debes registrar los IDs de las cámaras antes de marcar Terminado.")
    tabla = "venta_soporte_tecnico" if campo in _SOPORTE_BOOL_FIELDS else "venta_servicio_tecnico"
    date_col = _ST_BOOL_DATE.get(campo)
    now = datetime.now()
    eid = db.execute(text(
        f"SELECT TOP 1 id FROM {tabla} WHERE LOWER(TRIM(odt)) = LOWER(TRIM(:c))"
    ), {"c": codigo}).scalar_one_or_none()
    if eid:
        if date_col:
            db.execute(text(
                f"UPDATE {tabla} SET {campo}=:v, {date_col}=:d WHERE id=:id"
            ), {"v": valor, "d": now if valor else None, "id": eid})
        else:
            db.execute(text(
                f"UPDATE {tabla} SET {campo}=:v WHERE id=:id"
            ), {"v": valor, "id": eid})
    else:
        cols = f"odt, {campo}"
        vals = ":odt, :v"
        params: dict[str, object] = {"odt": codigo, "v": valor}
        if date_col:
            cols += f", {date_col}"
            vals += ", :d"
            params["d"] = now if valor else None
        db.execute(text(
            f"INSERT INTO {tabla} ({cols}) VALUES ({vals})"
        ), params)
    db.commit()
    return {"ok": True}


@router.post("/api/soporte-tecnico/ods/actualizar-valor")
def st_ods_actualizar_valor(
    payload: dict,
    db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    _ = current_user
    _ensure_st_campos(db)
    _ensure_soporte_campos(db)
    codigo = str(payload.get("codigo") or "").strip()
    campo = str(payload.get("campo") or "").strip()
    valor = str(payload.get("valor") or "").strip()
    if not codigo or campo not in _ST_TEXT_FIELDS:
        raise HTTPException(status_code=400, detail="Parametros invalidos")
    tabla = "venta_soporte_tecnico" if campo in _SOPORTE_TEXT_FIELDS else "venta_servicio_tecnico"
    previous_value = ""
    row_actual = db.execute(text(
        f"SELECT TOP 1 id, {campo} FROM {tabla} WHERE LOWER(TRIM(odt)) = LOWER(TRIM(:c))"
    ), {"c": codigo}).mappings().first()
    if row_actual:
        eid = row_actual.get("id")
        previous_value = str(row_actual.get(campo) or "").strip()
        db.execute(text(
            f"UPDATE {tabla} SET {campo}=:v WHERE id=:id"
        ), {"v": valor or None, "id": eid})
    else:
        db.execute(text(
            f"INSERT INTO {tabla} (odt, {campo}) VALUES (:odt, :v)"
        ), {"odt": codigo, "v": valor or None})
    db.commit()
    email_result: dict[str, object] = {"email_sent": False, "email_to": [], "email_error": ""}
    if campo in _SOPORTE_TEXT_FIELDS and valor != previous_value:
        soporte_vals = db.execute(text("""
            SELECT COALESCE(requiere_puesto_nuevo, '') AS requiere_puesto_nuevo,
                   COALESCE(numero_central_asignado, '') AS numero_central_asignado
            FROM venta_soporte_tecnico
            WHERE LOWER(TRIM(odt)) = LOWER(TRIM(:c))
            ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
        """), {"c": codigo}).mappings().first()
        requiere_puesto = str(soporte_vals.get("requiere_puesto_nuevo") or "").strip() if soporte_vals else ""
        numero_puesto = str(soporte_vals.get("numero_central_asignado") or "").strip() if soporte_vals else ""
        requiere_norm = unicodedata.normalize("NFD", requiere_puesto).encode("ascii", "ignore").decode("ascii").strip().lower()
        debe_notificar = (
            requiere_norm.startswith("no")
            or (requiere_norm.startswith("si") and bool(numero_puesto))
            or (campo == "numero_central_asignado" and bool(numero_puesto))
        )
        if debe_notificar:
            from ATC.app.services.venta_trace_email_service import notify_puesto_soporte
            email_result = notify_puesto_soporte(db, codigo, requiere_puesto, numero_puesto)
    return {"ok": True, **email_result}


@router.post("/api/soporte-tecnico/ods/guardar-camaras")
def st_ods_guardar_camaras(
    payload: dict,
    db: Session = Depends(get_incidencias_db),
):
    service = IncidenciasService(db)
    _ensure_soporte_campos(db)
    codigo = str(payload.get("odt") or payload.get("codigo") or "").strip()
    ids = payload.get("ids") or []
    token = str(payload.get("token") or "").strip()
    if not codigo:
        raise HTTPException(status_code=400, detail="Codigo ODT requerido")
    if token and not service.usuario_logueado_por_token(token):
        raise HTTPException(status_code=401, detail="No autenticado")
    incoming = [str(v or "").strip() for v in ids]
    if not any(incoming):
        raise HTTPException(status_code=400, detail="Debes registrar al menos una cámara")

    total_vigilar = db.execute(text("""
        SELECT numero_camaras_vigilar
        FROM venta_comercial
        WHERE LOWER(TRIM(codigo)) = LOWER(TRIM(:c))
        ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
    """), {"c": codigo}).scalar_one_or_none()
    try:
        total_vigilar_int = int(total_vigilar or 0)
    except Exception:
        total_vigilar_int = 0
    if total_vigilar_int > 0 and len(incoming) != total_vigilar_int:
        raise HTTPException(status_code=400, detail=f"Debes registrar {total_vigilar_int} cámara(s).")
    if any(not v for v in incoming):
        raise HTTPException(status_code=400, detail="Debes completar todas las cámaras.")

    eid = db.execute(text(
        "SELECT TOP 1 id, camaras_registradas FROM venta_soporte_tecnico WHERE LOWER(TRIM(odt)) = LOWER(TRIM(:c))"
    ), {"c": codigo}).mappings().first()
    existentes = [str(v or "").strip() for v in _st_parse_json_list(eid.get("camaras_registradas") if eid else None)]
    total_slots = max(total_vigilar_int, len(incoming), len(existentes))
    merged: list[str] = []
    for idx in range(total_slots):
        actual = existentes[idx] if idx < len(existentes) else ""
        nuevo = incoming[idx] if idx < len(incoming) else ""
        if actual and nuevo and actual != nuevo:
            raise HTTPException(status_code=400, detail=f"La Cámara {idx + 1} ya está registrada y no se puede modificar.")
        merged.append(actual or nuevo)
    if all(str(v or "").strip() for v in existentes[:total_slots]) and existentes:
        raise HTTPException(status_code=400, detail="Las cámaras ya están registradas y no se pueden modificar.")

    ids_json = json.dumps(merged, ensure_ascii=False)
    if eid:
        db.execute(text(
            "UPDATE venta_soporte_tecnico SET camaras_registradas=:v WHERE id=:id"
        ), {"v": ids_json, "id": eid.get("id")})
    else:
        db.execute(text(
            "INSERT INTO venta_soporte_tecnico (odt, camaras_registradas) VALUES (:odt, :v)"
        ), {"odt": codigo, "v": ids_json})
    db.commit()
    return {"ok": True, "mensaje": f"{len([v for v in merged if v])} camaras guardadas", "ids": merged}


@router.post("/api/soporte-tecnico/ods/guardar-materiales-bodega")
def st_ods_guardar_materiales_bodega(
    payload: dict,
    db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    _ = current_user
    _ensure_st_campos(db)
    codigo = str(payload.get("codigo") or payload.get("odt") or "").strip()
    items = payload.get("items") or []
    observacion = str(payload.get("observacion") or "").strip()
    if not codigo:
        raise HTTPException(status_code=400, detail="Codigo ODS requerido")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="Items invalidos")

    sane_items: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        nombre = str(item.get("nombre") or "").strip()
        presupuestado = str(item.get("presupuestado") or "").strip()
        entregado = str(item.get("entregado") or "").strip()
        if not nombre and not presupuestado and not entregado:
            continue
        sane_items.append(
            {
                "nombre": nombre,
                "presupuestado": presupuestado,
                "entregado": entregado,
            }
        )

    data_json = json.dumps(
        {
            "items": sane_items,
            "observacion": observacion,
            "guardado_por": str(current_user.name or current_user.username or "").strip(),
            "guardado_en": datetime.now().isoformat(),
        },
        ensure_ascii=False,
    )

    eid = db.execute(text(
        "SELECT TOP 1 id FROM venta_servicio_tecnico WHERE LOWER(TRIM(odt)) = LOWER(TRIM(:c))"
    ), {"c": codigo}).scalar_one_or_none()
    if eid:
        db.execute(text(
            "UPDATE venta_servicio_tecnico SET materiales_bodega=:v WHERE id=:id"
        ), {"v": data_json, "id": eid})
    else:
        db.execute(text(
            "INSERT INTO venta_servicio_tecnico (odt, materiales_bodega) VALUES (:odt, :v)"
        ), {"odt": codigo, "v": data_json})
    db.commit()
    return {"ok": True, "mensaje": "Materiales de bodega guardados"}


@router.post("/api/soporte-tecnico/ods/upload-imagenes")
async def st_ods_upload_imagenes(
    request: Request,
    db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_web),
):
    _ensure_soporte_campos(db)
    payload = await request.json()
    odt = str(payload.get("odt") or "").strip()
    archivos = payload.get("archivos") or []
    if not odt:
        raise HTTPException(status_code=400, detail="ODT requerido")
    if not isinstance(archivos, list) or not archivos:
        raise HTTPException(status_code=400, detail="Debes adjuntar al menos una imagen")

    ods_row = db.execute(
        text("SELECT TOP 1 id, codigo, creado_por, nombre_sucursal, razon_social FROM venta_comercial WHERE UPPER(TRIM(codigo)) = UPPER(TRIM(:c))"),
        {"c": odt},
    ).mappings().first()
    if not ods_row:
        raise HTTPException(status_code=404, detail=f"ODS {odt} no encontrada")

    creado_por = str(ods_row.get("creado_por") or "").strip().strip("'\"")
    soporte_row = db.execute(text("""
        SELECT id, imagenes_ejecutivo_envios
        FROM venta_soporte_tecnico
        WHERE LOWER(TRIM(odt)) = LOWER(TRIM(:c))
        ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
    """), {"c": odt}).mappings().first()
    envios_previos = _st_parse_json_list(soporte_row.get("imagenes_ejecutivo_envios") if soporte_row else None)[:2]
    if len(envios_previos) >= 2:
        raise HTTPException(status_code=400, detail="Esta ODS ya tiene los 2 envíos de imágenes permitidos.")

    ejecutivo_email: str | None = None
    if creado_por:
        ejecutivo_email = db.execute(
            text("SELECT TOP 1 email FROM users WHERE LOWER(TRIM(name)) = LOWER(TRIM(:n)) AND email IS NOT NULL"),
            {"n": creado_por},
        ).scalar_one_or_none()

    guardadas = 0
    inline_images_bytes: list[dict] = []
    imagenes_sql: list[dict[str, str]] = []
    cid_counter = 0

    for f in archivos:
        b64 = str(f.get("base64") or "")
        nombre = str(f.get("nombre") or f"imagen_{guardadas + 1}")
        mime = str(f.get("mimeType") or "image/png")
        if not b64:
            continue
        try:
            content = base64.b64decode(b64)
        except Exception:
            continue
        if not content:
            continue

        ext = mimetypes.guess_extension(mime) or ".png"
        filename = f"{nombre}{ext}" if not nombre.endswith(ext) else nombre
        data_uri = f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"
        guardadas += 1
        imagenes_sql.append({"nombre": filename, "mime_type": mime, "data": data_uri})
        cid_counter += 1
        inline_images_bytes.append({
            "cid": f"img{cid_counter}",
            "bytes": content,
            "mime_type": mime,
            "filename": filename,
        })

    if not guardadas:
        raise HTTPException(status_code=400, detail="No se pudo procesar ninguna imagen.")

    email_sent = False
    email_error: str | None = None

    if not ejecutivo_email:
        email_error = f"No se encontró email para el ejecutivo '{creado_por}'"
    elif not guardadas:
        email_error = "No se guardó ninguna imagen"
    else:
        try:
            from ATC.app.integrations.email_smtp import send_corporate_image_email
            nombre_sucursal = str(ods_row.get("nombre_sucursal") or ods_row.get("razon_social") or odt)
            cuerpo = (
                f'<p style="margin:0 0 14px;font-family:Arial,sans-serif;font-size:14px;'
                f'line-height:1.6;color:#1e293b;">'
                f'Hola <strong>{creado_por or "Ejecutivo"}</strong>,</p>'
                f'<p style="margin:0 0 14px;font-family:Arial,sans-serif;font-size:14px;'
                f'line-height:1.6;color:#374151;">'
                f'Adjunto encontrará las imágenes de las cámaras de seguridad de la sucursal '
                f'<strong>{nombre_sucursal}</strong>. Por favor, revíselas y realice los ajustes '
                f'o correcciones que correspondan.'
                f'</p>'
                f'<p style="margin:0;font-family:Arial,sans-serif;font-size:14px;'
                f'line-height:1.6;color:#374151;">'
                f'</p>'
            )
            send_corporate_image_email(
                to=ejecutivo_email,
                subject=f"[ATC] Vista de cámaras — ODS {odt}",
                titulo=f"ODS {odt}",
                subtitulo=nombre_sucursal,
                cuerpo_html=cuerpo,
                images=inline_images_bytes,
                usar_smtp_informe=True,
            )
            email_sent = True
        except Exception as mail_exc:
            email_error = str(mail_exc)
            import logging
            logging.getLogger(__name__).warning("No se pudo enviar correo vista cámaras ODS %s: %s", odt, mail_exc)

    envio = {
        "fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "imagenes": guardadas,
        "archivos": imagenes_sql,
        "email_sent": email_sent,
        "email_to": ejecutivo_email or "",
        "email_error": email_error or "",
        "guardado_por": str(current_user.name or current_user.username or "").strip(),
    }
    envios_actualizados = [*envios_previos, envio][:2]
    envios_json = json.dumps(envios_actualizados, ensure_ascii=False)
    if soporte_row:
        db.execute(text("""
            UPDATE venta_soporte_tecnico
            SET imagenes_ejecutivo_envios=:envios, updated_at=GETDATE()
            WHERE id=:id
        """), {"envios": envios_json, "id": soporte_row.get("id")})
    else:
        db.execute(text("""
            INSERT INTO venta_soporte_tecnico (odt, imagenes_ejecutivo_envios)
            VALUES (:odt, :envios)
        """), {"odt": odt, "envios": envios_json})
    db.commit()

    return {
        "ok": True,
        "guardadas": guardadas,
        "email_sent": email_sent,
        "email_error": email_error,
        "email_to": ejecutivo_email,
        "envios": [
            {
                "fecha": str(e.get("fecha") or ""),
                "imagenes": int(e.get("imagenes") or 0),
                "email_sent": bool(e.get("email_sent")),
                "email_to": str(e.get("email_to") or ""),
                "email_error": str(e.get("email_error") or ""),
            }
            for e in envios_actualizados
            if isinstance(e, dict)
        ],
    }


@router.post("/api/soporte-tecnico/ods/notificar-termino")
def st_ods_notificar_termino(
    payload: dict,
    current_user: User = Depends(get_current_user_web),
):
    _ = current_user
    codigo = str(payload.get("codigo") or payload.get("odt") or "").strip()
    if not codigo:
        raise HTTPException(status_code=400, detail="Codigo ODS requerido")
    return {"ok": True, "mensaje": "sin_notificacion_adicional"}
