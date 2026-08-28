from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ATC.app.core.db import Base


class TicketSlaFeedback(Base):
    __tablename__ = "ticket_sla_feedback"

    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), primary_key=True)
    technician_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    resolution_satisfied: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # Pregunta 3 (opcional) de la encuesta publica — se guarda tal cual
    # aunque no haya calificacion/resolucion todavia, para poder mostrarla
    # en el detalle del ticket en el dashboard de soporte aunque el cliente
    # solo haya llenado esta — pedido explicito, ago 2026.
    observacion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

