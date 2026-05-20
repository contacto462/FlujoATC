import ATC.app.models
import logging
from sqlalchemy import inspect, text

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from ATC.app.core.db import engine, Base, SessionLocal
from ATC.app.core.config import settings

# =========================
# IMPORTAR MODELOS (OBLIGATORIO para create_all)
# =========================
from ATC.app.models.user import User  # noqa
from ATC.app.models.ticket import Ticket  # noqa
from ATC.app.models.message import Message  # noqa
from ATC.app.models.internal_chat_message import InternalChatMessage  # noqa
from ATC.app.models.internal_chat_read_state import InternalChatReadState  # noqa
from ATC.app.models.ticket_alert_read_state import TicketAlertReadState  # noqa
from ATC.app.models.ticket_message_read_state import TicketMessageReadState  # noqa
from ATC.app.models.ticket_internal_note_read_state import TicketInternalNoteReadState  # noqa
from ATC.app.models.requester import Requester  # noqa
from ATC.app.models.ticket_history import TicketAssignmentHistory  # noqa
from ATC.app.models.email_sync_state import EmailSyncState  # noqa
from ATC.app.models.ticket_sla_feedback import TicketSlaFeedback  # noqa
from ATC.app.models.ticket_sla_feedback_event import TicketSlaFeedbackEvent  # noqa
from ATC.app.models.automation_log import AutomationLog  # noqa

# =========================
# IMPORTAR ROUTERS API
# =========================
from ATC.app.routes.tickets import router as tickets_router
from ATC.app.routes.messages import router as messages_router
from ATC.app.routes.whatsapp_webhook import router as whatsapp_router
from ATC.app.routes.requesters import router as requesters_router
from ATC.app.routes.public import router as public_router

# =========================
# IMPORTAR ROUTER WEB (CRM)
# =========================
from ATC.app.routes.web import router as web_router

# =========================
# SERVICIO EMAIL
# =========================
from ATC.app.services.email_service import fetch_emails_and_create_tickets
from ATC.app.services.automation_service import run_pending_auto_close

# =========================
# UTILS
# =========================
from ATC.app.core.security import hash_password
from ATC.app.core.text import decode_mime_words

import threading
import time
import os


