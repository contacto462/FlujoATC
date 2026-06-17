from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ATC.app.core.db import Base


class Incidencia(Base):
    __tablename__ = "incidencias_importadas"
    __table_args__ = (
        # Evita duplicados al reimportar el mismo archivo.
        UniqueConstraint("source_file", "source_row", name="uq_incidencias_source_row"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    odt: Mapped[str | None] = mapped_column(String(50), index=True, nullable=True)
    fecha: Mapped[str | None] = mapped_column(String(60), nullable=True)
    puesto: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sucursal: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    problema: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    derivacion: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacion: Mapped[str | None] = mapped_column(Text, nullable=True)
    tecnico: Mapped[str | None] = mapped_column(String(140), index=True, nullable=True)
    estado: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    cantidad_dias_ejecucion: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fecha_cierre: Mapped[str | None] = mapped_column(String(60), nullable=True)
    fecha_derivacion_area: Mapped[str | None] = mapped_column(String(60), nullable=True)
    fecha_derivacion_tecnico: Mapped[str | None] = mapped_column(String(60), nullable=True)
    direccion: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacion_final: Mapped[str | None] = mapped_column(Text, nullable=True)
    prioridad: Mapped[str | None] = mapped_column(Text, nullable=True)
    materiales: Mapped[str | None] = mapped_column(Text, nullable=True)
    acompanante: Mapped[str | None] = mapped_column(String(140), nullable=True)
    estado_avance: Mapped[str | None] = mapped_column(String(120), nullable=True)
    observaciones_avance: Mapped[str | None] = mapped_column(Text, nullable=True)
    estado_agrupado: Mapped[str | None] = mapped_column(String(120), nullable=True)
    categoria: Mapped[str | None] = mapped_column(String(120), nullable=True)

    source_file: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source_row: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

