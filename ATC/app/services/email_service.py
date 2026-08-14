from __future__ import annotations

import email
import imaplib
import json
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from ATC.app.core.config import settings
from ATC.app.core.image_optimizer import optimize_image_bytes
from ATC.app.core.text import decode_mime_words
from ATC.app.models.email_sync_state import EmailSyncState
from ATC.app.models.message import Message
from ATC.app.models.requester import Requester
from ATC.app.models.ticket import Ticket
from ATC.app.models.user import User
from ATC.app.services.ticket_status_service import apply_ticket_status_change


# Absoluto y no relativo a cwd: "uploads" relativo dependía de que el proceso
# se lanzara con cwd=raiz del repo, y con cwd distinto (p. ej. una consola
# abierta en otra carpeta) las imagenes de correos entrantes se guardaban en
# un directorio que la app nunca sirve — quedaban 404 pese a haberse
# extraido bien del correo. ATC/app/main.py sirve "/uploads" desde
# ATC/uploads exactamente; hay que escribir ahí siempre.
UPLOAD_DIR = str(Path(__file__).resolve().parents[2] / "uploads")
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


# Sanitizacion de HTML de correos entrantes: el remitente es 100% externo y
# no confiable (cualquiera puede mandar un correo a la casilla de soporte),
# y ese HTML se guarda tal cual en Message.content y se renderiza con
# "{{ m.content | safe }}" en detalle_ticket.html — sin este filtro, un
# correo con <script>/onerror=... ejecuta contra la sesion del agente que
# abre el ticket. Se implementa a mano con html.parser (stdlib) en vez de
# una libreria externa (bleach/nh3) para no depender de que se instale un
# paquete nuevo en el Python de produccion del Windows Server.
import html as _html_mod
from html.parser import HTMLParser as _HTMLParser

_EMAIL_HTML_ALLOWED_TAGS = {
    "a", "b", "strong", "i", "em", "u", "p", "br", "div", "span",
    "ul", "ol", "li", "table", "thead", "tbody", "tr", "td", "th",
    "img", "blockquote", "pre", "code", "h1", "h2", "h3", "h4", "h5",
    "h6", "hr", "small", "font", "sub", "sup", "center",
}
_EMAIL_HTML_VOID_TAGS = {"br", "hr", "img"}
_EMAIL_HTML_DROP_CONTENT_TAGS = {
    "script", "style", "iframe", "object", "embed", "form",
    "button", "noscript", "svg",
}
# Elementos vacios (sin tag de cierre en HTML real, p.ej. el <meta charset=...>
# que trae practicamente todo correo generado por Outlook) — si se tratan
# igual que _EMAIL_HTML_DROP_CONTENT_TAGS (empujar a la pila de "descartar
# hasta el cierre"), esa pila nunca se vacia porque el cierre nunca llega, y
# TODO el resto del documento (incluido el texto real) queda silenciado. Se
# ignoran sin abrir ningun contexto de "descarte" (bug real, ago 2026 —
# provocaba el placeholder "correo sin contenido legible" en cualquier email
# de Outlook/Exchange con <meta> en el <head>).
_EMAIL_HTML_VOID_DROP_TAGS = {"meta", "link", "base", "input"}
_EMAIL_HTML_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "width", "height"},
    "font": {"color", "size", "face"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}


