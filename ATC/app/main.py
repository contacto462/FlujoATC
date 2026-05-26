import ATC.app.models
import logging
from sqlalchemy import inspect, text

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from ATC.app.core.db import engine, Base, SessionLocal
from ATC.app.core.config import settings
from ATC.app.core.static import MultiDirectoryStaticFiles

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
from ATC.app.modules.client_notes import router as client_notes_router
from ATC.app.modules.incidencias import register_incidencias_module
from ATC.app.modules.unified_access import router as unified_access_router

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
import re
from pathlib import Path

_ATC_ROOT = Path(__file__).resolve().parents[1]
_UPLOADS_DIR = _ATC_ROOT / "uploads"
_STATIC_DIR = _ATC_ROOT / "static"
_INCIDENCIAS_ROOT = _ATC_ROOT / "incidencias"
_INCIDENCIAS_UPLOADS_DIR = _INCIDENCIAS_ROOT / "uploads"
_INCIDENCIAS_STATIC_DIR = _INCIDENCIAS_ROOT / "app" / "static"


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
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
_STATIC_DIR.mkdir(parents=True, exist_ok=True)

# =========================
# SERVIR ARCHIVOS ESTATICOS
# =========================
app.mount(
    "/uploads",
    MultiDirectoryStaticFiles([_INCIDENCIAS_UPLOADS_DIR, _UPLOADS_DIR]),
    name="uploads",
)
app.mount(
    "/static",
    MultiDirectoryStaticFiles([_INCIDENCIAS_STATIC_DIR, _STATIC_DIR]),
    name="static",
)
app.mount("/shared-static", StaticFiles(directory=str(_STATIC_DIR)), name="shared-static")

# =========================
# CREAR TABLAS (SOLO DEV)
# =========================
Base.metadata.create_all(bind=engine)


def ensure_users_unified_columns():
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("users"):
                return

            column_names = {column["name"] for column in inspector.get_columns("users")}
            renames = {
                "username": "user",
                "hashed_password": "password",
                "is_active": "is_activate",
                "department": "departament",
            }
            for old_name, new_name in renames.items():
                if old_name in column_names and new_name not in column_names:
                    conn.execute(text(f'ALTER TABLE users RENAME COLUMN "{old_name}" TO "{new_name}"'))
                    column_names.remove(old_name)
                    column_names.add(new_name)

            additions = {
                "user": "VARCHAR(50)",
                "password": "VARCHAR(255)",
                "name": "VARCHAR(100)",
                "role": "VARCHAR(20) NOT NULL DEFAULT 'agent'",
                "is_activate": "BOOLEAN NOT NULL DEFAULT TRUE",
                "departament": "VARCHAR(80)",
                "created_at": "TIMESTAMP DEFAULT NOW()",
                "updated_at": "TIMESTAMP DEFAULT NOW()",
            }
            for column_name, column_type in additions.items():
                if column_name not in column_names:
                    nullable_default = " DEFAULT '$2b$12$11IAjAQbjYhCCxKc4Qo0zu2rcMO1U5BHQrK.Z1vNoqYckEd3SHzYq'" if column_name == "password" else ""
                    conn.execute(text(f'ALTER TABLE users ADD COLUMN "{column_name}" {column_type}{nullable_default}'))
                    column_names.add(column_name)
    except Exception as e:
        print("Error ensuring unified users columns:", e)


ensure_users_unified_columns()


FERNANDO_FULL_DEPARTMENTS = "Soporte;Servicio Tecnico;Operador;Comercial;Finanzas;Administracion;Operaciones"


def ensure_fernando_full_panel_access():
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("users"):
                return
            conn.execute(
                text(
                    """
                    UPDATE users
                    SET departament = :departments,
                        role = 'admin',
                        is_activate = TRUE,
                        updated_at = NOW()
                    WHERE "user" = '21134285-4'
                       OR name = 'Fernando Lubiano'
                    """
                ),
                {"departments": FERNANDO_FULL_DEPARTMENTS},
            )
    except Exception as e:
        print("Error ensuring Fernando full panel access:", e)


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
app.include_router(unified_access_router)
app.include_router(client_notes_router)
register_incidencias_module(app)

# =========================
# INCLUIR ROUTER WEB (DASHBOARD)
# =========================
app.include_router(web_router)


# =========================
# SEED USUARIOS POR DEFECTO
# =========================
def _rut_dv(number: int) -> str:
    total = 0
    multiplier = 2
    for digit in reversed(str(number)):
        total += int(digit) * multiplier
        multiplier = 2 if multiplier == 7 else multiplier + 1
    value = 11 - (total % 11)
    if value == 11:
        return "0"
    if value == 10:
        return "K"
    return str(value)


def _is_rut(value: str | None) -> bool:
    return bool(re.fullmatch(r"\d{7,8}-[0-9Kk]", str(value or "").strip()))


def _fake_rut_for_user_id(user_id: int, used: set[str]) -> str:
    base = 24000000 + int(user_id or 0)
    while True:
        rut = f"{base}-{_rut_dv(base)}"
        if rut not in used:
            return rut
        base += 1


def seed_default_users():
    """
    Crea usuarios iniciales si no existen.
    Password inicial: 123456 (CAMBIAR EN PRODUCCIÃ“N).
    """
    db = SessionLocal()
    try:
        defaults = [
            {"name": "Ronald Montilla", "username": "17654321-3", "role": "admin", "department": "Soporte", "aliases": ["ronald"]},
            {"name": "Fernando Lubiano", "username": "21134285-4", "role": "admin", "department": "Soporte", "aliases": ["fernando"]},
            {"name": "Julissa Mella", "username": "18987654-8", "role": "agent", "department": "Soporte", "aliases": ["julissa"]},
            {"name": "Antonio Bahamondes", "username": "16789456-9", "role": "agent", "department": "Soporte", "aliases": ["antonio"]},
            {"name": "Sthefan Leal", "username": "20345678-6", "role": "agent", "department": "Soporte", "aliases": ["sthefan"]},
            {"name": "Felipe Mora", "username": "19456789-8", "role": "agent", "department": "Soporte", "aliases": ["felipe"]},
        ]

        for u in defaults:
            aliases = [u["username"], *u.get("aliases", [])]
            exists = (
                db.query(User)
                .filter((User.name == u["name"]) | (User.username.in_(aliases)))
                .first()
            )
            if exists:
                exists.name = u["name"]
                exists.username = u["username"]
                exists.role = u["role"]
                exists.department = u["department"]
                exists.is_active = True
                continue

            user = User(
                name=u["name"],
                username=u["username"],
                role=u["role"],
                department=u["department"],
                hashed_password=hash_password("123456"),
                is_active=True,
            )
            db.add(user)

        db.flush()
        used_ruts = {str(u.username or "").strip() for u in db.query(User).all() if _is_rut(u.username)}
        for user in db.query(User).order_by(User.id.asc()).all():
            if _is_rut(user.username):
                continue
            generated = _fake_rut_for_user_id(user.id, used_ruts)
            user.username = generated
            used_ruts.add(generated)

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
            print("Error IMAP1:", e)

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
            print("Error IMAP2:", e)

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
            print("Error ejecutando automatizaciones:", e)
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
    ensure_fernando_full_panel_access()

    # Iniciar thread de email
    threading.Thread(target=email_loop, daemon=True).start()
    threading.Thread(target=automation_loop, daemon=True).start()
