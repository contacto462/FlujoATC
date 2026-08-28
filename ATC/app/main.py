import logging
import mimetypes
import os
import re
import threading
import time
from pathlib import Path

# Sin esto, LOGGER.info(...) en cualquier modulo de la app queda mudo en
# produccion: el logger raiz de Python arranca en WARNING por defecto, asi
# que solo LOGGER.warning/.exception llegaban a los logs (via el "last
# resort handler" de stderr, sin timestamp propio). uvicorn no configura el
# logger raiz (solo "uvicorn"/"uvicorn.error"/"uvicorn.access"), asi que esto
# no pisa nada de lo suyo. stderr ya va redirigido a logs/uvicorn.err.log
# por el watchdog, asi que no hace falta un FileHandler aparte.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger(__name__)

import ATC.app.models
from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request as StarletteRequest
from sqlalchemy import inspect, text

from ATC.app.core.config import settings
from ATC.app.core.db import Base, SessionLocal, engine
from ATC.app.core.db_compat import add_column, rename_column, table_has_column
from ATC.app.core.static import MultiDirectoryStaticFiles
from ATC.app.core.text import decode_mime_words

# =========================
# COMPATIBILIDAD MIME WINDOWS
# =========================
# Windows Server puede registrar los archivos JavaScript como text/plain.
# Los módulos JavaScript requieren application/javascript.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")


# Starlette 0.49 limita por defecto cada campo multipart a 1 MB. En la
# ticketera, las capturas pegadas viajan dentro del campo HTML "content" antes
# de convertirse a CID, asi que una imagen normal puede romper el parser antes
# de llegar al endpoint.
_ORIGINAL_STARLETTE_GET_FORM = StarletteRequest._get_form
_MULTIPART_MAX_PART_SIZE = 100 * 1024 * 1024


async def _get_form_with_large_text_parts(
    self,
    *,
    max_files=1000,
    max_fields=1000,
    max_part_size=1024 * 1024,
):
    if max_part_size == 1024 * 1024:
        max_part_size = _MULTIPART_MAX_PART_SIZE
    return await _ORIGINAL_STARLETTE_GET_FORM(
        self,
        max_files=max_files,
        max_fields=max_fields,
        max_part_size=max_part_size,
    )


StarletteRequest._get_form = _get_form_with_large_text_parts


# =========================
# IMPORTAR MODELOS
# Obligatorio para Base.metadata.create_all()
# =========================
from ATC.app.models.automation_log import AutomationLog  # noqa: F401
from ATC.app.models.compras import SolicitudCompra  # noqa: F401
from ATC.app.models.email_sync_state import EmailSyncState  # noqa: F401
from ATC.app.models.prevencion import EstatusGestionItem, EstatusDocumentacionTecnico  # noqa: F401
from ATC.app.models.incidencias import PruebaSonido, MantencionVinaConfig, CierreAperturaImagen  # noqa: F401
from ATC.app.models.inicio_turno import (  # noqa: F401
    GuardiaJustificacion,
    InicioTurnoGuardia,
    InicioTurnoRegistro,
    RecintoQrGenerado,
    RondaRegistro,
    SupervisorRegistro,
    TurnoAlertaEnviada,
    TurnoEstipulado,
)
from ATC.app.models.message import Message  # noqa: F401
from ATC.app.models.portal_cliente import PortalPersonaLogin  # noqa: F401
from ATC.app.models.requester import Requester  # noqa: F401
from ATC.app.models.requester_internal_note_read_state import (  # noqa: F401
    RequesterInternalNoteReadState,
)
from ATC.app.models.ticket import Ticket, TicketChecklistItem  # noqa: F401
from ATC.app.models.ticket_alert_read_state import TicketAlertReadState  # noqa: F401
from ATC.app.models.ticket_history import TicketAssignmentHistory  # noqa: F401
from ATC.app.models.ticket_internal_note_read_state import (  # noqa: F401
    TicketInternalNoteReadState,
)
from ATC.app.models.message_mention import MessageMention  # noqa: F401
from ATC.app.models.ticket_manual_unread import TicketManualUnread  # noqa: F401
from ATC.app.models.ticket_message_read_state import (  # noqa: F401
    TicketMessageReadState,
)
from ATC.app.models.ticket_sla_feedback import TicketSlaFeedback  # noqa: F401
from ATC.app.models.ticket_sla_feedback_event import (  # noqa: F401
    TicketSlaFeedbackEvent,
)
from ATC.app.models.user import User  # noqa: F401
from ATC.app.models.suscripciones import Suscripcion  # noqa: F401

