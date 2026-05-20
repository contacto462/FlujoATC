from ATC.app.core.db import SessionLocal
from ATC.app.services.email_service import fetch_emails_and_create_tickets

def run_email_import():
    db = SessionLocal()
    try:
        fetch_emails_and_create_tickets(db, limit=100)
    finally:
        db.close()

