from __future__ import annotations

from datetime import datetime
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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import MetaData, Table, inspect, select, text
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ATC.app.core.incidencias_db import Base, SessionLocal, build_engine, engine, get_db
from ATC.app.core.incidencias_config import settings
from ATC.app.models.incidencias import LoginSession, User
from ATC.app.schemas.incidencias import (
    CerrarIncidenciaRequest,
    DerivarTecnicoRequest,
    EditarIncidenciaTablaRequest,
    EnProcesoRequest,
    EnviarInformacionContactoRequest,
    FinalizarIncidenciaRequest,
    FormularioRegistro,
    IncidenciaNueva,
    LoginRequest,
    LoginResponse,
    ProtocoloRegistroCreateRequest,
    RendicionRequest,
    TareaManualRequest,
)
from ATC.app.services.incidencias_service import AREA_PANEL_DESTINOS, IncidenciasService, seed_default_identity_data
from ATC.app.services.incidencias_drive_report_service import download_support_drive_file_bytes
from ATC.app.services.protocolos_service import ProtocolosService


INCIDENCIAS_APP_DIR = Path(__file__).resolve().parents[2] / "incidencias" / "app"
templates = Jinja2Templates(directory=str(INCIDENCIAS_APP_DIR / "templates"))

router = APIRouter()
LOGGER = logging.getLogger(__name__)
_protocolos_weekly_worker_started = False

TIPOS_Y_ESPECIFICACIONES = {
    "GestiÃ³n de Grabaciones y Evidencia": [
        "Solicitud / envÃ­o de grabaciones",
        "Solicitud de imÃ¡genes",
        "Grabaciones faltantes",
    ],
    "Monitoreo y Estado de CÃ¡maras": [
        "CÃ¡maras caÃ­das / intermitentes",
        "CÃ¡maras fuera de horario",
        "CÃ¡maras obstruidas / tapadas / movidas",
        "CÃ¡mara nueva / reemplazo",
        "VerificaciÃ³n de cÃ¡maras en lÃ­nea",
        "InicializaciÃ³n / renombrado de cÃ¡maras",
    ],
    "ConfiguraciÃ³n y Ajustes de CÃ¡maras / NVR / DVR": [
        "ConfiguraciÃ³n de cÃ¡maras nuevas",
        "ConfiguraciÃ³n de NVR / DVR / XVR",
        "Planes de grabaciÃ³n",
        "RetenciÃ³n de dÃ­as",
        "ConfiguraciÃ³n FTP / IVS / PTZ",
        "CorrecciÃ³n IVS",
        "Cambio de nombre / orden de cÃ¡maras",
        "NormalizaciÃ³n de hora",
    ],
    "Sistema de Audio y Alertas": [
        "Problemas de audio",
        "Pruebas de audio",
        "Sonidos molestos",
        "Audio por activaciÃ³n de IVS",
        "Sistema de audio no funciona",
    ],
    "IVS, POP UPS y Automatizaciones": [
        "CreaciÃ³n / ajuste de IVS",
        "Alertas automÃ¡ticas",
        "POP UPS",
        "ActivaciÃ³n / revisiÃ³n de POP UPS",
    ],
    "Soporte a Equipos de ComputaciÃ³n": [
        "Notebook lento / no enciende",
        "Formateo de PC / notebook",
        "Cambio de RAM",
        "Cambio de computador / torre",
        "Problemas de disco duro",
        "Limpieza de equipos",
        "ActivaciÃ³n de Windows",
    ],
    "Soporte a Pantallas y PerifÃ©ricos": [
        "Pantallas sin seÃ±al",
        "HDMI / VGA defectuoso",
        "Monitores apagados / intermitentes",
        "Mouse / teclado",
        "Impresoras (tÃ©rmica / normal)",
    ],
    "Redes y Conectividad": [
        "CaÃ­das de red",
        "Cambio IP / DHCP",
        "Router / antenas",
        "Intermitencia de enlace",
        "Internet caÃ­do",
    ],
    "Sistema de Alarmas y Sensores": [
        "Problemas de alarma",
        "Sensores (humo / gas / pÃ¡nico)",
        "Sirenas",
        "ZonificaciÃ³n",
        "Panel de alarma",
        "Notificaciones que no llegan",
    ],
    "Soporte a Software y Plataformas": [
        "DSS / HikCentral lento o fallando",
        "SoftGuard",
        "App de alarma / cÃ¡mara",
        "ConfiguraciÃ³n de usuarios",
        "Credenciales",
        "Cambio de contraseÃ±as",
    ],
    "GestiÃ³n Operativa y Administrativa": [
        "Orden y creaciÃ³n de planillas",
        "Registro de incidencias",
        "Correos informativos",
        "Solicitud de folios / QR",
        "CoordinaciÃ³n con tÃ©cnicos externos",
        "ComunicaciÃ³n con clientes / prioridades",
    ],
    "Cierres, Validaciones y RevisiÃ³n de Novedades": [
        "Cierre de caseta / instalaciÃ³n (Emergencia)",
        "RevisiÃ³n de novedades",
        "Validaciones finales",
        "Pruebas posteriores a intervenciÃ³n",
    ],
    "Mantenimiento Preventivo": [
        "MantenciÃ³n de equipos",
        "RevisiÃ³n periÃ³dica",
        "NormalizaciÃ³n preventiva",
        "RevisiÃ³n programada de IVS / audio / cÃ¡maras",
    ],
}