# Carga adicional de modelos para create_all()
from ATC.app.models import incidencias as _incidencias_models  # noqa: F401
from ATC.app.models import venta as _venta_models  # noqa: F401


# =========================
# IMPORTAR ROUTERS API
# =========================
from ATC.app.modules.client_notes import router as client_notes_router
from ATC.app.modules.unified_access import router as unified_access_router
from ATC.app.routes.bitacora import router as bitacora_router
from ATC.app.routes.compras import router as compras_router
from ATC.app.routes.incidencias import (
    router as incidencias_router,
    startup_incidencias,
)
from ATC.app.routes.inicio_turno import (
    router as inicio_turno_router,
    seed_default_inicio_turno_guardias,
)
from ATC.app.routes.messages import router as messages_router
from ATC.app.routes.portal_cliente import router as portal_cliente_router
from ATC.app.routes.public import lavados_router, router as public_router
from ATC.app.routes.requesters import router as requesters_router
from ATC.app.routes.tickets import router as tickets_router
from ATC.app.routes.venta import router as venta_router
from ATC.app.routes.whatsapp_webhook import router as whatsapp_router


# =========================
# IMPORTAR ROUTER WEB
# =========================
from ATC.app.routes.web import router as web_router


# =========================
# SERVICIOS
# =========================
from ATC.app.services.automation_service import run_pending_auto_close
from ATC.app.services.email_service import fetch_emails_and_create_tickets


_ATC_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _ATC_ROOT.parent
_UPLOADS_DIR = _ATC_ROOT / "uploads"
_LEGACY_UPLOADS_DIR = _PROJECT_ROOT / "uploads"
_APP_STATIC_DIR = Path(__file__).resolve().parent / "static"
_STATIC_DIR = _ATC_ROOT / "static"


