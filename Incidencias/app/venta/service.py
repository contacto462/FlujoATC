from __future__ import annotations

import json
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ClienteBBDD
from app.venta.schemas import VentaClienteCreateRequest


def normalize_rut(value: str) -> str:
    cleaned = re.sub(r"[^0-9kK]", "", (value or "")).upper()
    if len(cleaned) < 2:
        return cleaned
    return f"{cleaned[:-1]}-{cleaned[-1]}"


def rut_exists(db: Session, rut: str) -> bool:
    safe_rut = normalize_rut(rut)
    if not safe_rut:
        return False
    row = (
        db.query(ClienteBBDD.id)
        .filter(func.lower(func.trim(ClienteBBDD.rut)) == safe_rut.lower())
        .first()
    )
    return row is not None


def create_cliente(db: Session, payload: VentaClienteCreateRequest, ejecutivo_email: str) -> ClienteBBDD:
    rut = normalize_rut(payload.rut)
    if rut_exists(db, rut):
        raise HTTPException(status_code=409, detail="El RUT ya esta registrado.")

    record = ClienteBBDD(
        cliente=payload.razonSocial.strip(),
        direccion=payload.direccion.strip(),
        contacto=(payload.nombreRepresentante or "").strip() or None,
        correo=str(payload.emailRepresentante or payload.emailFacturas).strip(),
        rut=rut,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _fetch_catalog(path: str) -> dict:
    base_url = (
        getattr(settings, "venta_catalogo_base_url", "")
        or getattr(settings, "VENTA_CATALOGO_BASE_URL", "")
        or ""
    ).rstrip("/")
    if not base_url:
        raise HTTPException(status_code=503, detail="Catalogo externo no configurado.")
    req = Request(url=f"{base_url}{path}", method="GET")
    try:
        timeout = int(
            getattr(settings, "venta_catalogo_timeout_seconds", 8)
            or getattr(settings, "VENTA_CATALOGO_TIMEOUT_SECONDS", 8)
            or 8
        )
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No fue posible consultar el catalogo externo: {exc}")


def fetch_regiones() -> list[str]:
    fallback = [
        "Arica y Parinacota",
        "Tarapaca",
        "Antofagasta",
        "Atacama",
        "Coquimbo",
        "Valparaiso",
        "Metropolitana de Santiago",
        "O Higgins",
        "Maule",
        "Nuble",
        "Biobio",
        "La Araucania",
        "Los Rios",
        "Los Lagos",
        "Aysen",
        "Magallanes y de la Antartica Chilena",
    ]
    try:
        data = _fetch_catalog("/regiones")
        regiones = data.get("regiones") if isinstance(data, dict) else None
        if not isinstance(regiones, list):
            return fallback
        cleaned = [str(item).strip() for item in regiones if str(item).strip()]
        return cleaned or fallback
    except HTTPException:
        return fallback


def fetch_comunas(region: str) -> list[str]:
    fallback_by_region = {
        "Valparaiso": ["Valparaiso", "Vina del Mar", "Concon", "Quilpue", "Villa Alemana", "Quillota", "La Calera", "Los Andes", "San Felipe", "San Antonio"],
        "Metropolitana de Santiago": ["Santiago", "Providencia", "Las Condes", "Vitacura", "Nunoa", "Maipu", "Puente Alto", "La Florida", "San Bernardo", "Quilicura"],
        "Biobio": ["Concepcion", "Talcahuano", "Chiguayante", "Hualpen", "Los Angeles", "Coronel", "Lota"],
    }
    encoded = quote((region or "").strip())
    try:
        data = _fetch_catalog(f"/comunas?region={encoded}")
        comunas = data.get("comunas") if isinstance(data, dict) else None
        if not isinstance(comunas, list):
            return fallback_by_region.get((region or "").strip(), [])
        cleaned = [str(item).strip() for item in comunas if str(item).strip()]
        return cleaned or fallback_by_region.get((region or "").strip(), [])
    except HTTPException:
        return fallback_by_region.get((region or "").strip(), [])


def get_clientes_table(db: Session) -> dict:
    headers = [
        "ID",
        "RUT",
        "Cliente",
        "Direccion",
        "Contacto",
        "Correo",
    ]
    rows = db.query(ClienteBBDD).order_by(ClienteBBDD.id.asc()).all()
    data_rows: list[list[str]] = []
    for row in rows:
        data_rows.append([
            str(row.id),
            row.rut or "",
            row.cliente or "",
            row.direccion or "",
            row.contacto or "",
            row.correo or "",
        ])
    return {"headers": headers, "rows": data_rows}


def update_cliente_row(db: Session, row_id: int, values: list[str]) -> None:
    if len(values) < 6:
        raise HTTPException(status_code=400, detail="Fila invalida: faltan columnas para actualizar.")
    record = db.query(ClienteBBDD).filter(ClienteBBDD.id == row_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Registro no encontrado.")

    new_rut = normalize_rut(values[1])
    if new_rut.lower() != (record.rut or "").lower():
        exists = (
            db.query(ClienteBBDD.id)
            .filter(func.lower(func.trim(ClienteBBDD.rut)) == new_rut.lower(), ClienteBBDD.id != row_id)
            .first()
        )
        if exists:
            raise HTTPException(status_code=409, detail="El RUT ya existe en otro registro.")

    record.cliente = (values[2] or "").strip()
    record.direccion = (values[3] or "").strip()
    record.contacto = (values[4] or "").strip()
    record.correo = (values[5] or "").strip()
    record.rut = new_rut
    db.commit()
