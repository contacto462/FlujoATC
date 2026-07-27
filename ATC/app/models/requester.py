from __future__ import annotations

from typing import List, Optional
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Text

from ATC.app.core.db import Base
from ATC.app.models.ticket import Ticket


class Requester(Base):
    __tablename__ = "clientes"

    # =========================
    # ðŸ†” IDENTIFICACIÃ“N
    # =========================
    id: Mapped[int] = mapped_column(primary_key=True)

    # =========================
    # ðŸ‘¤ INFORMACIÃ“N DEL CLIENTE
    # =========================
    email: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    phone: Mapped[Optional[str]] = mapped_column(
        String(32),
        nullable=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    # Alias interno visible para el equipo de soporte.
    internal_name: Mapped[Optional[str]] = mapped_column(
        String(120),
        nullable=True,
        default=None,
    )

    # =========================
    # ðŸ“ NOTAS INTERNAS DEL CLIENTE
    # =========================
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        default=None
    )

    # =========================
    # ðŸŽ« RELACIÃ“N CON TICKETS
    # =========================
    tickets: Mapped[List["Ticket"]] = relationship(
        "Ticket",
        back_populates="requester",
        cascade="all, delete-orphan",
    )

    @property
    def display_name(self) -> str:
        alias = (self.internal_name or "").strip()
        if alias:
            return alias
        base_name = (self.name or "").strip()
        return base_name or "Cliente"

    # =========================
    # ðŸ§¾ REPRESENTACIÃ“N DEBUG
    # =========================
    def __repr__(self) -> str:
        return (
            f"<Requester id={self.id} "
            f"email={self.email} "
            f"name={self.name} "
            f"internal_name={self.internal_name}>"
        )

