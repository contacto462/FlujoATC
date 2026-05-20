from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ATC.app.core.db import Base


class InternalChatReadState(Base):
    __tablename__ = "internal_chat_read_states"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        primary_key=True,
    )

    last_seen_message_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=True,
    )

    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return (
            "<InternalChatReadState "
            f"user_id={self.user_id} "
            f"last_seen_message_id={self.last_seen_message_id}>"
        )

