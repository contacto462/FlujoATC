from __future__ import annotations

from datetime import datetime, timezone
import csv
import json
import threading
import time
import logging
import re
import unicodedata
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from jose import JWTError, jwt
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy import MetaData, Table, inspect, or_, select, text
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ATC.app.core.db_compat import add_column, dialect_name, drop_column, quote_ident, rename_column
from ATC.app.core.incidencias_db import Base, SessionLocal, build_engine, engine, get_db
from ATC.app.core.incidencias_config import settings
from ATC.app.models.incidencias import LoginSession, User, PruebaSonido, Registro, Rendicion, ProtocoloInforme
from ATC.app.core.timeutil import chile_now
from ATC.app.models.message import Message
from ATC.app.models.requester import Requester
from ATC.app.models.ticket import Ticket
from ATC.app.routes.bitacora_access import can_access_bitacora
from ATC.app.core.session_policy import max_age_cookie_segundos
from ATC.app.schemas.incidencias import (
    ActualizarRegionComunaRequest,
    CerrarIncidenciaRequest,
    DerivarTecnicoRequest,
    EditarIncidenciaTablaRequest,
    EnProcesoRequest,
    EnviarInformacionContactoRequest,
    FinalizarIncidenciaRequest,
    FormularioRegistro,
    IncidenciaNueva,
    IniciarTrabajoRequest,
    LoginRequest,
    LoginResponse,
    ProtocoloRegistroCreateRequest,
    RegenerarInformeCierreRequest,
    RendicionRequest,
)
from ATC.app.services.incidencias_service import (
    AREA_PANEL_DESTINOS,
    IncidenciasService,
    geocodificar_region_comuna_google,
    seed_default_identity_data,
)
from ATC.app.services.incidencias_drive_report_service import download_support_drive_file_bytes
from ATC.app.services.protocolos_service import ProtocolosService


INCIDENCIAS_APP_DIR = Path(__file__).resolve().parents[1]
templates = Jinja2Templates(directory=str(INCIDENCIAS_APP_DIR / "templates"))

router = APIRouter()
LOGGER = logging.getLogger(__name__)
_protocolos_weekly_worker_started = False

TIPOS_Y_ESPECIFICACIONES = {
    "Gestión de Grabaciones y Evidencia": [
        "Solicitud / envío de grabaciones",
        "Solicitud de imágenes",
        "Grabaciones faltantes",
    ],
    "Monitoreo y Estado de Cámaras": [
        "Cámaras caídas / intermitentes",
        "Cámaras fuera de horario",
        "Cámaras obstruidas / tapadas / movidas",
        "Cámara nueva / reemplazo",
        "Verificación de cámaras en línea",
        "Inicialización / renombrado de cámaras",
    ],
    "Configuración y Ajustes de Cámaras / NVR / DVR": [
        "Configuración de cámaras nuevas",
        "Configuración de NVR / DVR / XVR",
        "Planes de grabación",
        "Retención de días",
        "Configuración FTP / IVS / PTZ",
        "Corrección IVS",
        "Cambio de nombre / orden de cámaras",
        "Normalización de hora",
    ],
    "Sistema de Audio y Alertas": [
        "Problemas de audio",
        "Pruebas de audio",
        "Sonidos molestos",
        "Audio por activación de IVS",
        "Sistema de audio no funciona",
    ],
    "IVS, POP UPS y Automatizaciones": [
        "Creación / ajuste de IVS",
        "Alertas automáticas",
        "POP UPS",
        "Activación / revisión de POP UPS",
    ],
    "Soporte a Equipos de Computación": [
        "Notebook lento / no enciende",
        "Formateo de PC / notebook",
        "Cambio de RAM",
        "Cambio de computador / torre",
        "Problemas de disco duro",
        "Limpieza de equipos",
        "Activación de Windows",
    ],
    "Soporte a Pantallas y Periféricos": [
        "Pantallas sin señal",
        "HDMI / VGA defectuoso",
        "Monitores apagados / intermitentes",
        "Mouse / teclado",
        "Impresoras (térmica / normal)",
    ],
    "Redes y Conectividad": [
        "Caídas de red",
        "Cambio IP / DHCP",
        "Router / antenas",
        "Intermitencia de enlace",
        "Internet caído",
    ],
    "Sistema de Alarmas y Sensores": [
        "Problemas de alarma",
        "Sensores (humo / gas / pánico)",
        "Sirenas",
        "Zonificación",
        "Panel de alarma",
        "Notificaciones que no llegan",
    ],
    "Soporte a Software y Plataformas": [
        "DSS / HikCentral lento o fallando",
        "SoftGuard",
        "App de alarma / cámara",
        "Configuración de usuarios",
        "Credenciales",
        "Cambio de contraseñas",
    ],
    "Gestión Operativa y Administrativa": [
        "Orden y creación de planillas",
        "Registro de incidencias",
        "Correos informativos",
        "Solicitud de folios / QR",
        "Coordinación con técnicos externos",
        "Comunicación con clientes / prioridades",
    ],
    "Cierres, Validaciones y Revisión de Novedades": [
        "Cierre de caseta / instalación (Emergencia)",
        "Revisión de novedades",
        "Validaciones finales",
        "Pruebas posteriores a intervención",
    ],
    "Mantenimiento Preventivo": [
        "Mantención de equipos",
        "Revisión periódica",
        "Normalización preventiva",
        "Revisión programada de IVS / audio / cámaras",
    ],
}


def startup_incidencias() -> None:
    global _protocolos_weekly_worker_started
    _ensure_database_relationships()
    _ensure_registro_optional_columns()
    _ensure_administracion_odt_optional_columns()
    _ensure_finanzas_odt_optional_columns()
    _ensure_servicio_tecnico_ventas_optional_columns()
    _ensure_rendiciones_optional_columns()
    _ensure_protocolos_optional_columns()
    _ensure_bbdd_clientes_optional_columns()
    _ensure_identity_optional_columns()
    _ensure_venta_ods_optional_columns()
    _seed_identity_data()
    _ensure_identity_views()
    if not _protocolos_weekly_worker_started:
        _protocolos_weekly_worker_started = True
        threading.Thread(
            target=_protocolos_weekly_worker_loop,
            name="protocolos-weekly-worker",
            daemon=True,
        ).start()


def _ensure_database_relationships() -> None:
    if engine.dialect.name != "postgresql":
        return

    def constraint_exists(conn, name: str) -> bool:
        return bool(
            conn.execute(
                text("SELECT 1 FROM pg_constraint WHERE conname = :name LIMIT 1"),
                {"name": name},
            ).first()
        )

    def table_exists(conn, table: str) -> bool:
        return inspect(conn).has_table(table)

    def add_unique_if_clean(conn, table: str, column: str, constraint: str) -> None:
        if constraint_exists(conn, constraint) or not table_exists(conn, table):
            return
        duplicates = conn.execute(
            text(
                f'''
                SELECT COUNT(*) FROM (
                    SELECT "{column}"
                    FROM "{table}"
                    WHERE "{column}" IS NOT NULL
                    GROUP BY "{column}"
                    HAVING COUNT(*) > 1
                ) dup
                '''
            )
        ).scalar_one()
        if duplicates:
            LOGGER.warning("No se creo %s: hay valores duplicados en %s.%s", constraint, table, column)
            return
        conn.execute(text(f'ALTER TABLE "{table}" ADD CONSTRAINT "{constraint}" UNIQUE ("{column}")'))

    def add_fk_if_clean(
        conn,
        *,
        constraint: str,
        child_table: str,
        child_column: str,
        parent_table: str,
        parent_column: str,
        on_delete: str | None = None,
    ) -> None:
        if constraint_exists(conn, constraint):
            return
        if not table_exists(conn, child_table) or not table_exists(conn, parent_table):
            return
        orphans = conn.execute(
            text(
                f'''
                SELECT COUNT(*)
                FROM "{child_table}" child
                LEFT JOIN "{parent_table}" parent
                  ON parent."{parent_column}" = child."{child_column}"
                WHERE child."{child_column}" IS NOT NULL
                  AND parent."{parent_column}" IS NULL
                '''
            )
        ).scalar_one()
        if orphans:
            LOGGER.warning(
                "No se creo %s: %s.%s tiene %s registros sin padre en %s.%s",
                constraint,
                child_table,
                child_column,
                orphans,
                parent_table,
                parent_column,
            )
            return
        delete_clause = f" ON DELETE {on_delete}" if on_delete else ""
        conn.execute(
            text(
                f'''
                ALTER TABLE "{child_table}"
                ADD CONSTRAINT "{constraint}"
                FOREIGN KEY ("{child_column}")
                REFERENCES "{parent_table}" ("{parent_column}"){delete_clause}
                '''
            )
        )

    with engine.begin() as conn:
        add_unique_if_clean(conn, "bbdd_clientes", "rut", "uq_bbdd_clientes_rut")
        for spec in [
            {
                "constraint": "fk_bbdd_sucursales_cliente_rut",
                "child_table": "bbdd_sucursales",
                "child_column": "rut",
                "parent_table": "bbdd_clientes",
                "parent_column": "rut",
            },
            {
                "constraint": "fk_sucursal_contactos_sucursal",
                "child_table": "sucursal_contactos_emergencia",
                "child_column": "sucursal_id",
                "parent_table": "bbdd_sucursales",
                "parent_column": "id",
                "on_delete": "CASCADE",
            },
            {
                "constraint": "fk_sucursal_personas_sucursal",
                "child_table": "sucursal_personas_autorizadas",
                "child_column": "sucursal_id",
                "parent_table": "bbdd_sucursales",
                "parent_column": "id",
                "on_delete": "CASCADE",
            },
            {
                "constraint": "fk_sucursal_guardias_sucursal",
                "child_table": "sucursal_guardias",
                "child_column": "sucursal_id",
                "parent_table": "bbdd_sucursales",
                "parent_column": "id",
                "on_delete": "CASCADE",
            },
            {
                "constraint": "fk_venta_comercial_cliente_rut",
                "child_table": "venta_comercial",
                "child_column": "rut_cliente",
                "parent_table": "bbdd_clientes",
                "parent_column": "rut",
            },
            {
                "constraint": "fk_venta_ods_archivos_ods",
                "child_table": "venta_ods_archivos",
                "child_column": "ods_id",
                "parent_table": "venta_comercial",
                "parent_column": "id",
                "on_delete": "CASCADE",
            },
            {
                "constraint": "fk_venta_administracion_venta_comercial",
                "child_table": "venta_administracion",
                "child_column": "odt",
                "parent_table": "venta_comercial",
                "parent_column": "codigo",
                "on_delete": "CASCADE",
            },
            {
                "constraint": "fk_venta_finanzas_venta_comercial",
                "child_table": "venta_finanzas",
                "child_column": "odt",
                "parent_table": "venta_comercial",
                "parent_column": "codigo",
                "on_delete": "CASCADE",
            },
            {
                "constraint": "fk_venta_servicio_tecnico_venta_comercial",
                "child_table": "venta_servicio_tecnico",
                "child_column": "odt",
                "parent_table": "venta_comercial",
                "parent_column": "codigo",
                "on_delete": "CASCADE",
            },
            {
                "constraint": "fk_venta_operaciones_venta_comercial",
                "child_table": "venta_operaciones",
                "child_column": "odt",
                "parent_table": "venta_comercial",
                "parent_column": "codigo",
                "on_delete": "CASCADE",
            },
            {
                "constraint": "fk_protocolos_informes_registro",
                "child_table": "protocolos_informes",
                "child_column": "registro_id",
                "parent_table": "protocolos_registro",
                "parent_column": "id",
                "on_delete": "CASCADE",
            },
        ]:
            add_fk_if_clean(conn, **spec)


_support_notes_engine = None


def _client_notes_key(value: str | None) -> str:
    text_value = re.sub(r"\s+", " ", (value or "")).strip().casefold()
    text_value = unicodedata.normalize("NFD", text_value)
    text_value = "".join(ch for ch in text_value if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text_value).strip()


def _support_requesters_table_name() -> str:
    schema = re.sub(r"[^A-Za-z0-9_]", "", settings.support_db_schema or "")
    if schema and settings.support_db_url.startswith("postgresql"):
        return f'"{schema}"."requesters"'
    return "requesters"


def _support_users_table_name() -> str:
    schema = re.sub(r"[^A-Za-z0-9_]", "", settings.support_db_schema or "")
    if schema and settings.support_db_url.startswith("postgresql"):
        return f'"{schema}"."users"'
    return "users"


def _get_support_notes_engine():
    global _support_notes_engine
    if not settings.support_db_url:
        return None
    if _support_notes_engine is None:
        _support_notes_engine = build_engine(settings.support_db_url, pool_pre_ping=True)
    return _support_notes_engine


