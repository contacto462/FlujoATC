from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from ATC.app.core.config import settings
from ATC.app.models.automation_log import AutomationLog
from ATC.app.models.message import Message
from ATC.app.models.requester import Requester
from ATC.app.models.ticket import Ticket
from ATC.app.services.ticket_status_service import apply_ticket_status_change


RULE_PENDING_AUTO_CLOSE = "pending_auto_close"
RULE_EMAIL_AUTO_REPLY = "email_auto_reply"

# Ruta absoluta del logo (la cuenta no depende del directorio de trabajo).
_LOGO_ATC_PATH = str(Path(__file__).resolve().parents[2] / "static" / "img" / "logo-atc.png")


def log_automation_event(
    db: Session,
    *,
    rule_key: str,
    event_name: str,
    status: str = "ok",
    ticket_id: int | None = None,
    details: dict | None = None,
) -> AutomationLog:
    # Guardamos cada ejecuciÃ³n para poder auditar quÃ© hizo
    # la automatizaciÃ³n y diagnosticar fallos despuÃ©s.
    row = AutomationLog(
        ticket_id=ticket_id,
        rule_key=rule_key,
        event_name=event_name,
        status=status,
        details=details or {},
    )
    db.add(row)
    db.flush()
    return row


def add_system_internal_note(
    db: Session,
    *,
    ticket_id: int,
    content: str,
) -> Message:
    # Reutilizamos el modelo Message para las notas automÃ¡ticas
    # del sistema, en vez de crear otro mecanismo paralelo.
    note = Message(
        ticket_id=ticket_id,
        sender_type="system",
        channel="internal",
        content=content,
        is_internal_note=True,
    )
    db.add(note)
    db.flush()
    return note


def close_ticket_for_inactivity(
    db: Session,
    *,
    ticket: Ticket,
    cutoff_at: datetime,
) -> None:
    # Aplicamos el mismo cambio de estado reutilizable que usa
    # el resto del sistema, para no duplicar reglas de negocio.
    change = apply_ticket_status_change(ticket, "resolved")

    add_system_internal_note(
        db,
        ticket_id=ticket.id,
        content=(
            "Ticket cerrado automaticamente por inactividad del cliente. "
            f"Ultima espera del agente anterior a {cutoff_at.strftime('%d-%m-%Y %H:%M UTC')}."
        ),
    )

    log_automation_event(
        db,
        rule_key=RULE_PENDING_AUTO_CLOSE,
        event_name="scheduled_check",
        ticket_id=ticket.id,
        details={
            "old_status": change["old_status"],
            "new_status": change["new_status"],
            "cutoff_at": cutoff_at.isoformat(),
        },
    )


def run_pending_auto_close(db: Session) -> dict[str, int]:
    # Esta regla cierra tickets en pending cuando el ultimo mensaje
    # visible del hilo lo enviÃ³ un agente y ya pasaron X dÃ­as.
    now = datetime.now(timezone.utc)
    cutoff_at = now - timedelta(days=max(int(settings.AUTOMATION_PENDING_CLOSE_DAYS or 3), 1))

    latest_message_subquery = (
        db.query(
            Message.ticket_id.label("ticket_id"),
            func.max(Message.created_at).label("latest_created_at"),
        )
        .group_by(Message.ticket_id)
        .subquery()
    )

    candidates = (
        db.query(Ticket, Message)
        .join(
            latest_message_subquery,
            latest_message_subquery.c.ticket_id == Ticket.id,
        )
        .join(
            Message,
            (Message.ticket_id == latest_message_subquery.c.ticket_id)
            & (Message.created_at == latest_message_subquery.c.latest_created_at),
        )
        .filter(
            Ticket.status == "pending",
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
            Message.sender_type == "agent",
            Message.is_internal_note == False,
            Message.created_at <= cutoff_at,
        )
        .all()
    )

    processed = 0

    for ticket, last_message in candidates:
        try:
            # Aislamos cada ticket en su propia subtransaccion para que
            # un fallo puntual no rompa el lote completo.
            with db.begin_nested():
                close_ticket_for_inactivity(
                    db,
                    ticket=ticket,
                    cutoff_at=last_message.created_at or cutoff_at,
                )
            processed += 1
        except Exception as exc:
            # Si una regla falla, registramos el error y seguimos con el resto.
            log_automation_event(
                db,
                rule_key=RULE_PENDING_AUTO_CLOSE,
                event_name="scheduled_check",
                status="error",
                ticket_id=ticket.id,
                details={"error": str(exc)},
            )

    db.commit()
    return {"processed": processed}


