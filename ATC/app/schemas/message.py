from pydantic import BaseModel
from datetime import datetime

class MessageCreate(BaseModel):
    content: str
    is_internal_note: bool = False

class MessageOut(BaseModel):
    id: int
    ticket_id: int
    sender_type: str
    channel: str
    content: str
    is_internal_note: bool
    created_at: datetime

    class Config:
        from_attributes = True