from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from Incidencias.app.database import Base


class VentaCliente(Base):
    __tablename__ = "venta_clientes"

    id: Mapped[int] = mapped_column(primary_key=True)
    rut: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    razon_social: Mapped[str] = mapped_column(String(255), nullable=False)
    giro: Mapped[str] = mapped_column(String(255), nullable=False)
    direccion: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str] = mapped_column(String(120), nullable=False)
    comuna: Mapped[str] = mapped_column(String(120), nullable=False)
    email_facturas: Mapped[str] = mapped_column(String(255), nullable=False)
    nombre_representante: Mapped[str] = mapped_column(String(255), nullable=False)
    rut_representante: Mapped[str] = mapped_column(String(32), nullable=False)
    telefono: Mapped[str] = mapped_column(String(32), nullable=False)
    email_representante: Mapped[str] = mapped_column(String(255), nullable=False)
    ejecutivo_email: Mapped[str] = mapped_column(String(255), nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index("ix_venta_clientes_rut", VentaCliente.rut, unique=True)