def startup_incidencias() -> None:
    global _protocolos_weekly_worker_started
    Base.metadata.create_all(bind=engine)
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
        return bool(conn.execute(text("SELECT to_regclass(:table) IS NOT NULL"), {"table": table}).scalar())

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
                "constraint": "fk_venta_ods_cliente_rut",
                "child_table": "venta_ods",
                "child_column": "rut_cliente",
                "parent_table": "bbdd_clientes",
                "parent_column": "rut",
            },
            {
                "constraint": "fk_venta_ods_archivos_ods",
                "child_table": "venta_ods_archivos",
                "child_column": "ods_id",
                "parent_table": "venta_ods",
                "parent_column": "id",
                "on_delete": "CASCADE",
            },
            {
                "constraint": "fk_administracion_odt_venta_ods",
                "child_table": "administracion_odt",
                "child_column": "odt",
                "parent_table": "venta_ods",
                "parent_column": "codigo",
                "on_delete": "CASCADE",
            },
            {
                "constraint": "fk_finanzas_odt_venta_ods",
                "child_table": "finanzas_odt",
                "child_column": "odt",
                "parent_table": "venta_ods",
                "parent_column": "codigo",
                "on_delete": "CASCADE",
            },
            {
                "constraint": "fk_servicio_tecnico_ventas_odt_venta_ods",
                "child_table": "servicio_tecnico_ventas_odt",
                "child_column": "odt",
                "parent_table": "venta_ods",
                "parent_column": "codigo",
                "on_delete": "CASCADE",
            },
            {
                "constraint": "fk_operaciones_venta_odt_venta_ods",
                "child_table": "operaciones_venta_odt",
                "child_column": "odt",
                "parent_table": "venta_ods",
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


@router.get("/api/client-notes")
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


@router.post("/api/client-notes")
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
        "materiales": "TEXT",
        "responsable_cierre": "VARCHAR(40)",
        "causa_cierre": "VARCHAR(120)",
        "accion_cierre": "VARCHAR(120)",
        "resultado_cierre": "VARCHAR(120)",
        "pruebas_cierre": "TEXT",
        "requiere_seguimiento": "BOOLEAN",
        "drive_cierre_folder_id": "VARCHAR(255)",
        "drive_cierre_folder_url": "TEXT",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("registro"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("registro")}
            for col_name, col_type in optional_columns.items():
                if col_name in existing_columns:
                    continue
                conn.execute(text(f'ALTER TABLE registro ADD COLUMN "{col_name}" {col_type}'))
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'registro': %s", exc)


def _ensure_administracion_odt_optional_columns() -> None:
    optional_columns: dict[str, str] = {
        "tecnico": "VARCHAR(255)",
        "acompanante": "VARCHAR(255)",
        "fecha_derivacion": "TIMESTAMP",
        "recepcion_info": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fecha_recepcion_info": "TIMESTAMP",
        "registro_alpha3": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fecha_registro_alpha3": "TIMESTAMP",
        "registro_intranet": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fecha_registro_intranet": "TIMESTAMP",
        "envio_solicitud_instalacion": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fecha_envio_solicitud_instalacion": "TIMESTAMP",
        "envio_datos_facturacion": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fecha_envio_datos_facturacion": "TIMESTAMP",
        "envio_carta_bienvenida": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fecha_envio_carta_bienvenida": "TIMESTAMP",
        "finalizado": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fecha_cierre": "TIMESTAMP",
        "updated_at": "TIMESTAMP DEFAULT NOW()",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("administracion_odt"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("administracion_odt")}
            for col_name, col_type in optional_columns.items():
                if col_name in existing_columns:
                    continue
                conn.execute(text(f'ALTER TABLE administracion_odt ADD COLUMN "{col_name}" {col_type}'))
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'administracion_odt': %s", exc)


def _ensure_venta_ods_optional_columns() -> None:
    optional_columns: dict[str, str] = {
        "drive_folder_id": "VARCHAR(255)",
        "drive_folder_url": "TEXT",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("venta_ods"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("venta_ods")}
            for col_name, col_type in optional_columns.items():
                if col_name in existing_columns:
                    continue
                conn.execute(text(f'ALTER TABLE venta_ods ADD COLUMN "{col_name}" {col_type}'))
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'venta_ods': %s", exc)


def _ensure_finanzas_odt_optional_columns() -> None:
    optional_columns: dict[str, str] = {
        "fecha_inicio_servicio": "VARCHAR(40)",
        "recepcion_datos_facturacion": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fecha_recepcion_datos_facturacion": "TIMESTAMP",
        "creacion_clientes_piriod": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fecha_creacion_clientes_piriod": "TIMESTAMP",
        "creacion_clientes_bd": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fecha_creacion_clientes_bd": "TIMESTAMP",
        "facturacion_instalacion": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fecha_facturacion_instalacion": "TIMESTAMP",
        "facturacion_servicio": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fecha_facturacion_servicio": "TIMESTAMP",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("finanzas_odt"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("finanzas_odt")}
            for col_name, col_type in optional_columns.items():
                if col_name in existing_columns:
                    continue
                conn.execute(text(f'ALTER TABLE finanzas_odt ADD COLUMN "{col_name}" {col_type}'))
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'finanzas_odt': %s", exc)


def _ensure_servicio_tecnico_ventas_optional_columns() -> None:
    optional_columns: dict[str, str] = {
        "recepcion_solicitud_instalacion": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fecha_recepcion_solicitud_instalacion": "TIMESTAMP",
        "llamar_cliente": "TEXT",
        "solicitud_materiales": "TEXT",
        "fecha_inicio_instalacion": "VARCHAR(40)",
        "fecha_fin_instalacion": "VARCHAR(40)",
        "tecnico_a_cargo": "VARCHAR(255)",
        "acompanante": "VARCHAR(255)",
        "requiere_puesto_nuevo": "VARCHAR(20)",
        "numero_central_asignado": "VARCHAR(40)",
        "instalacion_finalizada": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fecha_instalacion_finalizada": "TIMESTAMP",
        "finalizado": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fecha_cierre": "TIMESTAMP",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("servicio_tecnico_ventas_odt"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("servicio_tecnico_ventas_odt")}
            for col_name, col_type in optional_columns.items():
                if col_name in existing_columns:
                    continue
                conn.execute(text(f'ALTER TABLE servicio_tecnico_ventas_odt ADD COLUMN "{col_name}" {col_type}'))
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'servicio_tecnico_ventas_odt': %s", exc)


def _ensure_rendiciones_optional_columns() -> None:
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("rendiciones"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("rendiciones")}
            if "folio" in existing_columns:
                conn.execute(text('DROP INDEX IF EXISTS ix_rendiciones_folio'))
                conn.execute(text('DROP INDEX IF EXISTS idx_rendiciones_folio'))
                conn.execute(text('ALTER TABLE rendiciones DROP COLUMN "folio"'))
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
                conn.execute(text(f'ALTER TABLE protocolos_registro ADD COLUMN "{col_name}" {col_type}'))
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
        "fecha_creacion": "TIMESTAMP WITH TIME ZONE DEFAULT NOW()",
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
                conn.execute(text(f'ALTER TABLE bbdd_clientes ADD COLUMN "{col_name}" {col_type}'))
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
            "is_activate": "BOOLEAN NOT NULL DEFAULT TRUE",
            "created_at": "TIMESTAMP",
            "updated_at": "TIMESTAMP",
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
                            conn.execute(text(f'ALTER TABLE "{table}" RENAME COLUMN "{old_name}" TO "{new_name}"'))
                            existing_columns.remove(old_name)
                            existing_columns.add(new_name)
                for col_name, col_type in optional_columns.items():
                    if col_name in existing_columns:
                        continue
                    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{col_name}" {col_type}'))
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
            conn.execute(text("DROP VIEW IF EXISTS users_con_areas"))
    except Exception as exc:
        LOGGER.warning("No fue posible limpiar vista users_con_areas: %s", exc)


def get_service(db: Annotated[Session, Depends(get_db)]) -> IncidenciasService:
    return IncidenciasService(db)


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
    if not sesion or sesion.expires_at <= datetime.utcnow() or not sesion.user_id:
        raise HTTPException(status_code=401, detail="Sesion expirada o no valida.")

    current_user = service.db.get(User, int(sesion.user_id))
    if not current_user or not current_user.is_active:
        raise HTTPException(status_code=401, detail="Usuario no valido.")

    is_admin = str(current_user.role or "").strip().lower() == "admin"
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

    support_engine = _get_support_notes_engine()
    if support_engine is None:
        raise HTTPException(status_code=503, detail="SUPPORT_DB_URL no esta configurado.")

    requester_name = re.sub(r"\s+", " ", (current_user.name or "").strip()) or "Incidencias"

    try:
        schema = settings.support_db_schema if settings.support_db_url.startswith("postgresql") else None
        metadata = MetaData(schema=schema)
        requesters = Table("requesters", metadata, autoload_with=support_engine)
        tickets = Table("tickets", metadata, autoload_with=support_engine)
        messages = Table("messages", metadata, autoload_with=support_engine)
        users = Table("users", metadata, autoload_with=support_engine)

        with support_engine.begin() as conn:
            requester_id = conn.execute(
                select(requesters.c.id)
                .where(requesters.c.name == requester_name)
                .order_by(requesters.c.id.asc())
                .limit(1)
            ).scalar()
            if not requester_id:
                requester_values = {"name": requester_name[:100]}
                if "internal_name" in requesters.c:
                    requester_values["internal_name"] = requester_name[:120]
                if "email" in requesters.c:
                    requester_values["email"] = None
                requester_result = conn.execute(requesters.insert().values(**requester_values))
                requester_id = requester_result.inserted_primary_key[0] if requester_result.inserted_primary_key else None
                if not requester_id:
                    requester_id = conn.execute(
                        select(requesters.c.id)
                        .where(requesters.c.name == requester_name[:100])
                        .order_by(requesters.c.id.desc())
                        .limit(1)
                    ).scalar()
            if not requester_id:
                raise HTTPException(status_code=500, detail="No se pudo crear el requester interno.")

            user_filters = []
            if "name" in users.c:
                user_filters.append(users.c.name == current_user.name)
            if "user" in users.c:
                user_filters.append(users.c["user"] == current_user.username)
            support_user_id = None
            if user_filters:
                condition = user_filters[0]
                for extra_filter in user_filters[1:]:
                    condition = condition | extra_filter
                support_user_id = conn.execute(select(users.c.id).where(condition).limit(1)).scalar()

            ticket_values = {
                "subject": subject,
                "requester_id": int(requester_id),
                "assigned_to_id": None,
                "priority": "",
                "status": "open",
                "source": "internal",
                "is_deleted": False,
                "is_spam": False,
                "reopen_count": 0,
            }
            ticket_values = {key: value for key, value in ticket_values.items() if key in tickets.c}
            ticket_result = conn.execute(tickets.insert().values(**ticket_values))
            ticket_id = ticket_result.inserted_primary_key[0] if ticket_result.inserted_primary_key else None
            if not ticket_id:
                ticket_id = conn.execute(select(tickets.c.id).order_by(tickets.c.id.desc()).limit(1)).scalar()
            if not ticket_id:
                raise HTTPException(status_code=500, detail="No se pudo crear el ticket interno.")

            message_values = {
                "ticket_id": int(ticket_id),
                "sender_type": "agent",
                "sender_id": int(support_user_id) if support_user_id else None,
                "channel": "internal",
                "content": content,
                "is_internal_note": False,
            }
            message_values = {key: value for key, value in message_values.items() if key in messages.c}
            conn.execute(messages.insert().values(**message_values))

        return JSONResponse({"ok": True, "ticket_id": int(ticket_id), "subject": subject})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo crear el ticket interno: {exc}") from exc


def _protocolos_weekly_worker_loop() -> None:
    tz = ZoneInfo(settings.timezone or "America/Santiago")
    ultimo_dia_protocolo = ""
    ultimo_dia_mantenciones = ""
    ultimo_dia_mantenciones_mensuales = ""
    ultimo_dia_mantenciones_trimestrales = ""
    while True:
        try:
            now = datetime.now(tz)
            if now.weekday() == 0 and now.hour >= 6:
                dia_key = now.strftime("%Y-%m-%d")
                if dia_key != ultimo_dia_mantenciones:
                    db = SessionLocal()
                    try:
                        result = IncidenciasService(db).programar_mantenciones_quilpue(
                            fecha_referencia=now,
                            forzar=True,
                        )
                        LOGGER.info("Mantenciones programadas Quilpue: %s", result)
                    finally:
                        db.close()
                    ultimo_dia_mantenciones = dia_key

            if now.day == 1 and now.hour >= 6:
                dia_key = now.strftime("%Y-%m-%d")
                if dia_key != ultimo_dia_mantenciones_mensuales:
                    db = SessionLocal()
                    try:
                        result = IncidenciasService(db).programar_mantenciones_mensuales_llay_llay(
                            fecha_referencia=now,
                            forzar=True,
                        )
                        LOGGER.info("Mantenciones mensuales Llay Llay: %s", result)
                    finally:
                        db.close()
                    ultimo_dia_mantenciones_mensuales = dia_key

            if now.day == 1 and now.month in {3, 6, 9, 12} and now.hour >= 6:
                dia_key = now.strftime("%Y-%m-%d")
                if dia_key != ultimo_dia_mantenciones_trimestrales:
                    db = SessionLocal()
                    try:
                        result = IncidenciasService(db).programar_mantenciones_trimestrales_quintero_y_concon(
                            fecha_referencia=now,
                            forzar=True,
                        )
                        LOGGER.info("Mantenciones trimestrales Quintero/Concon: %s", result)
                    finally:
                        db.close()
                    ultimo_dia_mantenciones_trimestrales = dia_key

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
        "panelSelectorVenta",
        "incidencias",
        "cierreAperturaClientes",
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
        "panelSelectorVenta",
        "incidencias",
        "cierreAperturaClientes",
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

    if form in {
        "tecnicos",
        "rendiciones",
        "rendicionesTecnico",
        "panelSelector",
        "panelSelectorServicio",
        "panelSelectorCoordinacion",
        "panelSelectorAdministracion",
        "panelSelectorVenta",
        "coordinacion",
        "tablaProtocolos",
        "envioProtocolosSemanales",
    } and token:
        tecnico = service.get_usuario_actual(token)
    view_map = {
        "login": "login.html",
        "panelSelector": "seleccion_panel_operadores.html",
        "panelSelectorServicio": "seleccion_panel_soporte.html",
        "panelSelectorCoordinacion": "seleccion_panel_coordinacion.html",
        "panelSelectorAdministracion": "seleccion_panel_administracion.html",
        "panelSelectorVenta": "seleccion_panel_venta.html",
        "servicioTecnico": "incidencias_servicio_tecnico.html",
        "stVentas": "tabla_servicio_tecnico_venta.html",
        "incidencias": "incidencias_puestos.html",
        "cierreAperturaClientes": "cierre_apertura_clientes.html",
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
    } and token:
        if service.contar_areas_para_token(token) > 1:
            show_back_button = True
            helpdesk = (settings.helpdesk_base_url or "").rstrip("/")
            if helpdesk:
                back_url = f"{helpdesk}/seleccionar-area?token={token}"
    context = {
        "request": request,
        "title": "servicioTecnico" if form == "servicioTecnico" else form,
        "token": token,
        "tecnico": tecnico,
        "cliente": cliente,
        "odt": odt,
        "show_back_button": show_back_button,
        "back_url": back_url,
        "next_form": next_form
        if next_form
        in {
            "panelSelectorSoporte",
            "panelSelector",
            "panelSelectorServicio",
            "panelSelectorCoordinacion",
            "panelSelectorAdministracion",
            "panelSelectorVenta",
            "incidencias",
            "cierreAperturaClientes",
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
    app_url = str(request.base_url).rstrip("/")
    data = service.check_login(
        payload.nombre_tecnico,
        payload.clave,
        payload.token,
        app_url,
        payload.destino or "auto",
    )
    if data.get("success") and data.get("token"):
        response.set_cookie(
            key="atc_token",
            value=str(data["token"]),
            httponly=False,
            samesite="lax",
            max_age=60 * 60 * 18,
        )
    return LoginResponse(**data)


@router.post("/api/logout")
def logout(token: str, service: Annotated[IncidenciasService, Depends(get_service)]):
    return {"ok": service.logout(token)}


@router.get("/api/usuario-actual")
def get_usuario_actual(token: str, service: Annotated[IncidenciasService, Depends(get_service)]):
    return {"usuario": service.get_usuario_actual(token)}


@router.get("/sso/login")
def sso_login_standalone(
    request: Request,
    token: str = Query(default=""),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    token_limpio = (token or "").strip()
    if not token_limpio:
        return RedirectResponse(url="/?form=login&next=auto", status_code=303)

    sesion = service.db.get(LoginSession, token_limpio)
    if not sesion or sesion.expires_at <= datetime.utcnow():
        return RedirectResponse(url="/?form=login&next=auto", status_code=303)

    user = service.db.get(User, int(sesion.user_id)) if sesion.user_id else None
    if not user or not user.is_active:
        return RedirectResponse(url="/?form=login&next=auto", status_code=303)

    if len(service._area_codes_usuario(user)) > 1 and not (sesion.area_code or "").strip():
        return RedirectResponse(url=f"/seleccionar-area?token={token_limpio}", status_code=303)

    destino = AREA_PANEL_DESTINOS.get(sesion.area_code or "") or service._destino_principal_usuario(user)
    if destino == "panelSelectorSoporte":
        helpdesk = (settings.helpdesk_base_url or "").rstrip("/")
        base = helpdesk if helpdesk else ""
        return RedirectResponse(url=f"{base}/sso/login?token={token_limpio}&next=/panel?area=soporte", status_code=303)

    app_url = str(request.base_url).rstrip("/")
    return RedirectResponse(
        url=service._redirect_panel_destino(app_url, destino, token_limpio),
        status_code=303,
    )


@router.get("/resumen-equipos-tecnicos", response_class=HTMLResponse)
def resumen_equipos_tecnicos_page(
    request: Request,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    resumen = service.obtener_resumen_equipos_tecnicos_hoy()
    resp = templates.TemplateResponse(
        request,
        "resumen_equipos_tecnicos.html",
        {
            "request": request,
            "resumen": resumen,
        },
    )
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@router.get("/tabla-soporte", response_class=HTMLResponse)
def tabla_soporte_local_page(
    request: Request,
    token: str = Query(default=""),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
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
):
    return {
        "usuarios": service.obtener_usuarios_login_tecnicos(destino),
        "detalles": service.obtener_usuarios_login_detalle(destino),
    }


@router.get("/api/listas/bbdd")
def obtener_listas_bbdd(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_listas_bbdd()


@router.get("/api/materiales/buscar")
def buscar_materiales(
    q: str = "",
    limit: int = 10,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    try:
        return service.buscar_materiales_excel(q, limit)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/listas/incidencias")
def obtener_listas_incidencias(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_listas_incidencias()


@router.get("/api/catalogo-clientes")
def obtener_catalogo_clientes(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_catalogo_clientes()


@router.get("/api/registros")
def obtener_registros(
    tecnico: str = "",
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    try:
        return service.obtener_registros(tecnico)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/registros/administracion")
def obtener_registros_administracion(
    tecnico: str = "",
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    try:
        return service.obtener_registros_desde_administracion(tecnico)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/incidencias/puesto")
def obtener_incidencias_por_puesto(
    service: Annotated[IncidenciasService, Depends(get_service)],
    tecnico: str = "",
):
    try:
        return service.obtener_incidencias_por_puesto(tecnico)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/tecnicos/ruta-optima")
def obtener_ruta_optima_tecnico(
    service: Annotated[IncidenciasService, Depends(get_service)],
    tecnico: str = "",
):
    try:
        return service.obtener_ruta_optima_tecnico(tecnico=tecnico)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/incidencias/coordinacion")
def obtener_incidencias_coordinacion(
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        return service.obtener_incidencias_derivadas_cliente()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/sucursal/detalle")
def obtener_detalle_sucursal(
    odt: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    return service.obtener_datos_sucursal_con_coordenadas(odt)


@router.get("/api/sucursal/incidencias")
def obtener_historial_sucursal(
    cliente: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    return service.obtener_ultimas_incidencias_sucursal(cliente)


@router.get("/api/incidencias/imagenes")
def obtener_imagenes_incidencia(
    odt: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    return service.obtener_imagenes_finalizacion(odt)


@router.get("/api/incidencias/imagenes-tabla")
def obtener_imagenes_tabla(
    odt: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    return {"odt": odt, "imagenes": service.obtener_imagenes_tabla(odt)}


@router.get("/api/incidencias/drive-image/{file_id}")
def obtener_drive_image(
    file_id: str,
    request: Request,
    token: str = Query(default=""),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    tok = (token or "").strip() or str(request.cookies.get("atc_token") or "").strip()
    if not service or not service.usuario_logueado_por_token(tok):
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
        "Cache-Control": "private, max-age=600",
    }
    return Response(content=content, media_type=mime_type or "application/octet-stream", headers=headers)


@router.post("/api/incidencias/upload-image-tabla")
async def subir_imagenes_tabla(
    odt: str = Form(...),
    token: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    service: IncidenciasService = Depends(get_service),
):
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


@router.post("/api/formulario")
def enviar_formulario(
    payload: FormularioRegistro,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        message = service.enviar_formulario(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": message}


@router.post("/api/incidencias/nueva")
def guardar_incidencia_nueva(
    payload: IncidenciaNueva,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        odt = service.guardar_incidencia_nueva(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"odt": odt}


@router.post("/api/incidencias/multiples")
def enviar_multiples_incidencias(
    payload: list[IncidenciaNueva],
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        odts = service.enviar_multiples_incidencias(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"odts": odts}


@router.post("/api/incidencias/cerrar")
def cerrar_incidencia(
    payload: CerrarIncidenciaRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
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
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"result": ok}


@router.post("/api/incidencias/finalizar-completo")
def finalizar_incidencia_completo(
    payload: FinalizarIncidenciaRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
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


@router.post("/api/incidencias/cierre-mantencion")
async def cerrar_mantencion_con_imagenes(
    odt: str = Form(...),
    token: str = Form(""),
    diagnostico: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    service: IncidenciasService = Depends(get_service),
):
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
        staging_dir = service.crear_staging_cierre_mantencion(odt_limpia)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    staged_files = []
    for idx, upload in enumerate(uploads, start=1):
        mime_type = str(upload.content_type or "").lower()
        if not mime_type.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"{upload.filename or 'archivo'} no es una imagen valida.")

        target = staging_dir / service.nombre_staging_cierre_mantencion(idx, upload.filename or "", mime_type)
        total = 0
        with target.open("wb") as fh:
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
                fh.write(chunk)
        if total <= 0:
            raise HTTPException(status_code=400, detail=f"{upload.filename or 'archivo'} esta vacio.")
        staged_files.append(target)

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
            staged_files=staged_files,
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


@router.post("/api/incidencias/en-proceso")
def guardar_incidencia_en_proceso(
    payload: EnProcesoRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        ok = service.guardar_datos_en_proceso(
            payload.odt,
            payload.avance,
            payload.observacion,
            payload.token or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"result": ok}


@router.post("/api/incidencias/derivar-tecnico")
def derivar_incidencia_tecnico(
    payload: DerivarTecnicoRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        ok = service.derivar_odt_a_tecnico(
            payload.odt,
            payload.tecnico,
            payload.acompanante or "",
            payload.derivacion or "Servicio Técnico",
            payload.estado or "Pendiente",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=f"ODT {payload.odt} no encontrada")
    return {"ok": True}


@router.patch("/api/incidencias/editar-tabla")
def editar_incidencia_tabla(
    payload: EditarIncidenciaTablaRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
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
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=f"ODT {payload.odt} no encontrada")
    return result


@router.post("/api/incidencias/cerrar-encargado")
def cerrar_incidencia_encargado(
    odt: str,
    fecha_cierre: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        dt = datetime.fromisoformat(fecha_cierre)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="fecha_cierre debe ser ISO-8601") from exc
    ok = service.cerrar_incidencia(odt, dt)
    if not ok:
        raise HTTPException(status_code=404, detail=f"ODT {odt} no encontrada")
    return {"ok": True}


@router.get("/api/tecnicos/pendientes")
def obtener_tecnicos_pendientes(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_tecnicos_pendientes()


@router.post("/api/mantencion/correctiva")
def guardar_mantencion_correctiva(
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    return {"result": service.guardar_mantencion_correctiva(payload)}


@router.post("/api/mantencion/programada/quilpue/ejecutar")
def ejecutar_mantencion_programada_quilpue(
    fecha_referencia: str | None = None,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    ref = None
    if fecha_referencia:
        try:
            ref = datetime.fromisoformat(fecha_referencia)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="fecha_referencia debe venir en formato ISO-8601 (ej: 2026-04-20T06:00:00).",
            ) from exc
    return service.programar_mantenciones_quilpue(fecha_referencia=ref, forzar=True)


@router.post("/api/mantencion/programada/quintero/ejecutar")
def ejecutar_mantencion_programada_quintero(
    fecha_referencia: str | None = None,
    limite: int | None = None,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    ref = None
    if fecha_referencia:
        try:
            ref = datetime.fromisoformat(fecha_referencia)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="fecha_referencia debe venir en formato ISO-8601 (ej: 2026-06-01T06:00:00).",
            ) from exc
    return service.programar_mantenciones_trimestrales_quintero(fecha_referencia=ref, forzar=True, limite=limite)


@router.post("/api/mantencion/programada/concon/ejecutar")
def ejecutar_mantencion_programada_concon(
    fecha_referencia: str | None = None,
    limite: int | None = None,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    ref = None
    if fecha_referencia:
        try:
            ref = datetime.fromisoformat(fecha_referencia)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="fecha_referencia debe venir en formato ISO-8601 (ej: 2026-06-01T06:00:00).",
            ) from exc
    return service.programar_mantenciones_trimestrales_concon(fecha_referencia=ref, forzar=True, limite=limite)


@router.get("/api/mantencion/programada/plantilla")
def obtener_plantilla_mantencion_programada(
    sucursal: str,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    if not str(sucursal or "").strip():
        raise HTTPException(status_code=400, detail="sucursal es obligatoria.")
    imagenes = service.obtener_plantilla_imagenes_mantencion(sucursal)
    return {"sucursal": sucursal, "imagenes": imagenes, "total_imagenes": len(imagenes)}


@router.post("/api/mantencion/programada/plantilla")
def guardar_plantilla_mantencion_programada(
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
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
):
    try:
        sucursal = str(payload.get("sucursal") or "").strip()
        odt_origen = str(payload.get("odt_origen") or "").strip()
        return service.guardar_plantilla_imagenes_mantencion_desde_odt(
            sucursal=sucursal,
            odt_origen=odt_origen,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/contactos/sucursal")
def obtener_contactos_por_sucursal(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_contactos_por_sucursal()


@router.post("/api/contacto-cliente/enviar-info")
def enviar_info_contacto_cliente(
    payload: EnviarInformacionContactoRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        return service.registrar_envio_informacion_contacto(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/clientes-soporte")
def obtener_clientes_soporte(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_clientes_soporte()


@router.post("/api/sync/soporte/retry")
def reintentar_sync_soporte(
    limit: int = 50,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    return service.sync_soporte_pendientes(limit)


@router.get("/api/sync/soporte/outbox")
def estado_sync_soporte(
    limit: int = 100,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    return service.obtener_estado_sync_outbox(limit)


@router.get("/api/tareas/tipos")
def obtener_tipos_especificaciones():
    return TIPOS_Y_ESPECIFICACIONES


@router.get("/api/protocolos/listas")
def obtener_listas_protocolos(
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)],
):
    return service.obtener_listas()


@router.post("/api/protocolos/registro")
def crear_registro_protocolo(
    payload: ProtocoloRegistroCreateRequest,
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)],
):
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
):
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
):
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
):
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
):
    try:
        return service.obtener_contactos_informe(informe_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/protocolos/informes/{informe_id}/enviar")
def enviar_informe_semanal_protocolo(
    informe_id: int,
    payload: dict = Body(default_factory=dict),
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)] = None,
):
    try:
        return service.enviar_informe_semanal(informe_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/protocolos/informes/{informe_id}/rechazar")
def rechazar_informe_semanal_protocolo(
    informe_id: int,
    payload: dict = Body(default_factory=dict),
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)] = None,
):
    try:
        return service.rechazar_informe_semanal(informe_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/api/protocolos/informes/{informe_id}")
def eliminar_informe_protocolo(
    informe_id: int,
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)] = None,
):
    try:
        return service.eliminar_informe(informe_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/derivaciones")
def obtener_registros_derivaciones(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_registros_derivaciones()


@router.post("/api/coordinacion/finalizar")
def finalizar_odt_coordinacion(
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
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
):
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
):
    try:
        return service.registrar_envio_correo_coordinacion(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/tareas")
def registrar_tarea_manual(
    payload: TareaManualRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        codigo = service.registrar_tarea_manual(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": codigo}


@router.get("/api/tareas")
def obtener_tareas(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_registro_tareas()


@router.patch("/api/tareas/{tarea_id}")
def actualizar_tarea(
    tarea_id: int,
    columna: str,
    valor: str,
    token: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    ok = service.actualizar_celda_tarea(tarea_id, columna, valor, token)
    if not ok:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return {"ok": True}


@router.post("/api/rendiciones")
def registrar_rendicion(
    payload: RendicionRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        return service.registrar_gasto(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/rendiciones/url")
def obtener_url_formulario_rendicion(request: Request):
    return {"url": str(request.base_url).rstrip("/")}


@router.get("/api/rendiciones/duplicado")
def verificar_nro_documento_duplicado(
    nro_documento: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    return {"duplicado": service.existe_nro_documento_duplicado(nro_documento)}


@router.post("/api/rendiciones/upload-boleta")
async def subir_boleta_rendicion(
    file: UploadFile = File(...),
    tecnico: str = Form(""),
    odt: str = Form(""),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
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
):
    return service.obtener_rendiciones(tecnico=tecnico, pendientes_only=pendientes)


@router.patch("/api/rendiciones/{rendicion_id}")
def marcar_rendicion(
    rendicion_id: int,
    accion: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
    token: str = "",
):
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
def finanzas_consolidado(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_consolidado()


@router.get("/api/finanzas/viatico-especial")
def finanzas_viatico_especial(
    codigo: str = "",
    personalizados: bool = False,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    return service.obtener_viatico_especial(codigo=codigo, personalizados=personalizados)


@router.patch("/api/finanzas/viatico-cap/{codigo}")
def finanzas_set_viatico_cap(
    codigo: str,
    monto: float,
    token: str = "",
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    try:
        usuario = service.get_usuario_actual(token) if token else ""
        usuario = usuario if usuario and usuario != "Desconocido" else ""
        return service.set_viatico_cap(codigo, monto, usuario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/finanzas/pagos")
def finanzas_listar_pagos(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_pagos()


@router.post("/api/finanzas/pagos")
def finanzas_registrar_pago(
    payload: dict,
    token: str = "",
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    try:
        usuario = service.get_usuario_actual(token) if token else ""
        usuario = usuario if usuario and usuario != "Desconocido" else ""
        return service.registrar_pago(payload, usuario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/finanzas/pagos/{pago_id}")
def finanzas_eliminar_pago(
    pago_id: int,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    ok = service.eliminar_pago(pago_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    return {"ok": True}


@router.get("/api/finanzas/suma-pagos")
def finanzas_suma_pagos(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_suma_pagos()


@router.get("/api/planificacion")
def obtener_planificacion_total(
    mes: int,
    anio: int,
    estado: str = "Todos",
    tecnico: str = "Todos",
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    return service.obtener_planificacion_total(mes, anio, estado, tecnico)


@router.get("/api/debug/db")
def debug_db(
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    out = {
        "database_url": settings.database_url,
        "db_schema_setting": settings.db_schema,
    }
    try:
        out["current_schema"] = db.execute(text("SELECT current_schema()")).scalar_one()
    except Exception as e:
        out["current_schema_error"] = str(e)
    try:
        out["catalogo_clientes_count"] = db.execute(text("SELECT COUNT(*) FROM catalogo_clientes")).scalar_one()
    except Exception as e:
        out["catalogo_clientes_count_error"] = str(e)
    try:
        out["catalogo_clientes_columns"] = [
            r[0]
            for r in db.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'catalogo_clientes'
                    ORDER BY ordinal_position
                    """
                )
            ).all()
        ]
    except Exception as e:
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
                      AND table_schema NOT IN ('pg_catalog', 'information_schema')
                    ORDER BY table_schema
                    """
                )
            ).all()
        ]
    except Exception as e:
        out["catalogo_clientes_schemas_error"] = str(e)
    try:
        out["catalogo_clientes_sample"] = service.obtener_catalogo_clientes()[:10]
    except Exception as e:
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
def servicio_kpis_data(db: Annotated[Session, Depends(get_db)]):
    from sqlalchemy import select as sa_select
    from ATC.app.models.incidencias import Registro, ServicioTecnicoVentaODT, VentaODS

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

    registros = db.scalars(sa_select(Registro).order_by(Registro.fecha_registro.desc())).all()
    avance_por_odt: dict[str, Registro] = {}
    for reg in registros:
        odt_key = str(getattr(reg, "odt", "") or "").strip().upper()
        if odt_key and odt_key not in avance_por_odt:
            avance_por_odt[odt_key] = reg

    # Avance de instalación de cámaras: ODS de venta en etapa de servicio técnico
    # con cámaras contratadas (venta_ods."Cámaras a instalar")
    avance_rows = db.execute(
        sa_select(ServicioTecnicoVentaODT, VentaODS)
        .outerjoin(VentaODS, VentaODS.codigo == ServicioTecnicoVentaODT.odt)
        .where(VentaODS.numero_camaras_instalar.is_not(None))
        .where(VentaODS.numero_camaras_instalar > 0)
        .order_by(VentaODS.created_at.desc())
    ).all()
    avance_camaras = []
    for st, v in avance_rows:
        total = int(v.numero_camaras_instalar or 0)
        finalizada = bool(st and (st.instalacion_finalizada or st.finalizado))
        instaladas_registradas = _contar_camaras_registradas(getattr(st, "camaras_registradas", None)) if st else 0
        if not instaladas_registradas:
            reg = avance_por_odt.get(str(st.odt or v.codigo or "").strip().upper())
            if reg:
                instaladas_registradas = _porcentaje_a_instaladas(getattr(reg, "porcentaje_avance", None), total)
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
            }
        )
    return {
        "avance_camaras": avance_camaras,
        "config": {
            "sla_dias": settings.servicio_sla_dias,
            "odt_antigua_dias": settings.servicio_odt_antigua_dias,
            "reincidencia_ventana_dias": settings.servicio_reincidencia_ventana_dias,
            "instalacion_mala_dias": settings.servicio_instalacion_mala_dias,
            "instalacion_regular_dias": settings.servicio_instalacion_regular_dias,
        },
        "registros": [
            {
                "odt": r.odt,
                "fecha_registro": r.fecha_registro.isoformat() if r.fecha_registro else None,
                "fecha_cierre": r.fecha_cierre.isoformat() if r.fecha_cierre else None,
                "fecha_derivacion_area": r.fecha_derivacion_area.isoformat() if r.fecha_derivacion_area else None,
                "fecha_derivacion_tecnico": r.fecha_derivacion_tecnico.isoformat() if r.fecha_derivacion_tecnico else None,
                "cliente": r.cliente or "",
                "direccion": r.direccion or "",
                "puesto": r.puesto or "",
                "problema": r.problema or "",
                "estado": r.estado or "",
                "tecnicos": r.tecnicos or "",
                "acompanante": r.acompanante or "",
                "dias_ejecucion": r.dias_ejecucion,
                "responsable_cierre": r.responsable_cierre or "",
                "causa_cierre": r.causa_cierre or "",
                "accion_cierre": r.accion_cierre or "",
                "resultado_cierre": r.resultado_cierre or "",
                "pruebas_cierre": r.pruebas_cierre or "",
                "materiales": r.materiales or "",
                "requiere_seguimiento": bool(r.requiere_seguimiento),
                "observacion_final": r.observacion_final or "",
            }
            for r in registros
        ],
    }
