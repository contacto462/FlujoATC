from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ATC.app.core.db import Base


class Suscripcion(Base):
    """Registro de Suscripción de Finanzas — importado una vez desde el CSV
    histórico de Google Sheets (ver ATC/scripts/_importar_suscripciones_csv.py)
    a esta tabla, que es la fuente de verdad real; ya no se lee el CSV en
    vivo en cada carga de la página.

    "codigo" es el id único de la suscripción (ej. "sub_XXXX"), igual al
    que traía el CSV — se usa para reimportar sin duplicar (upsert).

    "sucursal_id" es la sucursal de ATC ya resuelta (por el cruce RUT +
    dirección exacta al importar, o asignada a mano después desde la
    página) — al tenerla como columna real, el cruce de "Cantidad Cámaras"
    contra bbdd_sucursales (solo para Servicio = Televigilancia) se hace
    con un JOIN simple en vez de reprocesar el CSV en cada request."""

    __tablename__ = "suscripcion"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    codigo: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    rut: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    nombre_cliente: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    link_piriod: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    servicio: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    inicio_servicio: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    cantidad_camaras: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    moneda: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    valor_neto_mensual: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    descuento: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    internet: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    valor_neto_televigilancia: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    valor_neto_total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    valor_por_camara: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    direccion: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    comuna: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    direccion_completa: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    nombre_sucursal: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    estado: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    fecha_termino: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    sucursal_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    sucursal_asignada_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
