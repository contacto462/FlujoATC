from __future__ import annotations

import json
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
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
    encoded = quote((region or "").strip())
    try:
        data = _fetch_catalog(f"/comunas?region={encoded}")
        comunas = data.get("comunas") if isinstance(data, dict) else None
        if not isinstance(comunas, list):
            return []
        return [str(item).strip() for item in comunas if str(item).strip()]
    except HTTPException:
        return []
