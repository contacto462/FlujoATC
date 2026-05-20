from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ATC.app.core.db import Base


class InternalChatMessage(Base):
    __tablename__ = "internal_chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)

    sender_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    content: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    sender = relationship("User", foreign_keys=[sender_id])

    def __repr__(self) -> str:
        return f"<InternalChatMessage id={self.id} sender_id={self.sender_id}>"

