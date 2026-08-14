from __future__ import annotations

import hashlib
import hmac
import json
import traceback
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from ATC.app.core.config import settings
from ATC.app.core.db import get_db
from ATC.app.core.timeutil import to_chile_naive
from ATC.app.models.message import Message
from ATC.app.models.requester import Requester
from ATC.app.models.ticket import Ticket
from ATC.app.services.ticket_status_service import apply_ticket_status_change


router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


# Tiempo maximo (segundos) sin actividad para reusar el ultimo ticket
# de un mismo numero. Si excede, abrimos uno nuevo.
THREAD_REUSE_WINDOW_SECONDS = 60 * 60 * 24 * 3  # 3 dias


def _normalize_phone(raw: str | None) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return digits


def _short_preview(text_value: str, *, limit: int = 60) -> str:
    cleaned = " ".join(str(text_value or "").split())
    if not cleaned:
        return "Mensaje de WhatsApp"
    return cleaned[: limit - 1] + "…" if len(cleaned) > limit else cleaned


def _resolve_requester(db: Session, *, phone: str, profile_name: str) -> Requester:
    requester = (
        db.query(Requester)
        .filter(Requester.phone == phone)
        .first()
    )
    if requester:
        if profile_name and (not requester.name or requester.name.strip() == phone):
            requester.name = profile_name
        return requester

    requester = Requester(
        phone=phone,
        name=(profile_name or phone).strip() or phone,
    )
    db.add(requester)
    db.flush()
    return requester


def _resolve_ticket(
    db: Session,
    *,
    requester: Requester,
    subject_preview: str,
    received_at: datetime,
) -> Ticket:
    # Reusamos el ultimo ticket abierto del mismo numero si esta dentro
    # de la ventana de continuidad, para no abrir uno por cada mensaje.
    open_ticket = (
        db.query(Ticket)
        .filter(
            Ticket.requester_id == requester.id,
            Ticket.source == "whatsapp",
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
            Ticket.status.in_(["open", "pending"]),
        )
        .order_by(Ticket.updated_at.desc())
        .first()
    )
    if open_ticket:
        return open_ticket

    last_ticket = (
        db.query(Ticket)
        .filter(
            Ticket.requester_id == requester.id,
            Ticket.source == "whatsapp",
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
        )
        .order_by(Ticket.updated_at.desc())
        .first()
    )
    if last_ticket and last_ticket.updated_at is not None:
        # received_at ya viene naive (hora de Chile, ver to_chile_naive en el
        # caller) — updated_at puede volver naive o aware según el driver;
        # se despoja el tzinfo de ambos lados antes de restar para no
        # reventar con "can't subtract offset-naive and offset-aware
        # datetimes". Es una ventana de 3 días, no hace falta precisión al
        # segundo.
        updated_at_naive = last_ticket.updated_at.replace(tzinfo=None)
        received_at_naive = received_at.replace(tzinfo=None) if received_at.tzinfo else received_at
        delta = received_at_naive - updated_at_naive
        if delta.total_seconds() <= THREAD_REUSE_WINDOW_SECONDS:
            if last_ticket.status == "resolved":
                apply_ticket_status_change(last_ticket, "open")
            return last_ticket

    ticket = Ticket(
        subject=subject_preview,
        source="whatsapp",
        priority="",
        requester_id=requester.id,
        status="open",
        created_at=received_at,
    )
    db.add(ticket)
    db.flush()
    return ticket


def _extract_text_content(message: dict) -> str:
    msg_type = (message.get("type") or "").strip().lower()

    if msg_type == "text":
        return str((message.get("text") or {}).get("body") or "").strip()

    if msg_type in {"image", "video", "audio", "document", "sticker"}:
        media = message.get(msg_type) or {}
        caption = str(media.get("caption") or "").strip()
        media_id = str(media.get("id") or "").strip()
        prefix = f"[Adjunto {msg_type}{(': ' + media_id) if media_id else ''}]"
        return f"{prefix} {caption}".strip()

    if msg_type == "location":
        loc = message.get("location") or {}
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        name = loc.get("name") or ""
        return f"[Ubicacion {lat},{lng}] {name}".strip()

    if msg_type == "interactive":
        interactive = message.get("interactive") or {}
        sub_type = interactive.get("type") or ""
        reply = interactive.get(sub_type) or {}
        return str(reply.get("title") or reply.get("id") or "").strip() or "[Respuesta interactiva]"

    if msg_type == "button":
        return str((message.get("button") or {}).get("text") or "").strip() or "[Boton]"

    if msg_type == "reaction":
        emoji = (message.get("reaction") or {}).get("emoji") or ""
        return f"[Reaccion {emoji}]".strip()

    return f"[Mensaje WhatsApp tipo {msg_type or 'desconocido'} no soportado]"


