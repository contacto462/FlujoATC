from __future__ import annotations

import hmac
import json as _json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from ATC.app.routes.bitacora_access import can_access_bitacora, is_bitacora_admin, _normalize, _split_departments
from ATC.app.core.config import settings
from ATC.app.core.db import get_db, get_incidencias_db
from ATC.app.models.incidencias import SucursalBBDD, SucursalPersonaAutorizada
from ATC.app.models.user import User
from ATC.app.core.security import hash_password
from ATC.app.services.user_service import UserService


router = APIRouter(tags=["bitacora"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
COOKIE_NAME = "access_token"


class NoticiaCreate(BaseModel):
    nombre_empresa: str = Field(min_length=1)
    nombre_sucursal: str = Field(min_length=1)
    fecha_fin_noticia: date
    mensaje: str = Field(min_length=1)


class SucursalEditPayload(BaseModel):
    sucursal_id: int
    nombre_sucursal: str = ""
    direccion_sucursal: str = ""
    referencia_ubicacion: str = ""
    email_facturas: str = ""
    horario_apertura: str = ""
    horario_cierre: str = ""
    horario_habil: str = ""
    horario_no_habil: str = ""
    plan_cuadrante: str = ""
    carabineros: str = ""
    bomberos: str = ""
    seguridad_ciudadana: str = ""
    camaras_contratadas: str = ""
    camaras_televigiladas: str = ""
    codigo_p2p: str = ""
    codigo_dss: str = ""
    telefono_porton: str = ""
    telefono_recepcion: str = ""
    internet_atc: str = ""
    compania_electricidad: str = ""
    numero_cliente_electricidad: str = ""
    proveedor_internet_cliente: str = ""


def _decode_cookie_token(token: str) -> str:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
        username = payload.get("sub")
        if not username:
            raise ValueError("Token sin sub")
        return str(username)
    except (JWTError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Token inválido") from exc


def _require_bitacora_access(user: User) -> None:
    if not can_access_bitacora(user):
        raise HTTPException(
            status_code=403,
            detail="La bitácora no está disponible para usuarios con acceso solo Técnico.",
        )


def _bitacora_users(db: Session) -> list[dict[str, str | bool | int]]:
    users = db.query(User).order_by(User.name.asc(), User.username.asc()).all()
    result = []
    for user in users:
        if not can_access_bitacora(user):
            continue
        departments = [_normalize(d) for d in _split_departments(getattr(user, "department", None))]
        result.append({
            "id": user.id,
            "name": str(user.name or "").strip(),
            "username": str(user.username or "").strip(),
            "email": str(user.email or "").strip(),
            "role": user.role,
            "user_type": "Administrador" if getattr(user, "is_admin", False) else "Operador",
            "status": "Activado" if bool(getattr(user, "is_active", False)) else "Desactivado",
            "is_active": bool(getattr(user, "is_active", False)),
            "is_televigilante": "televigilante" in departments,
        })
    return result


def _ensure_bitacora_noticias_table(db: Session) -> None:
    db.execute(
        text(
            """
            IF OBJECT_ID('bitacora_noticias', 'U') IS NULL
            BEGIN
            CREATE TABLE bitacora_noticias (
                id BIGINT IDENTITY(1,1) PRIMARY KEY,
                nombre_empresa NVARCHAR(MAX) NOT NULL,
                nombre_sucursal NVARCHAR(MAX) NOT NULL,
                usuario_registra NVARCHAR(MAX) NOT NULL,
                fecha_registro DATETIME2 NOT NULL DEFAULT GETDATE(),
                fecha_fin_noticia DATETIME2 NOT NULL,
                mensaje NVARCHAR(MAX) NOT NULL
            )
            END
            """
        )
    )
    db.commit()


def _serialize_noticia(row: dict) -> dict[str, str | int]:
    fecha_registro = row.get("fecha_registro")
    fecha_fin = row.get("fecha_fin_noticia")
    return {
        "id": row.get("id"),
        "nombre_empresa": str(row.get("nombre_empresa") or "").strip(),
        "nombre_sucursal": str(row.get("nombre_sucursal") or "").strip(),
        "usuario_registra": str(row.get("usuario_registra") or "").strip(),
        "mensaje": str(row.get("mensaje") or "").strip(),
        "fecha_registro": fecha_registro.isoformat(sep=" ", timespec="seconds") if isinstance(fecha_registro, datetime) else str(fecha_registro or ""),
        "fecha_fin_noticia": fecha_fin.isoformat(sep=" ", timespec="seconds") if isinstance(fecha_fin, datetime) else str(fecha_fin or ""),
    }


def _list_noticias(db: Session, estado: Literal["activas", "expiradas"]) -> list[dict[str, str | int]]:
    _ensure_bitacora_noticias_table(db)
    comparator = ">=" if estado == "activas" else "<"
    order_by = "fecha_registro DESC, id DESC" if estado == "activas" else "fecha_fin_noticia DESC, id DESC"
    rows = db.execute(
        text(
            f"""
            SELECT
                id,
                nombre_empresa,
                nombre_sucursal,
                usuario_registra,
                fecha_registro,
                fecha_fin_noticia,
                mensaje
            FROM bitacora_noticias
            WHERE fecha_fin_noticia {comparator} CURRENT_TIMESTAMP
            ORDER BY {order_by}
            """
        )
    ).mappings().all()
    return [_serialize_noticia(row) for row in rows]


def _detail_value(row: dict, key: str) -> str:
    value = row.get(key)
    text_value = str(value or "").strip()
    return text_value or "-"


def _first_non_empty(*values: object) -> str:
    for value in values:
        text_value = str(value or "").strip()
        if text_value:
            return text_value
    return "-"


_TODOS_LOS_DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def _first_image_url(imagenes_json: str | None) -> str:
    """Extrae la primera URL de un campo imagenes (JSON array de strings)."""
    raw = str(imagenes_json or "").strip()
    if not raw or raw == "-":
        return "-"
    try:
        urls = _json.loads(raw)
        if isinstance(urls, list) and urls:
            url = str(urls[0]).strip()
            return url if url else "-"
    except Exception:
        pass
    return "-"


def _dias_no_habiles(dias_funcionamiento: str) -> str:
    """Devuelve los días que NO están en dias_funcionamiento."""
    import unicodedata

    def _norm(s: str) -> str:
        return "".join(
            c for c in unicodedata.normalize("NFD", s.lower().strip())
            if unicodedata.category(c) != "Mn"
        )

    raw = str(dias_funcionamiento or "").strip()
    if not raw or raw == "-":
        return "-"
    habiles = {_norm(d) for d in raw.split(",")}
    no_habiles = [d for d in _TODOS_LOS_DIAS if _norm(d) not in habiles]
    return ", ".join(no_habiles) if no_habiles else "-"


def get_current_user_bitacora(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="No autenticado")

    username = _decode_cookie_token(token)
    user = UserService.find_by_login(db, username)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="No autenticado")
    return user


@router.get("/bitacora", response_class=HTMLResponse)
def bitacora_page(
    request: Request,
    db: Session = Depends(get_db),
    incidencias_db: Session = Depends(get_incidencias_db),
    token: str = Query(default=""),
):
    current_user: User | None = None
    token_limpio = (token or "").strip()
    if token_limpio:
        return RedirectResponse(url=f"/sso/login?token={token_limpio}&next=/bitacora", status_code=303)

    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token:
        try:
            username = _decode_cookie_token(cookie_token)
            current_user = UserService.find_by_login(db, username)
        except Exception:
            current_user = None

    if not current_user or not current_user.is_active:
        return RedirectResponse(url="/login", status_code=303)

    _require_bitacora_access(current_user)

    empresas_rows = incidencias_db.execute(
        text(
            """
            SELECT DISTINCT TRIM(nombre_empresa) AS empresa
            FROM bbdd_sucursales
            WHERE COALESCE(TRIM(nombre_empresa), '') <> ''
            ORDER BY TRIM(nombre_empresa) ASC
            """
        )
    ).mappings().all()
    empresas = [str(row.get("empresa") or "").strip() for row in empresas_rows if str(row.get("empresa") or "").strip()]
    # La tabla de usuarios (nombre/correo/rol/estado) solo debe llegar al HTML si el
    # usuario es admin — antes se enviaba siempre y el panel "Registro de Usuario"
    # no estaba gateado en el template, exponiendo el listado completo a cualquiera.
    bitacora_users = _bitacora_users(db) if is_bitacora_admin(current_user) else []

    resp = templates.TemplateResponse(
        request,
        "bitacora.html",
        {
            "request": request,
            "user": current_user,
            "is_bitacora_admin": is_bitacora_admin(current_user),
            "is_operador": not bool(getattr(current_user, "is_admin", False)) and not is_bitacora_admin(current_user),
            "empresas": empresas,
            "bitacora_users": bitacora_users,
        },
    )
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@router.get("/api/bitacora/sucursales")
def bitacora_sucursales_api(
    empresa: str = Query(default=""),
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    empresa_limpia = str(empresa or "").strip()

    # Sin `empresa`: listar todas las sucursales de todas las empresas —
    # usado por los buscadores que permiten elegir sucursal directo sin
    # elegir empresa antes (autocompletan la empresa dueña con el dato
    # nombre_empresa que ya viene en cada fila).
    if empresa_limpia:
        where_clause = "WHERE LOWER(TRIM(nombre_empresa)) = LOWER(TRIM(:empresa))"
        params: dict[str, str] = {"empresa": empresa_limpia}
    else:
        where_clause = ""
        params = {}

    rows = incidencias_db.execute(
        text(
            f"""
            SELECT
                id,
                COALESCE(TRIM(rut), '') AS rut,
                COALESCE(TRIM(nombre_empresa), '') AS nombre_empresa,
                COALESCE(TRIM(nombre_sucursal), '') AS nombre_sucursal,
                COALESCE(TRIM(direccion_sucursal), '') AS direccion_sucursal,
                COALESCE(TRIM(comuna), '') AS comuna,
                COALESCE(TRIM(region), '') AS region
            FROM bbdd_sucursales
            {where_clause}
            ORDER BY nombre_empresa ASC, nombre_sucursal ASC, direccion_sucursal ASC, id ASC
            """
        ),
        params,
    ).mappings().all()

    sucursales = [
        {
            "id": row.get("id"),
            "rut": str(row.get("rut") or "").strip(),
            "nombre_empresa": str(row.get("nombre_empresa") or "").strip(),
            "nombre_sucursal": str(row.get("nombre_sucursal") or "").strip(),
            "direccion_sucursal": str(row.get("direccion_sucursal") or "").strip(),
            "comuna": str(row.get("comuna") or "").strip(),
            "region": str(row.get("region") or "").strip(),
        }
        for row in rows
    ]
    return {"empresa": empresa_limpia, "total": len(sucursales), "sucursales": sucursales}


@router.get("/api/bitacora/noticias")
def bitacora_noticias_api(
    estado: Literal["activas", "expiradas"] = Query(default="activas"),
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    noticias = _list_noticias(incidencias_db, estado)
    return {"estado": estado, "total": len(noticias), "noticias": noticias}


@router.get("/api/bitacora/busqueda-empresa")
def bitacora_busqueda_empresa_api(
    empresa: str = Query(...),
    sucursal: str = Query(default=""),
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    _ensure_bitacora_noticias_table(incidencias_db)

    empresa_limpia = str(empresa or "").strip()
    sucursal_limpia = str(sucursal or "").strip()
    if not empresa_limpia:
        raise HTTPException(status_code=400, detail="Debes indicar una empresa.")

    sucursales_rows = incidencias_db.execute(
        text(
            """
            SELECT
                id,
                COALESCE(TRIM(rut), '') AS rut,
                COALESCE(TRIM(nombre_empresa), '') AS nombre_empresa,
                COALESCE(TRIM(nombre_sucursal), '') AS nombre_sucursal,
                COALESCE(TRIM(direccion_sucursal), '') AS direccion_sucursal,
                COALESCE(TRIM(region), '') AS region,
                COALESCE(TRIM(comuna), '') AS comuna,
                COALESCE(TRIM(referencia_ubicacion), '') AS referencia_ubicacion,
                COALESCE(TRIM(email_facturas), '') AS email_facturas,
                COALESCE(TRIM(proveedor_internet), '') AS proveedor_internet,
                COALESCE(TRIM(proveedor_electricidad), '') AS proveedor_electricidad,
                COALESCE(TRIM(nro_proveedor_electricidad), '') AS nro_proveedor_electricidad,
                COALESCE(TRIM(horario_apertura), '') AS horario_apertura,
                COALESCE(TRIM(horario_cierre), '') AS horario_cierre,
                COALESCE(TRIM(dias_funcionamiento), '') AS dias_funcionamiento,
                created_at
            FROM bbdd_sucursales
            WHERE LOWER(TRIM(nombre_empresa)) = LOWER(TRIM(:empresa))
            ORDER BY nombre_sucursal ASC, id ASC
            """
        ),
        {"empresa": empresa_limpia},
    ).mappings().all()

    if not sucursales_rows:
        return {
            "empresa": empresa_limpia,
            "sucursal_actual": "",
            "sucursales": [],
            "detalle": {},
            "contactos_emergencia": [],
            "indicaciones_especiales": [],
            "noticias": [],
            "personas_autorizadas": [],
        }

    sucursales = [
        {
            "id": row.get("id"),
            "nombre_sucursal": _detail_value(row, "nombre_sucursal"),
        }
        for row in sucursales_rows
    ]

    selected_row = next(
        (row for row in sucursales_rows if _detail_value(row, "nombre_sucursal").casefold() == sucursal_limpia.casefold()),
        sucursales_rows[0],
    )
    selected_sucursal = _detail_value(selected_row, "nombre_sucursal")

    venta_row = None
    venta_rows: list = []
    try:
        venta_rows = incidencias_db.execute(
            text(
                """
                SELECT
                    codigo,
                    estado,
                    tipo_plan,
                    numero_camaras_instalar,
                    numero_camaras_desinstalar,
                    numero_camaras_vigilar
                FROM venta_comercial
                WHERE LOWER(TRIM(rut_cliente)) = LOWER(TRIM(:rut))
                  AND (
                    LOWER(TRIM(direccion_sucursal)) = LOWER(TRIM(:direccion))
                    OR LOWER(TRIM(nombre_sucursal)) = LOWER(TRIM(:sucursal))
                  )
                ORDER BY id DESC
                """
            ),
            {
                "rut": _detail_value(selected_row, "rut"),
                "direccion": _detail_value(selected_row, "direccion_sucursal"),
                "sucursal": selected_sucursal,
            },
        ).mappings().all()
    except Exception:
        venta_rows = []

    venta_row = venta_rows[0] if venta_rows else None  # ultima ODS: para tipo_plan/codigo, etc.

    # Las camaras se ACUMULAN entre todas las ODS de la sucursal (mismo criterio que
    # venta_service.get_cliente_sucursal_resumen): una ODS de upgrade con 2 camaras
    # suma sobre las 4 existentes -> 6, no las pisa. Se excluyen las ODS anuladas.
    ods_validas = [o for o in venta_rows if str(o.get("estado") or "").strip().lower() != "anulada"]
    total_camaras_instalar = sum(int(o.get("numero_camaras_instalar") or 0) for o in ods_validas)
    total_camaras_desinstalar = sum(int(o.get("numero_camaras_desinstalar") or 0) for o in ods_validas)
    total_camaras_vigilar = sum(int(o.get("numero_camaras_vigilar") or 0) for o in ods_validas)
    total_camaras_contratadas = max(total_camaras_instalar - total_camaras_desinstalar, 0)

    cliente_row = None
    try:
        cliente_row = incidencias_db.execute(
            text(
                """
                SELECT telefono
                FROM bbdd_clientes
                WHERE LOWER(TRIM(cliente)) = LOWER(TRIM(:empresa))
                   OR LOWER(TRIM(rut)) = LOWER(TRIM(:rut))
                ORDER BY id DESC OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
                """
            ),
            {"empresa": empresa_limpia, "rut": _detail_value(selected_row, "rut")},
        ).mappings().first()
    except Exception:
        cliente_row = None

    emergency_rows = []
    try:
        emergency_rows = incidencias_db.execute(
            text(
                """
                SELECT
                    COALESCE(NULLIF(TRIM(nombre), ''), '-') AS nombre,
                    COALESCE(NULLIF(TRIM(telefono), ''), '-') AS celular,
                    '-' AS prioridad
                FROM sucursal_contactos_emergencia
                WHERE sucursal_id = :sucursal_id
                ORDER BY id ASC
                """
            ),
            {"sucursal_id": selected_row.get("id")},
        ).mappings().all()
    except Exception:
        emergency_rows = []

    personas_autorizadas_rows = []
    try:
        personas_autorizadas_rows = incidencias_db.execute(
            text(
                """
                SELECT
                    COALESCE(NULLIF(TRIM(nombre), ''), '-') AS nombre,
                    COALESCE(NULLIF(TRIM(rut), ''), '-') AS rut,
                    COALESCE(NULLIF(TRIM(telefono), ''), '-') AS celular,
                    COALESCE(NULLIF(TRIM(email), ''), '-') AS email,
                    COALESCE(NULLIF(TRIM(clave_verde), ''), '-') AS clave_verde,
                    COALESCE(NULLIF(TRIM(clave_roja), ''), '-') AS clave_roja
                FROM sucursal_personas_autorizadas
                WHERE sucursal_id = :sucursal_id
                ORDER BY id ASC
                """
            ),
            {"sucursal_id": selected_row.get("id")},
        ).mappings().all()
    except Exception:
        personas_autorizadas_rows = []

    indicaciones_rows = []
    try:
        indicaciones_rows = incidencias_db.execute(
            text(
                """
                SELECT
                    fecha,
                    mensaje
                FROM bitacora_indicaciones_bdatc
                WHERE sucursal_id = :sucursal_id
                  AND COALESCE(TRIM(mensaje), '') <> ''
                ORDER BY fecha DESC, id DESC
                """
            ),
            {"sucursal_id": selected_row.get("id")},
        ).mappings().all()
    except Exception:
        try:
            indicaciones_rows = incidencias_db.execute(
                text(
                    """
                    SELECT
                        fecha_registro AS fecha,
                        mensaje
                    FROM bitacora_noticias
                    WHERE LOWER(TRIM(nombre_empresa)) = LOWER(TRIM(:empresa))
                      AND LOWER(TRIM(nombre_sucursal)) = LOWER(TRIM(:sucursal))
                      AND LOWER(TRIM(usuario_registra)) = 'bdatc indicaciones'
                      AND COALESCE(TRIM(mensaje), '') <> ''
                    ORDER BY fecha_registro DESC, id DESC
                    """
                ),
                {"empresa": empresa_limpia, "sucursal": selected_sucursal},
            ).mappings().all()
        except Exception:
            indicaciones_rows = []

    layout_row = None
    try:
        # Prioridad 1: layout_final subido por servicio técnico
        layout_row = incidencias_db.execute(
            text(
                """
                SELECT st.layout_final AS ruta_archivo
                FROM venta_servicio_tecnico st
                JOIN venta_comercial v ON v.codigo = st.odt
                WHERE st.layout_final IS NOT NULL AND TRIM(st.layout_final) <> ''
                  AND LOWER(TRIM(v.rut_cliente)) = LOWER(TRIM(:rut))
                  AND (
                    LOWER(TRIM(v.direccion_sucursal)) = LOWER(TRIM(:direccion))
                    OR LOWER(TRIM(v.nombre_sucursal)) = LOWER(TRIM(:sucursal))
                  )
                ORDER BY st.id DESC OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
                """
            ),
            {
                "rut": _detail_value(selected_row, "rut"),
                "direccion": _detail_value(selected_row, "direccion_sucursal"),
                "sucursal": selected_sucursal,
            },
        ).mappings().first()
    except Exception:
        layout_row = None

    if not layout_row:
        try:
            # Fallback: layout subido por comercial
            layout_row = incidencias_db.execute(
                text(
                    """
                    SELECT a.ruta_archivo
                    FROM venta_ods_archivos a
                    JOIN venta_comercial v ON v.codigo = a.codigo_ods
                    WHERE a.tipo_documento = 'Layout'
                      AND LOWER(TRIM(v.rut_cliente)) = LOWER(TRIM(:rut))
                      AND (
                        LOWER(TRIM(v.direccion_sucursal)) = LOWER(TRIM(:direccion))
                        OR LOWER(TRIM(v.nombre_sucursal)) = LOWER(TRIM(:sucursal))
                      )
                    ORDER BY a.id DESC OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
                    """
                ),
                {
                    "rut": _detail_value(selected_row, "rut"),
                    "direccion": _detail_value(selected_row, "direccion_sucursal"),
                    "sucursal": selected_sucursal,
                },
            ).mappings().first()
        except Exception:
            layout_row = None

    info_extra_row = None
    try:
        info_extra_row = incidencias_db.execute(
            text("SELECT TOP 1 * FROM sucursal_info_extra WHERE sucursal_id = :sid"),
            {"sid": selected_row.get("id")},
        ).mappings().first()
    except Exception:
        info_extra_row = None

    noticias_rows = []
    try:
        noticias_rows = incidencias_db.execute(
            text(
                """
                SELECT
                    id,
                    nombre_empresa,
                    nombre_sucursal,
                    usuario_registra,
                    fecha_registro,
                    fecha_fin_noticia,
                    mensaje
                FROM bitacora_noticias
                WHERE LOWER(TRIM(nombre_empresa)) = LOWER(TRIM(:empresa))
                  AND LOWER(TRIM(nombre_sucursal)) = LOWER(TRIM(:sucursal))
                  AND LOWER(TRIM(usuario_registra)) <> 'bdatc indicaciones'
                ORDER BY fecha_registro DESC, id DESC
                """
            ),
            {"empresa": empresa_limpia, "sucursal": selected_sucursal},
        ).mappings().all()
    except Exception:
        noticias_rows = []

    detalle = {
        "empresa": _detail_value(selected_row, "nombre_empresa"),
        "rut": _detail_value(selected_row, "rut"),
        "direccion": _detail_value(selected_row, "direccion_sucursal"),
        "referencia_ubicacion": _first_non_empty(_detail_value(selected_row, "referencia_ubicacion"), info_extra_row.get("referencia_ubicacion") if info_extra_row else None),
        "contacto": _first_non_empty(cliente_row.get("telefono") if cliente_row else None, info_extra_row.get("contacto") if info_extra_row else None),
        "correo": _first_non_empty(_detail_value(selected_row, "email_facturas"), info_extra_row.get("correo") if info_extra_row else None),
        "horario_apertura": _first_non_empty(_detail_value(selected_row, "horario_apertura")),
        "horario_cierre": _first_non_empty(_detail_value(selected_row, "horario_cierre")),
        "horario_habil": _first_non_empty(_detail_value(selected_row, "dias_funcionamiento"), info_extra_row.get("horario_habil") if info_extra_row else None),
        "horario_no_habil": _first_non_empty(_dias_no_habiles(_detail_value(selected_row, "dias_funcionamiento")), info_extra_row.get("horario_no_habil") if info_extra_row else None),
        "fecha_inicio": _first_non_empty(info_extra_row.get("fecha_inicio") if info_extra_row else None),
        "tipo_vigilancia": _first_non_empty(venta_row.get("tipo_plan") if venta_row else None, info_extra_row.get("tipo_vigilancia") if info_extra_row else None),
        "camaras_contratadas": _first_non_empty(str(total_camaras_contratadas) if ods_validas else None, info_extra_row.get("camaras_contratadas") if info_extra_row else None),
        "camaras_televigiladas": _first_non_empty(str(total_camaras_vigilar) if ods_validas else None, info_extra_row.get("camaras_televigiladas") if info_extra_row else None),
        "plano_camaras": _first_non_empty(layout_row.get("ruta_archivo") if layout_row else None),
        "plan_cuadrante": _first_non_empty(info_extra_row.get("plan_cuadrante") if info_extra_row else None),
        "carabineros": _first_non_empty(info_extra_row.get("carabineros") if info_extra_row else None),
        "bomberos": _first_non_empty(info_extra_row.get("bomberos") if info_extra_row else None),
        "seguridad_ciudadana": _first_non_empty(info_extra_row.get("seguridad_ciudadana") if info_extra_row else None),
        "codigo_p2p": _first_non_empty(info_extra_row.get("codigo_p2p") if info_extra_row else None),
        "codigo_dss": _first_non_empty(info_extra_row.get("codigo_dss") if info_extra_row else None),
        "telefono_porton": _first_non_empty(info_extra_row.get("telefono_porton") if info_extra_row else None),
        "telefono_recepcion": _first_non_empty(info_extra_row.get("telefono_recepcion") if info_extra_row else None),
        "internet_atc": _first_non_empty(info_extra_row.get("internet_atc") if info_extra_row else None),
        "compania_electricidad": _first_non_empty(_detail_value(selected_row, "proveedor_electricidad"), info_extra_row.get("compania_electricidad") if info_extra_row else None),
        "numero_cliente_electricidad": _first_non_empty(_detail_value(selected_row, "nro_proveedor_electricidad"), info_extra_row.get("numero_cliente_electricidad") if info_extra_row else None),
        "proveedor_internet_cliente": _first_non_empty(_detail_value(selected_row, "proveedor_internet"), info_extra_row.get("proveedor_internet_cliente") if info_extra_row else None),
    }

    return {
        "empresa": empresa_limpia,
        "sucursal_id": selected_row.get("id"),
        "sucursal_actual": selected_sucursal,
        "sucursales": sucursales,
        "detalle": detalle,
        "contactos_emergencia": [
            {
                "nombre": _first_non_empty(row.get("nombre")),
                "celular": _first_non_empty(row.get("celular")),
                "prioridad": _first_non_empty(row.get("prioridad")),
            }
            for row in emergency_rows
        ],
        "indicaciones_especiales": [
            {
                "fecha": row.get("fecha").isoformat(sep=" ", timespec="seconds")
                if isinstance(row.get("fecha"), datetime)
                else _first_non_empty(row.get("fecha")),
                "mensaje": _first_non_empty(row.get("mensaje")),
            }
            for row in indicaciones_rows
        ],
        "noticias": [_serialize_noticia(row) for row in noticias_rows],
        "personas_autorizadas": [
            {
                "nombre": _first_non_empty(row.get("nombre")),
                "rut": _first_non_empty(row.get("rut")),
                "celular": _first_non_empty(row.get("celular")),
                "email": _first_non_empty(row.get("email")),
                "clave_verde": _first_non_empty(row.get("clave_verde")),
                "clave_roja": _first_non_empty(row.get("clave_roja")),
            }
            for row in personas_autorizadas_rows
        ],
    }


@router.get("/api/bitacora/empresas-sucursales")
def bitacora_empresas_sucursales_api(
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)

    rows = incidencias_db.execute(
        text(
            """
            SELECT
                DISTINCT TRIM(nombre_empresa) AS nombre_empresa,
                TRIM(nombre_sucursal) AS nombre_sucursal
            FROM bbdd_sucursales
            WHERE COALESCE(TRIM(nombre_empresa), '') <> ''
            ORDER BY nombre_empresa ASC, nombre_sucursal ASC
            """
        )
    ).mappings().all()

    empresas_dict = {}
    for row in rows:
        empresa = str(row.get("nombre_empresa") or "").strip()
        sucursal = str(row.get("nombre_sucursal") or "").strip()
        if empresa:
            if empresa not in empresas_dict:
                empresas_dict[empresa] = []
            if sucursal:
                empresas_dict[empresa].append(sucursal)

    empresas_list = [
        {
            "empresa": empresa,
            "sucursales": sorted(list(set(sucursales)))
        }
        for empresa, sucursales in sorted(empresas_dict.items())
    ]
    return {"empresas": empresas_list}


@router.get("/api/bitacora/personas-autorizadas")
def bitacora_personas_autorizadas_api(
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    """Devuelve todas las personas autorizadas con datos de sucursal y plan."""
    _require_bitacora_access(current_user)
    try:
        rows = incidencias_db.execute(
            text("""
                SELECT
                    s.id AS sucursal_id,
                    p.id AS persona_id,
                    s.nombre_empresa,
                    s.nombre_sucursal,
                    p.nombre,
                    p.rut,
                    p.telefono,
                    p.email,
                    (
                        SELECT v.tipo_plan
                        FROM venta_comercial v
                        WHERE LOWER(TRIM(v.rut_cliente)) = LOWER(TRIM(s.rut))
                          AND (
                            LOWER(TRIM(v.direccion_sucursal)) = LOWER(TRIM(s.direccion_sucursal))
                            OR LOWER(TRIM(v.nombre_sucursal)) = LOWER(TRIM(s.nombre_sucursal))
                          )
                        ORDER BY v.id DESC OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
                    ) AS tipo_plan
                FROM sucursal_personas_autorizadas p
                JOIN bbdd_sucursales s ON s.id = p.sucursal_id
                WHERE p.habilitado = 1
                ORDER BY p.rut, s.nombre_empresa, s.nombre_sucursal
            """)
        ).mappings().all()
    except Exception:
        rows = []
    return {
        "personas": [
            {
                "empresa": r.get("nombre_empresa") or "",
                "sucursal": r.get("nombre_sucursal") or "",
                "sucursalId": r.get("sucursal_id"),
                "personaId": r.get("persona_id"),
                "horario": _first_non_empty(r.get("tipo_plan")),
                "nombre": r.get("nombre") or "",
                "rut": r.get("rut") or "",
                "celular": r.get("telefono") or "",
                "email": r.get("email") or "",
            }
            for r in rows
        ]
    }


@router.post("/api/bitacora/noticias")
def crear_bitacora_noticia(
    payload: NoticiaCreate,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    _ensure_bitacora_noticias_table(incidencias_db)

    nombre_empresa = payload.nombre_empresa.strip()
    nombre_sucursal = payload.nombre_sucursal.strip()
    mensaje = payload.mensaje.strip()
    usuario_registra = str(current_user.name or current_user.username or "").strip()
    fecha_fin = datetime.combine(payload.fecha_fin_noticia, time(23, 59, 59))

    if not nombre_empresa or not nombre_sucursal or not mensaje:
        raise HTTPException(status_code=400, detail="Debes completar todos los campos de la noticia.")

    row = incidencias_db.execute(
        text(
            """
            INSERT INTO bitacora_noticias (
                nombre_empresa,
                nombre_sucursal,
                usuario_registra,
                fecha_fin_noticia,
                mensaje
            )
            OUTPUT
                INSERTED.id,
                INSERTED.nombre_empresa,
                INSERTED.nombre_sucursal,
                INSERTED.usuario_registra,
                INSERTED.fecha_registro,
                INSERTED.fecha_fin_noticia,
                INSERTED.mensaje
            VALUES (
                :nombre_empresa,
                :nombre_sucursal,
                :usuario_registra,
                :fecha_fin_noticia,
                :mensaje
            )
            """
        ),
        {
            "nombre_empresa": nombre_empresa,
            "nombre_sucursal": nombre_sucursal,
            "usuario_registra": usuario_registra,
            "fecha_fin_noticia": fecha_fin,
            "mensaje": mensaje,
        },
    ).mappings().first()
    incidencias_db.commit()

    return {"ok": True, "noticia": _serialize_noticia(row)}


# ── OBSERVACIONES ────────────────────────────────────────────────────────────

class ObservacionCreate(BaseModel):
    nombre_empresa: str = Field(min_length=1)
    nombre_sucursal: str = Field(min_length=1)
    observacion: str = ""
    enviar_cliente: bool = False
    tipo_clave: str = "Registro Observacion"
    detalle_custom: str = ""


class ClaveResolverRequest(BaseModel):
    persona_id: int
    sucursal_id: int
    clave: str = ""


class ClaveRegistrarRequest(ClaveResolverRequest):
    tipo_clave: str = ""
    observacion: str = ""


def _ensure_bitacora_registros_table(db: Session) -> None:
    db.execute(
        text(
            """
            IF OBJECT_ID('bitacora_registros', 'U') IS NULL
            BEGIN
            CREATE TABLE bitacora_registros (
                id BIGINT IDENTITY(1,1) PRIMARY KEY,
                nombre_empresa NVARCHAR(MAX) NOT NULL,
                nombre_sucursal NVARCHAR(MAX) NOT NULL,
                operador NVARCHAR(MAX) NOT NULL,
                detalle NVARCHAR(MAX) NOT NULL,
                observacion NVARCHAR(MAX) NOT NULL,
                tipo_clave NVARCHAR(MAX) NOT NULL DEFAULT 'Registro Observacion',
                created_at DATETIME2 NOT NULL DEFAULT GETDATE()
            )
            END
            """
        )
    )
    db.commit()


def _serialize_registro(row: dict) -> dict:
    ts = row.get("created_at")
    return {
        "id": row.get("id"),
        "nombre_empresa": str(row.get("nombre_empresa") or "").strip(),
        "nombre_sucursal": str(row.get("nombre_sucursal") or "").strip(),
        "operador": str(row.get("operador") or "").strip(),
        "detalle": str(row.get("detalle") or "").strip(),
        "observacion": str(row.get("observacion") or "").strip(),
        "tipo_clave": str(row.get("tipo_clave") or "").strip(),
        "created_at": ts.isoformat(sep=" ", timespec="seconds") if isinstance(ts, datetime) else str(ts or ""),
    }


def _resolver_clave_persona(
    db: Session,
    *,
    persona_id: int,
    sucursal_id: int,
    clave: str,
) -> tuple[SucursalPersonaAutorizada, SucursalBBDD, str | None, bool]:
    persona = (
        db.query(SucursalPersonaAutorizada)
        .filter(
            SucursalPersonaAutorizada.id == persona_id,
            SucursalPersonaAutorizada.sucursal_id == sucursal_id,
            SucursalPersonaAutorizada.habilitado == True,  # noqa: E712
        )
        .first()
    )
    sucursal = db.get(SucursalBBDD, sucursal_id)
    if not persona or not sucursal:
        raise HTTPException(status_code=404, detail="Persona o sucursal no encontrada.")

    clave_ingresada = str(clave or "").strip()
    clave_verde = str(persona.clave_verde or "").strip()
    clave_roja = str(persona.clave_roja or "").strip()
    tiene_claves = bool(clave_verde or clave_roja)

    # Mensaje unico para todos los casos de clave invalida: si el detalle
    # distinguiera "no tiene claves registradas" de "clave incorrecta",
    # cualquiera podria usar este modal para averiguar quien tiene o no
    # clave configurada solo probando. No revelar esa informacion.
    CLAVE_INVALIDA = "Clave incorrecta."

    if not tiene_claves:
        if clave_ingresada:
            raise HTTPException(status_code=400, detail=CLAVE_INVALIDA)
        return persona, sucursal, None, True

    if not clave_ingresada:
        raise HTTPException(status_code=400, detail=CLAVE_INVALIDA)

    # Comparacion insensible a mayusculas/minusculas y tildes: al guardia le
    # puede llegar la clave hablada o escrita a mano, no debe fallar por eso.
    clave_ingresada_norm = _normalize(clave_ingresada)
    coincidencias = []
    if clave_verde and hmac.compare_digest(clave_ingresada_norm, _normalize(clave_verde)):
        coincidencias.append("Verde")
    if clave_roja and hmac.compare_digest(clave_ingresada_norm, _normalize(clave_roja)):
        coincidencias.append("Roja")
    if not coincidencias:
        raise HTTPException(status_code=400, detail=CLAVE_INVALIDA)
    if len(coincidencias) > 1:
        return persona, sucursal, None, True
    return persona, sucursal, coincidencias[0], False


@router.post("/api/bitacora/clave/resolver")
def resolver_clave_bitacora(
    payload: ClaveResolverRequest,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    _persona, _sucursal, tipo, requiere_tipo = _resolver_clave_persona(
        incidencias_db,
        persona_id=payload.persona_id,
        sucursal_id=payload.sucursal_id,
        clave=payload.clave,
    )
    return {"ok": True, "tipo": tipo, "requiere_tipo": requiere_tipo}


@router.post("/api/bitacora/clave/registrar")
def registrar_clave_bitacora(
    payload: ClaveRegistrarRequest,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    _ensure_bitacora_registros_table(incidencias_db)
    persona, sucursal, tipo_resuelto, requiere_tipo = _resolver_clave_persona(
        incidencias_db,
        persona_id=payload.persona_id,
        sucursal_id=payload.sucursal_id,
        clave=payload.clave,
    )

    tipo_solicitado = str(payload.tipo_clave or "").strip().casefold()
    if requiere_tipo:
        if tipo_solicitado not in {"verde", "roja"}:
            raise HTTPException(status_code=400, detail="Debes indicar si la clave es Verde o Roja.")
        tipo = "Verde" if tipo_solicitado == "verde" else "Roja"
    else:
        tipo = str(tipo_resuelto)
        if tipo_solicitado and tipo_solicitado != tipo.casefold():
            raise HTTPException(status_code=400, detail="El tipo no corresponde a la clave ingresada.")

    observacion = str(payload.observacion or "").strip()
    if tipo == "Roja" and not observacion:
        raise HTTPException(status_code=400, detail="La observación es obligatoria para una Clave Roja.")

    operador = str(current_user.name or current_user.username or "").strip()
    empresa = str(sucursal.nombre_empresa or "").strip()
    nombre_sucursal = str(sucursal.nombre_sucursal or "").strip()
    detalle = f"Clave {tipo} - {str(persona.nombre or '').strip()}".strip(" -")
    row = incidencias_db.execute(
        text(
            """
            INSERT INTO bitacora_registros
                (nombre_empresa, nombre_sucursal, operador, detalle, observacion, tipo_clave)
            OUTPUT
                INSERTED.id, INSERTED.nombre_empresa, INSERTED.nombre_sucursal,
                INSERTED.operador, INSERTED.detalle, INSERTED.observacion,
                INSERTED.tipo_clave, INSERTED.created_at
            VALUES
                (:empresa, :sucursal, :operador, :detalle, :observacion, :tipo_clave)
            """
        ),
        {
            "empresa": empresa,
            "sucursal": nombre_sucursal,
            "operador": operador,
            "detalle": detalle,
            "observacion": observacion,
            "tipo_clave": tipo,
        },
    ).mappings().first()
    incidencias_db.commit()
    return {"ok": True, "tipo": tipo, "registro": _serialize_registro(row)}


@router.post("/api/bitacora/observacion")
def crear_observacion(
    payload: ObservacionCreate,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    _ensure_bitacora_registros_table(incidencias_db)

    empresa = payload.nombre_empresa.strip()
    sucursal = payload.nombre_sucursal.strip()
    observacion = payload.observacion.strip()
    operador = str(current_user.name or current_user.username or "").strip()
    tipo_clave = payload.tipo_clave.strip() or "Registro Observacion"
    detalle_custom = payload.detalle_custom.strip()
    es_clave_verde = tipo_clave.casefold() == "verde"

    if not empresa or not sucursal:
        raise HTTPException(status_code=400, detail="Debes completar todos los campos.")
    if not observacion and es_clave_verde:
        return {"ok": True, "registro": None, "skipped": True}
    if not observacion:
        raise HTTPException(status_code=400, detail="Debes completar todos los campos.")

    detalle = detalle_custom if detalle_custom else f"Observacion de la empresa {empresa} en la sucursal {sucursal}"

    row = incidencias_db.execute(
        text(
            """
            INSERT INTO bitacora_registros
                (nombre_empresa, nombre_sucursal, operador, detalle, observacion, tipo_clave)
            OUTPUT
                INSERTED.id, INSERTED.nombre_empresa, INSERTED.nombre_sucursal,
                INSERTED.operador, INSERTED.detalle, INSERTED.observacion,
                INSERTED.tipo_clave, INSERTED.created_at
            VALUES
                (:empresa, :sucursal, :operador, :detalle, :observacion, :tipo_clave)
            """
        ),
        {
            "empresa": empresa,
            "sucursal": sucursal,
            "operador": operador,
            "detalle": detalle,
            "observacion": observacion,
            "tipo_clave": tipo_clave,
        },
    ).mappings().first()
    incidencias_db.commit()

    return {"ok": True, "registro": _serialize_registro(row)}


@router.get("/api/bitacora/observaciones")
def listar_observaciones(
    empresa: str = Query(default=""),
    sucursal: str = Query(default=""),
    desde: str = Query(default=""),
    hasta: str = Query(default=""),
    fecha: str = Query(default=""),
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    _ensure_bitacora_registros_table(incidencias_db)

    conditions = ["1=1"]
    params: dict = {}

    if empresa.strip():
        conditions.append("LOWER(TRIM(nombre_empresa)) = LOWER(TRIM(:empresa))")
        params["empresa"] = empresa.strip()
    if sucursal.strip():
        conditions.append("LOWER(TRIM(nombre_sucursal)) = LOWER(TRIM(:sucursal))")
        params["sucursal"] = sucursal.strip()
    if fecha.strip():
        conditions.append("CAST(created_at AS DATE) = :fecha")
        params["fecha"] = fecha.strip()
    else:
        if desde.strip():
            conditions.append("created_at >= :desde")
            params["desde"] = desde.strip() + " 00:00:00"
        if hasta.strip():
            conditions.append("created_at <= :hasta")
            params["hasta"] = hasta.strip() + " 23:59:59"

    where = " AND ".join(conditions)
    rows = incidencias_db.execute(
        text(
            f"""
            SELECT id, nombre_empresa, nombre_sucursal, operador, detalle, observacion, tipo_clave, created_at
            FROM bitacora_registros
            WHERE {where}
            ORDER BY created_at DESC OFFSET 0 ROWS FETCH NEXT 500 ROWS ONLY
            """
        ),
        params,
    ).mappings().all()

    return {"total": len(rows), "registros": [_serialize_registro(r) for r in rows]}


def _parse_rango_fechas(desde: str, hasta: str) -> tuple[datetime | None, datetime | None]:
    """'YYYY-MM-DD' → (inicio, fin_exclusivo). Cualquiera puede venir vacío."""
    inicio = fin_excl = None
    try:
        if str(desde or "").strip():
            inicio = datetime.combine(date.fromisoformat(desde.strip()), time.min)
    except ValueError:
        raise HTTPException(status_code=422, detail="Fecha 'desde' inválida (usa AAAA-MM-DD).")
    try:
        if str(hasta or "").strip():
            fin_excl = datetime.combine(date.fromisoformat(hasta.strip()) + timedelta(days=1), time.min)
    except ValueError:
        raise HTTPException(status_code=422, detail="Fecha 'hasta' inválida (usa AAAA-MM-DD).")
    if inicio and fin_excl and fin_excl <= inicio:
        raise HTTPException(status_code=422, detail="'Hasta' no puede ser anterior a 'Desde'.")
    return inicio, fin_excl


def _fmt_fecha(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d-%m-%Y %H:%M")
    return ""


def _conteo_ordenado(valores: list[str], tope: int | None = None) -> list[list]:
    """Cuenta ocurrencias y devuelve [["valor", n], ...] ordenado desc.
    Si hay tope, agrupa el resto como 'Otros'."""
    conteo: dict[str, int] = {}
    for v in valores:
        clave = str(v or "").strip() or "Sin clasificar"
        conteo[clave] = conteo.get(clave, 0) + 1
    pares = sorted(conteo.items(), key=lambda kv: (-kv[1], kv[0]))
    if tope and len(pares) > tope:
        visibles, resto = pares[:tope], pares[tope:]
        visibles.append((f"Otros ({len(resto)} tipos)", sum(n for _, n in resto)))
        pares = visibles
    return [[k, n] for k, n in pares]


_ESTADOS_INCIDENCIA_CERRADA = ("terminado", "repetida")
_MAX_DIAS_ACTIVIDAD = 30


def _agrupar_incidencias_por_dia(inc_detalle: list[dict]) -> list[dict]:
    """Une el detalle de incidencias por día calendario (más reciente primero,
    ya que inc_detalle viene ordenado DESC por fecha_registro), con cuántas
    quedaron pendientes/resueltas ese día y el promedio de días que llevan
    pendientes o que tardaron en resolverse."""
    grupos: dict[str, dict] = {}
    orden: list[str] = []
    for r in inc_detalle:
        fecha_reg = r["fecha_registro"]
        if not isinstance(fecha_reg, datetime):
            continue
        clave = fecha_reg.strftime("%d-%m-%Y")
        if clave not in grupos:
            grupos[clave] = {"cantidad": 0, "pendientes": 0, "resueltas": 0, "dias_pendiente": [], "dias_resolucion": []}
            orden.append(clave)
        g = grupos[clave]
        g["cantidad"] += 1
        if r["dias_abierta"] is not None:
            g["pendientes"] += 1
            g["dias_pendiente"].append(r["dias_abierta"])
        if r["dias_resolucion"] is not None:
            g["resueltas"] += 1
            g["dias_resolucion"].append(r["dias_resolucion"])
    resultado = []
    for clave in orden:
        g = grupos[clave]
        resultado.append({
            "fecha": clave,
            "cantidad": g["cantidad"],
            "pendientes": g["pendientes"],
            "resueltas": g["resueltas"],
            "dias_pendiente_prom": round(sum(g["dias_pendiente"]) / len(g["dias_pendiente"]), 1) if g["dias_pendiente"] else None,
            "dias_resolucion_prom": round(sum(g["dias_resolucion"]) / len(g["dias_resolucion"]), 1) if g["dias_resolucion"] else None,
        })
    return resultado


def _agrupar_protocolos_por_dia(proto_detalle: list[dict]) -> list[dict]:
    """Une el detalle de protocolos por día calendario (más reciente primero),
    con el desglose de cuántos hubo de cada tipo (Preventivo/Intrusivo/etc)."""
    grupos: dict[str, dict] = {}
    orden: list[str] = []
    for r in proto_detalle:
        fecha_reg = r["fecha_registro"]
        if not isinstance(fecha_reg, datetime):
            continue
        clave = fecha_reg.strftime("%d-%m-%Y")
        if clave not in grupos:
            grupos[clave] = {"cantidad": 0, "por_tipo": {}}
            orden.append(clave)
        g = grupos[clave]
        g["cantidad"] += 1
        tipo = r["tipo"] or "Sin clasificar"
        g["por_tipo"][tipo] = g["por_tipo"].get(tipo, 0) + 1
    resultado = []
    for clave in orden:
        g = grupos[clave]
        por_tipo = sorted(g["por_tipo"].items(), key=lambda kv: (-kv[1], kv[0]))
        resultado.append({
            "fecha": clave,
            "cantidad": g["cantidad"],
            "por_tipo": [[k, n] for k, n in por_tipo],
        })
    return resultado


def _informacion_cliente_data(
    incidencias_db: Session,
    empresa: str,
    sucursal: str,
    desde: str,
    hasta: str,
) -> dict:
    """Arma el detalle completo (Incidencias / Protocolos / Bitácora) para una
    empresa+sucursal, opcionalmente acotado a un rango de fechas. Compartido
    entre el endpoint JSON del panel y el informe Excel descargable. Todo el
    cruce entre módulos es por nombre de texto — no hay FK entre
    bbdd_sucursales e incidencias/protocolos."""
    empresa_limpia = str(empresa or "").strip()
    sucursal_limpia = str(sucursal or "").strip()
    if not sucursal_limpia:
        raise HTTPException(status_code=400, detail="Debes indicar una sucursal.")

    inicio, fin_excl = _parse_rango_fechas(desde, hasta)
    ahora = datetime.now()

    def _filtro_fecha(columna: str) -> tuple[str, dict]:
        sql, params = "", {}
        if inicio:
            sql += f" AND {columna} >= :rango_inicio"
            params["rango_inicio"] = inicio
        if fin_excl:
            sql += f" AND {columna} < :rango_fin"
            params["rango_fin"] = fin_excl
        return sql, params

    # ── Incidencias: Registro.cliente guarda el nombre de la SUCURSAL (esa
    #    tabla no tiene columna empresa, no se puede filtrar por empresa) ──
    filtro_inc, params_inc = _filtro_fecha("fecha_registro")
    inc_rows = incidencias_db.execute(
        text(
            "SELECT odt, fecha_registro, problema, derivacion, estado, fecha_cierre, tecnicos "
            "FROM incidencias "
            "WHERE LOWER(TRIM(cliente)) = LOWER(TRIM(:sucursal))" + filtro_inc +
            " ORDER BY fecha_registro DESC"
        ),
        {"sucursal": sucursal_limpia, **params_inc},
    ).mappings().all()

    inc_detalle: list[dict] = []
    pendientes_incidencias = 0
    dias_pend_acum = dias_pend_n = 0
    dias_cierre_acum = dias_cierre_n = 0
    for row in inc_rows:
        estado_norm = str(row.get("estado") or "").strip().lower()
        cerrada = estado_norm in _ESTADOS_INCIDENCIA_CERRADA
        fecha_reg = row.get("fecha_registro")
        fecha_cie = row.get("fecha_cierre")
        dias_abierta = None
        dias_resolucion = None
        if isinstance(fecha_reg, datetime):
            if cerrada and isinstance(fecha_cie, datetime):
                dias_resolucion = max((fecha_cie - fecha_reg).days, 0)
                dias_cierre_acum += dias_resolucion
                dias_cierre_n += 1
            elif not cerrada:
                dias_abierta = max((ahora - fecha_reg).days, 0)
                dias_pend_acum += dias_abierta
                dias_pend_n += 1
        if not cerrada:
            pendientes_incidencias += 1
        inc_detalle.append({
            "odt": str(row.get("odt") or ""),
            "fecha_registro": fecha_reg,
            "tipo": str(row.get("problema") or "").strip() or "Sin clasificar",
            "derivacion": str(row.get("derivacion") or "").strip(),
            "estado": str(row.get("estado") or "").strip(),
            "fecha_cierre": fecha_cie,
            "tecnicos": str(row.get("tecnicos") or "").strip(),
            "dias_resolucion": dias_resolucion,
            "dias_abierta": dias_abierta,
        })

    inc_fechas = [r["fecha_registro"] for r in inc_detalle if isinstance(r["fecha_registro"], datetime)]

    # ── Protocolos: activaciones con tipo (Preventivo/Intrusivo) y éxito ──
    filtro_proto, params_proto = _filtro_fecha("fecha_registro")
    proto_rows = incidencias_db.execute(
        text(
            "SELECT fecha_registro, tipo_protocolo, protocolo_exitoso, detectado, efectivo, "
            "sirena, voz, carabineros, alpha3, informado, puesto, operador "
            "FROM protocolos_registro "
            "WHERE LOWER(TRIM(cliente)) = LOWER(TRIM(:cliente)) AND LOWER(TRIM(sucursal)) = LOWER(TRIM(:sucursal))"
            + filtro_proto + " ORDER BY fecha_registro DESC"
        ),
        {"cliente": empresa_limpia, "sucursal": sucursal_limpia, **params_proto},
    ).mappings().all()

    proto_detalle: list[dict] = []
    proto_exitosos = proto_intrusivos = proto_preventivos = 0
    for row in proto_rows:
        tipo_norm = str(row.get("tipo_protocolo") or "").strip().lower()
        exito_norm = str(row.get("protocolo_exitoso") or "").strip().upper()
        if exito_norm == "SI":
            proto_exitosos += 1
        if tipo_norm == "intrusivo":
            proto_intrusivos += 1
        elif tipo_norm == "preventivo":
            proto_preventivos += 1
        proto_detalle.append({
            "fecha_registro": row.get("fecha_registro"),
            "tipo": str(row.get("tipo_protocolo") or "").strip() or "Sin clasificar",
            "exitoso": exito_norm or "-",
            "detectado": str(row.get("detectado") or "").strip(),
            "efectivo": str(row.get("efectivo") or "").strip(),
            "sirena": str(row.get("sirena") or "").strip(),
            "voz": str(row.get("voz") or "").strip(),
            "carabineros": str(row.get("carabineros") or "").strip(),
            "alpha3": str(row.get("alpha3") or "").strip(),
            "informado": str(row.get("informado") or "").strip(),
            "puesto": str(row.get("puesto") or "").strip(),
            "operador": str(row.get("operador") or "").strip(),
        })
    proto_fechas = [r["fecha_registro"] for r in proto_detalle if isinstance(r["fecha_registro"], datetime)]

    #    Informes de protocolo (PENDIENTE|OK|ERROR) — sin filtro de fecha: son
    #    derivados de las activaciones y su pendiente importa como estado global.
    proto_informes = incidencias_db.execute(
        text(
            "SELECT estado FROM protocolos_informes "
            "WHERE LOWER(TRIM(cliente)) = LOWER(TRIM(:cliente)) AND LOWER(TRIM(sucursal)) = LOWER(TRIM(:sucursal))"
        ),
        {"cliente": empresa_limpia, "sucursal": sucursal_limpia},
    ).mappings().all()
    total_informes = len(proto_informes)
    informes_pendientes = sum(1 for r in proto_informes if str(r.get("estado") or "").strip().upper() != "OK")

    # ── Bitácora ──
    _ensure_bitacora_registros_table(incidencias_db)
    filtro_bit, params_bit = _filtro_fecha("created_at")
    bit_rows = incidencias_db.execute(
        text(
            "SELECT created_at, tipo_clave, operador, detalle, observacion "
            "FROM bitacora_registros "
            "WHERE LOWER(TRIM(nombre_empresa)) = LOWER(TRIM(:empresa)) AND LOWER(TRIM(nombre_sucursal)) = LOWER(TRIM(:sucursal))"
            + filtro_bit + " ORDER BY created_at DESC"
        ),
        {"empresa": empresa_limpia, "sucursal": sucursal_limpia, **params_bit},
    ).mappings().all()

    hace_30_dias = ahora - timedelta(days=30)
    bit_detalle = [
        {
            "fecha": row.get("created_at"),
            "tipo": str(row.get("tipo_clave") or "").strip() or "Sin clasificar",
            "operador": str(row.get("operador") or "").strip(),
            "detalle": str(row.get("detalle") or "").strip(),
            "observacion": str(row.get("observacion") or "").strip(),
        }
        for row in bit_rows
    ]
    bit_fechas = [r["fecha"] for r in bit_detalle if isinstance(r["fecha"], datetime)]
    bitacora_30d = sum(1 for f in bit_fechas if f >= hace_30_dias)

    return {
        "empresa": empresa_limpia,
        "sucursal": sucursal_limpia,
        "desde": desde.strip() if desde else "",
        "hasta": hasta.strip() if hasta else "",
        "incidencias": {
            "total": len(inc_detalle),
            "pendientes": pendientes_incidencias,
            "cerradas": len(inc_detalle) - pendientes_incidencias,
            "antiguedad_promedio_dias": round(dias_pend_acum / dias_pend_n, 1) if dias_pend_n else 0,
            "resolucion_promedio_dias": round(dias_cierre_acum / dias_cierre_n, 1) if dias_cierre_n else 0,
            "por_tipo": _conteo_ordenado([r["tipo"] for r in inc_detalle], tope=10),
            "por_estado": _conteo_ordenado([r["estado"] or "Sin estado" for r in inc_detalle]),
            "primera_fecha": _fmt_fecha(min(inc_fechas)) if inc_fechas else "",
            "ultima_fecha": _fmt_fecha(max(inc_fechas)) if inc_fechas else "",
            "por_dia": _agrupar_incidencias_por_dia(inc_detalle)[:_MAX_DIAS_ACTIVIDAD],
            "por_dia_truncado": len(_agrupar_incidencias_por_dia(inc_detalle)) > _MAX_DIAS_ACTIVIDAD,
            "_detalle": inc_detalle,
        },
        "protocolos": {
            "total_registros": len(proto_detalle),
            "exitosos": proto_exitosos,
            "no_exitosos": len(proto_detalle) - proto_exitosos,
            "intrusivos": proto_intrusivos,
            "preventivos": proto_preventivos,
            "total_informes": total_informes,
            "informes_pendientes": informes_pendientes,
            "primera_fecha": _fmt_fecha(min(proto_fechas)) if proto_fechas else "",
            "ultima_fecha": _fmt_fecha(max(proto_fechas)) if proto_fechas else "",
            "por_dia": _agrupar_protocolos_por_dia(proto_detalle)[:_MAX_DIAS_ACTIVIDAD],
            "por_dia_truncado": len(_agrupar_protocolos_por_dia(proto_detalle)) > _MAX_DIAS_ACTIVIDAD,
            "_detalle": proto_detalle,
        },
        "bitacora": {
            "total": len(bit_detalle),
            "ultimos_30_dias": bitacora_30d,
            "por_tipo": _conteo_ordenado([r["tipo"] for r in bit_detalle], tope=10),
            "primera_fecha": _fmt_fecha(min(bit_fechas)) if bit_fechas else "",
            "ultima_fecha": _fmt_fecha(max(bit_fechas)) if bit_fechas else "",
            "_detalle": bit_detalle,
        },
    }


@router.get("/api/bitacora/informacion-cliente")
def informacion_cliente(
    empresa: str = Query(default=""),
    sucursal: str = Query(default=""),
    desde: str = Query(default=""),
    hasta: str = Query(default=""),
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    """Resumen gerencial con desgloses (Incidencias / Protocolos / Bitácora)
    para una empresa+sucursal, con rango de fechas opcional."""
    _require_bitacora_access(current_user)
    data = _informacion_cliente_data(incidencias_db, empresa, sucursal, desde, hasta)
    # Las filas una-a-una solo van en el informe Excel, no en el JSON del panel.
    for seccion in ("incidencias", "protocolos", "bitacora"):
        data[seccion].pop("_detalle", None)
    return data


@router.get("/api/bitacora/informacion-cliente/informe")
def informacion_cliente_informe(
    empresa: str = Query(default=""),
    sucursal: str = Query(default=""),
    desde: str = Query(default=""),
    hasta: str = Query(default=""),
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    """Informe Excel descargable por cliente: hoja de resumen + una hoja de
    detalle fila a fila por cada módulo (Incidencias / Protocolos / Bitácora)."""
    _require_bitacora_access(current_user)
    data = _informacion_cliente_data(incidencias_db, empresa, sucursal, desde, hasta)

    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    azul = "0B1424"
    azul_medio = "1E3A5F"
    naranja = "DE7B36"
    borde = Side(style="thin", color="CBD5E1")
    borde_full = Border(top=borde, left=borde, right=borde, bottom=borde)

    rango_txt = "Todo el historial"
    if data["desde"] or data["hasta"]:
        rango_txt = f"Desde {data['desde'] or 'el inicio'} hasta {data['hasta'] or 'hoy'}"

    def _titulo_hoja(ws, titulo: str, n_cols: int) -> None:
        ws.append([titulo])
        ws.append([f"{data['empresa']} — {data['sucursal']}  |  {rango_txt}  |  Generado: {datetime.now().strftime('%d-%m-%Y %H:%M')}"])
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=n_cols)
        ws["A1"].font = Font(bold=True, color="FFFFFF", size=14)
        ws["A1"].fill = PatternFill("solid", fgColor=azul)
        ws["A1"].alignment = Alignment(horizontal="center")
        ws["A2"].font = Font(bold=True, size=10, color="334155")
        ws["A2"].alignment = Alignment(horizontal="center")

    def _encabezados(ws, headers: list[str]) -> None:
        ws.append(headers)
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True, color="FFFFFF", size=10)
            cell.fill = PatternFill("solid", fgColor=azul_medio)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = borde_full

    def _autoancho(ws, anchos: list[int]) -> None:
        for idx, ancho in enumerate(anchos, start=1):
            ws.column_dimensions[get_column_letter(idx)].width = ancho

    def _bordear_datos(ws, desde_fila: int, n_cols: int) -> None:
        for fila in ws.iter_rows(min_row=desde_fila, max_row=ws.max_row, min_col=1, max_col=n_cols):
            for cell in fila:
                cell.border = borde_full
                if cell.font is None or not cell.font.b:
                    cell.font = Font(size=10)

    wb = Workbook()

    # ── Hoja Resumen: cada sección es una tabla horizontal — las variables
    #    van como COLUMNAS (encabezado) y los valores en una sola fila ──
    ws = wb.active
    ws.title = "Resumen"
    _titulo_hoja(ws, "ATC — Informe de Cliente", 9)
    inc, pro, bit = data["incidencias"], data["protocolos"], data["bitacora"]

    def _seccion_horizontal(titulo: str, headers: list[str], valores: list) -> None:
        ws.append([])
        ws.append([titulo])
        celda = ws.cell(row=ws.max_row, column=1)
        celda.font = Font(bold=True, color="FFFFFF", size=11)
        celda.fill = PatternFill("solid", fgColor=naranja)
        ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=len(headers))
        _encabezados(ws, headers)
        ws.append(valores)
        for cell in ws[ws.max_row]:
            cell.border = borde_full
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.font = Font(size=10)

    _seccion_horizontal(
        "INCIDENCIAS",
        ["Total", "Pendientes", "Cerradas", "Antigüedad prom. (días)", "Resolución prom. (días)",
         "Primera actividad", "Última actividad"],
        [inc["total"], inc["pendientes"], inc["cerradas"], inc["antiguedad_promedio_dias"],
         inc["resolucion_promedio_dias"], inc["primera_fecha"] or "-", inc["ultima_fecha"] or "-"],
    )
    _seccion_horizontal(
        "PROTOCOLOS",
        ["Activaciones", "Exitosos", "No exitosos", "Intrusivos", "Preventivos",
         "Informes", "Informes pendientes", "Primera actividad", "Última actividad"],
        [pro["total_registros"], pro["exitosos"], pro["no_exitosos"], pro["intrusivos"], pro["preventivos"],
         pro["total_informes"], pro["informes_pendientes"], pro["primera_fecha"] or "-", pro["ultima_fecha"] or "-"],
    )
    _seccion_horizontal(
        "BITÁCORA",
        ["Movimientos totales", "Últimos 30 días", "Primera actividad", "Última actividad"],
        [bit["total"], bit["ultimos_30_dias"], bit["primera_fecha"] or "-", bit["ultima_fecha"] or "-"],
    )

    # Desgloses por tipo — también con los tipos como columnas
    if inc["por_tipo"]:
        _seccion_horizontal("INCIDENCIAS POR TIPO",
                            [str(n) for n, _ in inc["por_tipo"]],
                            [c for _, c in inc["por_tipo"]])
    if inc["por_estado"]:
        _seccion_horizontal("INCIDENCIAS POR ESTADO",
                            [str(n) for n, _ in inc["por_estado"]],
                            [c for _, c in inc["por_estado"]])
    if bit["por_tipo"]:
        _seccion_horizontal("BITÁCORA POR TIPO DE MOVIMIENTO",
                            [str(n) for n, _ in bit["por_tipo"]],
                            [c for _, c in bit["por_tipo"]])
    _autoancho(ws, [18] * 12)

    # ── Hoja Incidencias ──
    ws = wb.create_sheet("Incidencias")
    headers_inc = ["ODT", "Fecha registro", "Tipo (problema)", "Derivación", "Estado",
                   "Fecha cierre", "Días resolución", "Días abierta (si pendiente)", "Técnicos"]
    _titulo_hoja(ws, "Detalle de Incidencias", len(headers_inc))
    _encabezados(ws, headers_inc)
    fila_datos = ws.max_row + 1
    for r in inc["_detalle"]:
        ws.append([
            r["odt"], _fmt_fecha(r["fecha_registro"]), r["tipo"], r["derivacion"], r["estado"],
            _fmt_fecha(r["fecha_cierre"]),
            r["dias_resolucion"] if r["dias_resolucion"] is not None else "",
            r["dias_abierta"] if r["dias_abierta"] is not None else "",
            r["tecnicos"],
        ])
    _bordear_datos(ws, fila_datos, len(headers_inc))
    _autoancho(ws, [12, 17, 30, 18, 14, 17, 13, 13, 24])
    ws.freeze_panes = "A4"

    # ── Hoja Protocolos ──
    ws = wb.create_sheet("Protocolos")
    headers_pro = ["Fecha", "Tipo", "Exitoso", "Detectado", "Efectivo", "Sirena", "Voz",
                   "Carabineros", "Alpha3", "Informado", "Puesto", "Operador"]
    _titulo_hoja(ws, "Detalle de Protocolos", len(headers_pro))
    _encabezados(ws, headers_pro)
    fila_datos = ws.max_row + 1
    for r in pro["_detalle"]:
        ws.append([
            _fmt_fecha(r["fecha_registro"]), r["tipo"], r["exitoso"], r["detectado"], r["efectivo"],
            r["sirena"], r["voz"], r["carabineros"], r["alpha3"], r["informado"], r["puesto"], r["operador"],
        ])
    _bordear_datos(ws, fila_datos, len(headers_pro))
    _autoancho(ws, [17, 13, 10, 10, 10, 9, 9, 12, 9, 11, 12, 22])
    ws.freeze_panes = "A4"

    # ── Hoja Bitácora ──
    ws = wb.create_sheet("Bitácora")
    headers_bit = ["Fecha", "Tipo de movimiento", "Operador", "Detalle", "Observación"]
    _titulo_hoja(ws, "Detalle de Bitácora", len(headers_bit))
    _encabezados(ws, headers_bit)
    fila_datos = ws.max_row + 1
    for r in bit["_detalle"]:
        ws.append([_fmt_fecha(r["fecha"]), r["tipo"], r["operador"], r["detalle"], r["observacion"]])
    _bordear_datos(ws, fila_datos, len(headers_bit))
    _autoancho(ws, [17, 24, 20, 45, 45])
    ws.freeze_panes = "A4"

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    nombre_ascii = re.sub(r"[^A-Za-z0-9_-]+", "_", f"Informe_{data['empresa']}_{data['sucursal']}")[:80]
    filename = f"{nombre_ascii}_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/bitacora/informacion-cliente/informe-pdf")
def informacion_cliente_informe_pdf(
    empresa: str = Query(default=""),
    sucursal: str = Query(default=""),
    desde: str = Query(default=""),
    hasta: str = Query(default=""),
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    """Informe PDF gerencial: estado completo del cliente (Incidencias /
    Protocolos / Bitácora) con estilo corporativo, para presentar a gerencia."""
    _require_bitacora_access(current_user)
    data = _informacion_cliente_data(incidencias_db, empresa, sucursal, desde, hasta)

    from io import BytesIO

    from ATC.app.services.informe_cliente_service import generar_informe_cliente_pdf

    pdf_bytes = generar_informe_cliente_pdf(data)
    nombre_ascii = re.sub(r"[^A-Za-z0-9_-]+", "_", f"Informe_Gerencial_{data['empresa']}_{data['sucursal']}")[:80]
    filename = f"{nombre_ascii}_{datetime.now().strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _ensure_sucursal_info_extra(db: Session) -> None:
    db.execute(text("""
        IF OBJECT_ID('sucursal_info_extra', 'U') IS NULL
        BEGIN
        CREATE TABLE sucursal_info_extra (
            id BIGINT IDENTITY(1,1) PRIMARY KEY,
            sucursal_id BIGINT NOT NULL UNIQUE,
            referencia_ubicacion NVARCHAR(MAX),
            contacto NVARCHAR(MAX),
            correo NVARCHAR(MAX),
            horario_habil NVARCHAR(MAX),
            horario_no_habil NVARCHAR(MAX),
            fecha_inicio NVARCHAR(MAX),
            tipo_vigilancia NVARCHAR(MAX),
            camaras_contratadas NVARCHAR(MAX),
            camaras_televigiladas NVARCHAR(MAX),
            plan_cuadrante NVARCHAR(MAX),
            carabineros NVARCHAR(MAX),
            bomberos NVARCHAR(MAX),
            seguridad_ciudadana NVARCHAR(MAX),
            codigo_p2p NVARCHAR(MAX),
            codigo_dss NVARCHAR(MAX),
            telefono_porton NVARCHAR(MAX),
            telefono_recepcion NVARCHAR(MAX),
            internet_atc NVARCHAR(MAX),
            compania_electricidad NVARCHAR(MAX),
            numero_cliente_electricidad NVARCHAR(MAX),
            proveedor_internet_cliente NVARCHAR(MAX)
        )
        END
    """))
    db.commit()


@router.get("/api/bitacora/sucursal-raw")
def bitacora_sucursal_raw(
    sucursal_id: int = Query(...),
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    base = incidencias_db.execute(
        text("""
            SELECT id, nombre_sucursal, direccion_sucursal, referencia_ubicacion,
                   email_facturas, horario_apertura, horario_cierre,
                   proveedor_electricidad, nro_proveedor_electricidad, proveedor_internet
            FROM bbdd_sucursales WHERE id = :sid
        """),
        {"sid": sucursal_id},
    ).mappings().first()
    if not base:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")

    extra = None
    try:
        extra = incidencias_db.execute(
            text("SELECT TOP 1 * FROM sucursal_info_extra WHERE sucursal_id = :sid"),
            {"sid": sucursal_id},
        ).mappings().first()
    except Exception:
        extra = None

    def sv(row, key):
        if not row:
            return ""
        return str(row.get(key) or "").strip()

    return {
        "sucursal_id": sucursal_id,
        "nombre_sucursal": sv(base, "nombre_sucursal"),
        "direccion_sucursal": sv(base, "direccion_sucursal"),
        "referencia_ubicacion": sv(extra, "referencia_ubicacion") or sv(base, "referencia_ubicacion"),
        "email_facturas": sv(base, "email_facturas"),
        "horario_apertura": sv(base, "horario_apertura"),
        "horario_cierre": sv(base, "horario_cierre"),
        "horario_habil": sv(extra, "horario_habil"),
        "horario_no_habil": sv(extra, "horario_no_habil"),
        "plan_cuadrante": sv(extra, "plan_cuadrante"),
        "carabineros": sv(extra, "carabineros"),
        "bomberos": sv(extra, "bomberos"),
        "seguridad_ciudadana": sv(extra, "seguridad_ciudadana"),
        "camaras_contratadas": sv(extra, "camaras_contratadas"),
        "camaras_televigiladas": sv(extra, "camaras_televigiladas"),
        "codigo_p2p": sv(extra, "codigo_p2p"),
        "codigo_dss": sv(extra, "codigo_dss"),
        "telefono_porton": sv(extra, "telefono_porton"),
        "telefono_recepcion": sv(extra, "telefono_recepcion"),
        "internet_atc": sv(extra, "internet_atc"),
        "compania_electricidad": sv(extra, "compania_electricidad") or sv(base, "proveedor_electricidad"),
        "numero_cliente_electricidad": sv(extra, "numero_cliente_electricidad") or sv(base, "nro_proveedor_electricidad"),
        "proveedor_internet_cliente": sv(extra, "proveedor_internet_cliente") or sv(base, "proveedor_internet"),
    }


@router.post("/api/bitacora/sucursal-editar")
def bitacora_sucursal_editar(
    payload: SucursalEditPayload,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    sid = payload.sucursal_id

    base_row = incidencias_db.execute(
        text("SELECT id FROM bbdd_sucursales WHERE id = :sid"),
        {"sid": sid},
    ).mappings().first()
    if not base_row:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")

    incidencias_db.execute(
        text("""
            UPDATE bbdd_sucursales SET
                nombre_sucursal         = :nombre_sucursal,
                direccion_sucursal      = :direccion_sucursal,
                referencia_ubicacion    = :referencia_ubicacion,
                email_facturas          = :email_facturas,
                horario_apertura        = :horario_apertura,
                horario_cierre          = :horario_cierre,
                proveedor_electricidad  = :compania_electricidad,
                nro_proveedor_electricidad = :numero_cliente_electricidad,
                proveedor_internet      = :proveedor_internet_cliente
            WHERE id = :sid
        """),
        {
            "sid": sid,
            "nombre_sucursal": payload.nombre_sucursal.strip(),
            "direccion_sucursal": payload.direccion_sucursal.strip(),
            "referencia_ubicacion": payload.referencia_ubicacion.strip(),
            "email_facturas": payload.email_facturas.strip(),
            "horario_apertura": payload.horario_apertura.strip(),
            "horario_cierre": payload.horario_cierre.strip(),
            "compania_electricidad": payload.compania_electricidad.strip(),
            "numero_cliente_electricidad": payload.numero_cliente_electricidad.strip(),
            "proveedor_internet_cliente": payload.proveedor_internet_cliente.strip(),
        },
    )

    _ensure_sucursal_info_extra(incidencias_db)
    incidencias_db.execute(
        text("""
            MERGE sucursal_info_extra AS t
            USING (VALUES (:sid)) AS s(sucursal_id) ON t.sucursal_id = s.sucursal_id
            WHEN MATCHED THEN UPDATE SET
                referencia_ubicacion    = :referencia_ubicacion,
                horario_habil           = :horario_habil,
                horario_no_habil        = :horario_no_habil,
                plan_cuadrante          = :plan_cuadrante,
                carabineros             = :carabineros,
                bomberos                = :bomberos,
                seguridad_ciudadana     = :seguridad_ciudadana,
                camaras_contratadas     = :camaras_contratadas,
                camaras_televigiladas   = :camaras_televigiladas,
                codigo_p2p              = :codigo_p2p,
                codigo_dss              = :codigo_dss,
                telefono_porton         = :telefono_porton,
                telefono_recepcion      = :telefono_recepcion,
                internet_atc            = :internet_atc,
                compania_electricidad   = :compania_electricidad,
                numero_cliente_electricidad = :numero_cliente_electricidad,
                proveedor_internet_cliente  = :proveedor_internet_cliente
            WHEN NOT MATCHED THEN INSERT (
                sucursal_id, referencia_ubicacion, horario_habil, horario_no_habil,
                plan_cuadrante, carabineros, bomberos, seguridad_ciudadana,
                camaras_contratadas, camaras_televigiladas, codigo_p2p, codigo_dss,
                telefono_porton, telefono_recepcion, internet_atc,
                compania_electricidad, numero_cliente_electricidad, proveedor_internet_cliente
            ) VALUES (
                :sid, :referencia_ubicacion, :horario_habil, :horario_no_habil,
                :plan_cuadrante, :carabineros, :bomberos, :seguridad_ciudadana,
                :camaras_contratadas, :camaras_televigiladas, :codigo_p2p, :codigo_dss,
                :telefono_porton, :telefono_recepcion, :internet_atc,
                :compania_electricidad, :numero_cliente_electricidad, :proveedor_internet_cliente
            );
        """),
        {
            "sid": sid,
            "referencia_ubicacion": payload.referencia_ubicacion.strip(),
            "horario_habil": payload.horario_habil.strip(),
            "horario_no_habil": payload.horario_no_habil.strip(),
            "plan_cuadrante": payload.plan_cuadrante.strip(),
            "carabineros": payload.carabineros.strip(),
            "bomberos": payload.bomberos.strip(),
            "seguridad_ciudadana": payload.seguridad_ciudadana.strip(),
            "camaras_contratadas": payload.camaras_contratadas.strip(),
            "camaras_televigiladas": payload.camaras_televigiladas.strip(),
            "codigo_p2p": payload.codigo_p2p.strip(),
            "codigo_dss": payload.codigo_dss.strip(),
            "telefono_porton": payload.telefono_porton.strip(),
            "telefono_recepcion": payload.telefono_recepcion.strip(),
            "internet_atc": payload.internet_atc.strip(),
            "compania_electricidad": payload.compania_electricidad.strip(),
            "numero_cliente_electricidad": payload.numero_cliente_electricidad.strip(),
            "proveedor_internet_cliente": payload.proveedor_internet_cliente.strip(),
        },
    )
    incidencias_db.commit()
    return {"ok": True}


# ──────────────────────────────────────────────
# Personas autorizadas por sucursal
# ──────────────────────────────────────────────

class PersonaAutorizadaCreate(BaseModel):
    sucursal_id: int
    nombre: str = ""
    rut: str = ""
    telefono: str = ""
    email: str = ""
    clave_verde: str = ""
    clave_roja: str = ""


class PersonaAutorizadaUpdate(BaseModel):
    nombre: str = ""
    rut: str = ""
    telefono: str = ""
    email: str = ""
    clave_verde: str = ""
    clave_roja: str = ""


def _ensure_personas_autorizadas_habilitado(db: Session) -> None:
    """Agrega columna habilitado a sucursal_personas_autorizadas si no existe."""
    try:
        db.execute(text("""
            IF NOT EXISTS (
                SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = 'sucursal_personas_autorizadas'
                  AND COLUMN_NAME = 'habilitado'
            )
            BEGIN
                ALTER TABLE sucursal_personas_autorizadas
                ADD habilitado BIT NOT NULL DEFAULT 1
            END
        """))
        db.commit()
    except Exception:
        db.rollback()


@router.get("/api/bitacora/sucursal/{sucursal_id}/personas-autorizadas")
def api_list_personas_autorizadas(
    sucursal_id: int,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    _ensure_personas_autorizadas_habilitado(incidencias_db)
    rows = (
        incidencias_db.query(SucursalPersonaAutorizada)
        .filter(SucursalPersonaAutorizada.sucursal_id == sucursal_id)
        .order_by(SucursalPersonaAutorizada.nombre)
        .all()
    )
    return [
        {
            "id": r.id,
            "nombre": r.nombre or "",
            "rut": r.rut or "",
            "telefono": r.telefono or "",
            "email": r.email or "",
            "clave_verde": r.clave_verde or "",
            "clave_roja": r.clave_roja or "",
            "habilitado": bool(r.habilitado),
        }
        for r in rows
    ]


@router.post("/api/bitacora/personas-autorizadas")
def api_create_persona_autorizada(
    payload: PersonaAutorizadaCreate,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    _ensure_personas_autorizadas_habilitado(incidencias_db)
    sucursal = incidencias_db.get(SucursalBBDD, payload.sucursal_id)
    if not sucursal:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")
    nueva = SucursalPersonaAutorizada(
        sucursal_id=payload.sucursal_id,
        nombre=payload.nombre.strip() or None,
        rut=payload.rut.strip() or None,
        telefono=payload.telefono.strip() or None,
        email=payload.email.strip() or None,
        clave_verde=payload.clave_verde.strip() or None,
        clave_roja=payload.clave_roja.strip() or None,
        habilitado=True,
    )
    incidencias_db.add(nueva)
    incidencias_db.commit()
    incidencias_db.refresh(nueva)
    return {"ok": True, "id": nueva.id}


@router.put("/api/bitacora/personas-autorizadas/{persona_id}")
def api_update_persona_autorizada(
    persona_id: int,
    payload: PersonaAutorizadaUpdate,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    row = incidencias_db.get(SucursalPersonaAutorizada, persona_id)
    if not row:
        raise HTTPException(status_code=404, detail="Persona no encontrada.")
    row.nombre = payload.nombre.strip() or None
    row.rut = payload.rut.strip() or None
    row.telefono = payload.telefono.strip() or None
    row.email = payload.email.strip() or None
    row.clave_verde = payload.clave_verde.strip() or None
    row.clave_roja = payload.clave_roja.strip() or None
    incidencias_db.commit()
    return {"ok": True}


@router.patch("/api/bitacora/personas-autorizadas/{persona_id}/toggle")
def api_toggle_persona_autorizada(
    persona_id: int,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    row = incidencias_db.get(SucursalPersonaAutorizada, persona_id)
    if not row:
        raise HTTPException(status_code=404, detail="Persona no encontrada.")
    row.habilitado = not bool(row.habilitado)
    incidencias_db.commit()
    return {"ok": True, "habilitado": bool(row.habilitado)}


@router.delete("/api/bitacora/personas-autorizadas/{persona_id}")
def api_delete_persona_autorizada(
    persona_id: int,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    row = incidencias_db.get(SucursalPersonaAutorizada, persona_id)
    if not row:
        raise HTTPException(status_code=404, detail="Persona no encontrada.")
    incidencias_db.delete(row)
    incidencias_db.commit()
    return {"ok": True}


class _CreateUserBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1)
    email: str | None = Field(default=None, max_length=255)
    role: str = Field(..., pattern=r"^(admin|agent)$")


# role -> departamento asignado automaticamente al crear desde este modal
# (Administrador queda con acceso a Bitacora; Operador con Televigilante).
_ROLE_DEPARTMENT = {"admin": "Bitacora", "agent": "Televigilante"}


@router.post("/api/bitacora/users")
def api_crear_bitacora_user(
    body: _CreateUserBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    if not is_bitacora_admin(current_user):
        raise HTTPException(status_code=403, detail="Solo administradores pueden registrar usuarios.")
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=409, detail="El nombre de usuario ya está en uso.")
    user = User(
        name=body.name,
        username=body.username,
        hashed_password=hash_password(body.password),
        email=body.email or None,
        role=body.role,
        department=_ROLE_DEPARTMENT[body.role],
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"ok": True, "id": user.id}


class _EditUserBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    username: str = Field(..., min_length=1, max_length=50)
    password: str | None = Field(default=None)
    email: str | None = Field(default=None, max_length=255)
    role: str = Field(..., pattern=r"^(admin|agent|superadmin)$")
    is_active: bool


@router.put("/api/bitacora/users/{user_id}")
def api_edit_bitacora_user(
    user_id: int,
    body: _EditUserBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    if not is_bitacora_admin(current_user):
        raise HTTPException(status_code=403, detail="Solo administradores pueden editar usuarios.")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    # Check username uniqueness if changed
    if body.username != user.username:
        conflict = db.query(User).filter(User.username == body.username, User.id != user_id).first()
        if conflict:
            raise HTTPException(status_code=409, detail="El nombre de usuario ya está en uso.")
    user.name = body.name
    user.username = body.username
    user.email = body.email or None
    user.role = body.role
    user.is_active = body.is_active
    if body.password:
        user.hashed_password = hash_password(body.password)
    db.commit()
    return {"ok": True, "id": user.id}
