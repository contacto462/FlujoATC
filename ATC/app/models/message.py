from typing import Optional
from datetime import datetime

from sqlalchemy import String, ForeignKey, DateTime, func, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ATC.app.core.db import Base

class Message(Base):
    __tablename__ = "messages"

    # ==============================
    # ðŸ”‘ IDENTIFICACIÃ“N
    # ==============================
    id: Mapped[int] = mapped_column(primary_key=True)

    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id"),
        index=True,
        nullable=False,
    )

    # ==============================
    # ðŸ‘¤ QUIÃ‰N ENVÃA EL MENSAJE
    # ==============================
    sender_type: Mapped[str] = mapped_column(
        String(20)
    )  # requester | agent | system

    # ðŸ”¥ NUEVO: usuario real que enviÃ³ el mensaje
    sender_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    # RelaciÃ³n al usuario (si es agent)
    sender = relationship("User", foreign_keys=[sender_id])

    # Identidad visible del remitente por mensaje.
    # Permite mostrar correctamente respuestas de personas en CC.
    sender_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    sender_email: Mapped[Optional[str]] = mapped_column(
        String(320),
        nullable=True,
    )

    # ==============================
    # ðŸ“¡ CANAL
    # ==============================
    channel: Mapped[str] = mapped_column(
        String(20)
    )  # email | whatsapp | internal

    # ==============================
    # ðŸ“ CONTENIDO
    # ==============================
    content: Mapped[str] = mapped_column(Text)

    # ID externo (email message-id o whatsapp id)
    external_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    # Nota interna (no visible al cliente)
    is_internal_note: Mapped[bool] = mapped_column(
        Boolean,
        server_default="false",
        nullable=False,
    )

    # ==============================
    # ðŸ•’ FECHA
    # ==============================
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # ==============================
    # ðŸ”— RELACIONES
    # ==============================
    ticket = relationship("Ticket", back_populates="messages")

    # ==============================
    # ðŸ§  DEBUG
    # ==============================
    def __repr__(self) -> str:
        return (
            f"<Message id={self.id} "
            f"ticket_id={self.ticket_id} "
            f"sender_type={self.sender_type} "
            f"sender_id={self.sender_id} "
            f"sender_email={self.sender_email}>"
        )

