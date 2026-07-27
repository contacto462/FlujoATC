from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ATC.app.core.db import Base


class SolicitudCompra(Base):
    __tablename__ = "solicitudes_compra"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    empresa: Mapped[str] = mapped_column(String(10))  # ATC | VL
    centro: Mapped[str] = mapped_column(String(120))
    fecha_solicitud: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now())
    solicitante_email: Mapped[str] = mapped_column(String(255))
    solicitante_nombre: Mapped[str] = mapped_column(String(255))
    items_json: Mapped[str] = mapped_column(Text)
    motivo: Mapped[str] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(String(60), default="Pendiente Operaciones", index=True)
    observaciones: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_compra: Mapped[int] = mapped_column(Integer, default=0)
    presupuesto_1: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    presupuesto_2: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    presupuesto_3: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # UUID used in email approval/rejection links; rotated after each decision
    decision_token: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    @property
    def items(self) -> list[dict]:
        try:
            return json.loads(self.items_json or "[]")
        except Exception:
            return []

    @property
    def presupuestos(self) -> list[str]:
        return [p for p in (self.presupuesto_1, self.presupuesto_2, self.presupuesto_3) if p]
