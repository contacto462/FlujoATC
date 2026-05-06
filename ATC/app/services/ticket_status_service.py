from __future__ import annotations

from datetime import datetime, timezone
import unicodedata

from app.models.ticket import Ticket


def _normalize_status(status: str | None) -> str:
    raw = " ".join(str(status or "").strip().split())
    key = unicodedata.normalize("NFD", raw.casefold())
    key = "".join(ch for ch in key if unicodedata.category(ch) != "Mn")
    key = key.replace(" ", "_").replace("-", "_")
    return {
        "abierto": "open",
        "pendiente": "pending",
        "pendiente_servicio": "pending_service",
        "pendiente_cliente": "pending_client",
        "resuelto": "resolved",
        "resuelto_servicio": "resolved_service",
        "resuleto_servicio": "resolved_service",
        "resuelto_cliente": "resolved_client",
        "cerrado": "closed",
    }.get(key, key)


def apply_ticket_status_change(ticket: Ticket, new_status: str) -> dict[str, object]:
    # Centralizamos el cambio de estado para reutilizar exactamente
    # la misma regla desde rutas web, correo y automatizaciones.
    old_status = _normalize_status(ticket.status)
    new_status = _normalize_status(new_status)
    now = datetime.now(timezone.utc)

    ticket.status = new_status

    pending_statuses = {"pending", "pending_service", "pending_client"}
    resolved_statuses = {"resolved", "resolved_service", "resolved_client", "closed"}
    became_resolved = new_status in resolved_statuses and old_status not in resolved_statuses
    reopened_from_resolved = new_status in {"open", *pending_statuses} and old_status in resolved_statuses

    if new_status in resolved_statuses and ticket.resolved_at is None:
        # Guardamos la fecha solo cuando el ticket pasa a resuelto.
        ticket.resolved_at = now

    if reopened_from_resolved:
        # Si el ticket vuelve a abrirse, limpiamos la resolución previa.
        ticket.reopen_count = (ticket.reopen_count or 0) + 1
        ticket.resolved_at = None

    return {
        "old_status": old_status,
        "new_status": new_status,
        "changed_at": now,
        "became_resolved": became_resolved,
        "reopened_from_resolved": reopened_from_resolved,
    }


def mark_first_agent_reply(ticket: Ticket) -> None:
    # La primera respuesta del agente se fija una sola vez
    # para alimentar métricas de tiempo de primera respuesta.
    if ticket.first_agent_reply_at is None:
        ticket.first_agent_reply_at = datetime.now(timezone.utc)