@router.get("/")
def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
):
    # Meta llama este endpoint al configurar el webhook.
    # Hay que devolver hub.challenge como texto plano cuando el token coincide.
    if hub_mode != "subscribe" or not hub_challenge:
        return {"message": "whatsapp endpoint funcionando"}

    expected = (settings.WA_VERIFY_TOKEN or "").strip()
    if not expected or hub_verify_token != expected:
        raise HTTPException(status_code=403, detail="Verify token invalido")

    return PlainTextResponse(content=hub_challenge, status_code=200)


def _verify_meta_signature(raw_body: bytes, signature_header: str | None) -> bool:
    app_secret = (settings.WA_APP_SECRET or "").strip()
    if not app_secret:
        # Sin secreto configurado no hay forma de distinguir un POST real de
        # Meta de uno fabricado — fail closed en vez de aceptar cualquier cosa.
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header, expected)


@router.post("/")
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
    # Meta espera siempre 200 dentro de 20s. Si tardamos o devolvemos error
    # vuelve a reintentar con el mismo payload (duplicados).
    raw_body = await request.body()
    if not _verify_meta_signature(raw_body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="Firma invalida")
    try:
        payload = json.loads(raw_body)
    except Exception:
        return {"status": "ignored", "reason": "payload no JSON"}

    processed = 0
    errors: list[str] = []

    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}

            # Indices contacto -> nombre desde el bloque 'contacts'
            contacts_by_wa_id: dict[str, str] = {}
            for contact in value.get("contacts") or []:
                wa_id = str(contact.get("wa_id") or "").strip()
                profile = contact.get("profile") or {}
                profile_name = str(profile.get("name") or "").strip()
                if wa_id:
                    contacts_by_wa_id[wa_id] = profile_name

            for message in value.get("messages") or []:
                external_id = str(message.get("id") or "").strip()
                if not external_id:
                    continue

                exists = (
                    db.query(Message)
                    .filter(Message.external_id == external_id)
                    .first()
                )
                if exists:
                    continue

                raw_from = str(message.get("from") or "").strip()
                phone = _normalize_phone(raw_from)
                if not phone:
                    continue

                try:
                    ts_raw = str(message.get("timestamp") or "").strip()
                    received_at = (
                        datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
                        if ts_raw.isdigit()
                        else datetime.now(timezone.utc)
                    )
                except Exception:
                    received_at = datetime.now(timezone.utc)
                # Meta manda el timestamp en UTC real — convertir a hora de
                # Chile naive acá, antes de usarlo tanto para la ventana de
                # reuso de ticket como para guardarlo en Message/Ticket.
                # created_at (que en el resto de la app son GETDATE() del
                # SQL Server, hora de Chile mal-etiquetada como UTC). Antes
                # esto quedaba en UTC real y mostraba la hora del mensaje de
                # WhatsApp corrida 3-4 horas en detalle_ticket.html.
                received_at = to_chile_naive(received_at)

                content = _extract_text_content(message)
                profile_name = contacts_by_wa_id.get(raw_from) or contacts_by_wa_id.get(phone) or ""

                try:
                    requester = _resolve_requester(db, phone=phone, profile_name=profile_name)
                    ticket = _resolve_ticket(
                        db,
                        requester=requester,
                        subject_preview=_short_preview(content),
                        received_at=received_at,
                    )

                    db.add(
                        Message(
                            ticket_id=ticket.id,
                            sender_type="requester",
                            channel="whatsapp",
                            content=content,
                            external_id=external_id,
                            is_internal_note=False,
                            sender_name=(profile_name or phone).strip() or None,
                            created_at=received_at,
                        )
                    )
                    db.commit()
                    processed += 1
                    print(f"WhatsApp procesado -> Ticket #{ticket.id} (id {external_id})")

                except Exception as exc:
                    db.rollback()
                    errors.append(str(exc))
                    print(f"Error procesando wa id {external_id}: {exc}")
                    print(traceback.format_exc())

    return {"status": "ok", "processed": processed, "errors": errors[:5]}