class _UvicornAccessNoiseFilter(logging.Filter):
    """
    Oculta logs de polling frecuentes para no ensuciar la consola.
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

# Compresion de respuestas grandes (ej. /api/servicio/kpis-data ~2.6 MB de
# JSON que gzip deja en ~200 KB). Los StreamingResponse del repo son descargas
# xlsx/pdf (ya comprimidos), no SSE, asi que el middleware es seguro.
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402

app.add_middleware(GZipMiddleware, minimum_size=1024)


# =========================
# 401/403 -> /login
# =========================
from fastapi import HTTPException as _HTTPExc  # noqa: E402
from fastapi import Request as _Req  # noqa: E402
from fastapi.responses import JSONResponse as _JSON  # noqa: E402
from fastapi.responses import RedirectResponse as _Redir  # noqa: E402


@app.exception_handler(_HTTPExc)
async def _html_auth_redirect(request: _Req, exc: _HTTPExc):
    """
    Si una ruta HTML devuelve 401 o 403, redirige al login.

    Las rutas de API siguen devolviendo JSON.
    """
    if exc.status_code in (401, 403):
        accept = (request.headers.get("accept") or "").lower()
        path = str(request.url.path or "")

        is_api = path.startswith("/api/") or path.startswith("/sso/")
        wants_html = "text/html" in accept or "*/*" in accept

        if not is_api and wants_html:
            return _Redir(url="/login", status_code=303)

    return _JSON(
        {"detail": exc.detail},
        status_code=exc.status_code,
        headers=dict(exc.headers or {}),
    )


# =========================
# CREAR CARPETAS
# =========================
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
_APP_STATIC_DIR.mkdir(parents=True, exist_ok=True)
_STATIC_DIR.mkdir(parents=True, exist_ok=True)


# =========================
# ARCHIVOS ESTÁTICOS
# =========================
app.mount(
    "/uploads",
    MultiDirectoryStaticFiles(
        [
            _UPLOADS_DIR,
            # Compatibilidad: versiones antiguas guardaron imagenes de correos
            # en uploads/ de la raiz. La app sirve /uploads desde ATC/uploads,
            # asi que esos mensajes quedaban como "Imagen no disponible".
            _LEGACY_UPLOADS_DIR,
        ]
    ),
    name="uploads",
)

app.mount(
    "/static",
    MultiDirectoryStaticFiles(
        [
            _APP_STATIC_DIR,
            _STATIC_DIR,
        ]
    ),
    name="static",
)

app.mount(
    "/shared-static",
    StaticFiles(directory=str(_STATIC_DIR)),
    name="shared-static",
)


# =========================
# COMPATIBILIDAD DE USERS
# =========================
def ensure_users_unified_columns() -> None:
    """
    Normaliza columnas antiguas y agrega columnas faltantes en dbo.users.

    No crea usuarios y no elimina datos.
    """
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)

            if not inspector.has_table("users"):
                return

            column_names = {
                column["name"]
                for column in inspector.get_columns("users")
            }

            renames = {
                "username": "user",
                "hashed_password": "password",
                "is_active": "is_activate",
                "department": "departament",
            }

            for old_name, new_name in renames.items():
                if old_name in column_names and new_name not in column_names:
                    rename_column(conn, "users", old_name, new_name)
                    column_names.remove(old_name)
                    column_names.add(new_name)

            additions = {
                "user": "VARCHAR(100)",
                "password": "VARCHAR(255)",
                "name": "VARCHAR(255)",
                "role": "VARCHAR(100) NOT NULL DEFAULT 'agent'",
                "is_activate": "BIT NOT NULL DEFAULT 1",
                "departament": "VARCHAR(500)",
                "cliente_rut": "VARCHAR(40)",
                "email": "VARCHAR(255)",
                "created_at": "DATETIME DEFAULT GETDATE()",
                "updated_at": "DATETIME DEFAULT GETDATE()",
            }

            for column_name, column_type in additions.items():
                if column_name in column_names:
                    continue

                password_default = (
                    " DEFAULT "
                    "'$2b$12$11IAjAQbjYhCCxKc4Qo0zu2rcMO1U5BHQrK.Z1vNoqYckEd3SHzYq'"
                    if column_name == "password"
                    else ""
                )

                add_column(
                    conn,
                    "users",
                    column_name,
                    f"{column_type}{password_default}",
                )

                column_names.add(column_name)

            # Amplía columnas existentes para evitar truncamientos.
            conn.execute(
                text(
                    """
                    IF EXISTS (
                        SELECT 1
                        FROM sys.columns
                        WHERE object_id = OBJECT_ID('dbo.users')
                          AND name = 'name'
                    )
                    ALTER TABLE dbo.users
                    ALTER COLUMN name VARCHAR(255) NOT NULL
                    """
                )
            )

            conn.execute(
                text(
                    """
                    IF EXISTS (
                        SELECT 1
                        FROM sys.columns
                        WHERE object_id = OBJECT_ID('dbo.users')
                          AND name = 'user'
                    )
                    ALTER TABLE dbo.users
                    ALTER COLUMN [user] VARCHAR(100) NOT NULL
                    """
                )
            )

            conn.execute(
                text(
                    """
                    IF EXISTS (
                        SELECT 1
                        FROM sys.columns
                        WHERE object_id = OBJECT_ID('dbo.users')
                          AND name = 'password'
                    )
                    ALTER TABLE dbo.users
                    ALTER COLUMN [password] VARCHAR(255) NOT NULL
                    """
                )
            )

            conn.execute(
                text(
                    """
                    IF EXISTS (
                        SELECT 1
                        FROM sys.columns
                        WHERE object_id = OBJECT_ID('dbo.users')
                          AND name = 'role'
                    )
                    ALTER TABLE dbo.users
                    ALTER COLUMN [role] VARCHAR(100) NOT NULL
                    """
                )
            )

            conn.execute(
                text(
                    """
                    IF EXISTS (
                        SELECT 1
                        FROM sys.columns
                        WHERE object_id = OBJECT_ID('dbo.users')
                          AND name = 'departament'
                    )
                    ALTER TABLE dbo.users
                    ALTER COLUMN departament VARCHAR(500) NULL
                    """
                )
            )

            conn.execute(
                text(
                    """
                    IF EXISTS (
                        SELECT 1
                        FROM sys.columns
                        WHERE object_id = OBJECT_ID('dbo.users')
                          AND name = 'email'
                    )
                    ALTER TABLE dbo.users
                    ALTER COLUMN email VARCHAR(255) NULL
                    """
                )
            )

    except Exception as exc:
        print("Error ensuring unified users columns:", exc)


FULL_AREA_SELECTION_DEPARTMENTS = (
    "Soporte;"
    "Servicio Tecnico;"
    "Operador;"
    "Comercial;"
    "Finanzas;"
    "Administracion;"
    "Operaciones;"
    "materiales;"
    "guardia;"
    "bitacora"
)


USER_AREA_DEPARTMENT_OVERRIDES = [
    {
        "names": ("Fernando Lubiano",),
        "usernames": ("21134285-4",),
        "departments": FULL_AREA_SELECTION_DEPARTMENTS,
        "role": "superadmin",
    },
    {
        "names": ("Gianpiero Lubiano",),
        "usernames": (),
        "departments": FULL_AREA_SELECTION_DEPARTMENTS,
        "role": "superadmin",
    },
    {
        "names": (
            "Maryorie Alegria",
            "Maryorie Alegría",
        ),
        "usernames": (),
        "departments": (
            "Administracion;"
            "Comercial;"
            "Finanzas;"
            "Servicio Tecnico;"
            "Soporte;"
            "Operaciones"
        ),
        "role": None,
    },
    {
        "names": (
            "Carlos Zamora",
            "Carlos Zamora Munita",
        ),
        "usernames": (),
        "departments": (
            "Operaciones;"
            "Servicio Tecnico;"
            "Soporte;"
            "Coordinacion"
        ),
        "role": None,
    },
]


def ensure_configured_user_area_access() -> None:
    """
    Aplica permisos especiales solamente si el usuario ya existe.

    No crea usuarios nuevos.
    """
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)

            if not inspector.has_table("users"):
                return

            for override in USER_AREA_DEPARTMENT_OVERRIDES:
                name_filters = [
                    f"name = :name_{index}"
                    for index, _ in enumerate(override["names"])
                ]

                username_filters = [
                    f'"user" = :username_{index}'
                    for index, _ in enumerate(override["usernames"])
                ]

                filters = name_filters + username_filters

                if not filters:
                    continue

                role_sql = ", role = :role" if override.get("role") else ""

                params = {
                    "departments": override["departments"],
                }

                params.update(
                    {
                        f"name_{index}": name
                        for index, name in enumerate(override["names"])
                    }
                )

                params.update(
                    {
                        f"username_{index}": username
                        for index, username in enumerate(
                            override["usernames"]
                        )
                    }
                )

                if override.get("role"):
                    params["role"] = override["role"]

                conn.execute(
                    text(
                        f"""
                        UPDATE dbo.users
                        SET
                            departament = :departments,
                            is_activate = 1,
                            updated_at = GETDATE()
                            {role_sql}
                        WHERE {" OR ".join(filters)}
                        """
                    ),
                    params,
                )

    except Exception as exc:
        print("Error ensuring configured user area access:", exc)


# =========================
# COMPATIBILIDAD CLIENTES
# =========================
def ensure_requesters_internal_name_column() -> None:
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)

            if not inspector.has_table("clientes"):
                return

            column_names = {
                column["name"]
                for column in inspector.get_columns("clientes")
            }

            if "internal_name" in column_names:
                return

            add_column(
                conn,
                "clientes",
                "internal_name",
                "VARCHAR(120)",
            )

            print("Schema updated: clientes.internal_name")

    except Exception as exc:
        print("Error ensuring clientes.internal_name:", exc)


def ensure_requesters_phone_column() -> None:
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)

            if not inspector.has_table("clientes"):
                return

            column_names = {
                column["name"]
                for column in inspector.get_columns("clientes")
            }

            if "phone" in column_names:
                return

            conn.execute(
                text(
                    """
                    IF NOT EXISTS (
                        SELECT 1
                        FROM sys.columns
                        WHERE name = 'phone'
                          AND object_id = OBJECT_ID('dbo.clientes')
                    )
                    ALTER TABLE dbo.clientes
                    ADD phone VARCHAR(32)
                    """
                )
            )

            conn.execute(
                text(
                    """
                    IF NOT EXISTS (
                        SELECT 1
                        FROM sys.indexes
                        WHERE name = 'ix_clientes_phone'
                          AND object_id = OBJECT_ID('dbo.clientes')
                    )
                    CREATE INDEX ix_clientes_phone
                    ON dbo.clientes(phone)
                    """
                )
            )

            print("Schema updated: clientes.phone")

    except Exception as exc:
        print("Error ensuring clientes.phone:", exc)


# =========================
# COMPATIBILIDAD MESSAGES
# =========================
def ensure_messages_sender_identity_columns() -> None:
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)

            if not inspector.has_table("messages"):
                return

            column_names = {
                column["name"]
                for column in inspector.get_columns("messages")
            }

            if "sender_name" not in column_names:
                add_column(
                    conn,
                    "messages",
                    "sender_name",
                    "VARCHAR(255)",
                )

                print("Schema updated: messages.sender_name")

            if "sender_email" not in column_names:
                add_column(
                    conn,
                    "messages",
                    "sender_email",
                    "VARCHAR(320)",
                )

                print("Schema updated: messages.sender_email")

    except Exception as exc:
        print("Error ensuring messages sender identity columns:", exc)


# =========================
# COMPATIBILIDAD TICKETS
# =========================
def ensure_tickets_inbound_mailbox_column() -> None:
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)

            if not inspector.has_table("tickets"):
                return

            column_names = {
                column["name"]
                for column in inspector.get_columns("tickets")
            }

            if "inbound_mailbox" not in column_names:
                add_column(
                    conn,
                    "tickets",
                    "inbound_mailbox",
                    "VARCHAR(320)",
                )

                print("Schema updated: tickets.inbound_mailbox")

    except Exception as exc:
        print("Error ensuring tickets.inbound_mailbox:", exc)


def ensure_tickets_team_broadcast_column() -> None:
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)

            if not inspector.has_table("tickets"):
                return

            column_names = {
                column["name"]
                for column in inspector.get_columns("tickets")
            }

            if "team_broadcast_at" not in column_names:
                add_column(
                    conn,
                    "tickets",
                    "team_broadcast_at",
                    "DATETIME",
                )

                print("Schema updated: tickets.team_broadcast_at")

    except Exception as exc:
        print("Error ensuring tickets.team_broadcast_at:", exc)


def ensure_incidencias_trabajo_columns() -> None:
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)

            if not inspector.has_table("incidencias"):
                return

            column_names = {
                column["name"]
                for column in inspector.get_columns("incidencias")
            }

            if "fecha_inicio_trabajo" not in column_names:
                add_column(conn, "incidencias", "fecha_inicio_trabajo", "DATETIME")
                print("Schema updated: incidencias.fecha_inicio_trabajo")

            if "fecha_fin_trabajo" not in column_names:
                add_column(conn, "incidencias", "fecha_fin_trabajo", "DATETIME")
                print("Schema updated: incidencias.fecha_fin_trabajo")

            if "solicitud_cliente" not in column_names:
                add_column(conn, "incidencias", "solicitud_cliente", "BIT")
                print("Schema updated: incidencias.solicitud_cliente")

            if "tecnico_cierre" not in column_names:
                add_column(conn, "incidencias", "tecnico_cierre", "VARCHAR(255)")
                print("Schema updated: incidencias.tecnico_cierre")

    except Exception as exc:
        print("Error ensuring incidencias trabajo columns:", exc)


def ensure_sucursal_camaras_dss_columns() -> None:
    """Campos opcionales para vincular camaras de ATC con Dahua DSS/VMS."""
    columns = {
        "dss_device_code": "VARCHAR(120)",
        "dss_channel_id": "VARCHAR(180)",
        "dss_channel_name": "VARCHAR(255)",
        "dss_last_status": "INT",
        "dss_last_checked_at": "DATETIME",
    }
    try:
        with engine.begin() as conn:
            for col_name, col_type in columns.items():
                if not table_has_column(conn, "sucursal_camaras_monitoreo", col_name):
                    add_column(conn, "sucursal_camaras_monitoreo", col_name, col_type)
                    print(f"Schema updated: sucursal_camaras_monitoreo.{col_name}")
    except Exception as exc:
        print("Error ensuring sucursal_camaras_monitoreo DSS columns:", exc)


def ensure_ticket_checklist_columns() -> None:
    """Columnas agregadas despues del create_all inicial de
    ticket_checklist_items (create_all no altera tablas existentes)."""
    columns = {
        "comentario": "NVARCHAR(MAX)",
    }
    try:
        with engine.begin() as conn:
            for col_name, col_type in columns.items():
                if not table_has_column(conn, "ticket_checklist_items", col_name):
                    add_column(conn, "ticket_checklist_items", col_name, col_type)
                    print(f"Schema updated: ticket_checklist_items.{col_name}")
    except Exception as exc:
        print("Error ensuring ticket_checklist_items columns:", exc)


# =========================
# ROUTERS API
# =========================
app.include_router(tickets_router, prefix="/api")
app.include_router(messages_router, prefix="/api")
app.include_router(whatsapp_router, prefix="/api")
app.include_router(requesters_router, prefix="/api")
app.include_router(public_router, prefix="/api")
app.include_router(public_router)
app.include_router(lavados_router)

app.include_router(unified_access_router)
app.include_router(client_notes_router)
app.include_router(bitacora_router)
app.include_router(portal_cliente_router)
app.include_router(incidencias_router)
app.include_router(venta_router)
app.include_router(inicio_turno_router)
app.include_router(compras_router)


# =========================
# ROUTER WEB
# =========================
app.include_router(web_router)


# =========================
# FUNCIÓN LEGADA DE SEED
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
    return bool(
        re.fullmatch(
            r"\d{7,8}-[0-9Kk]",
            str(value or "").strip(),
        )
    )


def _fake_rut_for_user_id(
    user_id: int,
    used: set[str],
) -> str:
    base = 24_000_000 + int(user_id or 0)

    while True:
        rut = f"{base}-{_rut_dv(base)}"

        if rut not in used:
            return rut

        base += 1


def seed_default_users() -> None:
    """
    Función legada para crear los seis usuarios originales.

    IMPORTANTE:
    Esta función se mantiene por compatibilidad, pero ya no se ejecuta
    automáticamente durante el inicio del sistema.
    """
    db = SessionLocal()

    try:
        defaults = [
            {
                "name": "Ronald Montilla",
                "username": "17654321-3",
                "role": "admin",
                "department": "Soporte",
                "aliases": ["ronald"],
            },
            {
                "name": "Fernando Lubiano",
                "username": "21134285-4",
                "role": "admin",
                "department": "Soporte",
                "aliases": ["fernando"],
            },
            {
                "name": "Julissa Mella",
                "username": "18987654-8",
                "role": "agent",
                "department": "Soporte",
                "aliases": ["julissa"],
            },
            {
                "name": "Antonio Bahamondes",
                "username": "16789456-9",
                "role": "agent",
                "department": "Soporte",
                "aliases": ["antonio"],
            },
            {
                "name": "Sthefan Leal",
                "username": "20345678-6",
                "role": "agent",
                "department": "Soporte",
                "aliases": ["sthefan"],
            },
            {
                "name": "Felipe Mora",
                "username": "19456789-8",
                "role": "agent",
                "department": "Soporte",
                "aliases": ["felipe"],
            },
        ]

        for user_data in defaults:
            aliases = [
                user_data["username"],
                *user_data.get("aliases", []),
            ]

            existing_user = (
                db.query(User)
                .filter(
                    (User.name == user_data["name"])
                    | (User.username.in_(aliases))
                )
                .first()
            )

            if existing_user:
                existing_user.name = user_data["name"]
                existing_user.username = user_data["username"]
                existing_user.role = user_data["role"]
                existing_user.department = user_data["department"]
                existing_user.is_active = True
                continue

            new_user = User(
                name=user_data["name"],
                username=user_data["username"],
                role=user_data["role"],
                department=user_data["department"],
                hashed_password="",
                is_active=True,
            )

            db.add(new_user)

        db.flush()

        used_ruts = {
            str(user.username or "").strip()
            for user in db.query(User).all()
            if _is_rut(user.username)
        }

        for user in db.query(User).order_by(User.id.asc()).all():
            if _is_rut(user.username):
                continue

            generated_rut = _fake_rut_for_user_id(
                user.id,
                used_ruts,
            )

            user.username = generated_rut
            used_ruts.add(generated_rut)

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# =========================
# NORMALIZAR CLIENTES
# =========================
def normalize_requester_names() -> None:
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

    except Exception as exc:
        db.rollback()
        print("Error normalizing requester names:", exc)

    finally:
        db.close()


# =========================
# ROOT
# =========================
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(
        url="/login",
        status_code=302,
    )


# Los navegadores piden "/favicon.ico" en la raíz en cada navegación, en
# paralelo al <link rel="icon"> de la página. Si no existe, el ícono
# parpadea al default mientras carga. Se sirve con cache larga para evitar
# ese refetch constante.
_FAVICON_ICO_PATH = _STATIC_DIR / "img" / "favicon.ico"


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico():
    return FileResponse(
        str(_FAVICON_ICO_PATH),
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
    }


# =========================
# EMAIL AUTO-IMPORT LOOP
# =========================
def _poll_imap(
    imap_host=None,
    imap_port=None,
    imap_user=None,
    imap_password=None,
    imap_folder=None,
) -> None:
    """
    Abre su propia sesión de base de datos, consulta un buzón y la cierra.
    """
    db = SessionLocal()

    try:
        fetch_emails_and_create_tickets(
            db,
            limit=25,
            imap_host=imap_host,
            imap_port=imap_port,
            imap_user=imap_user,
            imap_password=imap_password,
            imap_folder=imap_folder,
        )

    finally:
        db.close()


def email_loop() -> None:
    """
    Worker que revisa los buzones IMAP configurados.
    """
    while True:
        try:
            sleep_seconds = max(int(os.getenv("EMAIL_POLL_SECONDS", "60") or 60), 60)
        except ValueError:
            sleep_seconds = 60

        try:
            _poll_imap()

        except Exception as exc:
            print("Error IMAP1:", exc)
            if "Maximum number of connections" in str(exc):
                sleep_seconds = max(sleep_seconds, 300)

        try:
            if (
                settings.IMAP2_HOST
                and settings.IMAP2_USER
                and settings.IMAP2_PASSWORD
            ):
                _poll_imap(
                    imap_host=settings.IMAP2_HOST,
                    imap_port=settings.IMAP2_PORT,
                    imap_user=settings.IMAP2_USER,
                    imap_password=settings.IMAP2_PASSWORD,
                    imap_folder=settings.IMAP2_FOLDER,
                )

        except Exception as exc:
            print("Error IMAP2:", exc)
            if "Maximum number of connections" in str(exc):
                sleep_seconds = max(sleep_seconds, 300)

        time.sleep(sleep_seconds)


def _purge_ticketera_trash(dias: int = 15) -> int:
    """Borra definitivamente los tickets en Papelera (is_deleted=True) con
    mas de `dias` dias ahi (segun Ticket.deleted_at) — pedido explicito,
    ago 2026. Ninguna FK hacia tickets/messages tiene ON DELETE CASCADE, asi
    que hay que borrar las filas hijas primero (orden: lo que depende de
    messages antes que messages, todo lo demas antes que tickets)."""
    from datetime import datetime, timedelta, timezone

    db = SessionLocal()
    try:
        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        rows = db.execute(
            text("SELECT id FROM tickets WHERE is_deleted = 1 AND deleted_at IS NOT NULL AND deleted_at <= :corte"),
            {"corte": corte},
        ).fetchall()
        ids = [int(r[0]) for r in rows]
        if not ids:
            return 0
        ids_csv = ",".join(str(i) for i in ids)
        child_tables = [
            "message_mentions",
            "ticket_message_read_states",
            "ticket_internal_note_read_states",
            "ticket_manual_unread",
            "ticket_assignment_history",
            "ticket_sla_feedback_events",
            "ticket_sla_feedback",
            "automation_logs",
            "messages",
        ]
        for table in child_tables:
            db.execute(text(f"DELETE FROM {table} WHERE ticket_id IN ({ids_csv})"))
        db.execute(text(f"DELETE FROM tickets WHERE id IN ({ids_csv})"))
        db.commit()
        return len(ids)
    finally:
        db.close()


def automation_loop() -> None:
    """
    Worker de automatizaciones programadas.
    """
    while True:
        db = SessionLocal()

        try:
            run_pending_auto_close(db)

        except Exception as exc:
            print("Error ejecutando automatizaciones:", exc)

        finally:
            db.close()

        # Reintento de informes de cierre ODT que quedaron sin subir a
        # Drive (p. ej. por un archivo grande o una falla de red puntual).
        # Sesion separada para que un fallo aca no afecte el auto-cierre.
        db = SessionLocal()
        try:
            from ATC.app.services.incidencias_service import IncidenciasService

            resultado = IncidenciasService(db).reintentar_informes_drive_pendientes()
            if resultado.get("revisados"):
                LOGGER.info("Reintento informes Drive: %s", resultado)
        except Exception:
            LOGGER.exception("Error reintentando informes Drive")
        finally:
            db.close()

        # Reintento de archivos en cuarentena local (uploads/_pending_drive/)
        # de los flujos migrados a Drive (cierre ODT/mantencion, rendiciones,
        # apertura/cierre de sucursal): si Drive fallo en el momento del
        # upload, el archivo quedo guardado localmente como fallback y esta
        # funcion reintenta subirlo, repuntando la fila en BBDD y borrando
        # el archivo local al lograrlo (ver _guardar_en_cuarentena_drive).
        db = SessionLocal()
        try:
            from ATC.app.services.incidencias_service import IncidenciasService

            resultado_cuarentena = IncidenciasService(db).reintentar_uploads_pendientes_drive()
            if resultado_cuarentena.get("revisados"):
                LOGGER.info("Reintento cuarentena Drive: %s", resultado_cuarentena)
        except Exception:
            LOGGER.exception("Error reintentando cuarentena Drive")
        finally:
            db.close()

        # Papelera de Ticketera: borrado definitivo automatico a los 15
        # dias — pedido explicito, ago 2026.
        try:
            purgados = _purge_ticketera_trash(dias=15)
            if purgados:
                LOGGER.info("Purga papelera Ticketera: %s tickets borrados definitivamente", purgados)
        except Exception:
            LOGGER.exception("Error purgando papelera de Ticketera")

        # Alertas de turno (Día/Noche) incompleto pasadas 2 horas del inicio
        # de cada turno — ver routes/inicio_turno.py:
        # _verificar_alertas_turno_incompleto. Sesion propia para que un
        # fallo aca no afecte el resto del loop.
        db = SessionLocal()
        try:
            from ATC.app.routes.inicio_turno import _verificar_alertas_turno_incompleto

            _verificar_alertas_turno_incompleto(db)
        except Exception:
            LOGGER.exception("Error verificando alertas de turno incompleto")
        finally:
            db.close()

        poll_seconds = int(
            settings.AUTOMATION_POLL_SECONDS or 300
        )

        time.sleep(max(poll_seconds, 60))


def piriod_suscripciones_loop() -> None:
    """Refresca en background el cache de suscripciones de Piriod (Registro
    de Suscripción de Finanzas) — la API pagina de a 20 y hay ~600
    suscripciones, así que traerlas todas toma ~30 llamadas; hacerlo acá
    evita que el hilo de un request tenga que esperar eso (pedido
    explícito, ago 2026)."""
    from ATC.app.integrations.piriod_client import PiriodError
    from ATC.app.services.suscripciones_service import refrescar_cache_piriod

    while True:
        try:
            refrescar_cache_piriod()
        except PiriodError as exc:
            LOGGER.warning("No se pudo refrescar cache de suscripciones Piriod: %s", exc)
        except Exception:
            LOGGER.exception("Error refrescando cache de suscripciones Piriod")

        time.sleep(300)


# =========================
# STARTUP
# =========================
@app.on_event("startup")
def startup_tasks() -> None:
    _configure_access_log_noise_filter()

    # Diagnóstico: en Windows debe ser SelectorEventLoop (ver run_server.py);
    # con ProactorEventLoop el listener muere ante errores de accept.
    try:
        import asyncio as _asyncio

        print(f"Event loop activo: {type(_asyncio.get_running_loop()).__name__}")
    except Exception:
        pass

    # Crea las tablas que aún no existen.
    Base.metadata.create_all(bind=engine)

    # Compatibilidad y actualizaciones de estructura.
    ensure_users_unified_columns()
    ensure_requesters_internal_name_column()
    ensure_requesters_phone_column()
    ensure_messages_sender_identity_columns()
    ensure_tickets_inbound_mailbox_column()
    ensure_tickets_team_broadcast_column()
    ensure_incidencias_trabajo_columns()
    ensure_sucursal_camaras_dss_columns()
    ensure_ticket_checklist_columns()

    # Normalización de información existente.
    normalize_requester_names()

    # IMPORTANTE:
    # No se ejecuta seed_default_users().
    # Los usuarios deben venir de la importación o crearse manualmente.
    #
    # seed_default_users()

    # Aplica permisos especiales solamente a usuarios que ya existan.
    ensure_configured_user_area_access()

    db = SessionLocal()

    try:
        seed_default_inicio_turno_guardias(db)

    finally:
        db.close()

    # Workers en segundo plano.
    threading.Thread(
        target=email_loop,
        daemon=True,
    ).start()

    threading.Thread(
        target=automation_loop,
        daemon=True,
    ).start()

    threading.Thread(
        target=piriod_suscripciones_loop,
        daemon=True,
    ).start()

    startup_incidencias()
