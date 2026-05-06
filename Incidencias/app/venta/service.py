from __future__ import annotations

import json
import re
import ssl
import unicodedata
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


def _normalize_text(value: str) -> str:
    return (
        unicodedata.normalize("NFD", str(value or "").strip().lower())
        .encode("ascii", "ignore")
        .decode("ascii")
    )


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

    nombre_representante = (payload.nombreRepresentante or "").strip() or None
    email_facturas = str(payload.emailFacturas).strip()
    email_representante = str(payload.emailRepresentante).strip() if payload.emailRepresentante else None
    rut_representante = normalize_rut(payload.rutRepresentante or "") or None

    record = ClienteBBDD(
        cliente=payload.razonSocial.strip(),
        giro=(payload.giro or "").strip() or None,
        direccion=payload.direccion.strip(),
        region=(payload.region or "").strip() or None,
        comuna=(payload.comuna or "").strip() or None,
        contacto=nombre_representante,
        correo=email_facturas,
        rut=rut,
        email_facturas=email_facturas,
        nombre_representante=nombre_representante,
        rut_representante=rut_representante,
        telefono=(payload.telefono or "").strip() or None,
        email_representante=email_representante,
        ejecutivo_email=(ejecutivo_email or payload.ejecutivo or "").strip() or None,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _fetch_catalog(path: str) -> dict:
    base_url = (settings.venta_catalogo_base_url or "").rstrip("/")
    if not base_url:
        raise HTTPException(status_code=503, detail="La API externa de regiones/comunas no esta configurada.")
    req = Request(
        url=f"{base_url}{path}",
        method="GET",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )
    try:
        timeout = int(settings.venta_catalogo_timeout_seconds or 8)
        ssl_context = None
        if base_url.startswith("https://") and not settings.venta_catalogo_verify_ssl:
            ssl_context = ssl._create_unverified_context()
        with urlopen(req, timeout=timeout, context=ssl_context) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No fue posible consultar la API externa de regiones/comunas: {exc}")


def fetch_regiones() -> list[str]:
    data = _fetch_catalog("/regiones")
    regiones = None
    if isinstance(data, dict):
        regiones = data.get("regiones")
    elif isinstance(data, list):
        regiones = data
    if not isinstance(regiones, list):
        raise HTTPException(status_code=502, detail="La API externa devolvio una respuesta invalida para regiones.")

    cleaned: list[str] = []
    for item in regiones:
        if isinstance(item, str):
            nombre = item.strip()
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            nombre = str(item[1]).strip()
        elif isinstance(item, dict):
            nombre = str(item.get("nombre") or item.get("name") or "").strip()
        else:
            nombre = ""
        if nombre:
            cleaned.append(nombre)
    if not cleaned:
        raise HTTPException(status_code=502, detail="La API externa no devolvio regiones.")
    return cleaned


def fetch_comunas(region: str) -> list[str]:
    region_name = (region or "").strip()
    encoded = quote(region_name)
    data = None

    base_url = (settings.venta_catalogo_base_url or "").rstrip("/")
    if base_url.endswith("/dpa"):
        regiones_data = _fetch_catalog("/regiones")
        regiones = regiones_data if isinstance(regiones_data, list) else regiones_data.get("regiones", [])
        region_code = ""
        for item in regiones:
            if isinstance(item, dict):
                nombre = str(item.get("nombre") or "").strip()
                codigo = str(item.get("codigo") or "").strip()
                if _normalize_text(nombre) == _normalize_text(region_name):
                    region_code = codigo
                    break
        if not region_code:
            raise HTTPException(status_code=404, detail=f"No se encontro la region '{region_name}' en la API externa.")
        data = _fetch_catalog(f"/regiones/{quote(region_code)}/comunas")
    else:
        data = _fetch_catalog(f"/comunas?region={encoded}")

    comunas = None
    if isinstance(data, dict):
        comunas = data.get("comunas")
    elif isinstance(data, list):
        comunas = data
    if not isinstance(comunas, list):
        raise HTTPException(status_code=502, detail="La API externa devolvio una respuesta invalida para comunas.")

    cleaned: list[str] = []
    for item in comunas:
        if isinstance(item, str):
            nombre = item.strip()
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            nombre = str(item[1]).strip()
        elif isinstance(item, dict):
            nombre = str(item.get("nombre") or item.get("name") or "").strip()
        else:
            nombre = ""
        if nombre:
            cleaned.append(nombre)
    if not cleaned:
        raise HTTPException(status_code=502, detail=f"La API externa no devolvio comunas para la region '{region_name}'.")
    return cleaned


def get_clientes_table(db: Session) -> dict:
    headers = [
        "ID",
        "RUT",
        "Cliente",
        "Giro",
        "Direccion",
        "Region",
        "Comuna",
        "Email Facturas",
        "Nombre Representante",
        "RUT Representante",
        "Telefono",
        "Email Representante",
        "Ejecutivo",
    ]
    rows = db.query(ClienteBBDD).order_by(ClienteBBDD.id.asc()).all()
    data_rows: list[list[str]] = []
    for row in rows:
        data_rows.append([
            str(row.id),
            row.rut or "",
            row.cliente or "",
            row.giro or "",
            row.direccion or "",
            row.region or "",
            row.comuna or "",
            row.email_facturas or row.correo or "",
            row.nombre_representante or row.contacto or "",
            row.rut_representante or "",
            row.telefono or "",
            row.email_representante or "",
            row.ejecutivo_email or "",
        ])
    return {"headers": headers, "rows": data_rows}


def update_cliente_row(db: Session, row_id: int, values: list[str]) -> None:
    if len(values) < 13:
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

    record.rut = new_rut
    record.cliente = (values[2] or "").strip()
    record.giro = (values[3] or "").strip() or None
    record.direccion = (values[4] or "").strip()
    record.region = (values[5] or "").strip() or None
    record.comuna = (values[6] or "").strip() or None
    record.email_facturas = (values[7] or "").strip() or None
    record.nombre_representante = (values[8] or "").strip() or None
    record.rut_representante = normalize_rut(values[9]) or None
    record.telefono = (values[10] or "").strip() or None
    record.email_representante = (values[11] or "").strip() or None
    record.ejecutivo_email = (values[12] or "").strip() or None
    record.contacto = record.nombre_representante
    record.correo = record.email_facturas
    db.commit()
