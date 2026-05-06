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
    fallback_by_region = {
        "Arica y Parinacota": ["Arica", "Camarones", "General Lagos", "Putre"],
        "Tarapaca": ["Alto Hospicio", "Camina", "Colchane", "Huara", "Iquique", "Pica", "Pozo Almonte"],
        "Antofagasta": ["Antofagasta", "Calama", "Maria Elena", "Mejillones", "Ollague", "San Pedro de Atacama", "Sierra Gorda", "Taltal", "Tocopilla"],
        "Atacama": ["Alto del Carmen", "Caldera", "Chanaral", "Copiapo", "Diego de Almagro", "Freirina", "Huasco", "Tierra Amarilla", "Vallenar"],
        "Coquimbo": ["Andacollo", "Canela", "Combarbala", "Coquimbo", "Illapel", "La Higuera", "La Serena", "Los Vilos", "Monte Patria", "Ovalle", "Paiguano", "Punitaqui", "Rio Hurtado", "Salamanca", "Vicuna"],
        "Valparaiso": ["Algarrobo", "Cabildo", "Calera", "Calle Larga", "Cartagena", "Casablanca", "Catemu", "Concon", "El Quisco", "El Tabo", "Hijuelas", "Isla de Pascua", "Juan Fernandez", "La Cruz", "La Ligua", "Limache", "Llay Llay", "Los Andes", "Nogales", "Olmue", "Panquehue", "Papudo", "Petorca", "Puchuncavi", "Putaendo", "Quillota", "Quilpue", "Quintero", "Rinconada", "San Antonio", "San Esteban", "San Felipe", "Santa Maria", "Santo Domingo", "Valparaiso", "Villa Alemana", "Vina del Mar", "Zapallar"],
        "Metropolitana de Santiago": ["Alhue", "Buin", "Calera de Tango", "Cerrillos", "Cerro Navia", "Colina", "Conchali", "Curacavi", "El Bosque", "El Monte", "Estacion Central", "Huechuraba", "Independencia", "Isla de Maipo", "La Cisterna", "La Florida", "La Granja", "La Pintana", "La Reina", "Lampa", "Las Condes", "Lo Barnechea", "Lo Espejo", "Lo Prado", "Macul", "Maipu", "Maria Pinto", "Melipilla", "Nunoa", "Padre Hurtado", "Paine", "Pedro Aguirre Cerda", "Penaflor", "Penalolen", "Pirque", "Providencia", "Pudahuel", "Puente Alto", "Quilicura", "Quinta Normal", "Recoleta", "Renca", "San Bernardo", "San Joaquin", "San Jose de Maipo", "San Miguel", "San Pedro", "San Ramon", "Santiago", "Talagante", "Tiltil", "Vitacura"],
        "O Higgins": ["Chepica", "Chimbarongo", "Codegua", "Coinco", "Coltauco", "Donihue", "Graneros", "La Estrella", "Las Cabras", "Litueche", "Lolol", "Machali", "Malloa", "Marchigue", "Mostazal", "Nancagua", "Navidad", "Olivar", "Palmilla", "Paredones", "Peralillo", "Pichidegua", "Pichilemu", "Placilla", "Pumanque", "Quinta de Tilcoco", "Rancagua", "Rengo", "Requinoa", "San Fernando", "San Vicente"],
        "Maule": ["Cauquenes", "Chanco", "Colbun", "Constitucion", "Curepto", "Curico", "Empedrado", "Hualane", "Licanten", "Linares", "Longavi", "Maule", "Molina", "Parral", "Pelarco", "Pelluhue", "Pencahue", "Rauco", "Retiro", "Rio Claro", "Romeral", "Sagrada Familia", "San Clemente", "San Javier", "San Rafael", "Talca", "Teno", "Vichuquen", "Villa Alegre", "Yerbas Buenas"],
        "Nuble": ["Bulnes", "Chillan", "Chillan Viejo", "Cobquecura", "Coelemu", "Coihueco", "El Carmen", "Ninhue", "Niquen", "Pemuco", "Pinto", "Portezuelo", "Quillon", "Quirihue", "Ranquil", "San Carlos", "San Fabian", "San Ignacio", "San Nicolas", "Treguaco", "Yungay"],
        "Biobio": ["Alto Biobio", "Antuco", "Arauco", "Cabrero", "Canete", "Chiguayante", "Concepcion", "Contulmo", "Coronel", "Curanilahue", "Florida", "Hualpen", "Hualqui", "Laja", "Lebu", "Los Alamos", "Los Angeles", "Lota", "Mulchen", "Nacimiento", "Negrete", "Penco", "Quilaco", "Quilleco", "San Pedro de la Paz", "San Rosendo", "Santa Barbara", "Santa Juana", "Talcahuano", "Tirua", "Tome", "Tucapel", "Yumbel"],
        "La Araucania": ["Angol", "Carahue", "Cholchol", "Collipulli", "Cunco", "Curacautin", "Curarrehue", "Ercilla", "Freire", "Galvarino", "Gorbea", "Lautaro", "Loncoche", "Lonquimay", "Los Sauces", "Lumaco", "Melipeuco", "Nueva Imperial", "Padre Las Casas", "Perquenco", "Pitrufquen", "Pucon", "Purén", "Renaico", "Saavedra", "Temuco", "Teodoro Schmidt", "Tolten", "Traiguen", "Victoria", "Vilcun", "Villarrica"],
        "Los Rios": ["Corral", "Futrono", "La Union", "Lago Ranco", "Lanco", "Los Lagos", "Mariquina", "Paillaco", "Panguipulli", "Rio Bueno", "Valdivia"],
        "Los Lagos": ["Ancud", "Calbuco", "Castro", "Chaiten", "Chonchi", "Cochamo", "Curaco de Velez", "Dalcahue", "Fresia", "Frutillar", "Futaleufu", "Hualaihue", "Llanquihue", "Los Muermos", "Maullin", "Osorno", "Palena", "Puerto Montt", "Puerto Octay", "Puerto Varas", "Puqueldon", "Purranque", "Puyehue", "Queilen", "Quellon", "Quemchi", "Quinchao", "Rio Negro", "San Juan de la Costa", "San Pablo"],
        "Aysen": ["Aysen", "Chile Chico", "Cisnes", "Cochrane", "Coyhaique", "Guaitecas", "Lago Verde", "OHiggins", "Rio Ibanez", "Tortel"],
        "Magallanes y de la Antartica Chilena": ["Antartica", "Cabo de Hornos", "Laguna Blanca", "Natales", "Porvenir", "Primavera", "Punta Arenas", "Rio Verde", "San Gregorio", "Timaukel", "Torres del Paine"],
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
