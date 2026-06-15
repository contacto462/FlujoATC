from ATC.app.workers.celery_app import celery_app
from sqlalchemy.orm import Session

from ATC.app.core.db import SessionLocal
from ATC.app.models.ticket import Ticket
from ATC.app.models.message import Message
from ATC.app.models.requester import Requester
from ATC.app.integrations.email_smtp import send_email_reply
from ATC.app.integrations.whatsapp_cloud import send_whatsapp_message


@celery_app.task
def send_ticket_reply(message_id: int):
    db: Session = SessionLocal()

    message = db.get(Message, message_id)
    if message is None:
        db.close()
        return

    ticket = message.ticket
    requester = ticket.requester if ticket else None
    if ticket is None or requester is None:
        db.close()
        return

    if ticket.source == "email":
        send_email_reply(
            to=requester.email or "",
            subject=f"Re: {ticket.subject}",
            body=message.content,
            ticket_id=ticket.id,
        )

    elif ticket.source == "whatsapp":
        send_whatsapp_message(
            to_phone=requester.phone,
            body=message.content,
        )

    db.close()

