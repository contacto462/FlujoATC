from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, func
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
    __tablename__ = "bbdd_guardias"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rut: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class GuardiaJustificacion(Base):
    """Registro independiente de licencias/faltas/permisos/vacaciones de un
    guardia — no depende de que exista antes un registro en la tabla de
    supervisor (dia+recinto). Sirve como bitacora consultable aparte."""

    __tablename__ = "guardia_justificaciones"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rut: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    nombre_guardia: Mapped[str] = mapped_column(String(255), nullable=False)
    motivo: Mapped[str] = mapped_column(String(40), nullable=False)
    fecha_desde: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    fecha_hasta: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    creado_por: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class RecintoQrGenerado(Base):
    __tablename__ = "inicio_turno_qr_generados"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    recinto_id: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    recinto_label: Mapped[str] = mapped_column(String(255), nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    numero: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class RondaRegistro(Base):
    __tablename__ = "inicio_turno_ronda_registros"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    qr_generado_id: Mapped[int] = mapped_column(index=True, nullable=False)
    recinto_id: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    recinto_label: Mapped[str] = mapped_column(String(255), nullable=False)
    numero: Mapped[int] = mapped_column(Integer, nullable=False)
    rut_guardia: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    nombre_guardia: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    nota: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    aprobado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    aprobado_por: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    aprobado_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    registrado_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class SupervisorRegistro(Base):
    __tablename__ = "supervisor_registros"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    recinto: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    nombre_guardia: Mapped[str] = mapped_column(String(255), nullable=False)
    tipo_turno: Mapped[str] = mapped_column(String(80), nullable=False)
    supervisor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
