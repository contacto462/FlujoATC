from ATC.app.core.db import SessionLocal
from ATC.app.services.email_service import fetch_emails_and_create_tickets


def fetch_unseen_emails(limit: int = 100) -> dict:
    db = SessionLocal()
    try:
        return fetch_emails_and_create_tickets(db, limit=limit)
    finally:
        db.close()

