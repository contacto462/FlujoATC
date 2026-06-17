from __future__ import annotations

import hashlib
import hmac
import math
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ATC.app.core.config import settings
from ATC.app.core.db import get_db
from ATC.app.models.incidencias import SucursalBBDD
from ATC.app.models.inicio_turno import InicioTurnoGuardia, InicioTurnoRegistro
from ATC.app.services.incidencias_service import IncidenciasService


router = APIRouter(tags=["inicio-turno"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))

TIPOS_TURNO = ("Normal", "Extra")
RADIO_MAXIMO_METROS = 35.0
DEFAULT_GUARDIAS = (
    {"rut": "211342854", "nombre": "Fernando Lubiano"},
)


class InicioTurnoCreate(BaseModel):
    rut: str = Field(min_length=1, max_length=40)
    tipo_turno: str = Field(min_length=1, max_length=80)
    recinto: Optional[str] = Field(default=None, max_length=255)
    sucursal_id: Optional[int] = None
    latitud: Optional[float] = None
    longitud: Optional[float] = None
    precision_metros: Optional[float] = None
    ubicacion_estado: Optional[str] = Field(default=None, max_length=80)


def _normalizar_rut(value: object) -> str:
    text = str(value or "").strip().upper()
    return "".join(ch for ch in text if ch not in ". ")