class _UvicornAccessNoiseFilter(logging.Filter):
    """
    Oculta logs de polling del chat interno para no ensuciar consola.
    """

    _hidden_paths = (
        "/internal-chat/unread-count",
        "/internal-chat/messages",
        "/ticket-alerts/unread-count",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for path in self._hidden_paths:
            if path in message:
                return False
        return True


def _configure_access_log_noise_filter() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.addFilter(_UvicornAccessNoiseFilter())


# =========================
# APP
# =========================
app = FastAPI(
    title="Internal Helpdesk",
    description="Helpdesk / CRM interno estilo Zendesk",
    version="0.3.0",
)

# =========================
# CREAR CARPETAS NECESARIAS
# =========================
os.makedirs("uploads", exist_ok=True)
os.makedirs("static", exist_ok=True)

# =========================
# SERVIR ARCHIVOS ESTÃTICOS
# =========================
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/static", StaticFiles(directory="static"), name="static")

# =========================
# CREAR TABLAS (SOLO DEV)
# =========================
Base.metadata.create_all(bind=engine)


def ensure_requesters_internal_name_column():
    # En instalaciones existentes, agrega la columna sin requerir migracion manual.
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            column_names = {column["name"] for column in inspector.get_columns("requesters")}
            if "internal_name" in column_names:
                return
            conn.execute(text("ALTER TABLE requesters ADD COLUMN internal_name VARCHAR(120)"))
            print("Schema updated: requesters.internal_name")
    except Exception as e:
        print("Error ensuring requesters.internal_name:", e)


ensure_requesters_internal_name_column()


def ensure_messages_sender_identity_columns():
    # Guarda remitente por mensaje para distinguir respuestas de CC.
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            column_names = {column["name"] for column in inspector.get_columns("messages")}

            if "sender_name" not in column_names:
                conn.execute(text("ALTER TABLE messages ADD COLUMN sender_name VARCHAR(255)"))
                print("Schema updated: messages.sender_name")

            if "sender_email" not in column_names:
                conn.execute(text("ALTER TABLE messages ADD COLUMN sender_email VARCHAR(320)"))
                print("Schema updated: messages.sender_email")
    except Exception as e:
        print("Error ensuring messages sender identity columns:", e)


ensure_messages_sender_identity_columns()

# =========================
# INCLUIR ROUTERS API
# =========================
app.include_router(tickets_router, prefix="/api")
app.include_router(messages_router, prefix="/api")
app.include_router(whatsapp_router, prefix="/api")
app.include_router(requesters_router, prefix="/api")
app.include_router(public_router, prefix="/api")
app.include_router(public_router)

# =========================
# INCLUIR ROUTER WEB (DASHBOARD)
# =========================
app.include_router(web_router)


# =========================
# SEED USUARIOS POR DEFECTO
# =========================
def seed_default_users():
    """
    Crea usuarios iniciales si no existen.
    Password inicial: 123456 (CAMBIAR EN PRODUCCIÃ“N).
    """
    db = SessionLocal()
    try:
        defaults = [
            {"name": "Ronald Montilla", "username": "ronald", "role": "admin"},
            {"name": "Fernando Lubiano", "username": "fernando", "role": "admin"},
            {"name": "Julissa Mella", "username": "julissa", "role": "agent"},
            {"name": "Antonio Bahamondes", "username": "antonio", "role": "agent"},
            {"name": "Sthefan Leal", "username": "sthefan", "role": "agent"},
            {"name": "Felipe Mora", "username": "felipe", "role": "agent"},
        ]

        for u in defaults:
            exists = db.query(User).filter(User.username == u["username"]).first()
            if exists:
                continue

            user = User(
                name=u["name"],
                username=u["username"],
                role=u["role"],
                hashed_password=hash_password("123456"),
                is_active=True,
            )
            db.add(user)

        db.commit()
    finally:
        db.close()


def normalize_requester_names():
    db = SessionLocal()
    try:
        requesters = db.query(Requester).all()

        updated = 0
        for requester in requesters:
            decoded = decode_mime_words(requester.name)
            if decoded and decoded != requester.name:
                requester.name = decoded
                updated += 1

        if updated:
            db.commit()
            print(f"Normalized requester names: {updated}")
    except Exception as e:
        db.rollback()
        print("Error normalizing requester names:", e)
    finally:
        db.close()


# =========================
# ROOT
# =========================
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/login", status_code=302)


@app.get("/health")
def health():
    return {"status": "ok"}


# =========================
# EMAIL AUTO-IMPORT LOOP
# =========================
def _poll_imap(imap_host=None, imap_port=None, imap_user=None, imap_password=None, imap_folder=None):
    """Abre su propia sesiÃ³n de BD, pollea un buzÃ³n y la cierra. Fallos no afectan otros buzones."""
    db = SessionLocal()
    try:
        fetch_emails_and_create_tickets(
            db,
            limit=100,
            imap_host=imap_host,
            imap_port=imap_port,
            imap_user=imap_user,
            imap_password=imap_password,
            imap_folder=imap_folder,
        )
    finally:
        db.close()


def email_loop():
    """
    Worker que revisa el inbox de ambas cuentas IMAP
    y crea tickets automÃ¡ticamente.
    """
    while True:
        try:
            _poll_imap()
        except Exception as e:
            print("âŒ Error IMAP1:", e)

        try:
            if settings.IMAP2_HOST and settings.IMAP2_USER and settings.IMAP2_PASSWORD:
                _poll_imap(
                    imap_host=settings.IMAP2_HOST,
                    imap_port=settings.IMAP2_PORT,
                    imap_user=settings.IMAP2_USER,
                    imap_password=settings.IMAP2_PASSWORD,
                    imap_folder=settings.IMAP2_FOLDER,
                )
        except Exception as e:
            print("âŒ Error IMAP2:", e)

        time.sleep(5)


def automation_loop():
    """
    Worker de automatizaciones programadas.
    Por ahora ejecuta el autocierre por inactividad.
    """
    while True:
        db = SessionLocal()
        try:
            run_pending_auto_close(db)
        except Exception as e:
            print("âŒ Error ejecutando automatizaciones:", e)
        finally:
            db.close()

        # Ejecutamos menos frecuente que IMAP, porque es una regla programada.
        time.sleep(max(int(settings.AUTOMATION_POLL_SECONDS or 300), 60))

# =========================
# STARTUP
# =========================
@app.on_event("startup")
def startup_tasks():
    _configure_access_log_noise_filter()
    normalize_requester_names()
    seed_default_users()

    # Iniciar thread de email
    threading.Thread(target=email_loop, daemon=True).start()
    threading.Thread(target=automation_loop, daemon=True).start()

