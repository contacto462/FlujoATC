from __future__ import annotations

import json
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.venta.models import VentaCliente
from app.venta.schemas import VentaClienteCreateRequest


def normalize_rut(value: str) -> str:
    cleaned = re.sub(r"[^0-9kK]", "", (value or "")).upper()
    if len(cleaned) < 2:
        return cleaned
    return f"{cleaned[:-1]}-{cleaned[-1]}"


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("56"):
        digits = digits[2:]
    if digits.startswith("9"):
        return f"+56{digits}"
    return f"+{digits}" if digits else ""


def rut_exists(db: Session, rut: str) -> bool:
    safe_rut = normalize_rut(rut)
    if not safe_rut:
        return False
    row = (
        db.query(VentaCliente.id)
        .filter(func.lower(func.trim(VentaCliente.rut)) == safe_rut.lower())
        .first()
    )
    return row is not None


def create_cliente(db: Session, payload: VentaClienteCreateRequest, ejecutivo_email: str) -> VentaCliente:
    rut = normalize_rut(payload.rut)
    if rut_exists(db, rut):
        raise HTTPException(status_code=409, detail="El RUT ya está registrado.")

    record = VentaCliente(
        rut=rut,
        razon_social=payload.razonSocial.strip(),
        giro=payload.giro.strip(),
        direccion=payload.direccion.strip(),
        region=payload.region.strip(),
        comuna=payload.comuna.strip(),
        email_facturas=str(payload.emailFacturas).strip(),
        nombre_representante=payload.nombreRepresentante.strip(),
        rut_representante=normalize_rut(payload.rutRepresentante),
        telefono=normalize_phone(payload.telefono.strip()),
        email_representante=str(payload.emailRepresentante).strip(),
        ejecutivo_email=ejecutivo_email.strip(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _fetch_catalog(path: str) -> dict:
    base_url = (settings.VENTA_CATALOGO_BASE_URL or "").rstrip("/")
    if not base_url:
        raise HTTPException(status_code=503, detail="Catalogo externo no configurado.")
    req = Request(url=f"{base_url}{path}", method="GET")
    try:
        with urlopen(req, timeout=settings.VENTA_CATALOGO_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No fue posible consultar el catalogo externo: {exc}")


def fetch_regiones() -> list[str]:
    data = _fetch_catalog("/regiones")
    regiones = data.get("regiones") if isinstance(data, dict) else None
    if not isinstance(regiones, list):
        raise HTTPException(status_code=502, detail="Respuesta invalida del catalogo de regiones.")
    return [str(item).strip() for item in regiones if str(item).strip()]


def fetch_comunas(region: str) -> list[str]:
    encoded = quote((region or "").strip())
    data = _fetch_catalog(f"/comunas?region={encoded}")
    comunas = data.get("comunas") if isinstance(data, dict) else None
    if not isinstance(comunas, list):
        raise HTTPException(status_code=502, detail="Respuesta invalida del catalogo de comunas.")
    return [str(item).strip() for item in comunas if str(item).strip()]


def get_clientes_table(db: Session) -> dict:
    headers = [
        "ID",
        "RUT",
        "Razon Social",
        "Giro",
        "Direccion",
        "Region",
        "Comuna",
        "Email Facturas",
        "Nombre Representante",
        "RUT Representante",
        "Telefono",
        "Email Representante",
        "Ejecutivo Email",
        "Fecha Creacion",
    ]
    rows = db.query(VentaCliente).order_by(VentaCliente.id.asc()).all()
    data_rows: list[list[str]] = []
    for row in rows:
        data_rows.append([
            str(row.id),
            row.rut or "",
            row.razon_social or "",
            row.giro or "",
            row.direccion or "",
            row.region or "",
            row.comuna or "",
            row.email_facturas or "",
            row.nombre_representante or "",
            row.rut_representante or "",
            row.telefono or "",
            row.email_representante or "",
            row.ejecutivo_email or "",
            row.fecha_creacion.strftime("%Y-%m-%d %H:%M:%S") if row.fecha_creacion else "",
        ])
    return {"headers": headers, "rows": data_rows}


def update_cliente_row(db: Session, row_id: int, values: list[str]) -> None:
    if len(values) < 12:
        raise HTTPException(status_code=400, detail="Fila invalida: faltan columnas para actualizar.")
    record = db.query(VentaCliente).filter(VentaCliente.id == row_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Registro no encontrado.")

    new_rut = normalize_rut(values[1])
    if new_rut.lower() != (record.rut or "").lower():
        exists = (
            db.query(VentaCliente.id)
            .filter(func.lower(func.trim(VentaCliente.rut)) == new_rut.lower(), VentaCliente.id != row_id)
            .first()
        )
        if exists:
            raise HTTPException(status_code=409, detail="El RUT ya existe en otro registro.")

    record.rut = new_rut
    record.razon_social = (values[2] or "").strip()
    record.giro = (values[3] or "").strip()
    record.direccion = (values[4] or "").strip()
    record.region = (values[5] or "").strip()
    record.comuna = (values[6] or "").strip()
    record.email_facturas = (values[7] or "").strip()
    record.nombre_representante = (values[8] or "").strip()
    record.rut_representante = normalize_rut(values[9] or "")
    record.telefono = normalize_phone(values[10] or "")
    record.email_representante = (values[11] or "").strip()
    db.commit()
