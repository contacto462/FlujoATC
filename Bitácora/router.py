from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from Bitácora.access import can_access_bitacora
from ATC.app.core.config import settings
from ATC.app.core.db import get_db, get_incidencias_db
from ATC.app.models.user import User
from ATC.app.services.user_service import UserService


router = APIRouter(tags=["bitacora"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
COOKIE_NAME = "access_token"


class NoticiaCreate(BaseModel):
    nombre_empresa: str = Field(min_length=1)
    nombre_sucursal: str = Field(min_length=1)
    fecha_fin_noticia: date
    mensaje: str = Field(min_length=1)


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
    return [
        {
            "id": user.id,
            "name": str(user.name or "").strip(),
            "username": str(user.username or "").strip(),
            "email": "",
            "user_type": "Administrador" if getattr(user, "is_admin", False) else "Operador",
            "status": "Activado" if bool(getattr(user, "is_active", False)) else "Desactivado",
            "is_active": bool(getattr(user, "is_active", False)),
        }
        for user in users
        if can_access_bitacora(user)
    ]


def _ensure_bitacora_noticias_table(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS bitacora_noticias (
                id BIGSERIAL PRIMARY KEY,
                nombre_empresa TEXT NOT NULL,
                nombre_sucursal TEXT NOT NULL,
                usuario_registra TEXT NOT NULL,
                fecha_registro TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                fecha_fin_noticia TIMESTAMP NOT NULL,
                mensaje TEXT NOT NULL
            )
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
    bitacora_users = _bitacora_users(db)

    return templates.TemplateResponse(
        request,
        "bitacora.html",
        {
            "request": request,
            "user": current_user,
            "empresas": empresas,
            "bitacora_users": bitacora_users,
        },
    )


@router.get("/api/bitacora/sucursales")
def bitacora_sucursales_api(
    empresa: str = Query(...),
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    empresa_limpia = str(empresa or "").strip()
    if not empresa_limpia:
        raise HTTPException(status_code=400, detail="Debes indicar una empresa.")

    rows = incidencias_db.execute(
        text(
            """
            SELECT
                id,
                COALESCE(TRIM(rut), '') AS rut,
                COALESCE(TRIM(nombre_empresa), '') AS nombre_empresa,
                COALESCE(TRIM(nombre_sucursal), '') AS nombre_sucursal,
                COALESCE(TRIM(direccion_sucursal), '') AS direccion_sucursal,
                COALESCE(TRIM(comuna), '') AS comuna,
                COALESCE(TRIM(region), '') AS region
            FROM bbdd_sucursales
            WHERE LOWER(TRIM(nombre_empresa)) = LOWER(TRIM(:empresa))
            ORDER BY nombre_sucursal ASC, direccion_sucursal ASC, id ASC
            """
        ),
        {"empresa": empresa_limpia},
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

    venta_row = incidencias_db.execute(
        text(
            """
            SELECT
                codigo,
                tipo_plan,
                numero_camaras_instalar,
                numero_camaras_vigilar
            FROM venta_ods
            WHERE LOWER(TRIM(razon_social)) = LOWER(TRIM(:empresa))
               OR LOWER(TRIM(nombre_sucursal)) = LOWER(TRIM(:sucursal))
            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
            LIMIT 1
            """
        ),
        {"empresa": empresa_limpia, "sucursal": selected_sucursal},
    ).mappings().first()

    tecnico_row = None
    if venta_row and venta_row.get("codigo"):
        tecnico_row = incidencias_db.execute(
            text(
                """
                SELECT camaras_registradas
                FROM servicio_tecnico_ventas_odt
                WHERE odt = :odt
                ORDER BY id DESC
                LIMIT 1
                """
            ),
            {"odt": str(venta_row.get("codigo"))},
        ).mappings().first()

    cliente_row = incidencias_db.execute(
        text(
            """
            SELECT telefono
            FROM bbdd_clientes
            WHERE LOWER(TRIM(cliente)) = LOWER(TRIM(:empresa))
               OR LOWER(TRIM(rut)) = LOWER(TRIM(:rut))
            ORDER BY id DESC
            LIMIT 1
            """
        ),
        {"empresa": empresa_limpia, "rut": _detail_value(selected_row, "rut")},
    ).mappings().first()

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

    layout_row = incidencias_db.execute(
        text(
            """
            SELECT imagenes
            FROM mantenciones_imagenes_sucursal
            WHERE LOWER(TRIM(sucursal)) = LOWER(TRIM(:sucursal))
            ORDER BY updated_at DESC NULLS LAST, id DESC
            LIMIT 1
            """
        ),
        {"sucursal": selected_sucursal},
    ).mappings().first()

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
            ORDER BY fecha_registro DESC, id DESC
            """
        ),
        {"empresa": empresa_limpia, "sucursal": selected_sucursal},
    ).mappings().all()

    detalle = {
        "tipo_vigilancia": _first_non_empty(venta_row.get("tipo_plan") if venta_row else None),
        "horario_apertura": _detail_value(selected_row, "horario_apertura"),
        "horario_cierre": _detail_value(selected_row, "horario_cierre"),
        "empresa": _detail_value(selected_row, "nombre_empresa"),
        "rut": _detail_value(selected_row, "rut"),
        "nombre_sucursal": _detail_value(selected_row, "nombre_sucursal"),
        "direccion": _detail_value(selected_row, "direccion_sucursal"),
        "referencia_ubicacion": _detail_value(selected_row, "referencia_ubicacion"),
        "contacto": _first_non_empty(cliente_row.get("telefono") if cliente_row else None),
        "correo": _detail_value(selected_row, "email_facturas"),
        "plan_cuadrante": "-",
        "carabineros": "-",
        "bomberos": "-",
        "seguridad_ciudadana": "-",
        "fecha_inicio": _detail_value(selected_row, "created_at"),
        "camaras_contratadas": _first_non_empty(venta_row.get("numero_camaras_instalar") if venta_row else None),
        "camaras_televigiladas": _first_non_empty(
            venta_row.get("numero_camaras_vigilar") if venta_row else None,
            tecnico_row.get("camaras_registradas") if tecnico_row else None,
        ),
        "horario_habil": _detail_value(selected_row, "dias_funcionamiento"),
        "horario_no_habil": "-",
        "compania_electricidad": _detail_value(selected_row, "proveedor_electricidad"),
        "numero_cliente_electricidad": _detail_value(selected_row, "nro_proveedor_electricidad"),
        "proveedor_internet_cliente": _detail_value(selected_row, "proveedor_internet"),
        "internet_atc": "-",
        "telefono_porton": "-",
        "telefono_recepcion": "-",
        "plano_camaras": _first_non_empty(layout_row.get("imagenes") if layout_row else None),
    }

    return {
        "empresa": empresa_limpia,
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
        "indicaciones_especiales": [],
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
            VALUES (
                :nombre_empresa,
                :nombre_sucursal,
                :usuario_registra,
                :fecha_fin_noticia,
                :mensaje
            )
            RETURNING
                id,
                nombre_empresa,
                nombre_sucursal,
                usuario_registra,
                fecha_registro,
                fecha_fin_noticia,
                mensaje
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
