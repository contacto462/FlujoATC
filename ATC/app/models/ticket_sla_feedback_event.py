from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ATC.app.core.db import Base


class TicketSlaFeedbackEvent(Base):
    __tablename__ = "ticket_sla_feedback_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tickets.id"),
        nullable=True,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="fillout")
    technician_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    resolution_satisfied: Mapped[Optional[bool]] = mapped_column(nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

