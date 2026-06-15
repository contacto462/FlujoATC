from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from ATC.app.core.db import Base


class TicketMessageReadState(Base):
    __tablename__ = "ticket_message_read_states"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        primary_key=True,
        index=True,
    )
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id"),
        primary_key=True,
        index=True,
    )
    last_seen_message_id: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

