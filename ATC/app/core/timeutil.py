from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ATC.app.core.config import settings

_TZ_CHILE = ZoneInfo(settings.timezone or "America/Santiago")


def chile_now() -> datetime:
    """Hora actual en Chile, naive (sin tzinfo).

    Se guarda naive a propósito: `Message.created_at`/`Ticket.created_at` son
    columnas `DateTime(timezone=True)` sobre SQL Server, que en la práctica
    terminan como DATETIMEOFFSET con el offset que SQLAlchemy les ponga —
    mezclar naive/aware entre los distintos puntos donde se crean mensajes
    (ticket_service, automation_service, web.py, whatsapp_webhook, etc.)
    es lo que hacía que la hora mostrada en detalle_ticket.html saliera
    incorrecta para algunos canales (ej. WhatsApp guardaba UTC real en vez
    de hora de Chile). Todos los puntos de creación deben usar esta función
    en vez de dejar que el default de la columna use GETDATE() del server
    (cuyo huso horario no está garantizado) o de armar el datetime a mano.
    """
    return datetime.now(_TZ_CHILE).replace(tzinfo=None)


def to_chile_naive(dt: datetime) -> datetime:
    """Convierte un datetime real (ej. el timestamp que manda Meta para un
    mensaje de WhatsApp, que sí es UTC genuino) a hora de Chile naive.

    A diferencia de `chile_now()` (que arranca de "ahora"), esto convierte
    un instante ya conocido — si `dt` no tiene tzinfo se asume que ya es
    UTC (los `datetime.now(timezone.utc)` / `datetime.fromtimestamp(..,
    tz=timezone.utc)` de este mismo módulo)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TZ_CHILE).replace(tzinfo=None)
