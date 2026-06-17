from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ATC.app.core.db import Base


class InicioTurnoRegistro(Base):
    __tablename__ = "inicio_turno_registros"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rut: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    nombre_guardia: Mapped[str] = mapped_column(String(255), nullable=False)
    tipo_turno: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    recinto: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    sucursal_id: Mapped[Optional[int]] = mapped_column(nullable=True, index=True)
    latitud: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitud: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    precision_metros: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ubicacion_estado: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    ip_origen: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    registrado_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class InicioTurnoGuardia(Base):
    __tablename__ = "inicio_turno_guardias"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rut: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