def _parse_float(value: object) -> float | None:
    text = str(value or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _sucursal_coords(sucursal: SucursalBBDD | None) -> tuple[float | None, float | None]:
    if not sucursal:
        return None, None
    lat = _parse_float(sucursal.latitud)
    lng = _parse_float(sucursal.longitud)
    if lat is not None and lng is not None:
        return lat, lng

    raw = str(sucursal.latitud_longitud or "").strip()
    if "," not in raw:
        return None, None
    parts = raw.split(",", 1)
    return _parse_float(parts[0]), _parse_float(parts[1])


def _obtener_o_geocodificar_sucursal(db: Session, sucursal: SucursalBBDD) -> tuple[float | None, float | None]:
    lat, lng = _sucursal_coords(sucursal)
    if lat is not None and lng is not None:
        return lat, lng

    direccion = str(sucursal.direccion_sucursal or "").strip()
    comuna = str(sucursal.comuna or "").strip() or "Quintero"
    query = ", ".join(part for part in [direccion, comuna, "Chile"] if part)
    if not query:
        return None, None

    lat_txt, lng_txt = IncidenciasService(db)._geocodificar_direccion(query)
    lat = _parse_float(lat_txt)
    lng = _parse_float(lng_txt)
    if lat is None or lng is None:
        return None, None

    sucursal.latitud = f"{lat:.6f}"
    sucursal.longitud = f"{lng:.6f}"
    sucursal.latitud_longitud = f"{lat:.6f}, {lng:.6f}"
    db.commit()
    return lat, lng


def _distancia_metros(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    earth_radius_m = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return earth_radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _recinto_label(sucursal: SucursalBBDD | None) -> str:
    if not sucursal:
        return ""
    parts = [
        str(sucursal.nombre_empresa or "").strip(),
        str(sucursal.nombre_sucursal or "").strip(),
    ]
    label = " - ".join(part for part in parts if part)
    return label or str(sucursal.direccion_sucursal or "").strip()


def _listar_recintos(db: Session) -> list[dict[str, str | int]]:
    rows = (
        db.query(SucursalBBDD)
        .order_by(SucursalBBDD.nombre_empresa.asc(), SucursalBBDD.nombre_sucursal.asc())
        .all()
    )
    return [
        {
            "id": row.id,
            "label": _recinto_label(row),
            "direccion": str(row.direccion_sucursal or "").strip(),
        }
        for row in rows
        if _recinto_label(row)
    ]


def _listar_recintos_qr(db: Session) -> list[dict[str, str | int]]:
    prefix = "municipalidad de quintero"
    rows = (
        db.query(SucursalBBDD)
        .filter(
            SucursalBBDD.latitud.isnot(None),
            SucursalBBDD.longitud.isnot(None),
        )
        .order_by(SucursalBBDD.nombre_empresa.asc(), SucursalBBDD.nombre_sucursal.asc())
        .all()
    )
    return [
        {
            "id": row.id,
            "label": _recinto_label(row),
            "direccion": str(row.direccion_sucursal or "").strip(),
        }
        for row in rows
        if _recinto_label(row).casefold().startswith(prefix)
    ]


def _recinto_qr_token(recinto_id: object) -> str:
    secret = str(settings.JWT_SECRET or "inicio-turno").encode("utf-8")
    message = f"inicio-turno-recinto:{recinto_id}".encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:24]


def _resolver_recinto_por_qr(db: Session, qr: str) -> SucursalBBDD | None:
    token = str(qr or "").strip()
    if not token:
        return None
    for recinto in _listar_recintos_qr(db):
        if hmac.compare_digest(_recinto_qr_token(recinto["id"]), token):
            return db.get(SucursalBBDD, int(recinto["id"]))
    return None


def _buscar_guardia_por_rut(
    db: Session,
    rut: str,
    sucursal_id: int | None = None,
) -> InicioTurnoGuardia | None:
    rut_norm = _normalizar_rut(rut)
    if not rut_norm:
        return None

    for guardia in db.query(InicioTurnoGuardia).all():
        if _normalizar_rut(guardia.rut) == rut_norm:
            return guardia
    return None


def seed_default_inicio_turno_guardias(db: Session) -> None:
    for item in DEFAULT_GUARDIAS:
        rut = _normalizar_rut(item["rut"])
        existing = _buscar_guardia_por_rut(db, rut)
        if existing:
            if str(existing.nombre or "").strip() != item["nombre"]:
                existing.nombre = item["nombre"]
            continue
        db.add(InicioTurnoGuardia(rut=rut, nombre=item["nombre"]))
    db.commit()


@router.get("/inicio-turno", response_class=HTMLResponse)
def inicio_turno_page(
    request: Request,
    qr: str = Query(default=""),
    recinto_id: int | None = Query(default=None),
    recinto: str = Query(default=""),
    db: Session = Depends(get_db),
):
    sucursal = _resolver_recinto_por_qr(db, qr) if qr else None
    if not sucursal and recinto_id:
        sucursal = db.get(SucursalBBDD, recinto_id)
    recinto_qr = _recinto_label(sucursal) if sucursal else str(recinto or "").strip()
    return templates.TemplateResponse(
        request,
        "inicio_turno.html",
        {
            "request": request,
            "recinto_id": sucursal.id if sucursal else None,
            "recinto_qr": recinto_qr,
            "recintos": _listar_recintos(db) if not recinto_qr else [],
            "tipos_turno": TIPOS_TURNO,
        },
    )


@router.get("/inicio-turno/qr-recintos", response_class=HTMLResponse)
def inicio_turno_qr_recintos_page(
    request: Request,
    db: Session = Depends(get_db),
):
    base_url = str(request.base_url).rstrip("/")
    items = []
    for recinto in _listar_recintos_qr(db):
        target = f"{base_url}/inicio-turno?{urlencode({'qr': _recinto_qr_token(recinto['id'])})}"
        items.append({**recinto, "target": target})
    return templates.TemplateResponse(
        request,
        "inicio_turno_qr_recintos.html",
        {
            "request": request,
            "recintos": items,
        },
    )


@router.get("/api/inicio-turno/guardia")
def buscar_guardia(
    rut: str = Query(min_length=1),
    recinto_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    guardia = _buscar_guardia_por_rut(db, rut, recinto_id)
    if not guardia:
        return {"found": False, "nombre": "", "rut": _normalizar_rut(rut)}
    return {
        "found": True,
        "nombre": str(guardia.nombre or "").strip(),
        "rut": str(guardia.rut or "").strip(),
        "sucursal_id": recinto_id,
    }


@router.post("/api/inicio-turno")
def registrar_inicio_turno(
    payload: InicioTurnoCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    tipo_turno = str(payload.tipo_turno or "").strip()
    if tipo_turno not in TIPOS_TURNO:
        raise HTTPException(status_code=400, detail="Tipo de turno invalido")

    sucursal = db.get(SucursalBBDD, payload.sucursal_id) if payload.sucursal_id else None
    recinto = _recinto_label(sucursal) if sucursal else str(payload.recinto or "").strip()
    if not recinto:
        raise HTTPException(status_code=400, detail="Recinto requerido")

    if (
        str(payload.ubicacion_estado or "").strip() != "confirmada"
        or payload.latitud is None
        or payload.longitud is None
    ):
        raise HTTPException(status_code=400, detail="Ubicacion obligatoria para iniciar turno")

    if not sucursal:
        raise HTTPException(status_code=400, detail="Recinto sin sucursal valida para geocerca")

    recinto_lat, recinto_lng = _obtener_o_geocodificar_sucursal(db, sucursal)
    if recinto_lat is None or recinto_lng is None:
        raise HTTPException(status_code=400, detail="No se pudo validar la direccion del recinto")

    distancia = _distancia_metros(payload.latitud, payload.longitud, recinto_lat, recinto_lng)
    if distancia > RADIO_MAXIMO_METROS:
        raise HTTPException(
            status_code=400,
            detail=f"Estas a {round(distancia)} metros del recinto. Maximo permitido: {round(RADIO_MAXIMO_METROS)} metros",
        )

    guardia = _buscar_guardia_por_rut(db, payload.rut, payload.sucursal_id)
    if not guardia:
        raise HTTPException(status_code=404, detail="No existe un guardia registrado con ese RUT")

    registro = InicioTurnoRegistro(
        rut=_normalizar_rut(guardia.rut or payload.rut),
        nombre_guardia=str(guardia.nombre or "").strip(),
        tipo_turno=tipo_turno,
        recinto=recinto,
        sucursal_id=sucursal.id if sucursal else payload.sucursal_id,
        latitud=payload.latitud,
        longitud=payload.longitud,
        precision_metros=payload.precision_metros,
        ubicacion_estado=str(payload.ubicacion_estado or "").strip() or None,
        ip_origen=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(registro)
    db.commit()
    db.refresh(registro)

    return {
        "ok": True,
        "id": registro.id,
        "rut": registro.rut,
        "nombre": registro.nombre_guardia,
        "tipo_turno": registro.tipo_turno,
        "recinto": registro.recinto,
        "registrado_at": registro.registrado_at.isoformat(sep=" ", timespec="seconds"),
    }
