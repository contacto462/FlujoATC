from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import String, ForeignKey, DateTime, Boolean, Integer, Text as SAText, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ATC.app.core.db import Base

from ATC.app.models.user import User

class Ticket(Base):
    __tablename__ = "tickets"

    # =========================
    # CAMPOS PRINCIPALES
    # =========================
    id: Mapped[int] = mapped_column(primary_key=True)

    subject: Mapped[str] = mapped_column(
        String(300),
        nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="open",
        nullable=False
    )

    priority: Mapped[str] = mapped_column(
        String(20),
        default="",
        nullable=False
    )

    source: Mapped[str] = mapped_column(
        String(20),
        default="email",
        server_default="email",
        nullable=False
    )# email | whatsapp | internal

    inbound_mailbox: Mapped[Optional[str]] = mapped_column(
        String(320),
        nullable=True,
        index=True,
    )

    # =========================
    # SPAM / DELETE
    # =========================
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    # Cuando se movio a la papelera — usado por la purga automatica (15
    # dias) en automation_loop(), ver main.py — pedido explicito, ago 2026.
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    is_spam: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    is_no_ticket: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        server_default="0",
    )

    # Marca permanente: en algun momento se asigno a todo el equipo (via
    # "Asignar a todo el equipo"). No se limpia al ser tomado por alguien —
    # sirve para que el usuario con visibilidad total (Ronald Montilla) lo
    # siga viendo aunque otro agente ya lo haya tomado (pedido explicito,
    # jul 2026).
    team_broadcast_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # =========================
    # RELACIONES
    # =========================
    requester_id: Mapped[int] = mapped_column(
        ForeignKey("clientes.id"),
        nullable=False
    )

    assigned_to_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"),
        nullable=True
    )

    # =========================
    # FECHAS BASE
    # =========================
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # =========================
    # MÃ‰TRICAS / ANALÃTICA
    # =========================
    first_agent_reply_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    # A diferencia de resolved_at (que se limpia al reabrir el ticket para
    # reflejar el estado ACTUAL), estas dos nunca se borran: guardan cuándo
    # fue la última resolución y la última reapertura, para que el
    # historial pueda mostrar que el ticket sí se resolvió aunque después
    # el cliente lo haya reabierto.
    last_resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    last_reopened_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    reopen_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )

    # =========================
    # RELATIONSHIPS
    # =========================
    requester = relationship(
        "Requester",
        back_populates="tickets"
    )

    assigned_to = relationship(
        "User",
        back_populates="assigned_tickets"
    )

    messages: Mapped[List["Message"]] = relationship(
        "Message",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    checklist_items: Mapped[List["TicketChecklistItem"]] = relationship(
        "TicketChecklistItem",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketChecklistItem.orden",
    )

    # =========================
    # REPRESENTACIÃ“N
    # =========================
    def __repr__(self) -> str:
        return f"<Ticket id={self.id} status={self.status}>"



class TicketChecklistItem(Base):
    """Checklist embebido en UN solo ticket, para trabajo masivo (ej.
    'reconfigurar los 29 puestos' o 'revisar todos los clientes'): en vez
    de crear un ticket por puesto/cliente/sucursal, se crea un unico
    ticket con un item de checklist por cada uno seleccionado, marcable
    de a uno y con un % de avance — pedido explicito, ago 2026."""

    __tablename__ = "ticket_checklist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False, index=True)

    etiqueta: Mapped[str] = mapped_column(String(255), nullable=False)
    # Comentario libre por item (ej. "falta el cable UTP, pendiente de
    # bodega") — un solo comentario editable por item, no un hilo.
    comentario: Mapped[Optional[str]] = mapped_column(SAText, nullable=True)
    # "puesto" | "cliente" | "sucursal" — de donde salio el item, solo
    # informativo (no cambia el comportamiento del checklist).
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    # Numero de puesto, o id de bbdd_clientes / bbdd_sucursales segun tipo.
    referencia_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    orden: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    completado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completado_en: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completado_por_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket = relationship("Ticket", back_populates="checklist_items")
    completado_por = relationship("User")

    def __repr__(self) -> str:
        return f"<TicketChecklistItem id={self.id} ticket_id={self.ticket_id} completado={self.completado}>"
