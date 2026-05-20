from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ATC.app.core.db import Base


class EmailSyncState(Base):
    __tablename__ = "email_sync_states"

    mailbox_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    last_uid: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    uid_validity: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