class _EmailHtmlSanitizer(_HTMLParser):
    """Allowlist de tags/atributos para HTML de correos entrantes. Descarta
    cualquier tag no listado (incluye su contenido si es script/style/etc.),
    todo atributo que no sea de la lista explicita por tag (nada de
    on*=, style=, class=), y cualquier href/src con esquema javascript:/
    data:text/html."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip_stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _EMAIL_HTML_VOID_DROP_TAGS:
            return
        if tag in _EMAIL_HTML_DROP_CONTENT_TAGS:
            self._skip_stack.append(tag)
            return
        if self._skip_stack:
            return
        if tag not in _EMAIL_HTML_ALLOWED_TAGS:
            return
        allowed = _EMAIL_HTML_ALLOWED_ATTRS.get(tag, set())
        safe_attrs = []
        for name, value in attrs:
            name_l = (name or "").lower()
            if name_l not in allowed:
                continue
            value = (value or "").strip()
            if name_l in ("href", "src"):
                v_low = value.lower()
                if v_low.startswith(("javascript:", "vbscript:", "data:text/html")):
                    continue
                if name_l == "href" and not v_low.startswith(("http://", "https://", "mailto:", "/", "#")):
                    continue
                if name_l == "src" and not v_low.startswith(("http://", "https://", "/uploads/", "data:image/")):
                    continue
            safe_attrs.append(f'{name_l}="{_html_mod.escape(value, quote=True)}"')
        attr_str = (" " + " ".join(safe_attrs)) if safe_attrs else ""
        self.out.append(f"<{tag}{attr_str}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._skip_stack:
            if self._skip_stack[-1] == tag:
                self._skip_stack.pop()
            return
        if tag in _EMAIL_HTML_ALLOWED_TAGS and tag not in _EMAIL_HTML_VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self._skip_stack:
            return
        self.out.append(_html_mod.escape(data, quote=False))

    def get_html(self) -> str:
        return "".join(self.out)


def _sanitize_email_html(raw_html: str | None) -> str:
    if not raw_html:
        return raw_html or ""
    parser = _EmailHtmlSanitizer()
    try:
        parser.feed(raw_html)
        parser.close()
        return parser.get_html()
    except Exception:
        return _html_mod.escape(raw_html, quote=False)


def _escape_plain_text_to_html(text: str) -> str:
    return _html_mod.escape(text or "", quote=False).replace("\n", "<br>")


def _html_has_visible_content(html: str | None) -> bool:
    """True si el HTML deja algo visible para el agente (texto o imagen).

    Se usa para detectar el caso "el mensaje se guardo pero
    Message.content quedo vacio" (ticket 561, correo automatico de
    Google, ago 2026): el <img> por si solo cuenta como contenido aunque
    no haya texto, para no descartar de mas correos que son solo una
    imagen."""
    if not html:
        return False
    if "<img" in html.lower():
        return True
    text = re.sub(r"<[^>]+>", "", html)
    text = _html_mod.unescape(text.replace("&nbsp;", " "))
    return bool(text.strip())


def _decode_email_part_payload(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw_payload = part.get_payload()
        if isinstance(raw_payload, str):
            return raw_payload
        return ""

    charset_values = [
        part.get_content_charset(),
        str(part.get_charset() or "").strip() or None,
        "utf-8",
        "cp1252",
        "iso-8859-1",
    ]
    seen: set[str] = set()
    for charset in charset_values:
        if not charset:
            continue
        normalized = str(charset).strip().strip('"').lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        try:
            return payload.decode(normalized, errors="replace")
        except Exception:
            continue

    return payload.decode(errors="replace")


def _parse_header_recipients(headers: list[str], *, exclude: set[str] | None = None) -> list[str]:
    exclude = {item.lower() for item in (exclude or set()) if item}
    recipients: list[str] = []
    seen: set[str] = set()
    for _, address in getaddresses(headers or []):
        address = (address or "").strip().lower()
        if not address or address in exclude:
            continue
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address):
            continue
        if address in seen:
            continue
        seen.add(address)
        recipients.append(address)
    return recipients


def _email_recipient_marker(*, cc_recipients: list[str]) -> str:
    if not cc_recipients:
        return ""
    payload = {"to": [], "cc": cc_recipients, "bcc": []}
    return f"<!-- ATC_EMAIL_RECIPIENTS {json.dumps(payload, ensure_ascii=False)} -->\n"


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

    # Nombres como "image.png" son el default de Gmail y se repiten en
    # correos completamente distintos. Sin un sufijo único por archivo, dos
    # imágenes de mensajes distintos guardadas casi al mismo tiempo (p. ej.
    # el hilo de polling IMAP procesando un correo nuevo mientras se procesa
    # otro) pueden pisarse: el chequeo os.path.exists()+escritura de abajo
    # no es atómico, así que "no existe todavía" deja de ser garantía real
    # bajo concurrencia. El sufijo hace que la colisión sea prácticamente
    # imposible sin depender de ese chequeo.
    base = f"{base}_{uuid.uuid4().hex[:8]}"

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
    """Extrae el HTML del correo y guarda TODAS las imágenes (con o sin
    Content-ID). Intenta resolver cada referencia por cid, Content-Location y
    nombre de archivo; las imágenes que igual queden sin referencia se anexan
    al final del mensaje para no perder información."""
    html_body = None
    html_attachment_fallback = None
    imagenes: list[dict] = []

    for part in msg.walk():
        content_type = part.get_content_type()
        content_disposition = str(part.get("Content-Disposition") or "")

        if content_type in {"text/html", "application/xhtml+xml"}:
            decoded_html = _decode_email_part_payload(part).strip()
            if decoded_html:
                if "attachment" not in content_disposition.lower():
                    html_body = decoded_html
                elif html_attachment_fallback is None:
                    html_attachment_fallback = decoded_html

        if not content_type.startswith("image/"):
            continue

        payload = part.get_payload(decode=True)
        if not payload:
            continue
        payload = optimize_image_bytes(payload, content_type=content_type)

        content_id = str(part.get("Content-ID") or "").strip().strip("<>")
        location = str(part.get("Content-Location") or "").strip()
        nombre_original = decode_mime_words(part.get_filename() or "").strip()
        filename = _safe_attachment_filename(
            part.get_filename() or content_id or "imagen",
            content_type=content_type,
            fallback_prefix=(content_id or "inline_image"),
        )
        with open(os.path.join(UPLOAD_DIR, filename), "wb") as file_obj:
            file_obj.write(payload)
        imagenes.append({
            "archivo": filename,
            "cid": content_id,
            "location": location,
            "original": nombre_original,
        })

    if html_body is None and html_attachment_fallback is not None:
        html_body = html_attachment_fallback

    if not imagenes:
        return html_body

    if html_body is None:
        # Correo sin parte HTML pero con imágenes: partir del texto plano
        # para poder anexarlas abajo.
        html_body = _extract_body_text(msg).strip().replace("\n", "<br>")

    usadas: set[str] = set()

    # 1) Resolución directa: cid y Content-Location.
    for img in imagenes:
        url = f"/uploads/{img['archivo']}"
        resuelta = False
        if img["cid"] and f"cid:{img['cid']}" in html_body:
            html_body = html_body.replace(f"cid:{img['cid']}", url)
            resuelta = True
        if img["location"]:
            for patron in (f'src="{img["location"]}"', f"src='{img['location']}'"):
                if patron in html_body:
                    html_body = html_body.replace(patron, f'src="{url}"')
                    resuelta = True
        if resuelta:
            usadas.add(img["archivo"])

    # 2) cids que quedaron colgando: match laxo por nombre de archivo, y si
    #    solo queda una imagen libre, asumirla.
    for cid in re.findall(r'cid:([^"\'\s>)]+)', html_body):
        cid_low = cid.lower()
        candidata = None
        for img in imagenes:
            if img["archivo"] in usadas:
                continue
            orig = (img["original"] or "").lower()
            base = orig.rsplit(".", 1)[0] if orig else ""
            if orig and (orig in cid_low or (base and cid_low.startswith(base))):
                candidata = img
                break
        if candidata is None:
            libres = [i for i in imagenes if i["archivo"] not in usadas]
            if len(libres) == 1:
                candidata = libres[0]
        if candidata:
            html_body = html_body.replace(f"cid:{cid}", f"/uploads/{candidata['archivo']}")
            usadas.add(candidata["archivo"])

    # 3) Lo que siga sin referencia se anexa al final: la información no se pierde.
    sueltas = [i for i in imagenes if i["archivo"] not in usadas]
    if sueltas:
        bloques = "".join(
            f'<div style="margin:6px 0"><img src="/uploads/{i["archivo"]}" '
            f'alt="{i["original"] or i["archivo"]}" style="max-width:100%"></div>'
            for i in sueltas
        )
        html_body += (
            '<div style="margin-top:14px;padding-top:10px;border-top:1px solid #e5e7eb;">'
            '<div style="font-size:12px;color:#64748b;font-weight:600;margin-bottom:4px;">'
            "Imágenes del correo</div>" + bloques + "</div>"
        )

    return html_body


def _extract_body_text(msg) -> str:
    body = ""
    fallback_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition") or "")
            if content_type == "text/plain" and "attachment" not in content_disposition.lower():
                body = _decode_email_part_payload(part).strip()
                if body:
                    break
            elif (
                content_type.startswith("text/")
                and content_type != "text/html"
                and not fallback_body
            ):
                fallback_body = _decode_email_part_payload(part).strip()
    else:
        body = _decode_email_part_payload(msg).strip()

    return body or fallback_body


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
        _normalize_email_address(settings.smtp2_username),
        _normalize_email_address(settings.smtp2_from_email),
        _normalize_email_address(settings.SMTP_FROM),
    }
    return {item for item in mailboxes if item}


def _agent_emails(db: Session) -> dict[str, tuple[int, str]]:
    """Correos de agentes/usuarios internos activos (email -> (id, nombre)).
    Un agente que responde desde su propio correo (fuera de la ticketera) no
    es un cliente: si su correo termina matcheando/creando un Requester, esa
    cuenta queda "enlazada" para siempre (Requester.email es un campo global,
    sin alcance por ticket) y sus correos futuros se atribuyen a ese cliente
    en vez de al agente — paso con felipe.mora@soporteatc.cl, ago 2026."""
    mapping: dict[str, tuple[int, str]] = {}
    rows = db.query(User.id, User.name, User.email).filter(User.is_active == True).all()  # noqa: E712
    for uid, name, email_addr in rows:
        norm = _normalize_email_address(email_addr)
        if norm:
            mapping[norm] = (uid, name)
    return mapping


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
    """Fecha del correo en hora local de Chile (naive), consistente con el
    resto de los mensajes del sistema (GETDATE() del SQL Server)."""
    from zoneinfo import ZoneInfo

    tz_local = ZoneInfo("America/Santiago")
    raw_date = msg.get("Date")
    if raw_date:
        try:
            dt = parsedate_to_datetime(raw_date)
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(tz_local).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.now(tz_local).replace(tzinfo=None)


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
        r"(?is)(?:<br\s*/?>|\n|\r)\s*el\s+.{0,500}?escribi(?:o|ó|Ã³)\s*:",
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
    requester_id: int | None,
    message_dt: datetime,
    inbound_mailbox: str | None = None,
    allow_create: bool = True,
) -> tuple[Ticket | None, bool]:
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

    if not allow_create:
        # Correo de un agente/usuario interno que no encajo en ningun hilo
        # existente: no es un cliente nuevo, no se crea Requester ni Ticket
        # a partir de el (ver _agent_emails). Se descarta en silencio, igual
        # que los correos que llegan desde los buzones compartidos.
        return None, False

    # Deduplicación: si ya existe un ticket con el mismo asunto del mismo
    # requester creado en los últimos 10 minutos, reutilizarlo en vez de
    # crear un duplicado (cubre el caso de email procesado dos veces o
    # self-send desde el compositor interno).
    cutoff = message_dt - timedelta(minutes=10)
    duplicate = (
        db.query(Ticket)
        .filter(
            Ticket.requester_id == requester_id,
            Ticket.subject == (subject or "Sin asunto"),
            Ticket.source == "email",
            Ticket.created_at >= cutoff,
        )
        .order_by(Ticket.created_at.desc())
        .first()
    )
    if duplicate:
        if getattr(duplicate, "status", None) == "resolved":
            apply_ticket_status_change(duplicate, "open")
        return duplicate, True

    ticket = Ticket(
        subject=subject or "Sin asunto",
        source="email",
        priority="",
        requester_id=requester_id,
        status="open",
        inbound_mailbox=inbound_mailbox,
        created_at=message_dt,
    )
    db.add(ticket)
    db.flush()
    return ticket, False


# Reintentos por UID ante fallas transitorias (I/O de imagenes, lock de
# DB, etc.) antes de darlo por perdido. Antes, cualquier excepcion durante
# el procesamiento de un UID igual avanzaba sync_state.last_uid, asi que
# el correo (a veces una respuesta de cliente) se perdia para siempre sin
# reintento — nunca volvia a aparecer en detalle_ticket.html. Vive a nivel
# de modulo porque email_loop() en main.py llama a esta funcion una vez
# por ciclo de poll con una sesion de DB nueva cada vez; solo el proceso
# en memoria (no la DB) necesita recordar cuantos intentos van.
_uid_failure_counts: dict[str, int] = {}
MAX_UID_RETRIES = 3


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

    mail = None
    sync_state = None
    processed = 0

    try:
        try:
            imap_timeout = max(float(os.getenv("IMAP_TIMEOUT_SECONDS", "20") or 20), 5.0)
        except ValueError:
            imap_timeout = 20.0
        mail = imaplib.IMAP4_SSL(host, port, timeout=imap_timeout)
        mail.login(user, password)

        status, _ = mail.select(folder)
        if status != "OK":
            return {"count": 0}

        mailbox_key = _mailbox_key(user, folder)
        is_new_mailbox = db.get(EmailSyncState, mailbox_key) is None
        sync_state = _get_or_create_sync_state(db, mailbox_key)
        uid_validity = _parse_uid_validity(mail)

        # Primera conexión a este buzón: saltar historial, arrancar desde ahora.
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
            return {"count": 0, "skipped_initial_sync": True}

        if uid_validity and sync_state.uid_validity and sync_state.uid_validity != uid_validity:
            sync_state.last_uid = 0

        if uid_validity:
            sync_state.uid_validity = uid_validity
            db.commit()

        start_uid = max(int(sync_state.last_uid or 0) + 1, 1)
        search_status, data = mail.uid("search", None, f"UID {start_uid}:*")
        if search_status != "OK":
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

        support_mailboxes = _support_mailboxes()
        agent_emails = _agent_emails(db)
        inbound_mailbox = _normalize_email_address(user)

        # Mientras haya un UID mas viejo pendiente de reintento, el cursor
        # (sync_state.last_uid) no debe avanzar mas alla de el aunque UIDs
        # mas nuevos del mismo batch se procesen bien — si no, el proximo
        # poll ya no volveria a pedir ese UID y el correo se perderia igual,
        # solo que un batch mas tarde.
        stalled_uid: int | None = None

        for uid in uid_values:
            can_advance_cursor = stalled_uid is None
            fail_key = f"{mailbox_key}:{uid}"
            try:
                # BODY.PEEK[] (no RFC822) para no marcar el correo como leido
                # en el buzon real al descargarlo (pedido explicito, jul 2026).
                fetch_status, msg_data = mail.uid("fetch", str(uid), "(BODY.PEEK[])")
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
                        _uid_failure_counts.pop(fail_key, None)
                        if can_advance_cursor:
                            sync_state.last_uid = uid
                            db.commit()
                        continue

                from_name, from_email = parseaddr(msg.get("From"))
                from_name = _decode_mime_text(from_name)
                from_email = (from_email or "").strip().lower()
                if not from_email:
                    raise ValueError("Email sin remitente valido")

                if from_email in support_mailboxes:
                    _uid_failure_counts.pop(fail_key, None)
                    if can_advance_cursor:
                        sync_state.last_uid = uid
                        db.commit()
                    continue

                subject = _decode_subject(msg)
                html_body = _extract_html_and_save_images(msg)
                if html_body:
                    body = _sanitize_email_html(html_body)
                else:
                    body = _escape_plain_text_to_html(_extract_body_text(msg).strip())

                if not _html_has_visible_content(body):
                    plain_fallback = _extract_body_text(msg).strip()
                    if plain_fallback:
                        body = _escape_plain_text_to_html(plain_fallback)

                if not _html_has_visible_content(body):
                    # Ni la parte HTML ni el texto plano dejaron nada visible
                    # (payload vacio, MIME atipico de un correo automatico,
                    # etc.) — guardar "" acá deja el mensaje invisible en
                    # detalle_ticket.html sin ningun aviso (paso con un
                    # correo de alerta de Google, ago 2026). Mejor un
                    # placeholder visible que un hueco en blanco.
                    print(f"Email UID {uid} (msgid {message_id}) sin contenido visible tras extraer/sanitizar; se guarda placeholder")
                    body = "<p><em>Este correo no incluyo contenido de texto legible.</em></p>"

                agent_match = agent_emails.get(from_email)

                if agent_match:
                    # Correo desde el buzon propio de un agente (no via la
                    # ticketera): no crea ni matchea un Requester (eso
                    # dejaria su cuenta "enlazada" como cliente para
                    # siempre). Solo se adjunta si encaja en un hilo
                    # existente; si no, se descarta sin crear nada nuevo.
                    requester = None
                    ticket, ticket_exists = _resolve_ticket(
                        db,
                        msg=msg,
                        subject=subject,
                        body=body,
                        from_email=from_email,
                        support_mailboxes=support_mailboxes,
                        requester_id=None,
                        message_dt=message_dt,
                        inbound_mailbox=inbound_mailbox,
                        allow_create=False,
                    )
                    if ticket is None:
                        _uid_failure_counts.pop(fail_key, None)
                        if can_advance_cursor:
                            sync_state.last_uid = uid
                            db.commit()
                        continue
                else:
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
                        inbound_mailbox=inbound_mailbox,
                    )

                if not getattr(ticket, "inbound_mailbox", None):
                    ticket.inbound_mailbox = inbound_mailbox

                if ticket_exists and body:
                    body = _strip_ticket_thread_tail(
                        body,
                        support_mailboxes=support_mailboxes,
                        ticket_id=ticket.id,
                    )

                inbound_cc_recipients = _parse_header_recipients(
                    msg.get_all("Cc", []),
                    exclude={from_email, *support_mailboxes},
                )
                if inbound_cc_recipients:
                    body = f"{_email_recipient_marker(cc_recipients=inbound_cc_recipients)}{body}"

                db.add(
                    Message(
                        ticket_id=ticket.id,
                        sender_type="agent" if agent_match else "requester",
                        sender_id=agent_match[0] if agent_match else None,
                        channel="email",
                        content=body,
                        external_id=message_id,
                        is_internal_note=False,
                        sender_name=(agent_match[1] if agent_match else (from_name or from_email.split("@")[0])).strip() or None,
                        sender_email=from_email,
                        created_at=message_dt,
                    )
                )

                _uid_failure_counts.pop(fail_key, None)
                if can_advance_cursor:
                    sync_state.last_uid = uid
                    if uid_validity:
                        sync_state.uid_validity = uid_validity

                db.commit()
                processed += 1
                print(f"Email procesado -> Ticket #{ticket.id} (UID {uid})")

            except Exception as exc:
                db.rollback()
                attempts = _uid_failure_counts.get(fail_key, 0) + 1
                _uid_failure_counts[fail_key] = attempts

                if attempts < MAX_UID_RETRIES:
                    # Falla posiblemente transitoria: no tocamos el cursor,
                    # asi el proximo poll (email_loop, cada EMAIL_POLL_SECONDS)
                    # vuelve a pedir este mismo UID en vez de saltarselo.
                    print(
                        f"Error importando email UID {uid} "
                        f"(intento {attempts}/{MAX_UID_RETRIES}, se reintentara en el proximo poll): {exc}"
                    )
                    if stalled_uid is None:
                        stalled_uid = uid
                    continue

                # Se agotaron los reintentos: recien ahi lo damos por
                # perdido (a diferencia del comportamiento anterior, que
                # lo perdia silenciosamente en el primer intento fallido).
                print(f"Email UID {uid} descartado tras {attempts} intentos fallidos: {exc}")
                _uid_failure_counts.pop(fail_key, None)
                try:
                    if can_advance_cursor:
                        sync_state = _get_or_create_sync_state(db, mailbox_key)
                        sync_state.last_uid = uid
                        if uid_validity:
                            sync_state.uid_validity = uid_validity
                        db.commit()
                except Exception:
                    db.rollback()
                continue

        return {"count": processed, "last_uid": sync_state.last_uid if sync_state else None}

    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                try:
                    mail.shutdown()
                except Exception:
                    pass

