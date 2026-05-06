from pydantic import BaseModel
from datetime import datetime


# =========================
# CREATE
# =========================

class TicketCreate(BaseModel):
    subject: str
    source: str  # "email" | "whatsapp"
    priority: str = ""
    requester_id: int
    assigned_to_id: int | None = None


# =========================
# UPDATE (opcional pero recomendado)
# =========================

class TicketUpdate(BaseModel):
    subject: str | None = None
    status: str | None = None
    priority: str | None = None
    assigned_to_id: int | None = None


# =========================
# OUTPUT
# =========================

class TicketOut(BaseModel):
    id: int
    subject: str
    status: str
    priority: str
    source: str
    requester_id: int
    assigned_to_id: int | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True