def send_initial_email_auto_reply(
    db: Session,
    *,
    ticket: Ticket,
    requester: Requester | None,
    in_reply_to_external_id: str | None = None,
    event_name: str = "ticket_created",
) -> bool:
    # Solo respondemos automÃ¡ticamente tickets nuevos por email.
    if not bool(settings.AUTOMATION_EMAIL_AUTO_REPLY_ENABLED):
        return False

    if (ticket.source or "").strip().lower() != "email":
        return False

    requester_email = (requester.email if requester else "") or ""
    requester_email = requester_email.strip()
    if not requester_email:
        return False

    existing_auto_reply = (
        db.query(Message)
        .filter(
            Message.ticket_id == ticket.id,
            Message.sender_type == "system",
            Message.channel == "email",
        )
        .first()
    )
    if existing_auto_reply:
        # Evitamos duplicar la confirmacion si el ticket ya la tenia enviada.
        return False

    requester_name = ((requester.name if requester else "") or "Cliente").strip() or "Cliente"
    logo_cid = "logo-atc-auto-reply"

    # Priorizamos el Message-ID del correo entrante que gatillÃ³
    # este ticket para asegurar que viaje en el mismo hilo.
    in_reply_to = (in_reply_to_external_id or "").strip() or None
    references = in_reply_to
    if not in_reply_to:
        latest_requester_email_message = (
            db.query(Message)
            .filter(
                Message.ticket_id == ticket.id,
                Message.sender_type == "requester",
                Message.channel == "email",
                Message.external_id.isnot(None),
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .first()
        )
        in_reply_to = latest_requester_email_message.external_id if latest_requester_email_message else None
        references = in_reply_to

    # Usamos el mismo asunto del ticket para que el correo viaje en el
    # mismo hilo de la solicitud original del cliente.
    if (ticket.subject or "").strip().lower().startswith("re:"):
        subject = ticket.subject
    else:
        subject = f"Re: {ticket.subject}"
    body = f"""
    <div style="margin:0;padding:24px;background:#f8fafc;">
      <div style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:24px;overflow:hidden;font-family:Arial,sans-serif;color:#0f172a;">
        <div style="padding:24px 28px;background:linear-gradient(135deg,#0f172a 0%,#1d4ed8 100%);color:#ffffff;">
          <table role="presentation" cellspacing="0" cellpadding="0" width="100%" style="border-collapse:collapse;">
            <tr>
              <td style="vertical-align:top;padding-right:16px;">
                <div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#e2e8f0;">Soporte ATC</div>
                <h1 style="margin:10px 0 0;font-size:27px;line-height:1.2;color:#ffffff;">Hemos recibido su solicitud</h1>
                <p style="margin:10px 0 0;font-size:15px;line-height:1.6;color:#dbeafe;">Ticket #{ticket.id}</p>
              </td>
              <td align="right" style="vertical-align:top;">
                <img src="cid:{logo_cid}" alt="ATC" style="display:block;width:110px;max-width:110px;height:auto;">
              </td>
            </tr>
          </table>
        </div>
        <div style="padding:28px;">
          <p style="margin:0 0 16px;font-size:16px;line-height:1.7;">Hola {requester_name},</p>
          <p style="margin:0 0 14px;font-size:16px;line-height:1.7;">Le confirmamos que su solicitud fue recibida correctamente e ingresó a nuestra plataforma de soporte.</p>
          <p style="margin:0 0 14px;font-size:16px;line-height:1.7;">Nuestro equipo revisará su caso y se contactará con usted a la brevedad. No responda este correo ni duplique la solicitud, esto podría causar retrasos en la atención.</p>
          <p style="margin:22px 0 0;font-size:15px;line-height:1.7;">Gracias por contactar con el Soporte de Alguien te cuida.</p>
        </div>
      </div>
    </div>
    """

    # Documento completo con color-scheme para que los clientes (Apple Mail,
    # etc.) no inviertan los colores en modo oscuro y se respete el diseno.
    email_html = (
        '<!DOCTYPE html><html lang="es"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        '<meta name="color-scheme" content="light dark">'
        '<meta name="supported-color-schemes" content="light dark">'
        '</head><body style="margin:0;padding:0;background:#f8fafc;">'
        f'{body}'
        '</body></html>'
    )

    from ATC.app.integrations.email_smtp import send_email_reply

    outgoing_message_id = send_email_reply(
        to=requester_email,
        subject=subject,
        body=email_html,
        in_reply_to=in_reply_to,
        references=references,
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
            sender_type="system",
            channel="email",
            content=body,
            external_id=outgoing_message_id or None,
            # Lo guardamos para trazabilidad tecnica, pero no debe salir
            # en el chat visible del ticket.
            is_internal_note=True,
        )
    )

    log_automation_event(
        db,
        rule_key=RULE_EMAIL_AUTO_REPLY,
        event_name=event_name,
        ticket_id=ticket.id,
        details={"to": requester_email},
    )
    return True

