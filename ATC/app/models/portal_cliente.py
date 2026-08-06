from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ATC.app.core.db import Base


class PortalPersonaLogin(Base):
    """Login individual para el Portal Cliente de una persona autorizada o
    contacto de emergencia (identificado por su propio RUT, no el de la
    empresa). Se crea automáticamente en el primer ingreso exitoso con la
    clave por defecto (primeros 5 dígitos del RUT sin dígito verificador)."""

    __tablename__ = "portal_persona_login"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rut: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    nombre: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    password_changed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # RUT de la empresa (bbdd_clientes.rut) resuelto al momento del alta, para
    # no tener que recorrer personas/contactos en cada request.
    cliente_rut: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
