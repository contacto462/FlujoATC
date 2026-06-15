from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from ATC.app.core.db import Base


class IncidenciaImagen(Base):
    __tablename__ = "incidencias_imagenes"

    id: Mapped[int] = mapped_column(primary_key=True)
    odt: Mapped[str] = mapped_column(String(80), index=True, nullable=False, unique=True)
    sucursal: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    imagenes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[str | None] = mapped_column(String(180), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

