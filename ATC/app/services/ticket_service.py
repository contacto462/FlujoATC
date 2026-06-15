from sqlalchemy.orm import Session

from ATC.app.models.ticket import Ticket
from ATC.app.models.requester import Requester
from ATC.app.models.message import Message


# =========================
# CORE (reutilizable)
# =========================
def _create_ticket(
    db: Session,
    *,
    subject: str,
    requester: Requester,
    source: str,
    priority: str = "",
    initial_message: str,
    sender_type: str = "requester",
    channel: str,
):
    ticket = Ticket(
        subject=subject or "(sin asunto)",
        source=source,
        priority=priority,
        requester_id=requester.id,
        status="open",
    )

    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    message = Message(
        ticket_id=ticket.id,
        sender_type=sender_type,
        channel=channel,
        content=initial_message,
    )

    db.add(message)
    db.commit()

    return ticket


# =========================
# WEB / FORM PÃšBLICO
# =========================
def create_ticket_from_public(
    db: Session,
    name: str,
    email: str,
    subject: str,
    message_text: str,
):
    requester = db.query(Requester).filter(Requester.email == email).first()

    if not requester:
        requester = Requester(
            name=name,
            email=email,
            type="external",
        )
        db.add(requester)
        db.commit()
        db.refresh(requester)

    return _create_ticket(
        db=db,
        subject=subject,
        requester=requester,
        source="web",
        initial_message=message_text,
        sender_type="requester",
        channel="web",
    )


# =========================
# EMAIL (IMAP)
# =========================
def create_ticket_from_email(
    db: Session,
    from_email: str,
    subject: str,
    body: str,
):
    requester = db.query(Requester).filter(Requester.email == from_email).first()

    if not requester:
        requester = Requester(
            email=from_email,
            type="external",
        )
        db.add(requester)
        db.commit()
        db.refresh(requester)

    return _create_ticket(
        db=db,
        subject=subject,
        requester=requester,
        source="email",
        initial_message=body,
        sender_type="requester",
        channel="email",
    )
