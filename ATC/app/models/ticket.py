from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import String, ForeignKey, DateTime, Boolean, Integer, func
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

    # =========================
    # SPAM / DELETE
    # =========================
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    is_spam: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    # =========================
    # RELACIONES
    # =========================
    requester_id: Mapped[int] = mapped_column(
        ForeignKey("requesters.id"),
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

    # =========================
    # REPRESENTACIÃ“N
    # =========================
    def __repr__(self) -> str:
        return f"<Ticket id={self.id} status={self.status}>"
    