def _format_support_note_datetime(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "Sin fecha"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        return parsed.strftime("%d-%m-%Y %H:%M")
    return parsed.astimezone(ZoneInfo(settings.timezone)).strftime("%d-%m-%Y %H:%M")


def _parse_support_requester_notes(raw_notes: str | None) -> list[dict[str, str | int]]:
    if not raw_notes or not str(raw_notes).strip():
        return []
    notes_text = str(raw_notes).strip()
    try:
        parsed = json.loads(notes_text)
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, list):
        return [
            {
                "text": notes_text,
                "author": "Nota previa",
                "author_id": 0,
                "created_at": "",
                "created_at_display": "Sin fecha",
            }
        ]
    notes: list[dict[str, str | int]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        text_value = str(item.get("text", "") or "").strip()
        if not text_value:
            continue
        created_at = str(item.get("created_at", "") or "").strip()
        notes.append(
            {
                "text": text_value,
                "author": str(item.get("author", "") or "").strip() or "Agente",
                "author_id": int(item.get("author_id") or 0),
                "created_at": created_at,
                "created_at_display": _format_support_note_datetime(created_at),
            }
        )
    return notes


def _serialize_support_requester_notes(notes: list[dict[str, str | int]]) -> str:
    payload: list[dict[str, str | int]] = []
    for note in notes:
        text_value = str(note.get("text", "") or "").strip()
        if not text_value:
            continue
        payload.append(
            {
                "text": text_value,
                "author": str(note.get("author", "") or "").strip() or "Agente",
                "author_id": int(note.get("author_id") or 0),
                "created_at": str(note.get("created_at", "") or "").strip(),
            }
        )
    return json.dumps(payload, ensure_ascii=False)


def _support_requester_keys(row: dict) -> set[str]:
    values = [
        str(row.get("name") or ""),
        str(row.get("internal_name") or ""),
        str(row.get("email") or ""),
    ]
    return {key for key in (_client_notes_key(value) for value in values) if key}


def _support_fetch_requesters(conn) -> list[dict]:
    table_name = _support_requesters_table_name()
    inspector = inspect(conn)
    if not inspector.has_table("requesters", schema=settings.support_db_schema if settings.support_db_url.startswith("postgresql") else None):
        raise HTTPException(status_code=503, detail="La tabla requesters no existe en la base de soporte.")
    schema_arg = settings.support_db_schema if settings.support_db_url.startswith("postgresql") else None
    columns = {str(col.get("name", "")) for col in inspector.get_columns("requesters", schema=schema_arg)}
    internal_expr = "internal_name" if "internal_name" in columns else "'' AS internal_name"
    rows = conn.execute(
        text(f"SELECT id, name, {internal_expr}, email, notes FROM {_support_requesters_table_name()} ORDER BY id ASC")
    ).mappings().all()
    return [dict(row) for row in rows]


def _support_find_requesters(conn, client_name: str) -> list[dict]:
    target_key = _client_notes_key(client_name)
    if not target_key:
        return []
    return [
        row
        for row in _support_fetch_requesters(conn)
        if target_key in _support_requester_keys(row)
    ]


def _support_collect_client_notes(conn, client_name: str) -> list[dict[str, str | int]]:
    notes: list[dict[str, str | int]] = []
    seen: set[tuple[str, str, str]] = set()
    for requester in _support_find_requesters(conn, client_name):
        for note in _parse_support_requester_notes(str(requester.get("notes") or "")):
            text_value = str(note.get("text", "") or "").strip()
            note_key = (
                text_value,
                str(note.get("author", "") or ""),
                str(note.get("created_at", "") or ""),
            )
            if not text_value or note_key in seen:
                continue
            seen.add(note_key)
            enriched = dict(note)
            enriched["source_requester_id"] = int(requester.get("id") or 0)
            notes.append(enriched)
    notes.sort(key=lambda note: str(note.get("created_at", "") or ""))
    return notes


# Nota: sin @router — la ruta /api/client-notes vive en modules/client_notes.py,
# que despacha a esta implementación cuando la petición viene con token de Incidencias.
def get_client_internal_notes(client: str = Query("")) -> JSONResponse:
    client_name = re.sub(r"\s+", " ", (client or "").strip())
    if not client_name:
        return JSONResponse({"ok": True, "client": "", "notes": [], "count": 0})
    notes_engine = _get_support_notes_engine()
    if notes_engine is None:
        raise HTTPException(status_code=503, detail="SUPPORT_DB_URL no esta configurado.")
    with notes_engine.begin() as conn:
        notes = _support_collect_client_notes(conn, client_name)
    return JSONResponse({"ok": True, "client": client_name, "notes": notes, "count": len(notes)})


def add_client_internal_note(payload: dict = Body(...)) -> JSONResponse:
    client_name = re.sub(r"\s+", " ", str(payload.get("client", "") or "").strip())
    note_text = str(payload.get("note", "") or "").strip()
    if not client_name:
        raise HTTPException(status_code=400, detail="Debes indicar el cliente.")
    if not note_text:
        raise HTTPException(status_code=400, detail="Debes escribir una nota.")
    notes_engine = _get_support_notes_engine()
    if notes_engine is None:
        raise HTTPException(status_code=503, detail="SUPPORT_DB_URL no esta configurado.")

    author = re.sub(r"\s+", " ", str(payload.get("author", "") or "").strip()) or "Incidencias"
    table_name = _support_requesters_table_name()
    with notes_engine.begin() as conn:
        matches = _support_find_requesters(conn, client_name)
        if matches:
            requester = matches[0]
        else:
            conn.execute(
                text(f"INSERT INTO {table_name} (name, email, notes) VALUES (:name, NULL, NULL)"),
                {"name": client_name[:100] or "Cliente"},
            )
            matches = _support_find_requesters(conn, client_name)
            if not matches:
                raise HTTPException(status_code=500, detail="No se pudo crear el cliente para notas.")
            requester = matches[0]

        notes = _parse_support_requester_notes(str(requester.get("notes") or ""))
        notes.append(
            {
                "text": note_text,
                "author": author,
                "author_id": 0,
                "created_at": datetime.now(ZoneInfo(settings.timezone)).isoformat(),
            }
        )
        conn.execute(
            text(f"UPDATE {table_name} SET notes = :notes WHERE id = :id"),
            {"notes": _serialize_support_requester_notes(notes), "id": requester.get("id")},
        )
        merged_notes = _support_collect_client_notes(conn, client_name)

    return JSONResponse(
        {
            "ok": True,
            "client": client_name,
            "requester_id": int(requester.get("id") or 0),
            "notes": merged_notes,
            "count": len(merged_notes),
        }
    )


def _ensure_registro_optional_columns() -> None:
    optional_columns: dict[str, str] = {
        "detalle_problema": "TEXT",
        "observacion_soporte": "TEXT",
        "observacion_servicio": "TEXT",
        "observacion_coordinacion": "TEXT",
        "materiales": "TEXT",
        "responsable_cierre": "VARCHAR(40)",
        "causa_cierre": "VARCHAR(120)",
        "accion_cierre": "VARCHAR(120)",
        "resultado_cierre": "VARCHAR(120)",
        "pruebas_cierre": "TEXT",
        "requiere_seguimiento": "BIT",
        "drive_cierre_folder_id": "VARCHAR(255)",
        "drive_cierre_folder_url": "TEXT",
        "foto_1": "TEXT",
        "foto_2": "TEXT",
        "foto_3": "TEXT",
        "pdf_url": "TEXT",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("incidencias"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("incidencias")}
            for col_name, col_type in optional_columns.items():
                if col_name in existing_columns:
                    continue
                add_column(conn, "incidencias", col_name, col_type)
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'incidencias': %s", exc)


def _ensure_administracion_odt_optional_columns() -> None:
    optional_columns: dict[str, str] = {
        "tecnico": "VARCHAR(255)",
        "acompanante": "VARCHAR(255)",
        "fecha_derivacion": "DATETIME2",
        "recepcion_info": "BIT NOT NULL DEFAULT 0",
        "fecha_recepcion_info": "DATETIME2",
        "registro_alpha3": "BIT NOT NULL DEFAULT 0",
        "fecha_registro_alpha3": "DATETIME2",
        "registro_intranet": "BIT NOT NULL DEFAULT 0",
        "fecha_registro_intranet": "DATETIME2",
        "envio_solicitud_instalacion": "BIT NOT NULL DEFAULT 0",
        "fecha_envio_solicitud_instalacion": "DATETIME2",
        "envio_datos_facturacion": "BIT NOT NULL DEFAULT 0",
        "fecha_envio_datos_facturacion": "DATETIME2",
        "envio_carta_bienvenida": "BIT NOT NULL DEFAULT 0",
        "fecha_envio_carta_bienvenida": "DATETIME2",
        "finalizado": "BIT NOT NULL DEFAULT 0",
        "fecha_cierre": "DATETIME2",
        "updated_at": "DATETIME DEFAULT GETDATE()",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("venta_administracion"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("venta_administracion")}
            for col_name, col_type in optional_columns.items():
                if col_name in existing_columns:
                    continue
                add_column(conn, "venta_administracion", col_name, col_type)
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'venta_administracion': %s", exc)


def _ensure_venta_ods_optional_columns() -> None:
    optional_columns: dict[str, str] = {
        "drive_folder_id": "VARCHAR(255)",
        "drive_folder_url": "TEXT",
        "piriod_customer_id": "VARCHAR(60)",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("venta_comercial"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("venta_comercial")}
            for col_name, col_type in optional_columns.items():
                if col_name in existing_columns:
                    continue
                add_column(conn, "venta_comercial", col_name, col_type)
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'venta_comercial': %s", exc)


def _ensure_finanzas_odt_optional_columns() -> None:
    optional_columns: dict[str, str] = {
        "fecha_inicio_servicio": "VARCHAR(40)",
        "recepcion_datos_facturacion": "BIT NOT NULL DEFAULT 0",
        "fecha_recepcion_datos_facturacion": "DATETIME2",
        "creacion_clientes_piriod": "BIT NOT NULL DEFAULT 0",
        "fecha_creacion_clientes_piriod": "DATETIME2",
        "facturacion_instalacion": "BIT NOT NULL DEFAULT 0",
        "fecha_facturacion_instalacion": "DATETIME2",
        "facturacion_servicio": "BIT NOT NULL DEFAULT 0",
        "fecha_facturacion_servicio": "DATETIME2",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("venta_finanzas"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("venta_finanzas")}
            for col_name, col_type in optional_columns.items():
                if col_name in existing_columns:
                    continue
                add_column(conn, "venta_finanzas", col_name, col_type)
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'venta_finanzas': %s", exc)


def _ensure_servicio_tecnico_ventas_optional_columns() -> None:
    optional_columns: dict[str, str] = {
        "recepcion_solicitud_instalacion": "BIT NOT NULL DEFAULT 0",
        "fecha_recepcion_solicitud_instalacion": "DATETIME2",
        "llamar_cliente": "TEXT",
        "solicitud_materiales": "TEXT",
        "fecha_inicio_instalacion": "VARCHAR(40)",
        "fecha_fin_instalacion": "VARCHAR(40)",
        "fecha_inicio_trabajo": "DATETIME2",
        "fecha_fin_trabajo": "DATETIME2",
        "tecnico_a_cargo": "VARCHAR(255)",
        "acompanante": "VARCHAR(255)",
        "instalacion_finalizada": "BIT NOT NULL DEFAULT 0",
        "fecha_instalacion_finalizada": "DATETIME2",
        "finalizado": "BIT NOT NULL DEFAULT 0",
        "fecha_cierre": "DATETIME2",
        "camaras_instaladas_reales": "INT",
        "layout_final": "TEXT",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("venta_servicio_tecnico"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("venta_servicio_tecnico")}
            for col_name, col_type in optional_columns.items():
                if col_name in existing_columns:
                    continue
                add_column(conn, "venta_servicio_tecnico", col_name, col_type)
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'venta_servicio_tecnico': %s", exc)


def _ensure_rendiciones_optional_columns() -> None:
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("rendiciones"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("rendiciones")}
            if "folio" in existing_columns:
                dialect = dialect_name(conn)
                table = quote_ident("rendiciones", dialect)
                if dialect == "mssql":
                    for index_name in ("ix_rendiciones_folio", "idx_rendiciones_folio"):
                        conn.execute(
                            text(
                                "IF EXISTS (SELECT 1 FROM sys.indexes WHERE name=:name AND object_id=OBJECT_ID('rendiciones')) "
                                f"DROP INDEX {quote_ident(index_name, dialect)} ON {table}"
                            ),
                            {"name": index_name},
                        )
                else:
                    conn.execute(text('DROP INDEX IF EXISTS ix_rendiciones_folio'))
                    conn.execute(text('DROP INDEX IF EXISTS idx_rendiciones_folio'))
                drop_column(conn, "rendiciones", "folio")
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'rendiciones': %s", exc)


def _ensure_protocolos_optional_columns() -> None:
    optional_columns: dict[str, str] = {
        "protocolo_exitoso": "VARCHAR(20)",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("protocolos_registro"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("protocolos_registro")}
            for col_name, col_type in optional_columns.items():
                if col_name in existing_columns:
                    continue
                add_column(conn, "protocolos_registro", col_name, col_type)
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'protocolos_registro': %s", exc)


def _ensure_bbdd_clientes_optional_columns() -> None:
    optional_columns: dict[str, str] = {
        "giro": "VARCHAR(255)",
        "region": "VARCHAR(120)",
        "comuna": "VARCHAR(120)",
        "email_facturas": "VARCHAR(255)",
        "nombre_representante": "VARCHAR(255)",
        "rut_representante": "VARCHAR(40)",
        "telefono": "VARCHAR(32)",
        "email_representante": "VARCHAR(255)",
        "ejecutivo_email": "VARCHAR(255)",
        "fecha_creacion": "DATETIME2 DEFAULT GETDATE()",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("bbdd_clientes"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("bbdd_clientes")}
            for col_name, col_type in optional_columns.items():
                if col_name in existing_columns:
                    continue
                add_column(conn, "bbdd_clientes", col_name, col_type)
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'bbdd_clientes': %s", exc)


def _ensure_identity_optional_columns() -> None:
    optional_by_table: dict[str, dict[str, str]] = {
        "users": {
            "name": "VARCHAR(100)",
            "user": "VARCHAR(50)",
            "password": "VARCHAR(255)",
            "role": "VARCHAR(20) NOT NULL DEFAULT 'agent'",
            "departament": "VARCHAR(80)",
            "is_activate": "BIT NOT NULL DEFAULT 1",
            "created_at": "DATETIME2",
            "updated_at": "DATETIME2",
        },
        "login_sessions": {
            "user_id": "INTEGER",
            "area_code": "VARCHAR(50)",
            "department": "VARCHAR(80)",
        },
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            for table, optional_columns in optional_by_table.items():
                if not inspector.has_table(table):
                    continue
                existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns(table)}
                if table == "users":
                    for old_name, new_name in {
                        "username": "user",
                        "hashed_password": "password",
                        "is_active": "is_activate",
                        "department": "departament",
                    }.items():
                        if old_name in existing_columns and new_name not in existing_columns:
                            rename_column(conn, table, old_name, new_name)
                            existing_columns.remove(old_name)
                            existing_columns.add(new_name)
                for col_name, col_type in optional_columns.items():
                    if col_name in existing_columns:
                        continue
                    add_column(conn, table, col_name, col_type)
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales de identidad/usuarios: %s", exc)


def _seed_identity_data() -> None:
    db = SessionLocal()
    try:
        seed_default_identity_data(db)
    except Exception as exc:
        db.rollback()
        LOGGER.warning("No fue posible cargar usuarios/areas iniciales en BBDD: %s", exc)
    finally:
        db.close()


def _ensure_identity_views() -> None:
    try:
        with engine.begin() as conn:
            if engine.dialect.name == "mssql":
                conn.execute(text(
                    "IF OBJECT_ID('dbo.users_con_areas', 'V') IS NOT NULL "
                    "DROP VIEW dbo.users_con_areas"
                ))
            else:
                conn.execute(text("DROP VIEW IF EXISTS users_con_areas"))
    except Exception as exc:
        LOGGER.warning("No fue posible limpiar vista users_con_areas: %s", exc)


def _require_login(request: Request, db: Annotated[Session, Depends(get_db)]):
    """Dependencia para endpoints /api/* de este router que leen o modifican
    datos reales (incidencias, catalogo de clientes, rendiciones, finanzas,
    protocolos, etc.): exige la misma cookie de sesion que ya usan las
    paginas HTML (access_token, ver web.py._decode_cookie_token), o la sesion
    unificada de incidencias (atc_token/LoginSession), devolviendo 401 en vez
    de redirigir. Antes la gran mayoria de estos endpoints no verificaba
    ninguna sesion (hallazgo de auditoria de seguridad, ago 2026)."""
    from ATC.app.routes.web import COOKIE_NAME as _COOKIE_NAME, _decode_cookie_token as _decode_cookie_token_web
    from ATC.app.services.user_service import UserService as _UserService

    cookie = request.cookies.get(_COOKIE_NAME, "")
    if cookie:
        try:
            login = _decode_cookie_token_web(cookie)
            user = _UserService.find_by_login(db, login)
            if user and user.is_active:
                return user
        except Exception:
            pass

    token = (
        str(request.query_params.get("token") or "").strip()
        or str(request.cookies.get("atc_token") or "").strip()
    )
    if token:
        try:
            sesion = db.get(LoginSession, token)
            expires_at = getattr(sesion, "expires_at", None) if sesion else None
            if expires_at:
                now = datetime.now(expires_at.tzinfo) if getattr(expires_at, "tzinfo", None) else datetime.utcnow()
                if expires_at > now and getattr(sesion, "user_id", None):
                    user = db.get(User, int(sesion.user_id))
                    if user and user.is_active:
                        return user
        except Exception:
            pass
    raise HTTPException(status_code=401, detail="No autenticado.")


def get_service(db: Annotated[Session, Depends(get_db)]) -> IncidenciasService:
    return IncidenciasService(db)


def _current_user_label(current_user: object) -> str:
    for attr in ("name", "usuario", "login", "email"):
        value = str(getattr(current_user, attr, "") or "").strip()
        if value:
            return value
    return ""


def get_protocolos_service(db: Annotated[Session, Depends(get_db)]) -> ProtocolosService:
    return ProtocolosService(db)


@router.post("/ticketera/tickets/oficina-atc/create")
@router.post("/dashboard/tickets/oficina-atc/create")
def create_oficina_atc_ticket(
    service: Annotated[IncidenciasService, Depends(get_service)],
    subject: str = Form(...),
    content: str = Form(...),
    token: str = Form(""),
) -> JSONResponse:
    token_limpio = str(token or "").strip()
    if not token_limpio:
        raise HTTPException(status_code=401, detail="Sesion no valida.")

    sesion = service.db.get(LoginSession, token_limpio)
    expires_at = getattr(sesion, "expires_at", None) if sesion else None
    if expires_at is not None and expires_at.tzinfo is not None:
        session_expired = expires_at <= datetime.now(expires_at.tzinfo)
    else:
        session_expired = not expires_at or expires_at <= datetime.utcnow()
    if not sesion or session_expired or not sesion.user_id:
        raise HTTPException(status_code=401, detail="Sesion expirada o no valida.")

    current_user = service.db.get(User, int(sesion.user_id))
    if not current_user or not current_user.is_active:
        raise HTTPException(status_code=401, detail="Usuario no valido.")

    is_admin = str(current_user.role or "").strip().lower() == "superadmin"
    allowed_areas = {"incidencias", "soporte"}
    user_areas = set(service._area_codes_usuario(current_user))
    if not is_admin and not (user_areas & allowed_areas):
        raise HTTPException(status_code=403, detail="No tienes acceso a esta area.")

    subject = re.sub(r"\s+", " ", (subject or "").strip())
    content = (content or "").strip()
    if not subject:
        raise HTTPException(status_code=400, detail="Debes ingresar un asunto.")
    if not content:
        raise HTTPException(status_code=400, detail="Debes ingresar una descripcion.")

    requester_name = re.sub(r"\s+", " ", (current_user.name or "").strip()) or "Incidencias"

    try:
        requester = (
            service.db.query(Requester)
            .filter(Requester.name == requester_name[:100])
            .order_by(Requester.id.asc())
            .first()
        )
        if not requester:
            requester = Requester(
                name=requester_name[:100],
                internal_name=requester_name[:120],
            )
            service.db.add(requester)
            service.db.flush()

        ticket = Ticket(
            subject=subject,
            requester_id=requester.id,
            assigned_to_id=None,
            priority="",
            status="open",
            source="internal",
        )
        service.db.add(ticket)
        service.db.flush()

        message = Message(
            ticket_id=ticket.id,
            sender_type="agent",
            sender_id=current_user.id,
            sender_name=current_user.name,
            sender_email=getattr(current_user, "email", None),
            channel="internal",
            content=content,
            is_internal_note=False,
            created_at=chile_now(),
        )
        service.db.add(message)
        service.db.commit()
        service.db.refresh(ticket)

        return JSONResponse({"ok": True, "ticket_id": int(ticket.id), "subject": subject})
    except HTTPException:
        raise
    except Exception as exc:
        service.db.rollback()
        raise HTTPException(status_code=500, detail=f"No se pudo crear el ticket interno: {exc}") from exc


def _protocolos_weekly_worker_loop() -> None:
    # NOTA: la generacion automatica de mantenciones (Quilpue semanal, Llay
    # Llay mensual, Quintero/Concon trimestral) fue eliminada a pedido. El
    # resumen semanal de protocolos (mas abajo) SI sigue automatico.
    tz = ZoneInfo(settings.timezone or "America/Santiago")
    ultimo_dia_protocolo = ""
    while True:
        try:
            now = datetime.now(tz)
            if now.weekday() == 0 and now.hour >= 8:
                dia_key = now.strftime("%Y-%m-%d")
                if dia_key != ultimo_dia_protocolo:
                    db = SessionLocal()
                    try:
                        result = ProtocolosService(db).generar_resumenes_semanales_pendientes(forzar=True)
                        LOGGER.info("Resumen semanal protocolos: %s", result)
                    finally:
                        db.close()
                    ultimo_dia_protocolo = dia_key
        except Exception:
            LOGGER.exception("Fallo el worker semanal automatico (mantenciones/protocolos).")
        time.sleep(900)


def _resolver_token_atc(
    request: Request,
    service: IncidenciasService,
    token: str,
) -> tuple[str, RedirectResponse | None]:
    """Recupera el token de sesion desde la cookie "atc_token" (seteada en
    /api/login) cuando la URL no trae uno, para no depender de tenerlo
    siempre visible en la barra de direcciones. Si SI llega un token valido
    por la URL, arma el redirect que lo deja en la cookie y limpia la URL
    (mismo patron que venta.py._guard_page). Devuelve (token_a_usar,
    redirect_o_None) — si hay redirect, el caller debe devolverlo tal cual."""
    token_url = str(token or "").strip()
    if not token_url:
        token_cookie = str(request.cookies.get("atc_token") or "").strip()
        if token_cookie and service.usuario_logueado_por_token(token_cookie):
            return token_cookie, None
        return token, None
    if service.usuario_logueado_por_token(token_url):
        sesion = service.db.get(LoginSession, token_url)
        clean_url = str(request.url.remove_query_params(["token"]))
        resp = RedirectResponse(url=clean_url, status_code=303)
        resp.set_cookie(
            key="atc_token",
            value=token_url,
            httponly=False,
            samesite="lax",
            max_age=max_age_cookie_segundos(sesion.user_id if sesion else None, 60 * 60 * 18),
        )
        return token_url, resp
    return token, None


@router.get("/", response_class=HTMLResponse)
def do_get(
    request: Request,
    form: str = Query(default="login"),
    tecnico: str = Query(default=""),
    cliente: str = Query(default=""),
    odt: str = Query(default=""),
    token: str = Query(default=""),
    next_form: str = Query(default="auto", alias="next"),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    token, _token_redirect = _resolver_token_atc(request, service, token)
    if _token_redirect:
        return _token_redirect

    form_aliases = {
        "tabla": "servicioTecnico",
        "STVentas": "stVentas",
        "TablaServicioTecnico": "stVentas",
        "tablaServicioTecnico": "stVentas",
        "servicioTécnico": "servicioTecnico",
        "servicioTecnico": "servicioTecnico",
        "coordinación": "coordinacion",
        "coordinacion": "coordinacion",
    }
    form = form_aliases.get(form, form)
    next_form = form_aliases.get(next_form, next_form)
    formularios_validos = {
        "login",
        "panelSelectorSoporte",
        "panelSelector",
        "panelSelectorServicio",
        "panelSelectorCoordinacion",
        "panelSelectorAdministracion",
        "panelSelectorVenta",
        "panelSelectorSupervisores",
        "panelSelectorGerencia",
        "incidencias",
        "cierreAperturaClientes",
        "cierreAperturaImagenes",
        "controlProtocolos",
        "tablaProtocolos",
        "envioProtocolosSemanales",
        "pendientes",
        "tecnicos",
        "coordinacion",
        "resumen",
        "formularioViatico",
        "servicioTecnico",
        "stVentas",
        "rendiciones",
        "rendicionesTecnico",
        "dashboardOperacional",
        "dashboardAnalitico",
        "pruebasSonido",
    }

    if form not in formularios_validos:
        html = (
            f"<h2 style='font-family:sans-serif;color:darkred'>&#9888; Formulario desconocido: <code>{form}</code></h2>"
            "<p style='font-family:sans-serif'>Verifica que la URL este escrita correctamente.</p>"
        )
        return HTMLResponse(content=html, status_code=400)

    if form == "panelSelectorVenta" and not service.usuario_logueado_por_token(token):
        return RedirectResponse(url=f"/?form=login&next={form}", status_code=303)

    if form in {
        "panelSelectorSoporte",
        "panelSelector",
        "panelSelectorServicio",
        "panelSelectorCoordinacion",
        "panelSelectorAdministracion",
        "panelSelectorVenta",
        "panelSelectorSupervisores",
        "panelSelectorGerencia",
        "incidencias",
        "cierreAperturaClientes",
        "cierreAperturaImagenes",
        "controlProtocolos",
        "tablaProtocolos",
        "envioProtocolosSemanales",
        "pendientes",
        "tecnicos",
        "coordinacion",
        "formularioViatico",
        "rendiciones",
        "rendicionesTecnico",
        "servicioTecnico",
        "stVentas",
    } and not service.usuario_logueado_por_token(token):
        return RedirectResponse(url=f"/?form=login&next={form}", status_code=303)

    if form in {"servicioTecnico", "panelSelectorServicio", "stVentas"} and not service.usuario_autorizado_para_tabla(token):
        return RedirectResponse(url=f"/?form=login&next={form}", status_code=303)

    # panelSelectorSoporte fue eliminado; el panel oficial de soporte vive en /panel?area=soporte (ATC.app).
    # Redirigimos vía SSO bridge para que setee la cookie web y aterrice en el panel correcto.
    if form == "panelSelectorSoporte":
        helpdesk = (settings.helpdesk_base_url or "").rstrip("/")
        base = helpdesk if helpdesk else ""
        if token:
            return RedirectResponse(url=f"{base}/sso/login?token={token}&next=/panel?area=soporte", status_code=303)
        return RedirectResponse(url=f"{base}/login" if base else "/?form=login&next=panelSelectorSoporte", status_code=303)

    if form == "panelSelectorSupervisores":
        helpdesk = (settings.helpdesk_base_url or "").rstrip("/")
        base = helpdesk if helpdesk else ""
        if token:
            return RedirectResponse(url=f"{base}/supervisores?token={token}&next=panelSelectorSupervisores", status_code=303)
        return RedirectResponse(url="/?form=login&next=panelSelectorSupervisores", status_code=303)

    if form == "panelSelectorGerencia":
        helpdesk = (settings.helpdesk_base_url or "").rstrip("/")
        base = helpdesk if helpdesk else ""
        if token:
            return RedirectResponse(url=f"{base}/sso/login?token={token}&next=/gerencia", status_code=303)
        return RedirectResponse(url="/?form=login&next=panelSelectorGerencia", status_code=303)

    if form in {
        "tecnicos",
        "rendiciones",
        "rendicionesTecnico",
        "panelSelector",
        "panelSelectorServicio",
        "panelSelectorCoordinacion",
        "panelSelectorAdministracion",
        "panelSelectorVenta",
        "panelSelectorSupervisores",
        "panelSelectorGerencia",
        "coordinacion",
        "tablaProtocolos",
        "envioProtocolosSemanales",
        "pruebasSonido",
    } and token:
        tecnico = service.get_usuario_actual(token)
    view_map = {
        "login": "login.html",
        "panelSelector": "seleccion_panel_operadores.html",
        "panelSelectorServicio": "seleccion_panel_servicio.html",
        "panelSelectorCoordinacion": "seleccion_panel_coordinacion.html",
        "panelSelectorAdministracion": "seleccion_panel_administracion.html",
        "panelSelectorVenta": "seleccion_panel_venta.html",
        "panelSelectorSupervisores": "seleccion_panel_supervisores.html",
        "panelSelectorGerencia": "seleccion_panel_gerencia.html",
        "servicioTecnico": "incidencias_servicio_tecnico.html",
        "stVentas": "tabla_servicio_tecnico_venta.html",
        "incidencias": "incidencias_puestos.html",
        "cierreAperturaClientes": "cierre_apertura_clientes.html",
        "cierreAperturaImagenes": "cierre_apertura_imagenes.html",
        "controlProtocolos": "control_protocolos.html",
        "tablaProtocolos": "tabla_protocolos.html",
        "envioProtocolosSemanales": "envio_protocolos_semanales.html",
        "pendientes": "pendientes.html",
        "tecnicos": "tecnicos.html",
        "coordinacion": "incidencias_coordinacion.html",
        "formularioViatico": "formulario_viatico.html",
        "rendiciones": "rendiciones.html",
        "rendicionesTecnico": "rendiciones_tecnico.html",
        "dashboardOperacional": "dashboardOperacional.html",
        "dashboardAnalitico": "dashboardAnalitico.html",
        "pruebasSonido": "pruebas_sonido.html",
    }
    tpl = view_map.get(form, "incidencias_servicio_tecnico.html")
    show_back_button = False
    back_url = f"/seleccionar-area?token={token}" if token else "/seleccionar-area"
    if form in {
        "panelSelectorSoporte",
        "panelSelector",
        "panelSelectorServicio",
        "panelSelectorCoordinacion",
        "panelSelectorAdministracion",
        "panelSelectorVenta",
        "panelSelectorSupervisores",
        "panelSelectorGerencia",
    } and token:
        es_superadmin = False
        try:
            _ses = service.db.query(LoginSession).filter(LoginSession.token == token).first()
            if _ses and _ses.user_id:
                _usr = service.db.get(User, int(_ses.user_id))
                es_superadmin = str(getattr(_usr, "role", "") or "").strip().lower() == "superadmin"
        except Exception:
            es_superadmin = False
        # Superadmin siempre ve Volver (navega entre todas las areas);
        # el resto solo si tiene mas de un area asignada.
        if es_superadmin or service.contar_areas_para_token(token) > 1:
            show_back_button = True
            helpdesk = (settings.helpdesk_base_url or "").rstrip("/")
            if helpdesk:
                back_url = f"{helpdesk}/seleccionar-area?token={token}"
    bitacora_enabled = False
    if form == "panelSelector" and token:
        try:
            sesion = service.db.get(LoginSession, token.strip())
            if sesion and sesion.user_id:
                _u = service.db.get(User, int(sesion.user_id))
                bitacora_enabled = can_access_bitacora(_u)
        except Exception:
            bitacora_enabled = False

    pendiente_incidencias_st = 0
    pendiente_ventas_st = 0
    pendiente_rendiciones_st = 0
    if tpl == "seleccion_panel_servicio.html":
        import unicodedata as _ud

        def _sin_acentos(v: str) -> str:
            n = _ud.normalize("NFD", str(v or "").strip().lower())
            return "".join(c for c in n if _ud.category(c) != "Mn")

        # Ojo: "derivacion" tiene mojibake historico ("Servicio Tcnico",
        # "Servicio T?cnico", "Servicio Tecnico" son la misma cosa) — se usa
        # un LIKE amplio en vez de comparar el string exacto para no perder
        # miles de filas antiguas. "Repetida" se excluye (duplicado, no es
        # trabajo pendiente real).
        pendiente_incidencias_st = (
            service.db.query(Registro.id)
            .filter(
                or_(
                    Registro.derivacion.ilike("%servicio t%cnico%"),
                    Registro.derivacion.ilike("%cnico externo%"),
                    Registro.derivacion.ilike("%tecnico externo%"),
                ),
                or_(Registro.estado.is_(None), ~Registro.estado.ilike("Termin%")),
                or_(Registro.estado.is_(None), Registro.estado != "Repetida"),
            )
            .count()
        )

        from ATC.app.services.venta_service import get_servicio_tecnico_ventas_rows

        try:
            _ensure_servicio_tecnico_ventas_optional_columns()
            filas_ventas_st = get_servicio_tecnico_ventas_rows(service.db)
            pendiente_ventas_st = sum(
                1
                for fila in filas_ventas_st
                if not fila.get("anulada") and not fila.get("estados", {}).get("instalacion_finalizada")
            )
        except Exception as exc:
            service.db.rollback()
            LOGGER.warning("No fue posible calcular pendientes de ventas ST para panel servicio: %s", exc)
            pendiente_ventas_st = 0

        pendiente_rendiciones_st = sum(
            1
            for (estado_rev,) in service.db.query(Rendicion.estado_revision).all()
            if not any(e in _sin_acentos(estado_rev) for e in ("acept", "rechaz", "pagad"))
        )

    pendiente_incidencias_coord = 0
    pendiente_protocolos_semanales = 0
    if tpl == "seleccion_panel_coordinacion.html":
        # Mismo criterio de mojibake que en servicio tecnico: "Coordinacion"
        # aparece como "Coordinacin" (sin la o con tilde) en filas antiguas.
        pendiente_incidencias_coord = (
            service.db.query(Registro.id)
            .filter(
                or_(
                    Registro.derivacion.ilike("%client%"),
                    Registro.derivacion.ilike("%coordinaci%"),
                ),
                or_(Registro.estado.is_(None), ~Registro.estado.ilike("Termin%")),
                or_(Registro.estado.is_(None), Registro.estado != "Repetida"),
            )
            .count()
        )

        pendiente_protocolos_semanales = (
            service.db.query(ProtocoloInforme.id)
            .filter(
                ProtocoloInforme.tipo_informe == "SEMANAL",
                ProtocoloInforme.estado.notin_(["ENVIADO", "RECHAZADO"]),
            )
            .count()
        )

    context = {
        "request": request,
        "title": "servicioTecnico" if form == "servicioTecnico" else form,
        "token": token,
        "tecnico": tecnico,
        "cliente": cliente,
        "odt": odt,
        "show_back_button": show_back_button,
        "back_url": back_url,
        "bitacora_enabled": bitacora_enabled,
        "pendiente_incidencias_st": pendiente_incidencias_st,
        "pendiente_ventas_st": pendiente_ventas_st,
        "pendiente_rendiciones_st": pendiente_rendiciones_st,
        "pendiente_incidencias_coord": pendiente_incidencias_coord,
        "pendiente_protocolos_semanales": pendiente_protocolos_semanales,
        "next_form": next_form
        if next_form
        in {
            "panelSelectorSoporte",
            "panelSelector",
            "panelSelectorServicio",
            "panelSelectorCoordinacion",
            "panelSelectorAdministracion",
            "panelSelectorVenta",
            "panelSelectorSupervisores",
            "panelSelectorGerencia",
            "incidencias",
            "cierreAperturaClientes",
            "cierreAperturaImagenes",
            "controlProtocolos",
            "tablaProtocolos",
            "envioProtocolosSemanales",
            "pendientes",
            "tecnicos",
            "coordinacion",
            "formularioViatico",
            "rendiciones",
            "rendicionesTecnico",
            "servicioTecnico",
            "stVentas",
            "auto",
        }
        else "auto",
    }
    resp = templates.TemplateResponse(request, tpl, context)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@router.post("/api/login", response_model=LoginResponse)
def check_login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    from ATC.app.core.rate_limit import is_locked_out, record_failure, record_success

    identificador = str(payload.nombre_tecnico or "").strip()
    if is_locked_out(identificador):
        return LoginResponse(success=False, message="Demasiados intentos fallidos. Espera unos minutos antes de volver a intentar.")

    app_url = str(request.base_url).rstrip("/")
    data = service.check_login(
        payload.nombre_tecnico,
        payload.clave,
        payload.token,
        app_url,
        payload.destino or "auto",
    )
    if data.get("success"):
        record_success(identificador)
    else:
        record_failure(identificador)
    if data.get("success") and data.get("token"):
        response.set_cookie(
            key="atc_token",
            value=str(data["token"]),
            httponly=False,
            samesite="lax",
            max_age=max_age_cookie_segundos(data.get("user_id"), 60 * 60 * 18),
        )
    return LoginResponse(**data)


@router.post("/api/logout")
def logout(token: str, service: Annotated[IncidenciasService, Depends(get_service)]):
    return {"ok": service.logout(token)}




@router.get("/resumen-equipos-tecnicos", response_class=HTMLResponse)
def resumen_equipos_tecnicos_page(
    request: Request,
    token: str = Query(default=""),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    token, _token_redirect = _resolver_token_atc(request, service, token)
    if _token_redirect:
        return _token_redirect
    if not service.usuario_autorizado_para_resumen_equipos(token):
        return RedirectResponse(url="/?form=login&next=auto", status_code=303)
    resumen = service.obtener_resumen_equipos_tecnicos_hoy(
        incluir_pendientes_prioritarios=service.usuario_admin_para_resumen_equipos(token)
    )
    resp = templates.TemplateResponse(
        request,
        "resumen_equipos_tecnicos.html",
        {
            "request": request,
            "resumen": resumen,
            "token": token,
        },
    )
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


MANTENCION_VINA_PREFIJO = "MANT-VDM-"
MANTENCION_VINA_TOTAL_DEFAULT = 215
MANTENCION_VINA_TOTAL_MAXIMO = 999


def _mantencion_vina_config(db: Session) -> "MantencionVinaConfig":
    from ATC.app.models.incidencias import MantencionVinaConfig

    config = db.get(MantencionVinaConfig, 1)
    if not config:
        config = MantencionVinaConfig(id=1, total_puntos=MANTENCION_VINA_TOTAL_DEFAULT)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def _mantencion_vina_asegurar_puntos(db: Session, total: int) -> None:
    """Crea las filas Registro que falten para que 1..total tengan un punto
    real (idempotente) — necesario cuando el total sube."""
    existentes = {
        odt for (odt,) in db.query(Registro.odt).filter(Registro.odt.like(f"{MANTENCION_VINA_PREFIJO}%")).all()
    }
    ahora = datetime.now()
    creados = 0
    for n in range(1, total + 1):
        odt = f"{MANTENCION_VINA_PREFIJO}{n:03d}"
        if odt in existentes:
            continue
        db.add(Registro(
            odt=odt,
            fecha_registro=ahora,
            puesto=f"Punto {n}",
            cliente="Mantenciones Viña del Mar",
            problema="Mantencion Preventiva",
            detalle_problema=f"Mantencion preventiva de camara - punto {n}",
            derivacion="Mantencion Camara Vina del Mar",
            estado="Pendiente",
            direccion="Viña del Mar",
        ))
        creados += 1
    if creados:
        db.commit()


@router.get("/servicio-tecnico/mantenciones-vina-del-mar", response_class=HTMLResponse)
def mantenciones_vina_del_mar_page(
    request: Request,
    token: str = Query(default=""),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    # Publica a proposito: los tecnicos en terreno la abren desde el celular
    # sin necesidad de iniciar sesion; el tecnico/acompanante se ingresan a
    # mano en la propia pagina (ver campos + localStorage en el template).
    config = _mantencion_vina_config(service.db)
    total_puntos = config.total_puntos
    _mantencion_vina_asegurar_puntos(service.db, total_puntos)

    filas = (
        service.db.query(Registro.odt, Registro.estado, Registro.observacion_pendiente)
        .filter(Registro.odt.like(f"{MANTENCION_VINA_PREFIJO}%"))
        .all()
    )
    info_por_odt = {odt: (estado, obs_pend) for odt, estado, obs_pend in filas}

    puntos = []
    for n in range(1, total_puntos + 1):
        odt = f"{MANTENCION_VINA_PREFIJO}{n:03d}"
        estado, obs_pendiente = info_por_odt.get(odt, ("Pendiente", None))
        puntos.append({
            "numero": n,
            "odt": odt,
            "cerrado": str(estado or "").strip().lower() == "terminado",
            "obsPendiente": bool(str(obs_pendiente or "").strip()),
        })

    # Solo tecnicos "de campo" reales: department == 'Tecnicos' exacto (no
    # 'Servicio Tecnico', que es otra area/rol) — mismo criterio que en
    # Documentacion Tecnicos de Prevencion.
    usuarios = service.db.query(User).filter(User.is_active == True).order_by(User.name.asc()).all()
    tecnicos_nombres = []
    for u in usuarios:
        if str(u.role or "").strip().lower() in ("admin", "superadmin"):
            continue
        partes = {p.strip().lower() for p in str(u.department or "").split(";") if p.strip()}
        if "tecnicos" in partes or "técnicos" in partes:
            tecnicos_nombres.append(u.name)

    return templates.TemplateResponse(
        request,
        "mantenciones_vina_del_mar.html",
        {
            "request": request,
            "token": token,
            "puntos": puntos,
            "tecnicos_nombres": tecnicos_nombres,
            "total_puntos": total_puntos,
        },
    )


@router.patch("/api/servicio-tecnico/mantenciones-vina-del-mar/{odt}/tecnico")
def mantenciones_vina_del_mar_set_tecnico(
    odt: str,
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    odt_limpia = str(odt or "").strip()
    if not odt_limpia.startswith(MANTENCION_VINA_PREFIJO):
        raise HTTPException(status_code=400, detail="Punto invalido.")
    row = service.db.query(Registro).filter(Registro.odt == odt_limpia).first()
    if not row:
        raise HTTPException(status_code=404, detail="Punto no encontrado.")
    row.tecnicos = str(payload.get("tecnico") or "").strip() or None
    row.acompanante = str(payload.get("acompanante") or "").strip() or None
    service.db.commit()
    return {"ok": True}


@router.patch("/api/servicio-tecnico/mantenciones-vina-del-mar/total")
def mantenciones_vina_del_mar_set_total(
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    try:
        nuevo_total = int(payload.get("total"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Total invalido.")
    if nuevo_total < 1 or nuevo_total > MANTENCION_VINA_TOTAL_MAXIMO:
        raise HTTPException(status_code=400, detail="Total fuera de rango.")

    config = _mantencion_vina_config(service.db)
    config.total_puntos = nuevo_total
    service.db.commit()
    _mantencion_vina_asegurar_puntos(service.db, nuevo_total)
    return {"ok": True, "total": nuevo_total}


@router.post("/api/servicio-tecnico/mantenciones-vina-del-mar/{odt}/pendiente")
def mantenciones_vina_del_mar_marcar_pendiente(
    odt: str,
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    odt_limpia = str(odt or "").strip()
    if not odt_limpia.startswith(MANTENCION_VINA_PREFIJO):
        raise HTTPException(status_code=400, detail="Punto invalido.")
    observacion = str(payload.get("observacion") or "").strip()
    if not observacion:
        raise HTTPException(status_code=400, detail="La observacion es obligatoria.")

    row = service.db.query(Registro).filter(Registro.odt == odt_limpia).first()
    if not row:
        raise HTTPException(status_code=404, detail="Punto no encontrado.")

    row.estado = "Pendiente"
    row.observacion_pendiente = observacion
    tecnico = str(payload.get("tecnico") or "").strip()
    acompanante = str(payload.get("acompanante") or "").strip()
    if tecnico:
        row.tecnicos = tecnico
    if acompanante:
        row.acompanante = acompanante
    service.db.commit()
    return {"ok": True}


@router.get("/tabla-soporte", response_class=HTMLResponse)
def tabla_soporte_local_page(
    request: Request,
    token: str = Query(default=""),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    token, _token_redirect = _resolver_token_atc(request, service, token)
    if _token_redirect:
        return _token_redirect
    if not service.usuario_logueado_por_token(token):
        return RedirectResponse(url="/?form=login&next=auto", status_code=303)
    return templates.TemplateResponse(
        request,
        "tabla_soporte_tecnico_venta.html",
        {"request": request, "token": token},
    )


@router.get("/api/login/usuarios")
def get_usuarios_login(
    destino: str = "tecnicos",
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    return {
        "usuarios": service.obtener_usuarios_login_tecnicos(destino),
        "detalles": service.obtener_usuarios_login_detalle(destino),
    }


@router.get("/api/listas/bbdd")
def obtener_listas_bbdd(service: Annotated[IncidenciasService, Depends(get_service)], current_user=Depends(_require_login)):
    return service.obtener_listas_bbdd()


@router.get("/api/materiales/buscar")
def buscar_materiales(
    q: str = "",
    limit: int = 10,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    try:
        return service.buscar_materiales_excel(q, limit)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/listas/incidencias")
def obtener_listas_incidencias(service: Annotated[IncidenciasService, Depends(get_service)], current_user=Depends(_require_login)):
    return service.obtener_listas_incidencias()


@router.get("/api/catalogo-clientes")
def obtener_catalogo_clientes(service: Annotated[IncidenciasService, Depends(get_service)], current_user=Depends(_require_login)):
    return service.obtener_catalogo_clientes()


@router.get("/api/registros")
def obtener_registros(
    tecnico: str = "",
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    try:
        return service.obtener_registros(tecnico)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/registros/administracion")
def obtener_registros_administracion(
    tecnico: str = "",
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    try:
        return service.obtener_registros_desde_administracion(tecnico)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/tecnico-externo", response_class=HTMLResponse)
def tecnico_externo_page(request: Request):
    return templates.TemplateResponse(
        request,
        "tecnicos.html",
        {
            "request": request,
            "token": "",
            "tecnico": "",
            "modo_externo": True,
        },
    )


@router.get("/api/incidencias/puesto")
def obtener_incidencias_por_puesto(
    service: Annotated[IncidenciasService, Depends(get_service)],
    tecnico: str = "",
    vista: str = "",
current_user=Depends(_require_login)):
    try:
        return service.obtener_incidencias_por_puesto(
            tecnico,
            solo_panel_tecnico=str(vista or "").strip().lower() == "tecnicos",
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/incidencias/servicio-tecnico")
def obtener_incidencias_servicio_tecnico(
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        return service.obtener_incidencias_servicio_tecnico()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/tecnicos/ruta-optima")
def obtener_ruta_optima_tecnico(
    service: Annotated[IncidenciasService, Depends(get_service)],
    tecnico: str = "",
current_user=Depends(_require_login)):
    try:
        return service.obtener_ruta_optima_tecnico(tecnico=tecnico)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/incidencias/coordinacion")
def obtener_incidencias_coordinacion(
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        return service.obtener_incidencias_derivadas_cliente()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/sucursal/detalle")
def obtener_detalle_sucursal(
    odt: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    return service.obtener_datos_sucursal_con_coordenadas(odt)


@router.get("/api/sucursal/incidencias")
def obtener_historial_sucursal(
    cliente: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    return service.obtener_ultimas_incidencias_sucursal(cliente)


@router.get("/api/incidencias/imagenes")
def obtener_imagenes_incidencia(
    odt: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    return service.obtener_imagenes_finalizacion(odt)


@router.get("/api/incidencias/imagenes-tabla")
def obtener_imagenes_tabla(
    odt: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    return {"odt": odt, "imagenes": service.obtener_imagenes_tabla(odt)}


@router.get("/api/incidencias/informes-odt-cierre")
def obtener_informes_odt_cierre(
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    return {"items": service.obtener_informes_cierre_odt()}


# ── Navegador Drive de informes ODT (carpeta raiz GOOGLE_DRIVE_ROOT_FOLDER_ID:
#    sucursales → carpetas "ODT Ixxxx" → informe PDF + fotos) ──
_DRIVE_ODT_CACHE: dict[str, tuple[float, dict]] = {}
_DRIVE_ODT_CACHE_TTL = 120.0


def _drive_odt_client():
    from ATC.app.services.drive_base_service import _build_clients

    drive, _docs = _build_clients()
    return drive


@router.get("/api/incidencias/drive-odt/listar")
def drive_odt_listar(folder_id: str = Query(default=""), current_user=Depends(_require_login)):
    import time as _time

    fid = (folder_id or "").strip() or str(settings.google_drive_root_folder_id or "").strip()
    if not re.fullmatch(r"[\w-]{10,}", fid):
        raise HTTPException(status_code=400, detail="folder_id invalido")
    ahora = _time.time()
    hit = _DRIVE_ODT_CACHE.get(fid)
    if hit and ahora - hit[0] < _DRIVE_ODT_CACHE_TTL:
        return hit[1]
    try:
        drive = _drive_odt_client()
        files: list[dict] = []
        page_token = None
        while True:
            res = drive.files().list(
                q=f"'{fid}' in parents and trashed=false",
                pageSize=1000,
                fields="nextPageToken, files(id,name,mimeType)",
                orderBy="name",
                pageToken=page_token,
            ).execute()
            files.extend(res.get("files", []))
            page_token = res.get("nextPageToken")
            if not page_token:
                break
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo listar Drive: {exc}") from exc
    out = {
        "folders": [{"id": f["id"], "name": f["name"]} for f in files if f["mimeType"] == "application/vnd.google-apps.folder"],
        "files": [{"id": f["id"], "name": f["name"], "mimeType": f["mimeType"]} for f in files if f["mimeType"] != "application/vnd.google-apps.folder"],
    }
    _DRIVE_ODT_CACHE[fid] = (ahora, out)
    return out


@router.get("/api/incidencias/drive-odt/buscar")
def drive_odt_buscar(q: str = Query(default=""), current_user=Depends(_require_login)):
    texto = str(q or "").strip().replace("'", "")
    if len(texto) < 2:
        return {"folders": []}
    try:
        drive = _drive_odt_client()
        res = drive.files().list(
            q=f"name contains '{texto}' and mimeType = 'application/vnd.google-apps.folder' and trashed=false",
            pageSize=60,
            fields="files(id,name)",
            orderBy="name",
        ).execute()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo buscar en Drive: {exc}") from exc
    return {"folders": [{"id": f["id"], "name": f["name"]} for f in res.get("files", [])]}


def _incidencias_jwt_cookie_autorizado(request: Request, db: Session) -> bool:
    # Los usuarios del panel unificado (/soporte, cookie JWT "access_token")
    # no tienen "atc_token" — sin este fallback, las miniaturas de "Imagenes
    # ODT" nunca cargaban para ellos aunque la imagen SI existiera en Drive
    # (pedido explicito, jul 2026).
    raw_token = request.cookies.get("access_token")
    if not raw_token:
        return False
    try:
        payload = jwt.decode(raw_token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
    except (JWTError, ValueError):
        return False
    username = payload.get("sub")
    if not username:
        return False
    user = db.query(User).filter(User.username == username).first()
    return bool(user and user.is_active)


@router.get("/api/incidencias/drive-image/{file_id}")
def obtener_drive_image(
    file_id: str,
    request: Request,
    token: str = Query(default=""),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    tok = (token or "").strip() or str(request.cookies.get("atc_token") or "").strip()
    autorizado = bool(service and service.usuario_logueado_por_token(tok))
    if not autorizado and service:
        autorizado = _incidencias_jwt_cookie_autorizado(request, service.db)
    if not autorizado:
        return RedirectResponse(url="/?form=login&next=auto", status_code=302)

    try:
        content, mime_type, filename = download_support_drive_file_bytes(file_id=file_id)
    except Exception as exc:
        msg = str(exc)
        if "File not found" in msg or "notFound" in msg:
            return RedirectResponse(
                url=f"https://drive.google.com/file/d/{file_id}/view",
                status_code=302,
            )
        raise HTTPException(status_code=404, detail=msg) from exc

    safe_name = (filename or "imagen").replace('"', "").replace("\\", "_").strip() or "imagen"
    headers = {
        "Content-Disposition": f'inline; filename="{safe_name}"',
        # El contenido de un file_id de Drive es inmutable en el uso que le
        # da esta app (nunca se edita in-place; un reemplazo sube un archivo
        # NUEVO con otro id — ver download_support_drive_file_bytes), así
        # que el navegador puede cachearlo por mucho tiempo sin revalidar —
        # pedido explicito, ago 2026 (antes 10 min, cada reapertura del
        # popup de una cámara volvía a pedirla al proxy).
        "Cache-Control": "private, max-age=31536000, immutable",
    }
    return Response(content=content, media_type=mime_type or "application/octet-stream", headers=headers)


@router.post("/api/incidencias/upload-image-tabla")
async def subir_imagenes_tabla(
    odt: str = Form(...),
    token: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    service: IncidenciasService = Depends(get_service),
current_user=Depends(_require_login)):
    payloads: list[dict[str, object]] = []
    for upload in files or []:
        if not upload:
            continue
        content = await upload.read()
        if not content:
            continue
        payloads.append(
            {
                "filename": upload.filename or "imagen.png",
                "mime_type": (upload.content_type or "image/png"),
                "bytes": content,
            }
        )

    try:
        return service.subir_imagenes_tabla(odt=odt, image_payloads=payloads, token=token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/incidencias/cierre-apertura/imagen")
async def subir_imagen_cierre_apertura(
    client_id: str = Form(...),
    client_name: str = Form(""),
    token: str = Form(""),
    file: UploadFile = File(...),
    service: IncidenciasService = Depends(get_service),
current_user=Depends(_require_login)):
    content = await file.read()
    try:
        return service.guardar_imagen_cierre_apertura(
            client_id=client_id,
            client_name=client_name,
            content=content,
            token=token,
            usuario_fallback=_current_user_label(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/incidencias/cierre-apertura/imagenes")
def obtener_imagenes_cierre_apertura(
    client_id: str = Query(default=""),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    return {"imagenes": service.listar_imagenes_cierre_apertura(client_id)}


@router.post("/api/formulario")
def enviar_formulario(
    payload: FormularioRegistro,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        message = service.enviar_formulario(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": message}


@router.post("/api/incidencias/nueva")
def guardar_incidencia_nueva(
    payload: IncidenciaNueva,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        odt = service.guardar_incidencia_nueva(payload, usuario_fallback=_current_user_label(current_user))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"odt": odt}


@router.post("/api/incidencias/multiples")
def enviar_multiples_incidencias(
    payload: list[IncidenciaNueva],
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        odts = service.enviar_multiples_incidencias(payload, usuario_fallback=_current_user_label(current_user))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"odts": odts}


@router.post("/api/incidencias/cerrar")
def cerrar_incidencia(
    payload: CerrarIncidenciaRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        ok = service.registrar_finalizacion_rapida(
            payload.odt,
            payload.observacion,
            responsable_cierre=payload.responsable_cierre,
            causa_cierre=payload.causa_cierre,
            accion_cierre=payload.accion_cierre,
            resultado_cierre=payload.resultado_cierre,
            pruebas_cierre=payload.pruebas_cierre,
            materiales=payload.materiales,
            materiales_sin_uso=payload.materiales_sin_uso,
            requiere_seguimiento=payload.requiere_seguimiento,
            token=payload.token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"result": ok}


@router.post("/api/incidencias/finalizar-completo")
def finalizar_incidencia_completo(
    payload: FinalizarIncidenciaRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        result = service.continuar_finalizacion_asincrona(
            payload.odt,
            payload.fotos_base64,
            payload.observacion,
            responsable_cierre=payload.responsable_cierre,
            causa_cierre=payload.causa_cierre,
            accion_cierre=payload.accion_cierre,
            resultado_cierre=payload.resultado_cierre,
            pruebas_cierre=payload.pruebas_cierre,
            materiales=payload.materiales,
            materiales_sin_uso=payload.materiales_sin_uso,
            requiere_seguimiento=payload.requiere_seguimiento,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if isinstance(result, dict):
        return result
    return {"result": result}


async def _read_cierre_odt_uploads(
    *,
    service: IncidenciasService,
    uploads: list[UploadFile],
    max_files: int,
) -> list[dict[str, object]]:
    """Lee las imagenes de cierre de ODT a memoria (no se escriben a disco:
    la subida a Drive se hace despues, en un thread de segundo plano —
    ver cerrar_instalacion_venta / continuar_finalizacion_asincrona)."""
    files = [upload for upload in (uploads or []) if upload and upload.filename]
    if not files:
        raise HTTPException(status_code=400, detail="Debes adjuntar al menos una imagen para cerrar la ODT.")
    if len(files) > max_files:
        raise HTTPException(status_code=400, detail=f"Solo puedes adjuntar hasta {max_files} imagenes.")

    payloads: list[dict[str, object]] = []
    for idx, upload in enumerate(files, start=1):
        mime_type = str(upload.content_type or "").lower()
        if not mime_type.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"{upload.filename or 'archivo'} no es una imagen valida.")

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > service.CIERRE_ODT_MAX_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"{upload.filename or 'archivo'} supera el limite de 10 MB.",
                )
            chunks.append(chunk)
        if total <= 0:
            raise HTTPException(status_code=400, detail=f"{upload.filename or 'archivo'} esta vacio.")
        payloads.append({
            "filename": service.nombre_staging_cierre_odt(idx, upload.filename or "", mime_type),
            "mime_type": mime_type,
            "bytes": b"".join(chunks),
        })
    return payloads


@router.post("/api/incidencias/finalizar-completo-archivos")
async def finalizar_incidencia_completo_archivos(
    odt: str = Form(...),
    diagnostico: str = Form(...),
    token: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    service: IncidenciasService = Depends(get_service),
current_user=Depends(_require_login)):
    odt_limpia = str(odt or "").strip()
    try:
        data = json.loads(diagnostico or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Diagnostico de cierre invalido.") from exc

    foto_payloads = await _read_cierre_odt_uploads(
        service=service,
        uploads=files,
        max_files=3,
    )
    # Igual que en cierre-instalacion-archivos: ya no se sube a Drive acá
    # sincrónico (bloqueaba la respuesta varios segundos) — se manda
    # foto_payloads y la subida + informe se hacen en segundo plano dentro
    # de continuar_finalizacion_asincrona — pedido explicito, ago 2026.
    try:
        service.registrar_finalizacion_rapida(
            odt_limpia,
            str(data.get("observacion") or ""),
            responsable_cierre=str(data.get("responsableCierre") or ""),
            causa_cierre=data.get("causaCierre") or [],
            accion_cierre=data.get("accionCierre") or [],
            resultado_cierre=str(data.get("resultadoCierre") or ""),
            pruebas_cierre=data.get("pruebasCierre") or [],
            materiales=data.get("materiales") or [],
            materiales_sin_uso=bool(data.get("materialesSinUso")),
            requiere_seguimiento=bool(data.get("requiereSeguimiento")),
            token=token,
        )
        result = service.continuar_finalizacion_asincrona(
            odt_limpia,
            [],
            str(data.get("observacion") or ""),
            responsable_cierre=str(data.get("responsableCierre") or ""),
            causa_cierre=data.get("causaCierre") or [],
            accion_cierre=data.get("accionCierre") or [],
            resultado_cierre=str(data.get("resultadoCierre") or ""),
            pruebas_cierre=data.get("pruebasCierre") or [],
            materiales=data.get("materiales") or [],
            materiales_sin_uso=bool(data.get("materialesSinUso")),
            requiere_seguimiento=bool(data.get("requiereSeguimiento")),
            foto_payloads=foto_payloads,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if isinstance(result, dict):
        result["imagenes_guardadas_local"] = len(foto_payloads)
        return result
    return {"result": result, "imagenes_guardadas_local": len(fotos)}


@router.post("/api/incidencias/cierre-instalacion")
def cerrar_instalacion_venta(
    service: Annotated[IncidenciasService, Depends(get_service)],
    payload: dict = Body(...),
current_user=Depends(_require_login)):
    try:
        cantidad_instalada_total = payload.get("cantidadInstaladaTotal") or payload.get("cantidad_instalada_total")
        return service.cerrar_instalacion_venta(
            str(payload.get("odt") or ""),
            observacion=str(payload.get("observacion") or ""),
            instalacion_completa=bool(payload.get("instalacionCompleta") or payload.get("instalacion_completa")),
            pruebas_cierre=payload.get("pruebasCierre") or payload.get("pruebas_cierre") or [],
            fotos_base64=payload.get("fotosBase64") or payload.get("fotos_base64") or [],
            token=str(payload.get("token") or ""),
            cantidad_instalada_total=int(cantidad_instalada_total) if cantidad_instalada_total else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/incidencias/cierre-instalacion-archivos")
async def cerrar_instalacion_venta_archivos(
    odt: str = Form(...),
    token: str = Form(""),
    diagnostico: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    service: IncidenciasService = Depends(get_service),
current_user=Depends(_require_login)):
    odt_limpia = str(odt or "").strip()
    try:
        data = json.loads(diagnostico or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Diagnostico de cierre invalido.") from exc

    foto_payloads = await _read_cierre_odt_uploads(
        service=service,
        uploads=files,
        max_files=service.MAX_FOTOS_CIERRE_ODS,
    )
    # La subida a Drive ya NO se hace acá sincrónica (era ~6-7s por foto,
    # bloqueando la respuesta — reportado como "Failed to fetch" al cerrar
    # una instalación con la red del técnico en terreno). Se manda
    # foto_payloads (bytes en memoria) directo a cerrar_instalacion_venta,
    # que cierra la ODT al toque y sube las fotos + genera el informe en un
    # thread de segundo plano — pedido explicito, ago 2026.
    cantidad_instalada_total = data.get("cantidadInstaladaTotal") or data.get("cantidad_instalada_total")
    try:
        result = service.cerrar_instalacion_venta(
            odt_limpia,
            observacion=str(data.get("observacion") or ""),
            instalacion_completa=bool(data.get("instalacionCompleta") or data.get("instalacion_completa")),
            pruebas_cierre=data.get("pruebasCierre") or data.get("pruebas_cierre") or [],
            fotos_base64=[],
            token=token,
            cantidad_instalada_total=int(cantidad_instalada_total) if cantidad_instalada_total else None,
            foto_payloads=foto_payloads,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result["imagenes_guardadas_local"] = len(foto_payloads)
    return result


@router.post("/api/incidencias/cierre-mantencion")
async def cerrar_mantencion_con_imagenes(
    odt: str = Form(...),
    token: str = Form(""),
    diagnostico: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    service: IncidenciasService = Depends(get_service),
current_user=Depends(_require_login)):
    odt_limpia = str(odt or "").strip()
    try:
        data = json.loads(diagnostico or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Diagnostico de cierre invalido.") from exc

    uploads = [upload for upload in (files or []) if upload and upload.filename]
    if not uploads:
        raise HTTPException(status_code=400, detail="Debes adjuntar al menos una imagen para cerrar una mantencion.")
    if len(uploads) > service.MANTENCION_CIERRE_MAX_IMAGENES:
        raise HTTPException(
            status_code=400,
            detail=f"Solo puedes adjuntar hasta {service.MANTENCION_CIERRE_MAX_IMAGENES} imagenes.",
        )

    try:
        service.validar_odt_mantencion_preventiva(odt_limpia)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    foto_payloads: list[dict[str, object]] = []
    for idx, upload in enumerate(uploads, start=1):
        mime_type = str(upload.content_type or "").lower()
        if not mime_type.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"{upload.filename or 'archivo'} no es una imagen valida.")

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > service.MANTENCION_CIERRE_MAX_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"{upload.filename or 'archivo'} supera el limite de 10 MB.",
                )
            chunks.append(chunk)
        if total <= 0:
            raise HTTPException(status_code=400, detail=f"{upload.filename or 'archivo'} esta vacio.")
        foto_payloads.append({
            "filename": service.nombre_staging_cierre_mantencion(idx, upload.filename or "", mime_type),
            "mime_type": mime_type,
            "bytes": b"".join(chunks),
        })

    def _coerce_lista(valor: object) -> list[str]:
        if isinstance(valor, list):
            return [str(v).strip() for v in valor if str(v).strip()]
        if isinstance(valor, tuple):
            return [str(v).strip() for v in valor if str(v).strip()]
        texto = str(valor or "").strip()
        return [texto] if texto else []

    try:
        return service.cerrar_mantencion_con_imagenes_staging(
            odt=odt_limpia,
            foto_payloads=foto_payloads,
            observacion=str(data.get("observacion") or ""),
            responsable_cierre=str(data.get("responsableCierre") or ""),
            causa_cierre=_coerce_lista(data.get("causaCierre")),
            accion_cierre=_coerce_lista(data.get("accionCierre")),
            resultado_cierre=str(data.get("resultadoCierre") or ""),
            pruebas_cierre=data.get("pruebasCierre") or [],
            materiales=data.get("materiales") or [],
            materiales_sin_uso=bool(data.get("materialesSinUso")),
            requiere_seguimiento=bool(data.get("requiereSeguimiento")),
            token=token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/incidencias/iniciar-trabajo")
def iniciar_trabajo_incidencia(
    payload: IniciarTrabajoRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        ok = service.marcar_inicio_trabajo(payload.odt, payload.token or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"result": ok}


@router.post("/api/incidencias/en-proceso")
def guardar_incidencia_en_proceso(
    payload: EnProcesoRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        ok = service.guardar_datos_en_proceso(
            payload.odt,
            payload.avance,
            payload.observacion,
            payload.token or "",
            camaras_instaladas=payload.camaras_instaladas,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"result": ok}


@router.get("/api/incidencias/sugerir-acompanante")
def sugerir_acompanante_tecnico(
    tecnico: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
    current_user=Depends(_require_login),
):
    """Solo lectura: acompañante más frecuente históricamente con este
    técnico, para que el frontend le pregunte al usuario antes de asignarlo
    (no se guarda nada acá)."""
    return {"acompanante": service.sugerir_acompanante(tecnico)}


@router.post("/api/incidencias/derivar-tecnico")
def derivar_incidencia_tecnico(
    payload: DerivarTecnicoRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        resultado = service.derivar_odt_a_tecnico(
            payload.odt,
            payload.tecnico,
            payload.acompanante or "",
            payload.derivacion or "Servicio Técnico",
            payload.estado or "Pendiente",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if resultado is None:
        raise HTTPException(status_code=404, detail=f"ODT {payload.odt} no encontrada")
    return {"ok": True, **resultado}


@router.patch("/api/incidencias/editar-tabla")
def editar_incidencia_tabla(
    payload: EditarIncidenciaTablaRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        result = service.editar_incidencia_tabla(
            token=payload.token,
            odt=payload.odt,
            derivacion=payload.derivacion,
            observacion=payload.observacion,
            prioridad=payload.prioridad,
            observacion_servicio=payload.observacion_servicio,
            observacion_final=payload.observacion_final,
            repetida_odt_ref=payload.repetida_odt_ref,
            editar_ultima_observacion_servicio=payload.editar_ultima_observacion_servicio,
            observacion_coordinacion=payload.observacion_coordinacion,
            eliminar_ultima_observacion_coordinacion=payload.eliminar_ultima_observacion_coordinacion,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=f"ODT {payload.odt} no encontrada")
    return result


@router.post("/api/incidencias/actualizar-region-comuna")
def actualizar_region_comuna(
    payload: ActualizarRegionComunaRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        result = service.actualizar_region_comuna_sucursal(
            tabla=payload.tabla,
            entidad_id=payload.entidad_id,
            region=payload.region,
            comuna=payload.comuna,
            observacion=payload.observacion,
            usuario=_current_user_label(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.get("/api/incidencias/odt/{odt}/observacion-cierre")
def obtener_observacion_cierre(
    odt: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    return {"odt": odt, "observacion_final": service.obtener_observacion_cierre_odt(odt)}


@router.patch("/api/incidencias/regenerar-informe-cierre")
def regenerar_informe_cierre(
    payload: RegenerarInformeCierreRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    if not service.usuario_logueado_por_token(payload.token):
        raise HTTPException(status_code=401, detail="Sesión expirada. Inicia sesión nuevamente.")
    try:
        result = service.regenerar_informe_cierre_odt(payload.odt, payload.observacion, payload.token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    folder_id = result.get("drive_folder_id") or ""
    if folder_id:
        _DRIVE_ODT_CACHE.pop(folder_id, None)
    return result


@router.post("/api/incidencias/cerrar-encargado")
def cerrar_incidencia_encargado(
    odt: str,
    fecha_cierre: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        dt = datetime.fromisoformat(fecha_cierre)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="fecha_cierre debe ser ISO-8601") from exc
    ok = service.cerrar_incidencia(odt, dt)
    if not ok:
        raise HTTPException(status_code=404, detail=f"ODT {odt} no encontrada")
    return {"ok": True}


@router.get("/api/tecnicos/pendientes")
def obtener_tecnicos_pendientes(service: Annotated[IncidenciasService, Depends(get_service)], current_user=Depends(_require_login)):
    return service.obtener_tecnicos_pendientes()


@router.get("/api/mantencion/sucursales")
def listar_sucursales_mantencion(current_user=Depends(_require_login)):
    from ATC.app.services.incidencias_service import (
        MANTENCIONES_PROGRAMADAS_QUILPUE,
        MANTENCIONES_TRIMESTRALES_QUINTERO,
        MANTENCIONES_TRIMESTRALES_CONCON,
        MANTENCIONES_MENSUALES_LLAY_LLAY,
    )
    seen: set[str] = set()
    result: list[str] = []
    for group in [
        *MANTENCIONES_PROGRAMADAS_QUILPUE.values(),
        MANTENCIONES_TRIMESTRALES_QUINTERO,
        MANTENCIONES_TRIMESTRALES_CONCON,
        MANTENCIONES_MENSUALES_LLAY_LLAY,
    ]:
        for s in group:
            key = s.strip().lower()
            if key not in seen:
                seen.add(key)
                result.append(s.strip())
    result.sort(key=lambda x: x.lower())
    return result


@router.post("/api/mantencion/correctiva")
def guardar_mantencion_correctiva(
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    return {"result": service.guardar_mantencion_correctiva(payload)}


@router.get("/api/mantencion/programada/plantilla")
def obtener_plantilla_mantencion_programada(
    sucursal: str,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    if not str(sucursal or "").strip():
        raise HTTPException(status_code=400, detail="sucursal es obligatoria.")
    imagenes = service.obtener_plantilla_imagenes_mantencion(sucursal)
    return {"sucursal": sucursal, "imagenes": imagenes, "total_imagenes": len(imagenes)}


@router.post("/api/mantencion/programada/plantilla")
def guardar_plantilla_mantencion_programada(
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    try:
        sucursal = str(payload.get("sucursal") or "").strip()
        imagenes = payload.get("imagenes") or []
        return service.guardar_plantilla_imagenes_mantencion(sucursal=sucursal, imagenes=imagenes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/mantencion/programada/plantilla-desde-odt")
def guardar_plantilla_mantencion_programada_desde_odt(
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    try:
        sucursal = str(payload.get("sucursal") or "").strip()
        odt_origen = str(payload.get("odt_origen") or "").strip()
        return service.guardar_plantilla_imagenes_mantencion_desde_odt(
            sucursal=sucursal,
            odt_origen=odt_origen,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/sucursales/por-cliente")
def obtener_sucursales_por_cliente(db: Annotated[Session, Depends(get_db)], current_user=Depends(_require_login)):
    from ATC.app.models.incidencias import ClienteBBDD, SucursalBBDD
    rows = (
        db.query(ClienteBBDD.cliente, SucursalBBDD.nombre_sucursal, SucursalBBDD.direccion_sucursal)
        .join(SucursalBBDD, SucursalBBDD.rut == ClienteBBDD.rut)
        .order_by(ClienteBBDD.cliente, SucursalBBDD.nombre_sucursal)
        .all()
    )
    result: dict[str, list[dict]] = {}
    for cliente, nombre, direccion in rows:
        result.setdefault(cliente, []).append({"nombre": nombre, "direccion": direccion or ""})
    return result


@router.get("/api/contactos/sucursal")
def obtener_contactos_por_sucursal(service: Annotated[IncidenciasService, Depends(get_service)], current_user=Depends(_require_login)):
    return service.obtener_contactos_por_sucursal()


# ──────────────────────────────────────────────
# Cámaras por sucursal — desde sucursal_camaras_monitoreo (BBDD real)
# (usado en incidencias_puestos.html)
# ──────────────────────────────────────────────

def _normalizar_nombre_sucursal_camaras(valor: str) -> str:
    s = unicodedata.normalize("NFD", str(valor or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.strip().lower().split())


def _cargar_camaras_por_sucursal(db: Session) -> dict:
    from ATC.app.models.incidencias import SucursalBBDD, SucursalCamaraMonitoreo

    rows = (
        db.query(
            SucursalBBDD.nombre_sucursal,
            SucursalCamaraMonitoreo.nombre_camara_monitoreo,
            SucursalCamaraMonitoreo.camara_sin_monitoreo,
            SucursalCamaraMonitoreo.cantidad_camaras,
            SucursalCamaraMonitoreo.cantidad_equipos,
        )
        .join(SucursalBBDD, SucursalCamaraMonitoreo.sucursal_id == SucursalBBDD.id)
        .all()
    )

    agrupado: dict[str, dict] = {}
    for nombre_sucursal, camara_monitoreo, camara_sin_monitoreo, cantidad_camaras, cantidad_equipos in rows:
        nombre_sucursal = (nombre_sucursal or "").strip()
        if not nombre_sucursal:
            continue
        clave = _normalizar_nombre_sucursal_camaras(nombre_sucursal)
        entrada = agrupado.setdefault(clave, {
            "sucursal": nombre_sucursal,
            "camaras": [],
            "_vistas": set(),
            "cantidad_equipos": 0,
            "cantidad_camaras": 0,
        })
        if cantidad_camaras:
            entrada["cantidad_camaras"] = max(entrada["cantidad_camaras"], int(cantidad_camaras))
        if cantidad_equipos:
            entrada["cantidad_equipos"] = max(entrada["cantidad_equipos"], int(cantidad_equipos))
        for camara in (camara_monitoreo, camara_sin_monitoreo):
            camara = (camara or "").strip()
            if camara and camara not in entrada["_vistas"]:
                entrada["_vistas"].add(camara)
                entrada["camaras"].append(camara)

    for entrada in agrupado.values():
        entrada.pop("_vistas", None)
        if entrada["cantidad_equipos"] < 1:
            entrada["cantidad_equipos"] = 1

    return agrupado


@router.get("/api/incidencias/camaras-por-sucursal")
def obtener_camaras_por_sucursal(db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return _cargar_camaras_por_sucursal(db)


# ──────────────────────────────────────────────
# "Calidad de Visita Técnica entre Incidencias" (dashboard_servicio.html):
# visitas repetidas del técnico a la MISMA cámara de una sucursal. El nombre
# de cámara no es un campo propio de `incidencias` — viaje como texto libre
# dentro de detalle_problema (lo arma el picker de checkboxes de
# incidencias_soporte.html, con prefijos segun el tipo de problema). Antes
# de construir esto se validó contra datos reales: con matching exacto
# contra sucursal_camaras_monitoreo, el historico completo solo calza en
# 0.5% de los casos (la mayoria escribe texto abreviado tipo "Camara 8" en
# vez del nombre completo real) — pero desde que el picker se empezó a usar
# en serio (~04-08-2026) el calce sube a ~21%, y sigue mejorando. Por eso se
# filtra desde ese corte: antes de esa fecha los datos no son confiables
# para esta métrica en particular (pedido explícito, ago 2026).
# ──────────────────────────────────────────────

CALIDAD_VISITA_CUTOFF = datetime(2026, 8, 4)

_CALIDAD_VISITA_PREFIJOS = (
    "desconocida de:",
    "camara movida:",
    "cámara movida:",
    "intermitencia:",
    "debido a internet de:",
    "camara caida:",
    "cámara caída:",
    "sin señal:",
    "sin senal:",
)


def _normalizar_nombre_camara(valor: str) -> str:
    s = unicodedata.normalize("NFD", str(valor or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s.strip()).upper()


def _parsear_camaras_de_detalle(detalle: str | None) -> list[str]:
    """Extrae los tokens de cámara/equipo candidatos de detalle_problema.
    Devuelve strings normalizados (mayúscula, sin tildes) — el llamador es
    quien decide cuáles calzan con una cámara real registrada."""
    texto = str(detalle or "")
    # Quita el prefijo de auditoria "[Nombre - dd/mm/aaaa hh:mm] "
    texto = re.sub(r"^\[[^\]]+\]\s*", "", texto)
    # Todo lo que sigue a "Contacto:" es el contacto de emergencia, no la cámara.
    texto = re.split(r"\bContacto\s*:", texto, maxsplit=1)[0]
    texto_low = texto.strip().lower()
    for prefijo in _CALIDAD_VISITA_PREFIJOS:
        if texto_low.startswith(prefijo):
            texto = texto[len(prefijo):]
            break
    return [_normalizar_nombre_camara(p) for p in texto.split(",") if p.strip()]


def _token_equipo_unico_equivale_todo(token: str, info_sucursal: dict | None) -> bool:
    try:
        cantidad_equipos = int((info_sucursal or {}).get("cantidad_equipos") or 1)
    except (TypeError, ValueError):
        cantidad_equipos = 1
    if cantidad_equipos != 1:
        return False
    return re.fullmatch(r"(NVR|EQUIPO)\s*0*1", token or "") is not None


def _calidad_visita_camaras(db: Session) -> list[dict]:
    from sqlalchemy import func

    camaras_por_sucursal = _cargar_camaras_por_sucursal(db)

    rows = (
        db.query(
            Registro.odt,
            Registro.cliente,
            Registro.problema,
            Registro.tecnicos,
            Registro.fecha_registro,
            Registro.detalle_problema,
        )
        .filter(Registro.derivacion.ilike("%servicio t%"))
        .filter(func.lower(func.trim(Registro.estado)) != "repetida")
        .filter(Registro.detalle_problema.isnot(None))
        .filter(Registro.fecha_registro >= CALIDAD_VISITA_CUTOFF)
        .order_by(Registro.fecha_registro.asc())
        .all()
    )

    # sucursal_key -> { camara_normalizada: nombre_camara_original }
    camaras_por_sucursal_norm: dict[str, dict[str, str]] = {
        clave: {_normalizar_nombre_camara(c): c for c in info["camaras"]}
        for clave, info in camaras_por_sucursal.items()
    }

    visitas: dict[tuple[str, str], list[dict]] = {}
    sucursal_display: dict[str, str] = {}

    for odt, cliente, problema, tecnicos, fecha_registro, detalle in rows:
        suc_key = _normalizar_nombre_sucursal_camaras(cliente or "")
        cam_by_norm = camaras_por_sucursal_norm.get(suc_key)
        if not cam_by_norm:
            continue
        info_sucursal = camaras_por_sucursal.get(suc_key, {})
        camaras_afectadas: list[str] = []
        for token in _parsear_camaras_de_detalle(detalle):
            if token == "TODO" or _token_equipo_unico_equivale_todo(token, info_sucursal):
                camaras_afectadas = list(cam_by_norm.values())
                break
            if token not in cam_by_norm:
                continue
            camaras_afectadas.append(cam_by_norm[token])
        for camara_original in dict.fromkeys(camaras_afectadas):
            key = (suc_key, camara_original)
            visitas.setdefault(key, []).append(
                {
                    "fecha": fecha_registro.strftime("%Y-%m-%d") if fecha_registro else None,
                    "tecnico": (tecnicos or "").strip() or "Sin técnico asignado",
                    "motivo": (problema or "").strip() or "Sin especificar",
                    "odt": odt or "",
                }
            )
            sucursal_display[suc_key] = info_sucursal["sucursal"]

    agrupado: dict[str, dict] = {}
    for (suc_key, camara), lista_visitas in visitas.items():
        entrada = agrupado.setdefault(suc_key, {"sucursal": sucursal_display[suc_key], "camaras": []})
        entrada["camaras"].append({"camara": camara, "visitas": lista_visitas})

    return list(agrupado.values())


# ──────────────────────────────────────────────
# "Calidad de Instalación" (dashboard_servicio.html): cámaras recién
# instaladas que se caen poco después. A diferencia de "Calidad de Visita"
# de arriba, acá SÍ se necesita identificar la cámara puntual instalada —
# pero el campo pensado para eso (sucursal_camaras_monitoreo.odt_origen)
# está 100% vacío (0 de 3153 filas) y nunca se usó, y solo hay 7 ODT en toda
# la BBDD con SoporteTecnicoVentaODT.camaras_registradas (el nombre exacto
# de cada cámara instalada) poblado. No hay base histórica confiable para
# ligar "esta cámara puntual" a una caída posterior. Por pedido explícito
# (ago 2026) se cuenta solo desde CALIDAD_INSTALACION_CUTOFF en adelante,
# asumiendo que camaras_registradas se empieza a llenar de forma consistente
# desde ahora — antes de esa fecha esta métrica no es confiable.
# ──────────────────────────────────────────────

CALIDAD_INSTALACION_CUTOFF = datetime(2026, 8, 11)

# Mismas categorías que usaba el demo original para "caída" — desconexión o
# falla de video de la cámara misma; otros tipos (alarma, cámara sucia, etc)
# no son indicio de una instalación defectuosa.
_CALIDAD_INSTALACION_FALLA_TIPOS = {"DESCONEXION", "FALLA DE VIDEO"}


def _calidad_instalacion_camaras(db: Session) -> list[dict]:
    from ATC.app.models.incidencias import ServicioTecnicoVentaODT, SoporteTecnicoVentaODT, VentaODS

    instal_rows = (
        db.query(SoporteTecnicoVentaODT, ServicioTecnicoVentaODT, VentaODS)
        .join(VentaODS, VentaODS.codigo == SoporteTecnicoVentaODT.odt)
        .outerjoin(ServicioTecnicoVentaODT, ServicioTecnicoVentaODT.odt == SoporteTecnicoVentaODT.odt)
        .filter(SoporteTecnicoVentaODT.camaras_registradas.isnot(None))
        .all()
    )

    instalaciones: list[dict] = []
    for sop, st, v in instal_rows:
        fecha_instalacion = (
            (st.fecha_instalacion_finalizada if st else None)
            or sop.fecha_configuracion_camaras
            or sop.fecha_terminado
        )
        if not fecha_instalacion or fecha_instalacion < CALIDAD_INSTALACION_CUTOFF:
            continue
        try:
            nombres_camaras = json.loads(sop.camaras_registradas or "[]")
        except Exception:
            nombres_camaras = []
        if not isinstance(nombres_camaras, list) or not nombres_camaras:
            continue
        sucursal = (v.nombre_sucursal or v.razon_social or "").strip()
        if not sucursal:
            continue
        for nombre_camara in nombres_camaras:
            nombre_camara = str(nombre_camara or "").strip()
            if not nombre_camara:
                continue
            instalaciones.append(
                {
                    "ods": sop.odt,
                    "sucursal": sucursal,
                    "suc_key": _normalizar_nombre_sucursal_camaras(sucursal),
                    "camaraId": nombre_camara,
                    "camara_norm": _normalizar_nombre_camara(nombre_camara),
                    "fechaInstalacion": fecha_instalacion,
                    "tecnico": ((st.tecnico_a_cargo if st else "") or "").strip() or "Sin técnico asignado",
                }
            )

    if not instalaciones:
        return []

    min_fecha = min(i["fechaInstalacion"] for i in instalaciones)

    incidencia_rows = (
        db.query(Registro.cliente, Registro.problema, Registro.fecha_registro, Registro.detalle_problema)
        .filter(Registro.derivacion.ilike("%servicio t%"))
        .filter(Registro.fecha_registro > min_fecha)
        .filter(Registro.detalle_problema.isnot(None))
        .all()
    )

    incidencias_por_sucursal: dict[str, list[dict]] = {}
    for cliente, problema, fecha_registro, detalle in incidencia_rows:
        if _normalizar_nombre_camara(problema or "") not in _CALIDAD_INSTALACION_FALLA_TIPOS:
            continue
        tokens = set(_parsear_camaras_de_detalle(detalle))
        if not tokens:
            continue
        suc_key = _normalizar_nombre_sucursal_camaras(cliente or "")
        if not suc_key:
            continue
        incidencias_por_sucursal.setdefault(suc_key, []).append(
            {"fecha": fecha_registro, "problema": problema, "tokens": tokens}
        )

    resultado: list[dict] = []
    for inst in instalaciones:
        caida = None
        for inc in incidencias_por_sucursal.get(inst["suc_key"], []):
            if not inc["fecha"] or inc["fecha"] <= inst["fechaInstalacion"]:
                continue
            if inst["camara_norm"] not in inc["tokens"]:
                continue
            if caida is None or inc["fecha"] < caida["fecha"]:
                caida = inc

        item = {
            "ods": inst["ods"],
            "sucursal": inst["sucursal"],
            "camaraId": inst["camaraId"],
            "fechaInstalacion": inst["fechaInstalacion"].strftime("%Y-%m-%d"),
            "tecnico": inst["tecnico"],
            "caida": caida is not None,
        }
        if caida:
            item["fechaCaida"] = caida["fecha"].strftime("%Y-%m-%d")
            item["motivoCaida"] = (caida["problema"] or "").strip()
        resultado.append(item)

    return resultado


def _cargar_sucursales_por_puesto(db: Session) -> dict[str, list[str]]:
    """Mapa puesto (1-29, columna `central`) -> nombres de sucursal que
    monitorea, a partir de sucursal_camaras_monitoreo — la misma fuente que
    usa el informe "Información Puestos" de Bitácora. Se usa para acotar el
    selector de Sucursal al abrir el formulario de un puesto puntual en
    lugar de mostrar las sucursales de todo el sistema.

    El puesto 30 ("Solo clientes con alarma") es un caso especial: no viene
    de sucursal_camaras_monitoreo (no es un puesto de monitoreo real), asi
    que se arma aparte con TODAS las sucursales (bbdd_sucursales) de
    cualquier cliente en bbdd_clientes — antes caia al fallback generico
    (listasGlobales.sucursales en el frontend), que en realidad lista
    nombres de EMPRESA (ClienteBBDD.cliente), no de sucursal."""
    from ATC.app.models.incidencias import ClienteBBDD, SucursalBBDD, SucursalCamaraMonitoreo

    rows = (
        db.query(SucursalCamaraMonitoreo.central, SucursalBBDD.nombre_sucursal)
        .join(SucursalBBDD, SucursalCamaraMonitoreo.sucursal_id == SucursalBBDD.id)
        .filter(SucursalCamaraMonitoreo.central.isnot(None))
        .all()
    )
    agrupado: dict[str, dict[str, str]] = {}
    for central, nombre_sucursal in rows:
        nombre = (nombre_sucursal or "").strip()
        if not nombre:
            continue
        clave_puesto = str(int(central))
        vistas = agrupado.setdefault(clave_puesto, {})
        vistas.setdefault(_normalizar_nombre_sucursal_camaras(nombre), nombre)
    resultado = {puesto: sorted(nombres.values()) for puesto, nombres in agrupado.items()}

    sucursales_alarma = (
        db.query(SucursalBBDD.nombre_sucursal)
        .join(ClienteBBDD, SucursalBBDD.rut == ClienteBBDD.rut)
        .all()
    )
    vistas_alarma: dict[str, str] = {}
    for (nombre_sucursal,) in sucursales_alarma:
        nombre = (nombre_sucursal or "").strip()
        if not nombre:
            continue
        vistas_alarma.setdefault(_normalizar_nombre_sucursal_camaras(nombre), nombre)
    resultado["30"] = sorted(vistas_alarma.values())

    return resultado


@router.get("/api/incidencias/sucursales-por-puesto")
def obtener_sucursales_por_puesto(db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return _cargar_sucursales_por_puesto(db)


@router.post("/api/contacto-cliente/enviar-info")
def enviar_info_contacto_cliente(
    payload: EnviarInformacionContactoRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        return service.registrar_envio_informacion_contacto(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/clientes-soporte")
def obtener_clientes_soporte(service: Annotated[IncidenciasService, Depends(get_service)], current_user=Depends(_require_login)):
    return service.obtener_clientes_soporte()


@router.get("/api/tareas/tipos")
def obtener_tipos_especificaciones(current_user=Depends(_require_login)):
    return TIPOS_Y_ESPECIFICACIONES


@router.get("/api/protocolos/listas")
def obtener_listas_protocolos(
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)],
current_user=Depends(_require_login)):
    return service.obtener_listas()


@router.post("/api/protocolos/registro")
def crear_registro_protocolo(
    payload: ProtocoloRegistroCreateRequest,
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)],
current_user=Depends(_require_login)):
    try:
        return service.guardar_registro(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/protocolos/registros")
def listar_registros_protocolos(
    cliente: str = "",
    sucursal: str = "",
    tipo_protocolo: str = "",
    fecha_desde: str = "",
    fecha_hasta: str = "",
    limit: int = 300,
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)] = None,
current_user=Depends(_require_login)):
    try:
        return service.listar_registros(
            cliente=cliente,
            sucursal=sucursal,
            tipo_protocolo=tipo_protocolo,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/protocolos/reportes/semanal/ejecutar")
def ejecutar_reportes_semanales_protocolos(
    forzar: bool = False,
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)] = None,
current_user=Depends(_require_login)):
    try:
        return service.generar_resumenes_semanales_pendientes(forzar=forzar)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/protocolos/informes")
def listar_informes_protocolos(
    cliente: str = "",
    sucursal: str = "",
    tipo_informe: str = "",
    limit: int = 200,
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)] = None,
current_user=Depends(_require_login)):
    try:
        return service.listar_informes(
            cliente=cliente,
            sucursal=sucursal,
            tipo_informe=tipo_informe,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/protocolos/informes/{informe_id}/contactos")
def obtener_contactos_informe_protocolo(
    informe_id: int,
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)] = None,
current_user=Depends(_require_login)):
    try:
        return service.obtener_contactos_informe(informe_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/protocolos/informes/{informe_id}/enviar")
def enviar_informe_semanal_protocolo(
    informe_id: int,
    payload: dict = Body(default_factory=dict),
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)] = None,
current_user=Depends(_require_login)):
    try:
        return service.enviar_informe_semanal(informe_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/protocolos/informes/{informe_id}/rechazar")
def rechazar_informe_semanal_protocolo(
    informe_id: int,
    payload: dict = Body(default_factory=dict),
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)] = None,
current_user=Depends(_require_login)):
    try:
        return service.rechazar_informe_semanal(informe_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/api/protocolos/informes/{informe_id}")
def eliminar_informe_protocolo(
    informe_id: int,
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)] = None,
current_user=Depends(_require_login)):
    try:
        return service.eliminar_informe(informe_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/derivaciones")
def obtener_registros_derivaciones(service: Annotated[IncidenciasService, Depends(get_service)], current_user=Depends(_require_login)):
    return service.obtener_registros_derivaciones()


@router.post("/api/coordinacion/finalizar")
def finalizar_odt_coordinacion(
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        result = service.finalizar_odt_coordinacion(
            str(payload.get("odt") or ""),
            str(payload.get("observacion_final") or payload.get("observacionFinal") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail="ODT no encontrada")
    return result


@router.post("/api/coordinacion/observacion-final")
def guardar_observacion_final_coordinacion(
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        result = service.actualizar_observacion_final_coordinacion(
            str(payload.get("odt") or ""),
            str(payload.get("observacion_final") or payload.get("observacionFinal") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail="ODT no encontrada")
    return result


@router.post("/api/coordinacion/enviar-correo")
def enviar_correo_coordinacion(
    payload: EnviarInformacionContactoRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        return service.registrar_envio_correo_coordinacion(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/rendiciones")
def registrar_rendicion(
    payload: RendicionRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        return service.registrar_gasto(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/rendiciones/url")
def obtener_url_formulario_rendicion(request: Request, current_user=Depends(_require_login)):
    return {"url": str(request.base_url).rstrip("/")}


@router.get("/api/rendiciones/duplicado")
def verificar_nro_documento_duplicado(
    nro_documento: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    return {"duplicado": service.existe_nro_documento_duplicado(nro_documento)}


@router.post("/api/rendiciones/upload-boleta")
async def subir_boleta_rendicion(
    file: UploadFile = File(...),
    tecnico: str = Form(""),
    odt: str = Form(""),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    if not file:
        raise HTTPException(status_code=400, detail="Debes adjuntar una imagen de boleta.")
    if not str(file.content_type or "").lower().startswith("image/"):
        raise HTTPException(status_code=400, detail="El archivo debe ser una imagen.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacio.")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="La imagen supera el limite de 10MB.")

    try:
        url = service.guardar_boleta_rendicion(
            content=content,
            filename=file.filename or "boleta.jpg",
            tecnico=tecnico,
            odt=odt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"url": url}


@router.get("/api/rendiciones")
def obtener_rendiciones(
    tecnico: str = "",
    pendientes: bool = False,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    result = service.obtener_rendiciones(tecnico=tecnico, pendientes_only=pendientes)
    if pendientes:
        estados_finales = {"acept", "rechaz", "pagad"}
        result = [
            r for r in (result or [])
            if not any(e in str(r.get("estado_revision", "")).lower() for e in estados_finales)
        ]
    return result


@router.get("/api/finanzas/pagos-agrupados")
def obtener_pagos_agrupados(
    tipo: str = Query(default="atc"),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    tipo_norm = str(tipo or "").strip().lower()
    if tipo_norm not in ("atc", "vl"):
        raise HTTPException(status_code=400, detail="tipo debe ser 'atc' o 'vl'.")
    pagos = service.agrupar_pagos_pendientes()
    return pagos[tipo_norm]


@router.get("/api/rendiciones/exportar")
def exportar_pagos_rendiciones(
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    buffer = service.generar_excel_pagos_rendiciones()
    filename = f"pagos_rendiciones_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.patch("/api/rendiciones/{rendicion_id}/monto")
def actualizar_monto_rendicion(
    rendicion_id: int,
    monto: float,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    try:
        resultado = service.actualizar_monto_rendicion(rendicion_id, monto)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if resultado is None:
        raise HTTPException(status_code=404, detail="Rendición no encontrada")
    return {"ok": True, **resultado}


@router.patch("/api/rendiciones/{rendicion_id}")
def marcar_rendicion(
    rendicion_id: int,
    accion: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
    token: str = "",
current_user=Depends(_require_login)):
    try:
        usuario = service.get_usuario_actual(token) if token else ""
        usuario = usuario if usuario and usuario != "Desconocido" else ""
        ok = service.marcar_rendicion(rendicion_id, accion, usuario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Rendición no encontrada")
    return {"ok": True}


# ---------- Finanzas: vistas de rendiciones ----------

@router.get("/api/finanzas/consolidado")
def finanzas_consolidado(service: Annotated[IncidenciasService, Depends(get_service)], current_user=Depends(_require_login)):
    return service.obtener_consolidado()


@router.get("/api/finanzas/viatico-especial")
def finanzas_viatico_especial(
    codigo: str = "",
    personalizados: bool = False,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    return service.obtener_viatico_especial(codigo=codigo, personalizados=personalizados)


@router.patch("/api/finanzas/viatico-cap/{codigo}")
def finanzas_set_viatico_cap(
    codigo: str,
    monto: float,
    token: str = "",
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    try:
        usuario = service.get_usuario_actual(token) if token else ""
        usuario = usuario if usuario and usuario != "Desconocido" else ""
        return service.set_viatico_cap(codigo, monto, usuario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/finanzas/pagos")
def finanzas_listar_pagos(service: Annotated[IncidenciasService, Depends(get_service)], current_user=Depends(_require_login)):
    return service.obtener_pagos()


@router.post("/api/finanzas/pagos")
def finanzas_registrar_pago(
    payload: dict,
    token: str = "",
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    try:
        usuario = service.get_usuario_actual(token) if token else ""
        usuario = usuario if usuario and usuario != "Desconocido" else ""
        return service.registrar_pago(payload, usuario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/api/finanzas/pagos/{pago_id}")
def finanzas_actualizar_pago(
    pago_id: int,
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    try:
        return service.actualizar_pago(pago_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/finanzas/pagos/{pago_id}")
def finanzas_eliminar_pago(
    pago_id: int,
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    ok = service.eliminar_pago(pago_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    return {"ok": True}


@router.get("/api/finanzas/suma-pagos")
def finanzas_suma_pagos(service: Annotated[IncidenciasService, Depends(get_service)], current_user=Depends(_require_login)):
    return service.obtener_suma_pagos()


@router.get("/api/planificacion")
def obtener_planificacion_total(
    mes: int,
    anio: int,
    estado: str = "Todos",
    tecnico: str = "Todos",
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    return service.obtener_planificacion_total(mes, anio, estado, tecnico)


@router.get("/api/debug/db")
def debug_db(
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IncidenciasService, Depends(get_service)],
current_user=Depends(_require_login)):
    out = {
        "database_url": settings.database_url,
        "db_schema_setting": settings.db_schema,
    }
    try:
        out["current_schema"] = db.execute(text("SELECT SCHEMA_NAME()")).scalar_one()
    except Exception as e:
        db.rollback()
        out["current_schema_error"] = str(e)
    try:
        out["catalogo_clientes_count"] = db.execute(text("SELECT COUNT(*) FROM catalogo_clientes")).scalar_one()
    except Exception as e:
        db.rollback()
        out["catalogo_clientes_count_error"] = str(e)
    try:
        out["catalogo_clientes_columns"] = [
            r[0]
            for r in db.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = SCHEMA_NAME()
                      AND table_name = 'catalogo_clientes'
                    ORDER BY ordinal_position
                    """
                )
            ).all()
        ]
    except Exception as e:
        db.rollback()
        out["catalogo_clientes_columns_error"] = str(e)
    try:
        out["catalogo_clientes_schemas"] = [
            r[0]
            for r in db.execute(
                text(
                    """
                    SELECT DISTINCT table_schema
                    FROM information_schema.columns
                    WHERE table_name = 'catalogo_clientes'
                      AND table_schema NOT IN ('sys', 'INFORMATION_SCHEMA')
                    ORDER BY table_schema
                    """
                )
            ).all()
        ]
    except Exception as e:
        db.rollback()
        out["catalogo_clientes_schemas_error"] = str(e)
    try:
        out["catalogo_clientes_sample"] = service.obtener_catalogo_clientes()[:10]
    except Exception as e:
        db.rollback()
        out["catalogo_clientes_sample_error"] = str(e)
    return out


@router.get("/servicio/indicadores", response_class=HTMLResponse)
def servicio_indicadores_page(request: Request):
    return templates.TemplateResponse(
        request,
        "dashboard_servicio.html",
        {"request": request},
    )


@router.get("/api/servicio/kpis-data")
def servicio_kpis_data(db: Annotated[Session, Depends(get_db)], current_user=Depends(_require_login)):
    from sqlalchemy import func, select as sa_select
    from ATC.app.models.incidencias import Registro, ServicioTecnicoVentaODT, SoporteTecnicoVentaODT, VentaODS

    # Limitar el tiempo máximo de espera en SQL Server para evitar locks indefinidos
    try:
        if engine.dialect.name == "mssql":
            db.execute(text("SET LOCK_TIMEOUT 8000"))  # 8 segundos máximo esperando un lock
    except Exception:
        pass

    def _contar_camaras_registradas(raw: object) -> int:
        if raw in (None, "", [], (), {}):
            return 0
        if isinstance(raw, (list, tuple, set)):
            items = list(raw)
        else:
            texto = str(raw).strip()
            if not texto:
                return 0
            try:
                parsed = json.loads(texto)
            except Exception:
                parsed = [part.strip() for part in re.split(r"[,\n|;]+", texto) if part.strip()]
            if isinstance(parsed, list):
                items = parsed
            elif isinstance(parsed, tuple):
                items = list(parsed)
            elif isinstance(parsed, set):
                items = list(parsed)
            elif parsed in (None, ""):
                items = []
            else:
                items = [parsed]
        total = 0
        for item in items:
            if isinstance(item, dict):
                total += 1
            elif str(item or "").strip():
                total += 1
        return total

    def _porcentaje_a_instaladas(raw: object, total: int) -> int:
        if not raw or total <= 0:
            return 0
        texto = str(raw).strip().replace("%", "")
        if not texto:
            return 0
        try:
            pct = float(texto.replace(",", "."))
        except Exception:
            return 0
        if pct <= 0:
            return 0
        if pct > 100:
            pct = 100
        return max(0, min(total, int(round(total * pct / 100.0))))

    # Solo columnas usadas por el dashboard gerencial + porcentaje_avance
    # (fallback del avance de camaras). Ver nota en el return.
    registro_cols = (
        Registro.odt.label("odt"),
        Registro.fecha_registro.label("fecha_registro"),
        Registro.fecha_cierre.label("fecha_cierre"),
        Registro.cliente.label("cliente"),
        Registro.problema.label("problema"),
        Registro.estado.label("estado"),
        Registro.tecnicos.label("tecnicos"),
        Registro.acompanante.label("acompanante"),
        Registro.dias_ejecucion.label("dias_ejecucion"),
        Registro.porcentaje_avance.label("porcentaje_avance"),
        Registro.responsable_cierre.label("responsable_cierre"),
        Registro.causa_cierre.label("causa_cierre"),
        Registro.accion_cierre.label("accion_cierre"),
        Registro.resultado_cierre.label("resultado_cierre"),
        Registro.observacion_final.label("observacion_final"),
        Registro.fecha_inicio_trabajo.label("fecha_inicio_trabajo"),
        Registro.fecha_fin_trabajo.label("fecha_fin_trabajo"),
        Registro.tecnico_cierre.label("tecnico_cierre"),
    )

    try:
        registros = (
            db.execute(
                sa_select(*registro_cols)
                .where(func.lower(func.trim(Registro.estado)) != "repetida")
                # Solo ODTs derivadas a Servicio Tecnico; el ilike "%servicio t%"
                # cubre las variantes con mojibake ("Servicio T?cnico") y sin
                # tilde que existen en la BBDD.
                .where(Registro.derivacion.ilike("%servicio t%"))
                # Cualquier tipo de mantencion (Preventiva, o solo "Mantencion")
                # es trabajo programado, no una incidencia real; no debe
                # contarse en el dashboard gerencial.
                .where(~Registro.problema.ilike("%mantenc%"))
                .order_by(Registro.fecha_registro.desc())
            )
            .mappings()
            .all()
        )
    except Exception as exc:
        LOGGER.warning("kpis-data: error al cargar registros: %s", exc)
        db.rollback()
        registros = []

    avance_por_odt: dict[str, object] = {}
    for reg in registros:
        odt_key = str(reg.get("odt", "") or "").strip().upper()
        if odt_key and odt_key not in avance_por_odt:
            avance_por_odt[odt_key] = reg

    # Fallback de porcentaje_avance para el avance de camaras: las ODT de
    # instalacion de camaras se derivan como "Televigilancia | Instalacion",
    # no "Servicio Tecnico", por lo que quedan fuera de `registros` (filtrado
    # arriba) y de `avance_por_odt`. Se arma un dict aparte sin ese filtro.
    avance_por_odt_camaras: dict[str, object] = {}
    try:
        for odt_row, pct_row in db.execute(
            sa_select(Registro.odt, Registro.porcentaje_avance).where(
                func.lower(func.trim(Registro.estado)) != "repetida"
            )
        ).all():
            odt_key = str(odt_row or "").strip().upper()
            if odt_key and odt_key not in avance_por_odt_camaras:
                avance_por_odt_camaras[odt_key] = pct_row
    except Exception as exc:
        LOGGER.warning("kpis-data: error al cargar porcentaje_avance para camaras: %s", exc)
        db.rollback()

    # Avance de instalación de cámaras: ODS de venta en etapa de servicio técnico
    # con cámaras contratadas (venta_ods."Cámaras a instalar")
    avance_camaras = []
    try:
        avance_rows = db.execute(
            sa_select(ServicioTecnicoVentaODT, VentaODS, SoporteTecnicoVentaODT)
            .outerjoin(VentaODS, VentaODS.codigo == ServicioTecnicoVentaODT.odt)
            .outerjoin(SoporteTecnicoVentaODT, SoporteTecnicoVentaODT.odt == VentaODS.codigo)
            .where(VentaODS.numero_camaras_instalar.is_not(None))
            .where(VentaODS.numero_camaras_instalar > 0)
            .order_by(VentaODS.created_at.desc())
        ).all()
        for st, v, sop in avance_rows:
            total = int(v.numero_camaras_instalar or 0)
            finalizada_db = bool(st and (st.instalacion_finalizada or st.finalizado))
            instaladas_registradas = _contar_camaras_registradas(getattr(sop, "camaras_registradas", None)) if sop else 0
            if not instaladas_registradas:
                odt_key = str(st.odt or v.codigo or "").strip().upper()
                pct = avance_por_odt_camaras.get(odt_key)
                if pct is None:
                    reg = avance_por_odt.get(odt_key)
                    pct = reg.get("porcentaje_avance") if reg else None
                instaladas_registradas = _porcentaje_a_instaladas(pct, total)
            finalizada = finalizada_db or (total > 0 and instaladas_registradas >= total)
            instaladas = total if finalizada else min(total, instaladas_registradas)
            avance_camaras.append(
                {
                    "ods": st.odt if st else (v.codigo or ""),
                    "cliente": v.razon_social or "",
                    "sucursal": v.nombre_sucursal or "",
                    "direccion": v.direccion_sucursal or "",
                    "camaras_total": total,
                    "camaras_instaladas": instaladas,
                    "camaras_pendientes": max(total - instaladas, 0),
                    "finalizada": finalizada,
                    "estado_cierre": "Finalizado" if finalizada else "Pendiente",
                    "fecha_inicio": getattr(st, "fecha_inicio_instalacion", "") if st else "",
                    "fecha_fin": getattr(st, "fecha_fin_instalacion", "") if st else "",
                    "tecnico": getattr(st, "tecnico_a_cargo", "") if st else "",
                    "fecha_creacion": v.created_at.isoformat() if v.created_at else None,
                }
            )
    except Exception as exc:
        LOGGER.warning("kpis-data: error al cargar avance cámaras: %s", exc)
        db.rollback()

    try:
        calidad_visita_camaras = _calidad_visita_camaras(db)
    except Exception as exc:
        LOGGER.warning("kpis-data: error al cargar calidad de visita por camara: %s", exc)
        db.rollback()
        calidad_visita_camaras = []

    try:
        calidad_instalacion_camaras = _calidad_instalacion_camaras(db)
    except Exception as exc:
        LOGGER.warning("kpis-data: error al cargar calidad de instalacion: %s", exc)
        db.rollback()
        calidad_instalacion_camaras = []

    return {
        "avance_camaras": avance_camaras,
        "calidad_visita_camaras": calidad_visita_camaras,
        "calidad_instalacion_camaras": calidad_instalacion_camaras,
        "config": {
            "sla_dias": settings.servicio_sla_dias,
            "odt_antigua_dias": settings.servicio_odt_antigua_dias,
            "reincidencia_ventana_dias": settings.servicio_reincidencia_ventana_dias,
            "instalacion_mala_dias": settings.servicio_instalacion_mala_dias,
            "instalacion_regular_dias": settings.servicio_instalacion_regular_dias,
        },
        # Payload gerencial: solo los campos que consume dashboard_servicio.html.
        # Los campos de cierre (quien/como) se truncan porque pueden ser
        # texto libre largo; solo se usan para el detalle por sucursal.
        "registros": [
            {
                "odt": r.get("odt"),
                "fecha_registro": r.get("fecha_registro").isoformat() if r.get("fecha_registro") else None,
                "fecha_cierre": r.get("fecha_cierre").isoformat() if r.get("fecha_cierre") else None,
                "cliente": (r.get("cliente") or "")[:120],
                "problema": (r.get("problema") or "")[:140],
                "estado": r.get("estado") or "",
                "tecnicos": (r.get("tecnicos") or "")[:120],
                "acompanante": (r.get("acompanante") or "")[:120],
                "dias_ejecucion": r.get("dias_ejecucion"),
                "responsable_cierre": (r.get("responsable_cierre") or "")[:120],
                "causa_cierre": (r.get("causa_cierre") or "")[:200],
                "accion_cierre": (r.get("accion_cierre") or "")[:200],
                "resultado_cierre": (r.get("resultado_cierre") or "")[:200],
                "observacion_final": (r.get("observacion_final") or "")[:1000],
                "fecha_inicio_trabajo": r.get("fecha_inicio_trabajo").isoformat() if r.get("fecha_inicio_trabajo") else None,
                "fecha_fin_trabajo": r.get("fecha_fin_trabajo").isoformat() if r.get("fecha_fin_trabajo") else None,
                "tecnico_cierre": (r.get("tecnico_cierre") or "")[:120],
            }
            for r in registros
        ],
    }


@router.get("/servicio/indicadores/informe")
def servicio_indicadores_informe(
    desde: str = Query(default=""),
    hasta: str = Query(default=""),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    import io
    from datetime import date as _date

    fecha_desde = fecha_hasta = None
    try:
        if desde:
            fecha_desde = _date.fromisoformat(desde)
        if hasta:
            fecha_hasta = _date.fromisoformat(hasta)
    except ValueError:
        fecha_desde = fecha_hasta = None

    pdf_bytes = service.generar_informe_servicio_pdf(desde=fecha_desde, hasta=fecha_hasta)
    sufijo = f"_{desde}_a_{hasta}" if (desde and hasta) else ""
    nombre = f"Informe_Servicio_Tecnico{sufijo}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{nombre}"'},
    )


# ─────────────────────────────────────────────────────────────
# PRUEBAS DE SONIDO
# ─────────────────────────────────────────────────────────────

@router.get("/api/pruebas-sonido/sucursales")
def pruebas_sonido_sucursales(db: Session = Depends(get_db), current_user=Depends(_require_login)):
    """Devuelve sucursales agrupadas en 4 semanas por zona geográfica.

    La comuna/región de cada sucursal se lee directo de SucursalBBDD.comuna
    / .region — la misma columna que se edita desde "Región / Comuna" en
    incidencias_servicio_tecnico.html (ver actualizar_region_comuna_sucursal
    en incidencias_service.py) — así que un cambio hecho ahí se refleja acá
    sin nada adicional. Si a una sucursal le falta comuna, se completa al
    vuelo con la API de Geocoding de Google (misma fuente que usa esa
    pantalla), ya no con Nominatim/Photon (gratuitas, menos precisas para
    comunas chilenas y con un worker en segundo plano que las mezclaba)."""
    import unicodedata as _ud

    import re as _re

    def _norm(c: str) -> str:
        """Normaliza texto para comparar/agrupar: sin tildes, sin puntuación."""
        c = (c or "").strip().replace("-", " ")
        c = _ud.normalize("NFD", c.lower())
        c = "".join(ch for ch in c if _ud.category(ch) != "Mn")
        c = _re.sub(r"[^a-z0-9 ]", "", c)
        return " ".join(c.split())

    # Coordenadas de referencia para las principales comunas de Chile (lat, lon)
    # — solo para estimar la ZONA geográfica (centro/sur/norte) de una
    # sucursal que no tenga lat/lon propia; la comuna en sí nunca sale de
    # acá, siempre de SucursalBBDD.comuna o de Google más arriba.
    _COORDS_CL: dict[str, tuple[float, float]] = {
        "arica": (-18.47, -70.31), "iquique": (-20.21, -70.15),
        "calama": (-22.47, -68.93), "antofagasta": (-23.65, -70.40),
        "copiapo": (-27.37, -70.33), "la serena": (-29.91, -71.25),
        "coquimbo": (-29.96, -71.34), "ovalle": (-30.60, -71.20),
        "los andes": (-32.84, -70.60), "llay llay": (-32.84, -70.96),
        "hijuelas": (-32.82, -71.17), "la calera": (-32.79, -71.19),
        "quillota": (-32.88, -71.25), "quintero": (-32.78, -71.53),
        "concon": (-32.93, -71.52), "vina del mar": (-33.02, -71.55),
        "valparaiso": (-33.05, -71.62), "quilpue": (-33.05, -71.44),
        "villa alemana": (-33.04, -71.37), "limache": (-33.00, -71.26),
        "olmue": (-33.10, -71.19), "casablanca": (-33.32, -71.42),
        "nogales": (-32.74, -71.20), "santa maria": (-32.75, -70.66),
        "lampa": (-33.29, -70.88), "colina": (-33.20, -70.67),
        "huechuraba": (-33.35, -70.65), "quilicura": (-33.37, -70.73),
        "pudahuel": (-33.44, -70.75), "lo barnechea": (-33.35, -70.52),
        "vitacura": (-33.39, -70.58), "las condes": (-33.41, -70.58),
        "providencia": (-33.43, -70.62), "la reina": (-33.45, -70.56),
        "nunoa": (-33.46, -70.60), "santiago": (-33.46, -70.65),
        "penalolen": (-33.49, -70.55),
        "macul": (-33.49, -70.59), "la florida": (-33.52, -70.58),
        "maipu": (-33.52, -70.76), "cerrillos": (-33.49, -70.72),
        "pedro aguirre cerda": (-33.49, -70.69), "san miguel": (-33.50, -70.65),
        "san joaquin": (-33.50, -70.63), "lo espejo": (-33.52, -70.71),
        "el bosque": (-33.56, -70.66), "la pintana": (-33.58, -70.64),
        "san ramon": (-33.54, -70.63), "lo prado": (-33.46, -70.74),
        "estacion central": (-33.46, -70.71), "cerro navia": (-33.43, -70.74),
        "renca": (-33.40, -70.72), "conchalí": (-33.38, -70.67),
        "independencia": (-33.42, -70.65), "recoleta": (-33.41, -70.64),
        "cartagena": (-33.56, -71.59), "san antonio": (-33.59, -71.62),
        "el tabo": (-33.46, -71.66),
        "san bernardo": (-33.59, -70.71), "buin": (-33.73, -70.74),
        "paine": (-33.82, -70.74), "melipilla": (-33.69, -71.22),
        "rancagua": (-34.17, -70.74), "rengo": (-34.40, -70.86),
        "litueche": (-34.12, -71.73),
        "san fernando": (-34.58, -70.98), "curico": (-34.98, -71.24),
        "talca": (-35.43, -71.65), "linares": (-35.85, -71.60),
        "chillan": (-36.61, -72.10), "chillan viejo": (-36.63, -72.09),
        "penco": (-36.74, -72.99),
        "concepcion": (-36.83, -73.05), "talcahuano": (-36.72, -73.12),
        "coronel": (-37.02, -73.15), "los angeles": (-37.47, -72.35),
        "temuco": (-38.74, -72.59), "valdivia": (-39.81, -73.24),
        "osorno": (-40.57, -73.13), "puerto montt": (-41.47, -72.94),
        "coinco": (-34.27, -70.95), "colina santiago": (-33.20, -70.67),
        "region metropolitana": (-33.46, -70.65),
    }

    from ATC.app.models.incidencias import SucursalBBDD
    now = datetime.now()
    anio, mes = now.year, now.month

    sucursales = db.scalars(
        select(SucursalBBDD)
        .where(SucursalBBDD.habilitada == True)  # noqa: E712 - .is_(True) genera "IS 1", invalido en T-SQL
        .order_by(SucursalBBDD.nombre_sucursal)
    ).all()
    camaras_por_sucursal = _cargar_camaras_por_sucursal(db)

    registros_mes = db.scalars(
        select(PruebaSonido).where(PruebaSonido.anio == anio, PruebaSonido.mes == mes)
    ).all()
    estado_por_id = {r.sucursal_id: r for r in registros_mes}

    odts_pruebas = {
        str(registro.incidencia_odt or "").strip()
        for registro in registros_mes
        if str(registro.incidencia_odt or "").strip()
    }
    odts_finalizadas: set[str] = set()
    if odts_pruebas:
        incidencias_pruebas = db.scalars(
            select(Registro).where(Registro.odt.in_(odts_pruebas))
        ).all()
        for incidencia in incidencias_pruebas:
            estado_norm = _client_notes_key(incidencia.estado)
            derivacion_norm = _client_notes_key(incidencia.derivacion)
            finalizada = bool(incidencia.fecha_cierre) or any(
                marca in estado_norm or marca in derivacion_norm
                for marca in ("termin", "final", "solucion", "resuelt")
            )
            if finalizada:
                odts_finalizadas.add(str(incidencia.odt or "").strip().casefold())

    def _parse_coord(valor) -> float | None:
        try:
            txt = str(valor or "").strip().replace(",", ".")
            if not txt:
                return None
            num = float(txt)
            return num if -180 <= num <= 180 else None
        except (ValueError, TypeError):
            return None

    def _coords_validas(lat: float | None, lon: float | None) -> bool:
        return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180

    # ── Resolver cada sucursal a comuna/coordenada: SucursalBBDD.comuna es
    # la unica fuente (compartida con incidencias_servicio_tecnico); solo se
    # llama a Google si de verdad falta comuna, y se guarda al toque para no
    # volver a pedirla despues en cada carga de esta pantalla. ──
    # Comunas que Google (u otra fuente) puede devolver con variantes de
    # escritura pero que son la misma comuna real — se dejan todas con la
    # misma grafía para no duplicar el grupo en la grilla.
    _COMUNA_ALIAS_CANONICA = {"concon": "Con Con", "con con": "Con Con"}

    items = []
    geo_actualizada = False
    for s in sucursales:
        comuna_display = (s.comuna or "").strip()
        if not comuna_display and (s.direccion_sucursal or "").strip():
            region_google, comuna_google, error = geocodificar_region_comuna_google(
                direccion=s.direccion_sucursal
            )
            if comuna_google:
                s.comuna = comuna_google
                comuna_display = comuna_google
                if region_google and not (s.region or "").strip():
                    s.region = region_google
                geo_actualizada = True
            elif error:
                LOGGER.warning(
                    "pruebas-sonido: no se pudo geocodificar comuna de sucursal %s: %s",
                    s.id, error,
                )
        comuna_canonica = _COMUNA_ALIAS_CANONICA.get(_norm(comuna_display))
        if comuna_canonica and comuna_display != comuna_canonica:
            comuna_display = comuna_canonica
            if s.comuna != comuna_canonica:
                s.comuna = comuna_canonica
                geo_actualizada = True
        items.append({
            "sucursal": s,
            "comuna_norm": _norm(comuna_display) or "sin_comuna",
            "comuna_display": comuna_display or "Por geocodificar",
            "region_norm": _norm(s.region or ""),
            "lat": _parse_coord(s.latitud),
            "lon": _parse_coord(s.longitud),
        })

    if geo_actualizada:
        db.commit()

    def _coords_ref_item(item) -> tuple[float | None, float | None]:
        if _coords_validas(item["lat"], item["lon"]):
            return item["lat"], item["lon"]
        nk = item["comuna_norm"]
        if nk in _COORDS_CL:
            return _COORDS_CL[nk]
        for ck, cv in _COORDS_CL.items():
            if nk.startswith(ck) or ck.startswith(nk):
                return cv
        return None, None

    # ── Agrupar comunas completas y ordenarlas desde el centro de Chile ─
    # El orden solicitado es V Region / RM, luego hacia el sur y finalmente
    # hacia el norte. Las sucursales de una comuna quedan consecutivas.
    grupos_por_comuna: dict[str, list[dict]] = {}
    for item in items:
        grupos_por_comuna.setdefault(item["comuna_norm"], []).append(item)

    regiones_centro = ("valparaiso", "metropolitana")
    regiones_sur = (
        "ohiggins", "lib gral bernardo ohiggins", "maule", "nuble",
        "biobio", "araucania", "los rios", "los lagos", "aysen", "magallanes",
    )
    regiones_norte = ("coquimbo", "atacama", "antofagasta", "tarapaca", "arica")

    def _region_en(region_norm: str, regiones: tuple[str, ...]) -> bool:
        return any(region in region_norm for region in regiones)

    def _referencia_grupo(grupo: list[dict]) -> tuple[float | None, float | None]:
        comuna_norm = grupo[0]["comuna_norm"]
        if comuna_norm in _COORDS_CL:
            return _COORDS_CL[comuna_norm]
        coords = [_coords_ref_item(item) for item in grupo]
        coords = [(lat, lon) for lat, lon in coords if _coords_validas(lat, lon)]
        if not coords:
            return None, None
        lats = sorted(lat for lat, _ in coords)
        lons = sorted(lon for _, lon in coords)
        centro = len(coords) // 2
        return lats[centro], lons[centro]

    def _zona_grupo(grupo: list[dict], lat: float | None) -> int:
        regiones = {item["region_norm"] for item in grupo if item["region_norm"]}
        if any(_region_en(region, regiones_centro) for region in regiones):
            return 0
        if any(_region_en(region, regiones_sur) for region in regiones):
            return 1
        if any(_region_en(region, regiones_norte) for region in regiones):
            return 2
        if lat is None:
            return 3
        if -34.05 <= lat <= -32.0:
            return 0
        if lat < -34.05:
            return 1
        return 2

    def _grupo_geo_key(grupo: list[dict]):
        lat, lon = _referencia_grupo(grupo)
        zona = _zona_grupo(grupo, lat)
        # Centro y sur avanzan de norte a sur. Al llegar al norte, se parte
        # desde la zona mas cercana al centro y se avanza hacia Arica.
        lat_key = (-lat if zona in {0, 1} else lat) if lat is not None else 99.0
        return (
            zona,
            lat_key,
            lon if lon is not None else 99.0,
            _norm(grupo[0]["comuna_display"]),
        )

    grupos_ordenados = sorted(grupos_por_comuna.values(), key=_grupo_geo_key)
    comuna_orden = {
        grupo[0]["comuna_norm"]: idx
        for idx, grupo in enumerate(grupos_ordenados)
    }

    # ── Dividir en 4 semanas EN CANTIDAD lo más pareja posible ───────────
    # Antes se llenaba la semana actual con grupos de comuna ENTEROS hasta
    # cruzar la cuota (total/4) y recién ahí se pasaba a la siguiente —
    # como nunca se partía una comuna, el último grupo que cruzaba la cuota
    # podía dejar esa semana muy por arriba del objetivo (p. ej. 144/176/
    # 143/107 en vez de ~143 parejo). Ahora se aplana la lista completa (ya
    # viene ordenada geográficamente/por comuna) y se corta en 4 partes de
    # tamaño casi idéntico (a lo sumo 1 de diferencia); si el corte cae a
    # mitad de una comuna, esa comuna queda repartida entre dos semanas
    # — pedido explícito, ago 2026.
    cantidad_semanas = 4
    total_suc = sum(len(grupo) for grupo in grupos_ordenados)

    def _sucursal_direccion_key(s):
        direccion = (s.direccion_sucursal or "").strip()
        nombre = (s.nombre_sucursal or "").strip()
        return (0 if direccion else 1, _norm(direccion), _norm(nombre))

    grupos_ordenados = [
        sorted(grupo, key=lambda value: _sucursal_direccion_key(value["sucursal"]))
        for grupo in grupos_ordenados
    ]

    filas_totales = [item for grupo in grupos_ordenados for item in grupo]
    base = total_suc // cantidad_semanas
    extra = total_suc % cantidad_semanas
    semanas: list[list[dict]] = []
    pos = 0
    for i in range(cantidad_semanas):
        tamano = base + (1 if i < extra else 0)
        semanas.append(filas_totales[pos:pos + tamano])
        pos += tamano

    # ── Construir respuesta flat con campo semana ────────────────────────
    result = []
    for sem_idx, items_semana in enumerate(semanas):
        items_semana_ordenados = sorted(
            items_semana,
            key=lambda item: (
                comuna_orden.get(item["comuna_norm"], 9999),
                _sucursal_direccion_key(item["sucursal"]),
            ),
        )
        for item in items_semana_ordenados:
            s = item["sucursal"]
            lat, lon = _coords_ref_item(item)
            reg = estado_por_id.get(s.id)
            camaras_info = camaras_por_sucursal.get(
                _normalizar_nombre_sucursal_camaras(s.nombre_sucursal or ""),
                {},
            )
            try:
                cantidad_equipos = max(1, int(camaras_info.get("cantidad_equipos") or 1))
            except (TypeError, ValueError):
                cantidad_equipos = 1
            result.append({
                "id": s.id,
                "nombre_sucursal": s.nombre_sucursal or "",
                "comuna": item["comuna_display"],
                "direccion": s.direccion_sucursal or "",
                "orden_comuna": comuna_orden.get(item["comuna_norm"], 9999),
                "orden_direccion": _norm(s.direccion_sucursal or ""),
                "latitud": f"{lat:.6f}" if lat is not None else "",
                "longitud": f"{lon:.6f}" if lon is not None else "",
                "email_facturas": s.email_facturas or "",
                "cantidad_equipos": cantidad_equipos,
                "semana": sem_idx + 1,
                "resultado": reg.resultado if reg else None,
                "observacion": reg.observacion if reg else None,
                "prueba_id": reg.id if reg else None,
                "incidencia_odt": reg.incidencia_odt if reg else None,
                "odt_finalizada": bool(
                    reg
                    and str(reg.incidencia_odt or "").strip().casefold() in odts_finalizadas
                ),
            })
    return result


@router.post("/api/pruebas-sonido")
def registrar_prueba_sonido(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
current_user=Depends(_require_login)):
    """Registra resultado de prueba de sonido. Exitoso o sin coordinación envían email (con copia
    a tahira.riquelme.atc@gmail.com); falla crea incidencia sin enviar email."""
    from ATC.app.models.incidencias import SucursalBBDD, Registro
    import threading as _thr

    sucursal_id = int(payload.get("sucursal_id") or 0)
    resultado   = str(payload.get("resultado") or "").strip()   # exitoso | falla | no_coordinacion
    observacion = str(payload.get("observacion") or "").strip()
    equipo_audio = str(payload.get("equipo_audio") or "").strip()
    operador    = str(payload.get("operador") or "").strip()

    if not sucursal_id:
        raise HTTPException(400, "sucursal_id requerido")
    if resultado not in ("exitoso", "falla", "no_coordinacion"):
        raise HTTPException(400, "resultado debe ser 'exitoso', 'falla' o 'no_coordinacion'")

    now = datetime.now()
    anio, mes = now.year, now.month

    suc = db.get(SucursalBBDD, sucursal_id)
    if not suc:
        raise HTTPException(404, "Sucursal no encontrada")

    # Upsert: si ya existe prueba este mes para esta sucursal, actualizar
    existente = db.scalar(
        select(PruebaSonido).where(
            PruebaSonido.sucursal_id == sucursal_id,
            PruebaSonido.anio == anio,
            PruebaSonido.mes == mes,
        )
    )

    incidencia_odt = existente.incidencia_odt if existente else None
    observacion_registro = observacion
    if resultado == "falla" and equipo_audio:
        observacion_registro = f"{equipo_audio}"
        if observacion:
            observacion_registro += f": {observacion}"

    if existente:
        existente.resultado   = resultado
        existente.observacion = observacion_registro
        existente.operador    = operador
        prueba = existente
    else:
        prueba = PruebaSonido(
            sucursal_id=sucursal_id,
            anio=anio,
            mes=mes,
            resultado=resultado,
            observacion=observacion_registro,
            operador=operador,
        )
        db.add(prueba)

    # ── Si falla: crear incidencia ───────────────────────────
    if resultado == "falla":
        try:
            svc = IncidenciasService(db)
            nuevo_odt = svc._proximo_odt("I")
            usuario_obs = operador or "Usuario no identificado"
            marca_obs = now.strftime("%d/%m/%Y %H:%M")
            observacion_firmada = (
                f"[{usuario_obs} - {marca_obs}] {observacion_registro}".strip()
                if observacion_registro
                else ""
            )
            reg = Registro(
                odt=nuevo_odt,
                fecha_registro=now,
                cliente=suc.nombre_sucursal or "",
                direccion=suc.direccion_sucursal or "",
                problema="Problema de Parlante",
                # Esto lo gestionan los operadores (Registro Operaciones), no
                # Servicio Tecnico: observacion_servicio queda vacio a proposito
                # para que no aparezca duplicado en "Gestion Servicio Tecnico".
                observacion=observacion_firmada or None,
                observacion_pendiente=observacion_firmada or None,
                estado="Pendiente",
                derivacion="Servicio Técnico",
                fecha_derivacion_area=now,
                prioridad=2,
            )
            db.add(reg)
            db.flush()
            prueba.incidencia_odt = nuevo_odt
            incidencia_odt = nuevo_odt
        except Exception:
            LOGGER.exception("Error creando incidencia prueba sonido sucursal=%s", sucursal_id)

    db.commit()
    db.refresh(prueba)

    # ── Si exitoso o sin coordinación: enviar email via SMTP de incidencias ──
    _MESES_ES = ["enero","febrero","marzo","abril","mayo","junio",
                 "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    _CC_PRUEBAS_SONIDO = ["tahira.riquelme.atc@gmail.com"]  # copia oculta (BCC), no CC visible
    email_enviado = False
    if resultado in ("exitoso", "no_coordinacion"):
        from ATC.app.models.incidencias import SucursalContactoEmergencia
        _contacto_emails = [
            str(c.email).strip()
            for c in db.query(SucursalContactoEmergencia)
                        .filter(SucursalContactoEmergencia.sucursal_id == sucursal_id)
                        .order_by(SucursalContactoEmergencia.id.asc())
                        .all()
            if c.email and str(c.email).strip()
        ]
        email_destino = ", ".join(_contacto_emails)
        nombre_empresa = suc.nombre_empresa or suc.nombre_sucursal or ""
        nombre_suc = suc.nombre_sucursal or ""
        mes_nombre = f"{_MESES_ES[now.month - 1]} de {now.year}"
        fecha_str = f"{now.day} de {_MESES_ES[now.month - 1]} de {now.year}"

    if resultado == "exitoso":
        asunto = f"Informe de Prueba de Sistema de Sonido — {nombre_suc}"

        cuerpo_txt = (
            f"Estimados,\n\n"
            f"Nos complace informarles que el equipo técnico de Alguien Te Cuida ha realizado "
            f"satisfactoriamente la prueba mensual del sistema de sonido correspondiente a {mes_nombre} "
            f"en la sucursal {nombre_suc} de {nombre_empresa}.\n\n"
            f"La prueba fue ejecutada en su totalidad y arrojó resultados 100% exitosos, "
            f"verificando el correcto funcionamiento de parlantes, amplificadores y toda la cadena "
            f"de audio del sistema. Esto confirma que su instalación opera en condiciones óptimas "
            f"y está lista para responder ante cualquier situación que lo requiera.\n\n"
            f"En Alguien Te Cuida realizamos estas verificaciones de forma periódica para garantizar "
            f"que usted cuente siempre con un sistema operativo al 100%.\n\n"
            f"Cualquier problema o dificultad que usted visualice en el sistema, ya sea de cámaras, "
            f"parlantes u otros componentes, le rogamos avisarnos a la brevedad posible.\n\n"
            f"Atentamente,\nEquipo Técnico — Alguien Te Cuida SpA"
        )

        cuerpo_html = f"""<!DOCTYPE html>
<html lang="es" xmlns="http://www.w3.org/1999/xhtml" style="color-scheme:light;">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <meta name="color-scheme" content="light">
  <title>{asunto}</title>
</head>
<body style="margin:0;padding:0;background-color:#f2f4f7;-webkit-text-size-adjust:100%;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background-color:#f2f4f7;min-width:320px;">
  <tr>
    <td align="center" style="padding:40px 16px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
             style="max-width:600px;width:100%;background-color:#ffffff;border-radius:6px;
                    overflow:hidden;border:1px solid #d1d5db;">

        <!-- HEADER -->
        <tr>
          <td style="background-color:#0d1f2d;padding:20px 36px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="vertical-align:middle;">
                  <img src="cid:logoatc" alt="Alguien Te Cuida"
                       style="height:38px;width:auto;display:block;border:0;" />
                </td>
                <td align="right" style="vertical-align:middle;">
                  <span style="font-family:Arial,sans-serif;font-size:10px;font-weight:600;
                               color:#8aabb8;letter-spacing:0.12em;text-transform:uppercase;">
                    Informe Técnico
                  </span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- FRANJA ACENTO -->
        <tr>
          <td style="background-color:#1e3a5f;padding:20px 36px;">
            <p style="margin:0;font-family:Arial,sans-serif;font-size:10px;font-weight:600;
                      color:#93b4cc;letter-spacing:0.12em;text-transform:uppercase;">
              Verificación mensual &nbsp;·&nbsp; {mes_nombre}
            </p>
            <p style="margin:7px 0 0;font-family:Arial,sans-serif;font-size:20px;font-weight:700;
                      color:#ffffff;letter-spacing:-0.01em;line-height:1.25;">
              Prueba de Sistema de Sonido
            </p>
            <p style="margin:5px 0 0;font-family:Arial,sans-serif;font-size:13px;
                      color:#a8c4d8;line-height:1.4;">
              {nombre_suc} &nbsp;·&nbsp; {nombre_empresa}
            </p>
          </td>
        </tr>

        <!-- BADGE RESULTADO -->
        <tr>
          <td style="background-color:#ffffff;padding:26px 36px 4px;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="background-color:#f0fdf4;border:1px solid #a7f3d0;border-radius:5px;
                           padding:9px 16px;">
                  <span style="font-family:Arial,sans-serif;font-size:12px;font-weight:700;
                               color:#15803d;letter-spacing:0.02em;">EXITOSO</span>
                  <span style="font-family:Arial,sans-serif;font-size:12px;color:#6b7280;
                               margin-left:14px;">{fecha_str}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- CUERPO -->
        <tr>
          <td style="padding:20px 36px 8px;background-color:#ffffff;">
            <p style="margin:0 0 13px;font-family:Arial,sans-serif;font-size:14px;
                      line-height:1.65;color:#374151;">Estimados,</p>
            <p style="margin:0 0 13px;font-family:Arial,sans-serif;font-size:14px;
                      line-height:1.65;color:#374151;">
              Nos complace informarles que el equipo técnico de <strong>Alguien Te Cuida</strong>
              ha realizado satisfactoriamente la prueba mensual del sistema de sonido correspondiente
              a <strong>{mes_nombre}</strong> en la sucursal <strong>{nombre_suc}</strong>.
            </p>
            <p style="margin:0 0 13px;font-family:Arial,sans-serif;font-size:14px;
                      line-height:1.65;color:#374151;">
              La prueba fue ejecutada en su totalidad y arrojó resultados <strong>100&#37; exitosos</strong>,
              verificando el correcto funcionamiento de parlantes, amplificadores y toda la cadena de
              audio del sistema. Esto confirma que su instalación opera en condiciones óptimas y está
              lista para responder ante cualquier situación que lo requiera.
            </p>
            <p style="margin:0 0 13px;font-family:Arial,sans-serif;font-size:14px;
                      line-height:1.65;color:#374151;">
              En <strong>Alguien Te Cuida</strong> entendemos que la tranquilidad de su operación
              depende de que cada componente de su sistema de seguridad funcione correctamente.
              Por eso realizamos estas verificaciones de forma periódica: para garantizar que usted
              cuente siempre con un sistema al 100&#37;, sin sorpresas ni imprevistos.
            </p>
            <p style="margin:0 0 24px;font-family:Arial,sans-serif;font-size:14px;
                      line-height:1.65;color:#374151;">
              Cualquier problema o dificultad que usted visualice en el sistema, ya sea de
              <strong>cámaras</strong>, <strong>parlantes</strong> u otros componentes, le rogamos
              avisarnos a la brevedad posible.
            </p>
          </td>
        </tr>

        <!-- SEPARADOR -->
        <tr>
          <td style="padding:0 36px 18px;background-color:#ffffff;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
              <tr><td style="border-top:1px solid #e5e7eb;font-size:0;line-height:0;">&nbsp;</td></tr>
            </table>
          </td>
        </tr>

        <!-- FIRMA -->
        <tr>
          <td style="padding:0 36px 26px;background-color:#ffffff;">
            <p style="margin:0 0 1px;font-family:Arial,sans-serif;font-size:13px;
                      font-weight:700;color:#111827;">Equipo Técnico</p>
            <p style="margin:0 0 1px;font-family:Arial,sans-serif;font-size:12px;color:#6b7280;">Alguien Te Cuida SpA</p>
            <p style="margin:0;font-family:Arial,sans-serif;font-size:12px;color:#6b7280;">contacto@alguientecuida.cl</p>
          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td style="background-color:#f8fafc;border-top:1px solid #e5e7eb;
                     padding:14px 36px;border-radius:0 0 6px 6px;">
            <p style="margin:0;font-family:Arial,sans-serif;font-size:11px;color:#9ca3af;line-height:1.5;">
              Este mensaje fue generado automáticamente por el sistema de Alguien Te Cuida SpA.
              Por favor no responda directamente a este correo.
            </p>
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>
</body>
</html>"""

    elif resultado == "no_coordinacion":
        asunto = f"No fue posible coordinar prueba de sonido — {nombre_suc}"

        cuerpo_txt = (
            f"Estimados,\n\n"
            f"El equipo técnico de Alguien Te Cuida intentó contactarlos para coordinar la prueba "
            f"mensual del sistema de sonido correspondiente a {mes_nombre} en la sucursal {nombre_suc} "
            f"de {nombre_empresa}, y lamentablemente no fue posible concretar la coordinación.\n\n"
            f"Quedamos atentos y a su entera disposición para agendar la prueba a la brevedad, y así "
            f"verificar que su sistema de audio se encuentre operativo al 100%.\n\n"
            f"En Alguien Te Cuida realizamos estas verificaciones de forma periódica para garantizar "
            f"que usted cuente siempre con un sistema operativo al 100%.\n\n"
            f"Atentamente,\nEquipo Técnico — Alguien Te Cuida SpA"
        )

        cuerpo_html = f"""<!DOCTYPE html>
<html lang="es" xmlns="http://www.w3.org/1999/xhtml" style="color-scheme:light;">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <meta name="color-scheme" content="light">
  <title>{asunto}</title>
</head>
<body style="margin:0;padding:0;background-color:#f2f4f7;-webkit-text-size-adjust:100%;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background-color:#f2f4f7;min-width:320px;">
  <tr>
    <td align="center" style="padding:40px 16px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
             style="max-width:600px;width:100%;background-color:#ffffff;border-radius:6px;
                    overflow:hidden;border:1px solid #d1d5db;">

        <!-- HEADER -->
        <tr>
          <td style="background-color:#0d1f2d;padding:20px 36px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="vertical-align:middle;">
                  <img src="cid:logoatc" alt="Alguien Te Cuida"
                       style="height:38px;width:auto;display:block;border:0;" />
                </td>
                <td align="right" style="vertical-align:middle;">
                  <span style="font-family:Arial,sans-serif;font-size:10px;font-weight:600;
                               color:#8aabb8;letter-spacing:0.12em;text-transform:uppercase;">
                    Informe Técnico
                  </span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- FRANJA ACENTO -->
        <tr>
          <td style="background-color:#1e3a5f;padding:20px 36px;">
            <p style="margin:0;font-family:Arial,sans-serif;font-size:10px;font-weight:600;
                      color:#93b4cc;letter-spacing:0.12em;text-transform:uppercase;">
              Verificación mensual &nbsp;·&nbsp; {mes_nombre}
            </p>
            <p style="margin:7px 0 0;font-family:Arial,sans-serif;font-size:20px;font-weight:700;
                      color:#ffffff;letter-spacing:-0.01em;line-height:1.25;">
              Prueba de Sistema de Sonido
            </p>
            <p style="margin:5px 0 0;font-family:Arial,sans-serif;font-size:13px;
                      color:#a8c4d8;line-height:1.4;">
              {nombre_suc} &nbsp;·&nbsp; {nombre_empresa}
            </p>
          </td>
        </tr>

        <!-- BADGE RESULTADO -->
        <tr>
          <td style="background-color:#ffffff;padding:26px 36px 4px;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="background-color:#fffbeb;border:1px solid #fcd34d;border-radius:5px;
                           padding:9px 16px;">
                  <span style="font-family:Arial,sans-serif;font-size:12px;font-weight:700;
                               color:#b45309;letter-spacing:0.02em;">SIN COORDINACIÓN</span>
                  <span style="font-family:Arial,sans-serif;font-size:12px;color:#6b7280;
                               margin-left:14px;">{fecha_str}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- CUERPO -->
        <tr>
          <td style="padding:20px 36px 8px;background-color:#ffffff;">
            <p style="margin:0 0 13px;font-family:Arial,sans-serif;font-size:14px;
                      line-height:1.65;color:#374151;">Estimados,</p>
            <p style="margin:0 0 13px;font-family:Arial,sans-serif;font-size:14px;
                      line-height:1.65;color:#374151;">
              El equipo técnico de <strong>Alguien Te Cuida</strong> intentó contactarlos para
              coordinar la prueba mensual del sistema de sonido correspondiente a
              <strong>{mes_nombre}</strong> en la sucursal <strong>{nombre_suc}</strong>, y
              lamentablemente no fue posible concretar la coordinación.
            </p>
            <p style="margin:0 0 13px;font-family:Arial,sans-serif;font-size:14px;
                      line-height:1.65;color:#374151;">
              Quedamos atentos y a su entera disposición para agendar la prueba a la brevedad, y así
              verificar que su instalación se encuentre operando en condiciones óptimas.
            </p>
            <p style="margin:0 0 24px;font-family:Arial,sans-serif;font-size:14px;
                      line-height:1.65;color:#374151;">
              En <strong>Alguien Te Cuida</strong> entendemos que la tranquilidad de su operación
              depende de que cada componente de su sistema de seguridad funcione correctamente.
              Por eso realizamos estas verificaciones de forma periódica: para garantizar que usted
              cuente siempre con un sistema al 100&#37;, sin sorpresas ni imprevistos.
            </p>
          </td>
        </tr>

        <!-- SEPARADOR -->
        <tr>
          <td style="padding:0 36px 18px;background-color:#ffffff;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
              <tr><td style="border-top:1px solid #e5e7eb;font-size:0;line-height:0;">&nbsp;</td></tr>
            </table>
          </td>
        </tr>

        <!-- FIRMA -->
        <tr>
          <td style="padding:0 36px 26px;background-color:#ffffff;">
            <p style="margin:0 0 1px;font-family:Arial,sans-serif;font-size:13px;
                      font-weight:700;color:#111827;">Equipo Técnico</p>
            <p style="margin:0 0 1px;font-family:Arial,sans-serif;font-size:12px;color:#6b7280;">Alguien Te Cuida SpA</p>
            <p style="margin:0;font-family:Arial,sans-serif;font-size:12px;color:#6b7280;">contacto@alguientecuida.cl</p>
          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td style="background-color:#f8fafc;border-top:1px solid #e5e7eb;
                     padding:14px 36px;border-radius:0 0 6px 6px;">
            <p style="margin:0;font-family:Arial,sans-serif;font-size:11px;color:#9ca3af;line-height:1.5;">
              Este mensaje fue generado automáticamente por el sistema de Alguien Te Cuida SpA.
              Por favor no responda directamente a este correo.
            </p>
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>
</body>
</html>"""

    if resultado in ("exitoso", "no_coordinacion") and email_destino:
        import pathlib as _pl
        _logo_path = _pl.Path(__file__).resolve().parents[2] / "static" / "img" / "logo-atc.png"
        _logo_bytes = _logo_path.read_bytes() if _logo_path.exists() else None

        def _enviar(dest=email_destino, subj=asunto, txt=cuerpo_txt, html=cuerpo_html,
                    logo=_logo_bytes, sid=sucursal_id, bcc=_CC_PRUEBAS_SONIDO):
            try:
                svc_mail = IncidenciasService(SessionLocal())
                svc_mail._enviar_correo_automatico(
                    dest, subj, txt, html_body=html, logo_bytes=logo,
                    cfg_override=svc_mail._contacto_smtp_runtime_config(),
                    bcc_emails_extra=bcc,
                )
            except Exception:
                LOGGER.exception("Error enviando email prueba sonido sucursal=%s", sid)
        _thr.Thread(target=_enviar, daemon=True, name=f"email-sonido-{sucursal_id}").start()
        email_enviado = True

    return {
        "ok": True,
        "prueba_id": prueba.id,
        "resultado": resultado,
        "incidencia_odt": incidencia_odt,
        "email_enviado": email_enviado,
    }


@router.delete("/api/pruebas-sonido/{prueba_id}")
def eliminar_prueba_sonido(prueba_id: int, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    """Elimina el registro de prueba (permite re-marcar).

    Si la prueba estaba marcada como 'falla' y la incidencia que generó
    todavía sigue pendiente (nadie la trabajó ni la cerró), se borra también
    esa incidencia — evita que quede una ODT huérfana cuando el operador se
    equivocó de marca y la corrige (p.ej. a exitosa). Si la incidencia ya fue
    trabajada/finalizada, no se toca: es trabajo real de un técnico."""
    prueba = db.get(PruebaSonido, prueba_id)
    if not prueba:
        raise HTTPException(404, "Registro no encontrado")

    if prueba.resultado == "falla" and prueba.incidencia_odt:
        from ATC.app.models.incidencias import Registro
        odt = str(prueba.incidencia_odt).strip()
        incidencia = db.scalar(select(Registro).where(Registro.odt == odt)) if odt else None
        if incidencia:
            estado_norm = _client_notes_key(incidencia.estado)
            derivacion_norm = _client_notes_key(incidencia.derivacion)
            finalizada = bool(incidencia.fecha_cierre) or any(
                marca in estado_norm or marca in derivacion_norm
                for marca in ("termin", "final", "solucion", "resuelt")
            )
            if not finalizada:
                db.delete(incidencia)

    db.delete(prueba)
    db.commit()
    return {"ok": True}
