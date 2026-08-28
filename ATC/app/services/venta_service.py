from __future__ import annotations

import base64
import logging
import re
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import HTTPException
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from ATC.app.core.incidencias_config import settings
from ATC.app.integrations.piriod_client import PiriodError, crear_cliente as piriod_crear_cliente
from ATC.app.services.incidencias_drive_report_service import (
    DriveReportError,
    build_ods_folder_url,
    download_support_drive_file_bytes,
    find_ods_drive_file_id,
    find_ods_drive_folder_id,
    upload_ods_files_to_drive,
)
from ATC.app.models.user import User
from ATC.app.models.incidencias import (
    AdministracionODT,
    ClienteBBDD,
    FinanzasODT,
    OperacionesVentaODT,
    Registro,
    ServicioTecnicoVentaODT,
    SoporteTecnicoVentaODT,
    SucursalBBDD,
    SucursalContactoEmergencia,
    SucursalGuardia,
    SucursalInfoExtra,
    SucursalPersonaAutorizada,
    VentaODS,
    VentaODSArchivo,
)
from ATC.app.services.incidencias_service import IncidenciasService
from ATC.app.services.venta_trace_email_service import (
    notify_fechas_instalacion_definidas,
    notify_inicio_servicio,
    notify_instalacion_finalizada,
    notify_instalacion_terreno_finalizada,
    notify_materiales_bodega,
    notify_oc_requerida,
    notify_ods_registered,
    notify_recepcion_administracion_cliente,
    notify_servicio_operativo,
    notify_sucursal_lista_para_bitacora,
)
from ATC.app.schemas.venta import VentaClienteCreateRequest, VentaODSArchivoRequest, VentaODSCreateRequest, VentaSucursalCreateRequest

_log = logging.getLogger(__name__)

PROVEEDORES_INTERNET = [
    "ATC",
    "Claro",
    "Entel",
    "GTD",
    "Movistar",
    "Mundo",
    "Starlink",
    "Telmex",
    "VTR",
    "WOM",
    "Otro",
]

PROVEEDORES_ELECTRICIDAD = [
    "CGE",
    "Chilquinta",
    "Coelcha",
    "Coopelan",
    "Enel",
    "Frontel",
    "Luz Linares",
    "Saesa",
    "Otro",
]

_ATC_ROOT = Path(__file__).resolve().parents[2]
VENTA_UPLOADS_DIR = _ATC_ROOT / "uploads" / "venta_ods"


def normalize_rut(value: str) -> str:
    cleaned = re.sub(r"[^0-9kK]", "", (value or "")).upper()
    if len(cleaned) < 2:
        return cleaned
    return f"{cleaned[:-1]}-{cleaned[-1]}"


def _normalize_text(value: str) -> str:
    value = _repair_text_encoding(value)
    normalized = (
        unicodedata.normalize("NFD", str(value or "").strip().lower())
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _repair_text_encoding(value: str) -> str:
    text = str(value or "").strip()
    if not text or "Ã" not in text:
        return text
    try:
        return text.encode("latin1").decode("utf-8")
    except UnicodeError:
        return text


def _repair_payload_encoding(value: Any) -> Any:
    if isinstance(value, str):
        return _repair_text_encoding(value)
    if isinstance(value, list):
        return [_repair_payload_encoding(item) for item in value]
    if isinstance(value, dict):
        return {key: _repair_payload_encoding(item) for key, item in value.items()}
    return value


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


def get_cliente_nombre_by_rut(db: Session, rut: str) -> str:
    safe_rut = normalize_rut(rut)
    if not safe_rut:
        return ""
    row = (
        db.query(ClienteBBDD.cliente)
        .filter(func.lower(func.trim(ClienteBBDD.rut)) == safe_rut.lower())
        .first()
    )
    return str(row[0]).strip() if row and row[0] else ""


def get_clientes_informacion_list(db: Session, q: str = "") -> dict[str, Any]:
    rows = db.execute(text("""
        SELECT
            c.id AS cliente_id,
            c.rut AS rut,
            c.cliente AS cliente,
            c.giro AS giro,
            s.id AS sucursal_id,
            s.nombre_empresa AS nombre_empresa,
            s.nombre_sucursal AS nombre_sucursal,
            s.direccion_sucursal AS direccion_sucursal,
            s.aceptada_bitacora AS aceptada_bitacora,
            e.campos_pendientes AS campos_pendientes,
            e.campos_pendientes_fecha AS campos_pendientes_fecha
        FROM bbdd_clientes c
        LEFT JOIN bbdd_sucursales s
          ON LOWER(TRIM(COALESCE(s.rut, ''))) = LOWER(TRIM(COALESCE(c.rut, '')))
        LEFT JOIN sucursal_info_extra e ON e.sucursal_id = s.id
        ORDER BY c.cliente ASC, s.nombre_sucursal ASC, s.direccion_sucursal ASC
    """)).mappings().all()

    filtro = _normalize_text(q)
    filtro_rut = re.sub(r"[^0-9kK]", "", str(q or "")).lower()
    clientes: dict[str, dict[str, Any]] = {}

    for row in rows:
        rut = str(row.get("rut") or "").strip()
        key = rut.lower() or f"id:{row.get('cliente_id')}"
        cliente = clientes.setdefault(key, {
            "id": row.get("cliente_id"),
            "rut": rut,
            "rutLimpio": re.sub(r"[^0-9kK]", "", rut).lower(),
            "nombre": str(row.get("cliente") or "").strip(),
            "giro": str(row.get("giro") or "").strip(),
            "sucursales": [],
            "totalSucursales": 0,
            "pendientesBitacora": 0,
            "ultimaPendienteBitacora": "",
            "_search": [],
        })

        partes_busqueda = [
            cliente["rut"],
            cliente["rutLimpio"],
            cliente["nombre"],
            cliente["giro"],
        ]

        sucursal_id = row.get("sucursal_id")
        if sucursal_id is not None:
            campos_raw = str(row.get("campos_pendientes") or "").strip()
            pendiente = (not bool(row.get("aceptada_bitacora"))) and bool(campos_raw)
            fecha = row.get("campos_pendientes_fecha")
            fecha_txt = fecha.isoformat(sep=" ", timespec="minutes") if isinstance(fecha, datetime) else ""
            sucursal = {
                "id": sucursal_id,
                "nombre": str(row.get("nombre_sucursal") or "").strip(),
                "nombreEmpresa": str(row.get("nombre_empresa") or "").strip(),
                "direccion": str(row.get("direccion_sucursal") or "").strip(),
                "pendienteBitacora": pendiente,
                "camposPendientes": [c.strip() for c in campos_raw.split(",") if c.strip()],
                "observadoEn": fecha_txt,
            }
            cliente["sucursales"].append(sucursal)
            cliente["totalSucursales"] += 1
            partes_busqueda.extend([sucursal["nombre"], sucursal["nombreEmpresa"], sucursal["direccion"]])
            if pendiente:
                cliente["pendientesBitacora"] += 1
                if fecha_txt and fecha_txt > cliente["ultimaPendienteBitacora"]:
                    cliente["ultimaPendienteBitacora"] = fecha_txt

        cliente["_search"].extend(partes_busqueda)

    items: list[dict[str, Any]] = []
    for cliente in clientes.values():
        search_text = _normalize_text(" ".join(str(x or "") for x in cliente["_search"]))
        rut_text = str(cliente.get("rutLimpio") or "")
        if filtro and filtro not in search_text and (not filtro_rut or filtro_rut not in rut_text):
            continue
        cliente.pop("_search", None)
        items.append(cliente)

    items.sort(
        key=lambda item: (
            -int(item.get("pendientesBitacora") or 0),
            str(item.get("nombre") or "").lower(),
            str(item.get("rut") or "").lower(),
        )
    )
    return _repair_payload_encoding({"clientes": items, "total": len(items)})


def get_proveedores_internet() -> list[str]:
    return PROVEEDORES_INTERNET[:]


def get_proveedores_electricidad() -> list[str]:
    return PROVEEDORES_ELECTRICIDAD[:]


def get_coordinates_for_address(db: Session, direccion: str, comuna: str) -> dict[str, str]:
    query = ", ".join(part for part in [str(direccion or "").strip(), str(comuna or "").strip(), "Chile"] if part)
    if not query:
        return {"lat": "", "lng": ""}
    service = IncidenciasService(db)
    lat, lng = service._geocodificar_direccion(query)
    return {"lat": lat or "", "lng": lng or ""}


def get_ods_data_by_rut(db: Session, rut: str) -> dict[str, list[str] | str]:
    safe_rut = normalize_rut(rut)
    if not safe_rut:
        return {"razonSocial": "", "direcciones": [], "nombresSucursales": []}

    sucursales = (
        db.query(SucursalBBDD)
        .filter(func.lower(func.trim(SucursalBBDD.rut)) == safe_rut.lower())
        .order_by(SucursalBBDD.nombre_sucursal.asc(), SucursalBBDD.direccion_sucursal.asc())
        .all()
    )
    razon_social = get_cliente_nombre_by_rut(db, safe_rut)
    direcciones = [str(s.direccion_sucursal or "").strip() for s in sucursales if str(s.direccion_sucursal or "").strip()]
    nombres = [str(s.nombre_sucursal or "").strip() for s in sucursales]

    return {
        "razonSocial": razon_social,
        "direcciones": direcciones,
        "nombresSucursales": nombres,
    }


def get_cliente_resumen_by_rut(db: Session, rut: str) -> dict:
    safe_rut = normalize_rut(rut)
    cliente = (
        db.query(ClienteBBDD)
        .filter(func.lower(func.trim(ClienteBBDD.rut)) == safe_rut.lower())
        .first()
    )
    if not cliente:
        raise HTTPException(status_code=404, detail="No se encontro un cliente para el RUT ingresado.")

    sucursales = (
        db.query(SucursalBBDD)
        .filter(func.lower(func.trim(SucursalBBDD.rut)) == safe_rut.lower())
        .order_by(SucursalBBDD.nombre_sucursal.asc(), SucursalBBDD.direccion_sucursal.asc())
        .all()
    )

    return _repair_payload_encoding({
        "id": cliente.id,
        "rutCliente": cliente.rut or "",
        "nombreCliente": cliente.cliente or "",
        "giro": cliente.giro or "",
        "direccionCasaMatriz": cliente.direccion or "",
        "regionCasaMatriz": _canonical_region(cliente.region),
        "comunaCasaMatriz": _canonical_comuna(cliente.region, cliente.comuna),
        "emailFacturas": cliente.email_facturas or "",
        "nombreRepresentante": cliente.nombre_representante or "",
        "rutRepresentante": cliente.rut_representante or "",
        "telefonoCliente": cliente.telefono or "",
        "emailRepresentante": cliente.email_representante or "",
        "ejecutivo": cliente.ejecutivo_email or "",
        "cliente": {
            "id": cliente.id,
            "rut": cliente.rut or "",
            "razonSocial": cliente.cliente or "",
            "giro": cliente.giro or "",
            "direccion": cliente.direccion or "",
            "region": _canonical_region(cliente.region),
            "comuna": _canonical_comuna(cliente.region, cliente.comuna),
            "emailFacturas": cliente.email_facturas or "",
            "nombreRepresentante": cliente.nombre_representante or "",
            "rutRepresentante": cliente.rut_representante or "",
            "telefono": cliente.telefono or "",
            "emailRepresentante": cliente.email_representante or "",
            "ejecutivo": cliente.ejecutivo_email or "",
        },
        "sucursales": [
            {
                "id": sucursal.id,
                "rut": sucursal.rut or "",
                "nombreEmpresa": sucursal.nombre_empresa or "",
                "nombre": sucursal.nombre_sucursal or "",
                "direccion": sucursal.direccion_sucursal or "",
                "region": _canonical_region(sucursal.region),
                "comuna": _canonical_comuna(sucursal.region, sucursal.comuna),
                "referenciaUbicacion": sucursal.referencia_ubicacion or "",
                "latitud": sucursal.latitud or "",
                "longitud": sucursal.longitud or "",
                "latitudLongitud": sucursal.latitud_longitud or (
                    f"{sucursal.latitud}, {sucursal.longitud}" if sucursal.latitud and sucursal.longitud else ""
                ),
                "emailFacturas": sucursal.email_facturas or "",
                "proveedorInternet": sucursal.proveedor_internet or "",
                "proveedorElectricidad": sucursal.proveedor_electricidad or "",
                "numeroClienteElectricidad": sucursal.nro_proveedor_electricidad or "",
                "horarioApertura": sucursal.horario_apertura or "",
                "horarioCierre": sucursal.horario_cierre or "",
                "diasFuncionamiento": sucursal.dias_funcionamiento or "",
                "label": f"{sucursal.nombre_sucursal or 'Sucursal'} - {sucursal.direccion_sucursal or ''}".strip(" -"),
            }
            for sucursal in sucursales
        ],
    })


def get_cliente_sucursal_resumen(db: Session, rut: str, sucursal_id: int) -> dict:
    safe_rut = normalize_rut(rut)
    sucursal = (
        db.query(SucursalBBDD)
        .filter(SucursalBBDD.id == sucursal_id, func.lower(func.trim(SucursalBBDD.rut)) == safe_rut.lower())
        .first()
    )
    if not sucursal:
        raise HTTPException(status_code=404, detail="No se encontro la sucursal seleccionada para ese cliente.")

    direccion_sucursal = str(sucursal.direccion_sucursal or "").strip()
    nombre_sucursal = str(sucursal.nombre_sucursal or "").strip()
    ods_sucursal = (
        db.query(VentaODS)
        .filter(func.lower(func.trim(VentaODS.rut_cliente)) == safe_rut.lower())
        .filter(
            or_(
                func.lower(func.trim(VentaODS.direccion_sucursal)) == direccion_sucursal.lower(),
                func.lower(func.trim(VentaODS.nombre_sucursal)) == nombre_sucursal.lower(),
            )
        )
        .order_by(VentaODS.id.desc())
        .all()
    )
    ultima_ods = ods_sucursal[0] if ods_sucursal else None

    # Las camaras se ACUMULAN entre todas las ODS de la sucursal (una ODS de
    # upgrade con 2 camaras suma sobre las 5 existentes: 7, no las pisa).
    # Se excluyen las ODS anuladas del acumulado.
    ods_validas = [o for o in ods_sucursal if str(o.estado or "").strip().lower() != "anulada"]
    total_instalar = sum(int(o.numero_camaras_instalar or 0) for o in ods_validas)
    total_desinstalar = sum(int(o.numero_camaras_desinstalar or 0) for o in ods_validas)
    total_vigilar = sum(int(o.numero_camaras_vigilar or 0) for o in ods_validas)
    total_camaras = total_vigilar or max(total_instalar - total_desinstalar, 0)

    contactos = (
        db.query(SucursalContactoEmergencia)
        .filter(SucursalContactoEmergencia.sucursal_id == sucursal.id)
        .order_by(SucursalContactoEmergencia.id.asc())
        .all()
    )
    autorizados = (
        db.query(SucursalPersonaAutorizada)
        .filter(SucursalPersonaAutorizada.sucursal_id == sucursal.id)
        .order_by(SucursalPersonaAutorizada.id.asc())
        .all()
    )
    guardias = (
        db.query(SucursalGuardia)
        .filter(SucursalGuardia.sucursal_id == sucursal.id)
        .order_by(SucursalGuardia.id.asc())
        .all()
    )
    info_extra = (
        db.query(SucursalInfoExtra)
        .filter(SucursalInfoExtra.sucursal_id == sucursal.id)
        .first()
    )

    return _repair_payload_encoding({
        "id": sucursal.id,
        "rutCliente": sucursal.rut or "",
        "nombreEmpresa": sucursal.nombre_empresa or "",
        "nombreSucursal": sucursal.nombre_sucursal or "",
        "direccionSucursal": sucursal.direccion_sucursal or "",
        "regionSucursal": _canonical_region(sucursal.region),
        "comunaSucursal": sucursal.comuna or "",
        "emailFacturasSucursal": sucursal.email_facturas or "",
        "latitud": sucursal.latitud or "",
        "longitud": sucursal.longitud or "",
        "latitudLongitud": sucursal.latitud_longitud or (
            f"{sucursal.latitud}, {sucursal.longitud}" if sucursal.latitud and sucursal.longitud else ""
        ),
        "referenciaUbicacion": sucursal.referencia_ubicacion or "",
        "codigoODS": ultima_ods.codigo or "" if ultima_ods else "",
        "estadoODS": ultima_ods.estado or "" if ultima_ods else "",
        "cantidadCamaras": str(total_camaras or "") if ods_validas else "",
        "camarasInstalar": str(total_instalar or "") if ods_validas else "",
        "camarasDesinstalar": str(total_desinstalar or "") if ods_validas else "",
        "camarasVigilar": str(total_vigilar or "") if ods_validas else "",
        "diasGrabacion": str(ultima_ods.dias_grabacion or "") if ultima_ods else "",
        "diasMonitoreoDesde": ultima_ods.dias_monitoreo_desde or "" if ultima_ods else "",
        "diasMonitoreoHasta": ultima_ods.dias_monitoreo_hasta or "" if ultima_ods else "",
        "diasMonitoreoAdicional": ultima_ods.dias_monitoreo_adicional or "" if ultima_ods else "",
        "horarioMonitoreo": ultima_ods.horario_monitoreo or "" if ultima_ods else "",
        "proveedorInternet": sucursal.proveedor_internet or "",
        "proveedorElectricidad": sucursal.proveedor_electricidad or "",
        "numeroClienteElectricidad": sucursal.nro_proveedor_electricidad or "",
        "diasApertura": sucursal.dias_funcionamiento or "",
        "horarioApertura": sucursal.horario_apertura or "",
        "horarioCierre": sucursal.horario_cierre or "",
        "tipoServicio": ultima_ods.tipo_servicio.replace(" | ", ", ") if ultima_ods and ultima_ods.tipo_servicio else "",
        "tipoPlan": ultima_ods.tipo_plan or "" if ultima_ods else "",
        "tipoCliente": ultima_ods.tipo_cliente or "" if ultima_ods else "",
        "observacionODS": ultima_ods.observacion or "" if ultima_ods else "",
        "materiales": ultima_ods.materiales or "" if ultima_ods else "",
        "consideraciones": ultima_ods.consideraciones or "" if ultima_ods else "",
        "aguaBano": ultima_ods.agua_bano or "" if ultima_ods else "",
        "requiereOC": ultima_ods.requiere_oc or "" if ultima_ods else "",
        "montosACobrar": ultima_ods.montos_a_cobrar or "" if ultima_ods else "",
        "sucursal": {
            "id": sucursal.id,
            "rut": sucursal.rut or "",
            "nombreEmpresa": sucursal.nombre_empresa or "",
            "nombreSucursal": sucursal.nombre_sucursal or "",
            "direccion": sucursal.direccion_sucursal or "",
            "region": _canonical_region(sucursal.region),
            "comuna": _canonical_comuna(sucursal.region, sucursal.comuna),
            "referenciaUbicacion": sucursal.referencia_ubicacion or "",
            "latitud": sucursal.latitud or "",
            "longitud": sucursal.longitud or "",
            "latitudLongitud": sucursal.latitud_longitud or (
                f"{sucursal.latitud}, {sucursal.longitud}" if sucursal.latitud and sucursal.longitud else ""
            ),
            "emailFacturas": sucursal.email_facturas or "",
            "proveedorInternet": sucursal.proveedor_internet or "",
            "proveedorElectricidad": sucursal.proveedor_electricidad or "",
            "numeroClienteElectricidad": sucursal.nro_proveedor_electricidad or "",
            "horarioApertura": sucursal.horario_apertura or "",
            "horarioCierre": sucursal.horario_cierre or "",
            "diasFuncionamiento": sucursal.dias_funcionamiento or "",
        },
        "ultimaODS": {
            "codigo": ultima_ods.codigo or "",
            "estado": ultima_ods.estado or "",
            "tipoCliente": ultima_ods.tipo_cliente or "",
            "tipoServicio": ultima_ods.tipo_servicio.replace(" | ", ", ") if ultima_ods.tipo_servicio else "",
            "tipoPlan": ultima_ods.tipo_plan or "",
            "camarasInstalar": str(ultima_ods.numero_camaras_instalar or ""),
            "camarasDesinstalar": str(ultima_ods.numero_camaras_desinstalar or ""),
            "camarasVigilar": str(ultima_ods.numero_camaras_vigilar or ""),
            "diasGrabacion": str(ultima_ods.dias_grabacion or ""),
            "diasMonitoreoDesde": ultima_ods.dias_monitoreo_desde or "",
            "diasMonitoreoHasta": ultima_ods.dias_monitoreo_hasta or "",
            "diasMonitoreoAdicional": ultima_ods.dias_monitoreo_adicional or "",
            "horarioMonitoreo": ultima_ods.horario_monitoreo or "",
            "observacion": ultima_ods.observacion or "",
            "materiales": ultima_ods.materiales or "",
            "consideraciones": ultima_ods.consideraciones or "",
            "aguaBano": ultima_ods.agua_bano or "",
            "requiereOC": ultima_ods.requiere_oc or "",
            "montosACobrar": ultima_ods.montos_a_cobrar or "",
        } if ultima_ods else {},
        "contactosEmergencia": [
            {
                "id": item.id,
                "nombre": item.nombre or "",
                "rut": item.rut or "",
                "telefono": item.telefono or "",
                "email": item.email or "",
            }
            for item in contactos
        ],
        "personasAutorizadas": [
            {
                "id": item.id,
                "nombre": item.nombre or "",
                "rut": item.rut or "",
                "telefono": item.telefono or "",
                "email": item.email or "",
                "claveVerde": item.clave_verde or "",
                "claveRoja": item.clave_roja or "",
            }
            for item in autorizados
        ],
        "guardias": [
            {
                "id": item.id,
                "nombre": item.nombre or "",
                "rut": item.rut or "",
                "telefono": item.telefono or "",
                "horarioDesde": item.horario_desde or "",
                "horarioHasta": item.horario_hasta or "",
            }
            for item in guardias
        ],
        "infoExtra": {
            "planCuadrante": info_extra.plan_cuadrante or "" if info_extra else "",
            "carabineros": info_extra.carabineros or "" if info_extra else "",
            "bomberos": info_extra.bomberos or "" if info_extra else "",
            "seguridadCiudadana": info_extra.seguridad_ciudadana or "" if info_extra else "",
            "codigoP2P": info_extra.codigo_p2p or "" if info_extra else "",
            "codigoDSS": info_extra.codigo_dss or "" if info_extra else "",
            "telefonoPorton": info_extra.telefono_porton or "" if info_extra else "",
            "telefonoRecepcion": info_extra.telefono_recepcion or "" if info_extra else "",
            "internetATC": info_extra.internet_atc or "" if info_extra else "",
        },
    })


def upsert_sucursal_info_extra(db: Session, sucursal_id: int, campo: str, valor: str) -> None:
    _CAMPOS_VALIDOS = {
        "planCuadrante": "plan_cuadrante",
        "carabineros": "carabineros",
        "bomberos": "bomberos",
        "seguridadCiudadana": "seguridad_ciudadana",
        "codigoP2P": "codigo_p2p",
        "codigoDSS": "codigo_dss",
        "telefonoPorton": "telefono_porton",
        "telefonoRecepcion": "telefono_recepcion",
        "internetATC": "internet_atc",
    }
    col = _CAMPOS_VALIDOS.get(campo)
    if not col:
        raise HTTPException(status_code=400, detail=f"Campo '{campo}' no válido.")
    record = db.query(SucursalInfoExtra).filter(SucursalInfoExtra.sucursal_id == sucursal_id).first()
    if record is None:
        record = SucursalInfoExtra(sucursal_id=sucursal_id)
        db.add(record)
    setattr(record, col, valor or None)
    db.commit()


def add_persona_registro(db: Session, payload) -> None:
    categoria = str(payload.categoria or "").strip().lower()
    sucursal = db.query(SucursalBBDD).filter(SucursalBBDD.id == payload.sucursalId).first()
    if not sucursal:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")

    if categoria == "contacto de emergencia":
        db.add(SucursalContactoEmergencia(
            sucursal_id=sucursal.id,
            nombre=_clean_text(payload.nombre),
            rut=normalize_rut(payload.rut or "") or None,
            telefono=_clean_text(payload.telefono),
            email=_clean_text(str(payload.email) if payload.email else ""),
        ))
    elif categoria == "persona autorizada":
        db.add(SucursalPersonaAutorizada(
            sucursal_id=sucursal.id,
            nombre=_clean_text(payload.nombre),
            rut=normalize_rut(payload.rut or "") or None,
            telefono=_clean_text(payload.telefono),
            email=_clean_text(str(payload.email) if payload.email else ""),
            clave_verde=_clean_text(payload.claveVerde),
            clave_roja=_clean_text(payload.claveRoja),
        ))
    elif categoria == "guardia":
        db.add(SucursalGuardia(
            sucursal_id=sucursal.id,
            nombre=_clean_text(payload.nombre),
            rut=normalize_rut(payload.rut or "") or None,
            telefono=_clean_text(payload.telefono),
            horario_desde=_clean_text(payload.horarioDesde),
            horario_hasta=_clean_text(payload.horarioHasta),
        ))
    else:
        raise HTTPException(status_code=400, detail="Categoria no reconocida.")

    db.commit()


def update_persona_campo(db: Session, payload) -> None:
    categoria = str(payload.categoria or "").strip().lower()
    campo = str(payload.campo or "").strip()
    nuevo_valor = str(payload.nuevoValor or "").strip()

    model = None
    allowed_fields: dict[str, str] = {}
    if categoria == "contacto de emergencia":
        model = SucursalContactoEmergencia
        allowed_fields = {"nombre": "nombre", "rut": "rut", "telefono": "telefono", "email": "email"}
    elif categoria == "persona autorizada":
        model = SucursalPersonaAutorizada
        allowed_fields = {
            "nombre": "nombre",
            "rut": "rut",
            "telefono": "telefono",
            "email": "email",
            "claveVerde": "clave_verde",
            "claveRoja": "clave_roja",
        }
    elif categoria == "guardia":
        model = SucursalGuardia
        allowed_fields = {
            "nombre": "nombre",
            "rut": "rut",
            "telefono": "telefono",
            "horarioDesde": "horario_desde",
            "horarioHasta": "horario_hasta",
        }
    else:
        raise HTTPException(status_code=400, detail="Categoria no reconocida.")

    record = db.query(model).filter(model.id == payload.registroId).first()
    if not record:
        raise HTTPException(status_code=404, detail="Registro no encontrado.")
    if campo not in allowed_fields:
        raise HTTPException(status_code=400, detail="Campo no permitido.")

    target_field = allowed_fields[campo]
    if campo == "rut":
        setattr(record, target_field, normalize_rut(nuevo_valor) or None)
    else:
        setattr(record, target_field, _clean_text(nuevo_valor))

    db.commit()


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


def _clean_text(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _split_lat_lng(latitud_longitud: str | None, latitud: str | None, longitud: str | None) -> tuple[str | None, str | None, str | None]:
    lat = _clean_text(latitud)
    lng = _clean_text(longitud)
    combined = _clean_text(latitud_longitud)

    if not (lat and lng) and combined and "," in combined:
        left, right = combined.split(",", 1)
        lat = lat or _clean_text(left)
        lng = lng or _clean_text(right)

    final_combined = f"{lat}, {lng}" if lat and lng else combined
    return lat, lng, final_combined


def create_sucursal(db: Session, payload: VentaSucursalCreateRequest, usuario_email: str) -> SucursalBBDD:
    rut = normalize_rut(payload.rut)
    if not rut:
        raise HTTPException(status_code=400, detail="RUT de empresa invalido.")

    nombre_empresa = _clean_text(payload.nombreEmpresa) or get_cliente_nombre_by_rut(db, rut)
    if not nombre_empresa:
        raise HTTPException(status_code=404, detail="No se encontro una razon social para el RUT indicado.")

    nombre_sucursal = str(payload.nombreSucursal).strip()
    direccion_sucursal = str(payload.direccionSucursal).strip()

    # Ojo: antes el duplicado se detectaba solo si rut+nombre+direccion coincidian
    # exactos. Si la direccion se tipeaba distinto en una ODS nueva para la misma
    # sucursal (mismo rut+nombre), el check no la pescaba y quedaba una fila
    # duplicada en bbdd_sucursales (pasó con "Fleischmann Chile S.A" y "Vigna
    # Ltda", jul 2026). Ahora basta que coincidan rut+nombre_sucursal.
    #
    # OJO 2: se evaluó bloquear tambien por rut+comuna+numero de calle (sin
    # exigir que el nombre coincida), pero se descartó: muchas empresas tienen
    # varias sucursales/dependencias reales que comparten la misma direccion
    # base (bodegas A3/B25 de un mismo galpon, dependencias de una
    # municipalidad, distintas alarmas de un mismo sitio) y ese chequeo las
    # bloqueaba como si fueran duplicados. Deduplicar esos casos requiere
    # revision humana, no un match automatico por direccion.
    existing = (
        db.query(SucursalBBDD.id)
        .filter(
            func.lower(func.trim(SucursalBBDD.rut)) == rut.lower(),
            func.lower(func.trim(SucursalBBDD.nombre_sucursal)) == nombre_sucursal.lower(),
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="La sucursal ya esta registrada para ese RUT.")

    contactos_validos = [
        c for c in payload.contactosEmergencia
        if any(_clean_text(v) for v in [c.nombre, c.rut, c.telefono, c.email])
    ]
    if not any(_clean_text(c.nombre) and _clean_text(c.rut) and _clean_text(c.telefono) and c.email for c in contactos_validos):
        raise HTTPException(status_code=400, detail="Debes completar al menos un contacto de emergencia con todos sus datos.")

    lat, lng, latlng = _split_lat_lng(payload.latitudLongitud, payload.latitud, payload.longitud)
    if not lat or not lng:
        raise HTTPException(status_code=400, detail="Debes ingresar latitud y longitud.")

    record = SucursalBBDD(
        rut=rut,
        nombre_empresa=nombre_empresa,
        nombre_sucursal=nombre_sucursal,
        direccion_sucursal=direccion_sucursal,
        region=str(payload.region).strip(),
        comuna=str(payload.comuna).strip(),
        referencia_ubicacion=_clean_text(payload.referenciaUbicacion),
        latitud=lat,
        longitud=lng,
        latitud_longitud=latlng,
        email_facturas=str(payload.emailFacturas).strip(),
        proveedor_internet=_clean_text(payload.proveedorInternet),
        proveedor_electricidad=_clean_text(payload.proveedorElectricidad),
        nro_proveedor_electricidad=_clean_text(payload.nroProveedorElectricidad),
        horario_apertura=_clean_text(payload.horarioApertura),
        horario_cierre=_clean_text(payload.horarioCierre),
        dias_funcionamiento=_clean_text(payload.diasFuncionamiento),
        created_by=_clean_text(usuario_email),
        # Nace pendiente de aceptación en Bitácora — un operador la revisa (por si es
        # un duplicado o le falta info) antes de que aparezca en las búsquedas/listados
        # de bitacora.py. Ver sección "Sucursales pendientes de aceptación".
        aceptada_bitacora=False,
    )
    db.add(record)
    db.flush()

    for contacto in contactos_validos:
        db.add(SucursalContactoEmergencia(
            sucursal_id=record.id,
            nombre=_clean_text(contacto.nombre),
            rut=normalize_rut(contacto.rut or "") or None,
            telefono=_clean_text(contacto.telefono),
            email=_clean_text(str(contacto.email) if contacto.email else ""),
        ))

    for persona in payload.personasAutorizadas:
        if not any(_clean_text(v) for v in [persona.nombre, persona.rut, persona.telefono, persona.email, persona.claveVerde, persona.claveRoja]):
            continue
        db.add(SucursalPersonaAutorizada(
            sucursal_id=record.id,
            nombre=_clean_text(persona.nombre),
            rut=normalize_rut(persona.rut or "") or None,
            telefono=_clean_text(persona.telefono),
            email=_clean_text(str(persona.email) if persona.email else ""),
            clave_verde=_clean_text(persona.claveVerde),
            clave_roja=_clean_text(persona.claveRoja),
        ))

    for guardia in payload.guardias:
        if not any(_clean_text(v) for v in [guardia.nombre, guardia.rut, guardia.telefono, guardia.horarioDesde, guardia.horarioHasta]):
            continue
        db.add(SucursalGuardia(
            sucursal_id=record.id,
            nombre=_clean_text(guardia.nombre),
            rut=normalize_rut(guardia.rut or "") or None,
            telefono=_clean_text(guardia.telefono),
            horario_desde=_clean_text(guardia.horarioDesde),
            horario_hasta=_clean_text(guardia.horarioHasta),
        ))

    db.commit()
    db.refresh(record)
    return record


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", str(value or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "archivo"


def _to_optional_int(value: str | None) -> int | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Valor numerico invalido: {cleaned}")


def _next_ods_code(db: Session, prefijo: str) -> str:
    existing_codes = [str(row[0] or "").strip() for row in db.query(VentaODS.codigo).all()]
    max_number = 0
    for code in existing_codes:
        match = re.search(r"(\d+)$", code)
        if not match:
            continue
        max_number = max(max_number, int(match.group(1)))
    return f"{prefijo}{max_number + 1:03d}"


def _decode_base64_payload(data: str | None) -> bytes:
    raw = str(data or "").strip()
    if not raw:
        return b""
    try:
        return base64.b64decode(raw)
    except Exception:
        try:
            if "," in raw:
                return base64.b64decode(raw.split(",", 1)[1])
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"No se pudo decodificar un archivo adjunto: {exc}") from exc
    return b""


def _save_ods_file(codigo: str, payload: VentaODSArchivoRequest | None) -> str | None:
    if not payload or not str(payload.data or "").strip():
        return None

    content = _decode_base64_payload(payload.data)
    if not content:
        return None

    folder = VENTA_UPLOADS_DIR / _safe_filename(codigo)
    folder.mkdir(parents=True, exist_ok=True)

    file_name = _safe_filename(payload.nombre or "archivo.bin")
    path = folder / file_name
    if path.exists():
        stem = path.stem or "archivo"
        suffix = path.suffix
        counter = 2
        while path.exists():
            path = folder / f"{stem} {counter}{suffix}"
            counter += 1
    path.write_bytes(content)
    return str(path.relative_to(_ATC_ROOT)).replace("\\", "/")


def _upsert_ods_archivo(
    db: Session,
    ods_id: int,
    codigo: str,
    tipo_documento: str,
    servicio: str | None,
    payload: VentaODSArchivoRequest | None,
) -> str | None:
    ruta = _save_ods_file(codigo, payload)
    if not ruta:
        return None

    existing = (
        db.query(VentaODSArchivo)
        .filter(
            VentaODSArchivo.ods_id == ods_id,
            func.lower(func.trim(VentaODSArchivo.tipo_documento)) == str(tipo_documento).strip().lower(),
            func.lower(func.trim(func.coalesce(VentaODSArchivo.servicio, ""))) == str(servicio or "").strip().lower(),
        )
        .first()
    )
    if existing:
        existing.nombre_archivo = _clean_text(payload.nombre if payload else "")
        existing.mime_type = _clean_text(payload.tipo if payload else "")
        existing.ruta_archivo = ruta
    else:
        db.add(VentaODSArchivo(
            ods_id=ods_id,
            codigo_ods=codigo,
            tipo_documento=_clean_text(tipo_documento),
            servicio=_clean_text(servicio),
            nombre_archivo=_clean_text(payload.nombre if payload else ""),
            mime_type=_clean_text(payload.tipo if payload else ""),
            ruta_archivo=ruta,
        ))
    return ruta


def _tipo_documento_base(value: str | None) -> str:
    cleaned = _clean_text(value) or "Archivo"
    cleaned = re.sub(r"\s+\d+$", "", cleaned).strip()
    return cleaned or "Archivo"


def _is_vista_camaras_archivo(tipo_documento: str | None) -> bool:
    normalized = _normalize_text(_tipo_documento_base(tipo_documento)).replace("_", " ")
    return normalized in {"vista camaras", "vista camara", "vistas camaras", "vistas camara"}


def _next_tipo_documento_label(db: Session, ods_id: int, tipo_documento: str) -> str:
    base = _tipo_documento_base(tipo_documento)
    existing = (
        db.query(VentaODSArchivo.tipo_documento)
        .filter(VentaODSArchivo.ods_id == ods_id)
        .all()
    )
    used_numbers: set[int] = set()
    for (raw_tipo,) in existing:
        raw = _clean_text(raw_tipo)
        if _tipo_documento_base(raw).lower() != base.lower():
            continue
        match = re.search(r"\s+(\d+)$", raw or "")
        used_numbers.add(int(match.group(1)) if match else 1)
    if not used_numbers:
        return base
    next_number = 2
    while next_number in used_numbers:
        next_number += 1
    return f"{base} {next_number}"


def _add_ods_archivo(
    db: Session,
    ods_id: int,
    codigo: str,
    tipo_documento: str,
    servicio: str | None,
    payload: VentaODSArchivoRequest | None,
) -> tuple[VentaODSArchivo | None, str | None]:
    ruta = _save_ods_file(codigo, payload)
    if not ruta:
        return None, None

    tipo_final = _next_tipo_documento_label(db, ods_id, tipo_documento)
    record = VentaODSArchivo(
        ods_id=ods_id,
        codigo_ods=codigo,
        tipo_documento=_clean_text(tipo_final),
        servicio=_clean_text(servicio),
        nombre_archivo=_clean_text(payload.nombre if payload else ""),
        mime_type=_clean_text(payload.tipo if payload else ""),
        ruta_archivo=ruta,
    )
    db.add(record)
    return record, ruta


_DRIVE_FILE_ID_RE = re.compile(r"/d/([a-zA-Z0-9_-]{10,})")
_DRIVE_FILE_ID_QS_RE = re.compile(r"[?&]id=([a-zA-Z0-9_-]{10,})")


def _extract_drive_file_id(raw: str) -> str | None:
    """Si ruta_archivo ya guarda un link de Drive/Docs/Sheets/Slides, extrae el file_id."""
    if not raw or "google.com" not in raw:
        return None
    m = _DRIVE_FILE_ID_RE.search(raw)
    if m:
        return m.group(1)
    m = _DRIVE_FILE_ID_QS_RE.search(raw)
    if m:
        return m.group(1)
    return None


def _local_ods_archivo_path(archivo: "VentaODSArchivo") -> Path | None:
    """Busca el archivo en disco. Devuelve None si no hay ruta o no existe (no lanza)."""
    raw = str(archivo.ruta_archivo or "").strip()
    if not raw:
        return None

    raw_values = [raw]
    parsed = urlparse(raw)
    if parsed.scheme and parsed.path:
        raw_values.append(parsed.path)
    decoded = unquote(raw)
    if decoded != raw:
        raw_values.append(decoded)

    candidates: list[Path] = []
    seen: set[str] = set()

    def add_candidate(candidate: Path) -> None:
        key = str(candidate)
        if key not in seen:
            candidates.append(candidate)
            seen.add(key)

    for value in raw_values:
        normalized = str(value or "").strip().replace("\\", "/")
        if not normalized:
            continue

        path = Path(normalized)
        if path.is_absolute():
            add_candidate(path)
            # La BD puede guardar URLs del tipo /uploads/..., pero los archivos
            # viven bajo ATC/uploads en disco.
            add_candidate(_ATC_ROOT / normalized.lstrip("/"))
            add_candidate(_ATC_ROOT.parent / normalized.lstrip("/"))
        else:
            add_candidate(_ATC_ROOT / normalized)
            add_candidate(_ATC_ROOT.parent / normalized)
            add_candidate(Path.cwd() / normalized)

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def resolve_ods_archivo_path(db: Session, archivo_id: int) -> tuple[Path, str]:
    """Resuelve un archivo de ODS solo en disco (compat). Usar resolve_ods_archivo para incluir Drive."""
    archivo = db.query(VentaODSArchivo).filter(VentaODSArchivo.id == archivo_id).first()
    if not archivo:
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    local = _local_ods_archivo_path(archivo)
    if local is None:
        raise HTTPException(status_code=404, detail="Archivo no encontrado en disco.")
    return local, _safe_filename(archivo.nombre_archivo or local.name)


def resolve_ods_archivo(db: Session, archivo_id: int) -> dict:
    """Resuelve el contenido de un archivo de ODS: primero en disco, si falta lo busca
    en la carpeta de Drive de la ODS correspondiente (misma estructura usada al subirlo).

    Devuelve un dict:
      {"mode": "local", "path": Path, "filename": str}
      {"mode": "drive", "content": bytes, "mime": str, "filename": str}
    """
    archivo = db.query(VentaODSArchivo).filter(VentaODSArchivo.id == archivo_id).first()
    if not archivo:
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")

    local = _local_ods_archivo_path(archivo)
    if local is not None:
        return {"mode": "local", "path": local, "filename": _safe_filename(archivo.nombre_archivo or local.name)}

    nombre_archivo = str(archivo.nombre_archivo or "").strip()
    raw_ruta = str(archivo.ruta_archivo or "").strip()

    # Caso mas comun hoy: ruta_archivo ya guarda directamente el link de Drive/Docs
    # con el que se subio el archivo (no una ruta local) — se extrae el file_id.
    file_id = _extract_drive_file_id(raw_ruta)

    # Fallback: buscar por nombre dentro de la carpeta de Drive de la ODS
    # (estructura usada por upload_ods_files_to_drive), por si ruta_archivo
    # quedo con una ruta local que ya no existe.
    if not file_id:
        ods = db.query(VentaODS).filter(VentaODS.id == archivo.ods_id).first()
        if ods is not None and nombre_archivo:
            try:
                file_id = find_ods_drive_file_id(
                    codigo=ods.codigo or archivo.codigo_ods or "",
                    rut=ods.rut_cliente or "",
                    razon_social=ods.razon_social or "",
                    servicio=archivo.servicio,
                    nombre_archivo=nombre_archivo,
                )
            except Exception:
                file_id = None

    if file_id:
        try:
            content, mime, filename = download_support_drive_file_bytes(file_id=file_id)
            return {
                "mode": "drive",
                "content": content,
                "mime": mime,
                "filename": _safe_filename(nombre_archivo or filename),
            }
        except Exception as exc:
            _log.warning("No se pudo descargar archivo ODS %s desde Drive: %s", archivo_id, exc)

    raise HTTPException(status_code=404, detail="Archivo no encontrado en disco ni en Drive.")


def _crear_cliente_piriod_para_ods(db: Session, codigo: str, rut: str, razon_social: str, direccion_sucursal: str) -> None:
    """Crea el cliente en Piriod para una ODS recien registrada y, si funciona,
    tilda solo el checkbox 'Creacion clientes Piriod' de Finanzas. Se llama
    desde el hilo en background de create_ods — cualquier falla queda solo
    logueada, sin bloquear ni afectar la ODS ya creada."""
    cliente = (
        db.query(ClienteBBDD)
        .filter(func.lower(func.trim(ClienteBBDD.rut)) == str(rut or "").strip().lower())
        .first()
    )
    email = getattr(cliente, "email_facturas", "") or ""
    if not email and direccion_sucursal:
        suc = (
            db.query(SucursalBBDD)
            .filter(func.lower(func.trim(SucursalBBDD.direccion_sucursal)) == str(direccion_sucursal).strip().lower())
            .first()
        )
        email = getattr(suc, "email_facturas", "") or ""

    try:
        resultado = piriod_crear_cliente(
            nombre=razon_social,
            email=email,
            direccion=direccion_sucursal,
            telefono=getattr(cliente, "telefono", "") or "",
            rut=rut,
            giro=getattr(cliente, "giro", "") or "",
        )
    except PiriodError as exc:
        _log.warning("Creacion automatica en Piriod fallo para ODS %s: %s", codigo, exc)
        return

    customer_id = str(resultado.get("id") or "").strip()
    ods = db.query(VentaODS).filter(VentaODS.codigo == codigo).first()
    if ods and customer_id:
        ods.piriod_customer_id = customer_id

    fin = _get_or_create_finanzas_row(db, codigo)
    fin.creacion_clientes_piriod = True
    fin.fecha_creacion_clientes_piriod = datetime.now(timezone.utc)
    db.commit()
    _log.info("Cliente Piriod creado automaticamente para ODS %s: %s", codigo, customer_id)


def _ods_requiere_montos(tipos: list[str]) -> bool:
    normalizados = {_normalize_text(item) for item in tipos}
    return bool(
        normalizados.intersection({
            "instalacion",
            "televigilancia",
            "alarma",
            "guardia",
            "servicio tecnico",
            "desinstalacion",
            "upgrade",
            "downgrade",
            "monitoreo adicional",
        })
    )


def create_ods(db: Session, payload: VentaODSCreateRequest, usuario_email: str) -> VentaODS:
    tipos = [str(item or "").strip() for item in payload.tipoServicio if str(item or "").strip()]
    if not tipos:
        raise HTTPException(status_code=400, detail="Debes seleccionar al menos un tipo de servicio.")

    rut = normalize_rut(payload.rutCliente)
    if not rut:
        raise HTTPException(status_code=400, detail="RUT cliente invalido.")

    razon_social = str(payload.razonSocial or "").strip()
    direccion_sucursal = str(payload.direccionSucursal or "").strip()
    if not razon_social or not direccion_sucursal:
        raise HTTPException(status_code=400, detail="Debes indicar razon social y direccion de sucursal.")

    if _ods_requiere_montos(tipos) and not _clean_text(payload.montosACobrar):
        raise HTTPException(status_code=400, detail="Los valores a cobrar son obligatorios.")

    tipos_normalizados = {_normalize_text(item) for item in tipos}
    prefijo = "S" if "servicio tecnico" in tipos_normalizados else "V"
    codigo = _next_ods_code(db, prefijo)

    cotizacion_path = _save_ods_file(codigo, payload.cotizacion)
    odc_path = _save_ods_file(codigo, payload.odc)
    desglose_path = _save_ods_file(codigo, payload.desglosePrecioArchivo)

    record = VentaODS(
        codigo=codigo,
        creado_por=_clean_text(usuario_email),
        rut_cliente=rut,
        razon_social=razon_social,
        direccion_sucursal=direccion_sucursal,
        nombre_sucursal=_clean_text(payload.nombreSucursal),
        tipo_cliente=_clean_text(payload.tipoCliente),
        tipo_servicio=" | ".join(tipos),
        tipo_plan=_clean_text(payload.tipoPlan),
        observacion=_clean_text(payload.observacion),
        numero_camaras_instalar=_to_optional_int(payload.numeroCamarasInstalar),
        numero_camaras_desinstalar=_to_optional_int(payload.numeroCamarasDesinstalar),
        numero_camaras_vigilar=_to_optional_int(payload.numeroCamarasVigilar),
        dias_grabacion=_to_optional_int(payload.diasGrabacion),
        dias_monitoreo_desde=_clean_text(payload.diasMonitoreoDesde),
        dias_monitoreo_hasta=_clean_text(payload.diasMonitoreoHasta),
        dias_monitoreo_adicional=_clean_text(payload.diasMonitoreoAdicional),
        horario_monitoreo=_clean_text(payload.horarioMonitoreo),
        materiales=_clean_text(payload.materiales),
        consideraciones=_clean_text(payload.consideraciones),
        agua_bano=_clean_text(payload.aguaBano),
        requiere_oc=_clean_text(payload.requiereOC),
        montos_a_cobrar=_clean_text(payload.montosACobrar),
        cotizacion_path=cotizacion_path,
        odc_path=odc_path,
        desglose_path=desglose_path,
        contrato_path=None,
    )
    db.add(record)
    db.flush()

    # Pre-crear registros de Ã¡rea con auto-completado segÃºn tipo_servicio.
    # Las Ã¡reas que NO aplican arrancan con sus flags en True (Terminado automÃ¡tico).
    areas = _calcular_areas_aplicables(tipos)
    db.add(ServicioTecnicoVentaODT(
        odt=codigo,
        recepcion_solicitud_instalacion=not areas["servtec"],
        instalacion_finalizada=not areas["servtec"],
        finalizado=not areas["servtec"],
    ))
    db.add(OperacionesVentaODT(
        odt=codigo,
        fecha_coordinacion=not areas["operaciones"],
        reunion_coordinacion=not areas["operaciones"],
        coord_apertura_puesto=not areas["operaciones"],
        coord_equipo=not areas["operaciones"],
    ))
    db.flush()

    archivos: list[VentaODSArchivoRequest] = []
    if payload.cotizacion and cotizacion_path:
        archivos.append(payload.cotizacion)
    if payload.odc and odc_path:
        archivos.append(payload.odc)
    if payload.desglosePrecioArchivo and desglose_path:
        archivos.append(payload.desglosePrecioArchivo)
    archivos.extend(payload.contratos or [])
    archivos.extend(payload.layouts or [])

    drive_files: list[dict] = []

    for archivo in archivos:
        ruta = None
        if archivo is payload.cotizacion:
            ruta = cotizacion_path
        elif archivo is payload.odc:
            ruta = odc_path
        elif archivo is payload.desglosePrecioArchivo:
            ruta = desglose_path
        else:
            ruta = _save_ods_file(codigo, archivo)
        if not ruta:
            continue
        tipo_doc = _clean_text(archivo.tipoDocumento) or ""
        db.add(VentaODSArchivo(
            ods_id=record.id,
            codigo_ods=codigo,
            tipo_documento=tipo_doc,
            servicio=_clean_text(archivo.servicio),
            nombre_archivo=_clean_text(archivo.nombre),
            mime_type=_clean_text(archivo.tipo),
            ruta_archivo=ruta,
        ))
        if tipo_doc.lower() == "contrato" and not record.contrato_path:
            record.contrato_path = ruta
        drive_files.append({
            "path": ruta,
            "nombre": _clean_text(archivo.nombre),
            "mime": _clean_text(archivo.tipo),
            "servicio": _clean_text(archivo.servicio) or "General",
        })

    db.commit()
    db.refresh(record)

    # Email y Drive en background — no bloquean la respuesta al usuario
    _codigo = record.codigo
    _record_id = record.id
    _drive_files = list(drive_files)
    _requiere_oc = str(record.requiere_oc or "").strip().lower() == "si"

    def _background():
        from ATC.app.core.db import SessionLocal
        bg_db = SessionLocal()
        try:
            notify_ods_registered(bg_db, _codigo)
        except Exception as exc:
            _log.warning("notify_ods_registered %s falló: %s", _codigo, exc)

        try:
            _crear_cliente_piriod_para_ods(bg_db, _codigo, rut, razon_social, direccion_sucursal)
        except Exception as exc:
            bg_db.rollback()
            _log.warning("Creacion cliente Piriod ODS %s falló: %s", _codigo, exc)

        if _requiere_oc:
            try:
                notify_oc_requerida(bg_db, _codigo)
            except Exception as exc:
                _log.warning("notify_oc_requerida %s falló: %s", _codigo, exc)

        if _drive_files:
            try:
                upload_ods_files_to_drive(
                    codigo=_codigo,
                    rut=rut,
                    razon_social=razon_social,
                    files=_drive_files,
                )
            except Exception as exc:
                _log.warning("Drive upload ODS %s falló: %s", _codigo, exc)
            try:
                folder_id = find_ods_drive_folder_id(_codigo, rut, razon_social)
                if folder_id:
                    bg_db.execute(
                        __import__("sqlalchemy").text(
                            "UPDATE venta_ods SET drive_folder_id=:fid, drive_folder_url=:url WHERE id=:id"
                        ),
                        {"fid": folder_id, "url": build_ods_folder_url(folder_id), "id": _record_id},
                    )
                    bg_db.commit()
            except Exception as exc:
                bg_db.rollback()
                _log.warning("drive_folder_url ODS %s falló: %s", _codigo, exc)
        bg_db.close()

    threading.Thread(target=_background, daemon=True).start()

    return record


def get_ods_codes(db: Session) -> list[dict]:
    # Mismo orden que las tablas (Finanzas/Operaciones): mas recientes primero.
    rows = db.query(VentaODS.codigo, VentaODS.nombre_sucursal).order_by(VentaODS.created_at.desc(), VentaODS.id.desc()).all()
    return [
        {"codigo": str(r[0]).strip(), "sucursal": str(r[1] or "").strip()}
        for r in rows if r and r[0]
    ]


def get_ods_detail(db: Session, codigo: str) -> dict[str, str]:
    record = (
        db.query(VentaODS)
        .filter(func.lower(func.trim(VentaODS.codigo)) == str(codigo or "").strip().lower())
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="ODS no encontrada.")

    archivos = [
        archivo
        for archivo in db.query(VentaODSArchivo).filter(VentaODSArchivo.ods_id == record.id).all()
        if not _is_vista_camaras_archivo(archivo.tipo_documento)
    ]
    cotizacion = record.cotizacion_path or ""
    layout = ""
    oc = record.odc_path or ""
    contrato = record.contrato_path or ""
    for archivo in archivos:
        tipo = str(archivo.tipo_documento or "").strip().lower()
        if tipo == "layout" and not layout:
            layout = archivo.ruta_archivo or ""
        if tipo == "contrato" and not contrato:
            contrato = archivo.ruta_archivo or ""

    return {
        "rut": record.rut_cliente or "",
        "razonSocial": record.razon_social or "",
        "direccionSucursal": record.direccion_sucursal or "",
        "nombreSucursal": record.nombre_sucursal or "",
        "observacion": record.observacion or "",
        "tipoCliente": record.tipo_cliente or "",
        "tipoServicio": record.tipo_servicio or "",
        "tipoPlan": record.tipo_plan or "",
        "aguaBano": record.agua_bano or "",
        "camInstalar": str(record.numero_camaras_instalar or ""),
        "camVigilar": str(record.numero_camaras_vigilar or ""),
        "camDesinstalar": str(record.numero_camaras_desinstalar or ""),
        "montoACobrar": record.montos_a_cobrar or "",
        "diasAdicional": record.dias_monitoreo_adicional or "",
        "horario": record.horario_monitoreo or "",
        "materiales": record.materiales or "",
        "consideraciones": record.consideraciones or "",
        "cotizacion": cotizacion,
        "layout": layout,
        "oc": oc,
        "contrato": contrato,
        "archivos": [
            {
                "id": archivo.id,
                "tipoDocumento": archivo.tipo_documento or "",
                "servicio": archivo.servicio or "",
                "nombreArchivo": archivo.nombre_archivo or "",
                "mimeType": archivo.mime_type or "",
                "rutaArchivo": archivo.ruta_archivo or "",
                "downloadUrl": f"/api/venta/ods/archivo/{archivo.id}",
                "createdAt": _fmt_date(getattr(archivo, "created_at", None)),
            }
            for archivo in sorted(archivos, key=lambda item: item.id or 0)
        ],
    }


ADMIN_ESTADO_FIELDS: dict[str, tuple[str, str | None]] = {
    "recepcion_info": ("recepcion_info", "fecha_recepcion_info"),
    "registro_alpha3": ("registro_alpha3", "fecha_registro_alpha3"),
    "registro_intranet": ("registro_intranet", "fecha_registro_intranet"),
    "envio_solicitud_instalacion": ("envio_solicitud_instalacion", "fecha_envio_solicitud_instalacion"),
    "envio_datos_facturacion": ("envio_datos_facturacion", "fecha_envio_datos_facturacion"),
    "envio_carta_bienvenida": ("envio_carta_bienvenida", "fecha_envio_carta_bienvenida"),
    "finalizado": ("finalizado", "fecha_cierre"),
}

FINANZAS_ESTADO_FIELDS: dict[str, tuple[str, str | None]] = {
    "recepcion_datos_facturacion": ("recepcion_datos_facturacion", "fecha_recepcion_datos_facturacion"),
    "creacion_clientes_piriod": ("creacion_clientes_piriod", "fecha_creacion_clientes_piriod"),
    "facturacion_instalacion": ("facturacion_instalacion", "fecha_facturacion_instalacion"),
    "facturacion_servicio": ("facturacion_servicio", "fecha_facturacion_servicio"),
    "finalizado": ("finalizado", "fecha_cierre"),
}

SERVICIO_TECNICO_VENTAS_ESTADO_FIELDS: dict[str, tuple[str, str | None]] = {
    "instalacion_finalizada": ("instalacion_finalizada", "fecha_instalacion_finalizada"),
    "finalizado": ("finalizado", "fecha_cierre"),
}

SERVICIO_TECNICO_VENTAS_VALOR_FIELDS = {
    "llamar_cliente",
    "solicitud_materiales",
    "fecha_inicio_instalacion",
    "fecha_fin_instalacion",
    "tecnico_a_cargo",
    "acompanante",
}


def _get_or_create_admin_row(db: Session, codigo: str) -> AdministracionODT:
    codigo_limpio = str(codigo or "").strip()
    row = (
        db.query(AdministracionODT)
        .filter(func.lower(func.trim(AdministracionODT.odt)) == codigo_limpio.lower())
        .first()
    )
    if row:
        return row
    row = AdministracionODT(odt=codigo_limpio)
    db.add(row)
    db.flush()
    return row


def _get_or_create_finanzas_row(db: Session, codigo: str) -> FinanzasODT:
    codigo_limpio = str(codigo or "").strip()
    row = (
        db.query(FinanzasODT)
        .filter(func.lower(func.trim(FinanzasODT.odt)) == codigo_limpio.lower())
        .first()
    )
    if row:
        return row
    row = FinanzasODT(odt=codigo_limpio)
    db.add(row)
    db.flush()
    return row


def _get_or_create_servicio_tecnico_venta_row(db: Session, codigo: str) -> ServicioTecnicoVentaODT:
    codigo_limpio = str(codigo or "").strip()
    row = (
        db.query(ServicioTecnicoVentaODT)
        .filter(func.lower(func.trim(ServicioTecnicoVentaODT.odt)) == codigo_limpio.lower())
        .first()
    )
    if row:
        return row
    row = ServicioTecnicoVentaODT(odt=codigo_limpio)
    db.add(row)
    db.flush()
    return row


_SENTINELS_SIN_DOCUMENTO = {"no requiere", "no aplica", "n/a", "na", "-"}


def _resolver_doc_url_ods(valor: str | None) -> str:
    """cotizacion_path/odc_path a veces son rutas relativas de /uploads, a
    veces links completos (Drive) y a veces texto libre tipo "No Requiere"
    cargado a mano — hay que distinguirlos antes de armar el link."""
    txt = str(valor or "").strip()
    if not txt or txt.lower() in _SENTINELS_SIN_DOCUMENTO:
        return ""
    if txt.lower().startswith("http://") or txt.lower().startswith("https://"):
        return txt
    return f"/{txt.lstrip('/')}"


def _fmt_date(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    return str(value)


def _fmt_date_only(value) -> str:
    """Como _fmt_date pero sin hora — para badges/pills chicos (ej. TERM.
    SOPORTE en tabla_operaciones_venta.html) donde "dd/mm/aaaa hh:mm"
    desborda el ancho de la pastilla (pedido explicito, ago 2026)."""
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    return str(value)


def _is_true(value) -> bool:
    return bool(value)


def _email_to_name_map(db: Session) -> dict[str, str]:
    rows = db.query(User.email, User.name).filter(User.email.isnot(None)).all()
    return {(r.email or "").strip().lower(): (r.name or "").strip() for r in rows if r.email}


def _resolve_ejecutivo(email_to_name: dict[str, str], email: str) -> str:
    key = (email or "").strip().lower()
    return email_to_name.get(key) or email or ""


def get_admin_ods_rows(db: Session) -> list[dict[str, object]]:
    email_to_name = _email_to_name_map(db)
    rows = (
        db.query(VentaODS, AdministracionODT)
        .outerjoin(AdministracionODT, func.lower(func.trim(AdministracionODT.odt)) == func.lower(func.trim(VentaODS.codigo)))
        .order_by(VentaODS.created_at.desc(), VentaODS.id.desc())
        .all()
    )
    out: list[dict[str, object]] = []
    for ods, adm in rows:
        estado_ods = str(ods.estado or "").strip()
        anulada = estado_ods.lower() == "anulada"
        carpeta = ods.drive_folder_url or ""
        out.append(
            {
                "codigo": ods.codigo or "",
                "fecha": _fmt_date(ods.created_at),
                "ejecutivo": _resolve_ejecutivo(email_to_name, ods.creado_por),
                "rutCliente": ods.rut_cliente or "",
                "razonSocial": ods.razon_social or "",
                "nombreSucursal": ods.nombre_sucursal or "",
                "direccionSucursal": ods.direccion_sucursal or "",
                "tipoServicio": ods.tipo_servicio or "",
                "tipoPlan": ods.tipo_plan or "",
                "carpeta": carpeta,
                "estados": {
                    "recepcion_info": _is_true(getattr(adm, "recepcion_info", False)),
                    "registro_alpha3": _is_true(getattr(adm, "registro_alpha3", False)),
                    "registro_intranet": _is_true(getattr(adm, "registro_intranet", False)),
                    "envio_solicitud_instalacion": _is_true(getattr(adm, "envio_solicitud_instalacion", False)),
                    "envio_datos_facturacion": _is_true(getattr(adm, "envio_datos_facturacion", False)),
                    "envio_carta_bienvenida": _is_true(getattr(adm, "envio_carta_bienvenida", False)),
                    "finalizado": _is_true(getattr(adm, "finalizado", False)),
                },
                "anulada": anulada,
            }
        )
    return out


def get_admin_ods_detail(db: Session, codigo: str) -> dict[str, str]:
    detail = get_ods_detail(db, codigo)
    return {
        "codigo": str(codigo or "").strip(),
        "observacion": detail.get("observacion") or "",
        "consideraciones": detail.get("consideraciones") or "",
        "camInstalar": detail.get("camInstalar") or "",
        "camVigilar": detail.get("camVigilar") or "",
        "camaras": detail.get("camInstalar") or detail.get("camVigilar") or "",
        "dias": detail.get("diasAdicional") or "",
        "diasAdicional": detail.get("diasAdicional") or "",
    }


def update_admin_ods_estado(db: Session, codigo: str, campo: str, valor: bool) -> dict[str, object]:
    codigo_limpio = str(codigo or "").strip()
    campo_limpio = str(campo or "").strip()
    if campo_limpio not in ADMIN_ESTADO_FIELDS:
        raise HTTPException(status_code=400, detail="Campo administrativo invalido.")

    ods = (
        db.query(VentaODS)
        .filter(func.lower(func.trim(VentaODS.codigo)) == codigo_limpio.lower())
        .first()
    )
    if not ods:
        raise HTTPException(status_code=404, detail="ODS no encontrada.")
    if str(ods.estado or "").strip().lower() == "anulada":
        raise HTTPException(status_code=400, detail="La ODS esta anulada.")

    row = _get_or_create_admin_row(db, codigo_limpio)
    bool_field, date_field = ADMIN_ESTADO_FIELDS[campo_limpio]
    previous_value = _is_true(getattr(row, bool_field, False))
    now = datetime.now(timezone.utc)
    setattr(row, bool_field, bool(valor))
    if date_field:
        setattr(row, date_field, now if valor else None)
    if campo_limpio == "envio_solicitud_instalacion":
        st_row = _get_or_create_servicio_tecnico_venta_row(db, codigo_limpio)
        st_row.recepcion_solicitud_instalacion = bool(valor)
        st_row.fecha_recepcion_solicitud_instalacion = now if valor else None
    db.commit()
    if campo_limpio == "recepcion_info" and bool(valor) and not previous_value:
        _codigo_bg = codigo_limpio
        def _bg_admin():
            from ATC.app.core.db import SessionLocal
            _db = SessionLocal()
            try:
                notify_recepcion_administracion_cliente(_db, _codigo_bg)
            except Exception as exc:
                _log.warning("notify_recepcion_administracion_cliente %s falló: %s", _codigo_bg, exc)
            finally:
                _db.close()
        threading.Thread(target=_bg_admin, daemon=True).start()
    return {
        "ok": True,
        "codigo": codigo_limpio,
        "campo": campo_limpio,
        "valor": bool(valor),
        "estado": "Completado" if valor else "Pendiente",
        "timestamp": _fmt_date(now) if valor else "",
        "notificacion": "correo_enviado" if (campo_limpio == "recepcion_info" and bool(valor) and not previous_value) else "",
        "email_sent": False, "email_to": [], "email_error": "",
    }


def _monto_es_sin_cobro(texto: str) -> bool:
    """True si TODOS los valores del campo montos_a_cobrar indican que no hay
    nada que cobrar ("0", "$0", "-", "sin costo", "sin cobro", "no aplica").
    Vacio o montos por definir ("a determinar por Gerencia") devuelven False:
    esas ODS deben seguir visibles en Finanzas."""
    t = str(texto or "").strip()
    if not t:
        return False
    valores: list[str] = []
    for linea in t.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        valores.append(linea.split(":", 1)[1].strip() if ":" in linea else linea)
    if not valores:
        return False
    for val in valores:
        low = val.casefold()
        sin_cobro = ("sin cobro" in low) or ("sin costo" in low) or ("no aplica" in low)
        cero = low in {"-", "0", "$0"} or re.fullmatch(r"[\$\s]*0([.,]0+)?\s*(\+?\s*iva)?", low) is not None
        if not (sin_cobro or cero):
            return False
    return True


def get_finanzas_ods_rows(db: Session) -> dict[str, object]:
    email_to_name = _email_to_name_map(db)
    rows = (
        db.query(VentaODS, FinanzasODT)
        .outerjoin(FinanzasODT, func.lower(func.trim(FinanzasODT.odt)) == func.lower(func.trim(VentaODS.codigo)))
        .order_by(VentaODS.created_at.desc(), VentaODS.id.desc())
        .all()
    )
    out: list[dict[str, object]] = []
    total_anuladas = 0
    dirty = False
    for ods, fin in rows:
        # ODS sin nada que cobrar no aparecen en la tabla de Finanzas.
        if _monto_es_sin_cobro(ods.montos_a_cobrar):
            continue
        estado_ods = str(ods.estado or "").strip()
        anulada = estado_ods.lower() == "anulada"
        if anulada:
            total_anuladas += 1

        carpeta_url = (ods.drive_folder_url or "").strip()
        if not carpeta_url:
            folder_id = find_ods_drive_folder_id(ods.codigo or "", ods.rut_cliente or "", ods.razon_social or "")
            if folder_id:
                carpeta_url = build_ods_folder_url(folder_id)
                ods.drive_folder_id = folder_id
                ods.drive_folder_url = carpeta_url
                dirty = True

        out.append(
            {
                "codigo": ods.codigo or "",
                "fecha": _fmt_date(ods.created_at),
                "ejecutivo": _resolve_ejecutivo(email_to_name, ods.creado_por),
                "rutCliente": ods.rut_cliente or "",
                "razonSocial": ods.razon_social or "",
                "nombreSucursal": ods.nombre_sucursal or "",
                "direccionSucursal": ods.direccion_sucursal or "",
                "tipoServicio": ods.tipo_servicio or "",
                "tipoPlan": ods.tipo_plan or "",
                "carpeta": carpeta_url,
                "cotizacionUrl": _resolver_doc_url_ods(ods.cotizacion_path),
                "odcUrl": _resolver_doc_url_ods(ods.odc_path),
                "fechaInicioServicio": getattr(fin, "fecha_inicio_servicio", "") if fin else "",
                "estados": {
                    "recepcion_datos_facturacion": _is_true(getattr(fin, "recepcion_datos_facturacion", False)),
                    "creacion_clientes_piriod": _is_true(getattr(fin, "creacion_clientes_piriod", False)),
                    "facturacion_instalacion": _is_true(getattr(fin, "facturacion_instalacion", False)),
                    "facturacion_servicio": _is_true(getattr(fin, "facturacion_servicio", False)),
                    "finalizado": _is_true(getattr(fin, "finalizado", False)),
                },
                "anulada": anulada,
            }
        )
    if dirty:
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            _log.warning("No se pudo persistir drive_folder_url cacheado: %s", exc)
    return {"rows": out, "totalAnuladas": total_anuladas}


def get_finanzas_ods_pendiente_count(db: Session) -> int:
    """Cantidad de ODS pendientes en la tabla de Finanzas (con algo que
    cobrar, ni anulada ni finalizada) — para el badge del panel de Finanzas
    (venta_finanzas_panel_page). A diferencia de get_finanzas_ods_rows, NO
    resuelve la carpeta de Drive de cada ODS: esa resolución hace hasta 2
    llamadas síncronas a la API de Drive POR CADA ODS sin drive_folder_url
    todavía cacheada (find_ods_drive_folder_id) — con varias decenas de ODS
    sin cache, el panel de Finanzas completo tardaba 15+ segundos en abrir
    solo para mostrar un número en un badge. Pedido explicito, ago 2026."""
    rows = (
        db.query(VentaODS.estado, VentaODS.montos_a_cobrar, FinanzasODT.finalizado)
        .outerjoin(FinanzasODT, func.lower(func.trim(FinanzasODT.odt)) == func.lower(func.trim(VentaODS.codigo)))
        .all()
    )
    pendientes = 0
    for estado, montos_a_cobrar, finalizado in rows:
        if _monto_es_sin_cobro(montos_a_cobrar):
            continue
        if str(estado or "").strip().lower() == "anulada":
            continue
        if _is_true(finalizado):
            continue
        pendientes += 1
    return pendientes


def get_finanzas_ods_detail(db: Session, codigo: str) -> dict[str, str]:
    detail = get_ods_detail(db, codigo)
    comuna = ""
    direccion = detail.get("direccionSucursal") or ""
    if direccion:
        suc = (
            db.query(SucursalBBDD)
            .filter(func.lower(func.trim(SucursalBBDD.direccion_sucursal)) == direccion.strip().lower())
            .first()
        )
        comuna = getattr(suc, "comuna", "") or ""
    return {
        "comuna": comuna,
        "numeroCamaras": detail.get("camInstalar") or "",
        "camarasVigilar": detail.get("camVigilar") or "",
        "montosACobrar": detail.get("montoACobrar") or "",
        "observacion": detail.get("observacion") or "",
        "requiereOC": detail.get("oc") or "",
    }


def get_finanzas_ods_facturacion(db: Session, codigo: str) -> dict[str, str]:
    ods = (
        db.query(VentaODS)
        .filter(func.lower(func.trim(VentaODS.codigo)) == str(codigo or "").strip().lower())
        .first()
    )
    if not ods:
        raise HTTPException(status_code=404, detail="ODS no encontrada.")

    cliente = (
        db.query(ClienteBBDD)
        .filter(func.lower(func.trim(ClienteBBDD.rut)) == str(ods.rut_cliente or "").strip().lower())
        .first()
        if ods.rut_cliente
        else None
    )

    email_facturas_sucursal = ""
    if ods.direccion_sucursal:
        suc = (
            db.query(SucursalBBDD)
            .filter(func.lower(func.trim(SucursalBBDD.direccion_sucursal)) == str(ods.direccion_sucursal).strip().lower())
            .first()
        )
        email_facturas_sucursal = getattr(suc, "email_facturas", "") or ""

    return {
        "razonSocial": ods.razon_social or "",
        "rutCliente": ods.rut_cliente or "",
        "nombreSucursal": ods.nombre_sucursal or "",
        "direccionSucursal": ods.direccion_sucursal or "",
        "giro": getattr(cliente, "giro", "") or "",
        "emailFacturas": email_facturas_sucursal or getattr(cliente, "email_facturas", "") or "",
        "nombreRepresentante": getattr(cliente, "nombre_representante", "") or "",
        "rutRepresentante": getattr(cliente, "rut_representante", "") or "",
        "telefono": getattr(cliente, "telefono", "") or "",
        "emailRepresentante": getattr(cliente, "email_representante", "") or "",
    }


def update_finanzas_ods_estado(db: Session, codigo: str, campo: str, valor: bool) -> dict[str, object]:
    codigo_limpio = str(codigo or "").strip()
    campo_limpio = str(campo or "").strip()
    if campo_limpio not in FINANZAS_ESTADO_FIELDS:
        raise HTTPException(status_code=400, detail="Campo finanzas invalido.")

    ods = (
        db.query(VentaODS)
        .filter(func.lower(func.trim(VentaODS.codigo)) == codigo_limpio.lower())
        .first()
    )
    if not ods:
        raise HTTPException(status_code=404, detail="ODS no encontrada.")
    if str(ods.estado or "").strip().lower() == "anulada":
        raise HTTPException(status_code=400, detail="La ODS esta anulada.")

    row = _get_or_create_finanzas_row(db, codigo_limpio)
    bool_field, date_field = FINANZAS_ESTADO_FIELDS[campo_limpio]
    now = datetime.now(timezone.utc)
    setattr(row, bool_field, bool(valor))
    if date_field:
        setattr(row, date_field, now if valor else None)
    db.commit()
    return {"ok": True, "codigo": codigo_limpio, "campo": campo_limpio, "valor": bool(valor), "estado": "Completado" if valor else "Pendiente", "timestamp": _fmt_date(now) if valor else ""}


def get_servicio_tecnico_ventas_rows(db: Session) -> list[dict[str, object]]:
    email_to_name = _email_to_name_map(db)
    rows = (
        db.query(VentaODS, ServicioTecnicoVentaODT, AdministracionODT, Registro)
        .outerjoin(
            ServicioTecnicoVentaODT,
            func.lower(func.trim(ServicioTecnicoVentaODT.odt)) == func.lower(func.trim(VentaODS.codigo)),
        )
        .outerjoin(
            AdministracionODT,
            func.lower(func.trim(AdministracionODT.odt)) == func.lower(func.trim(VentaODS.codigo)),
        )
        .outerjoin(
            Registro,
            func.lower(func.trim(Registro.odt)) == func.lower(func.trim(VentaODS.codigo)),
        )
        .order_by(VentaODS.created_at.desc(), VentaODS.id.desc())
        .all()
    )
    out: list[dict[str, object]] = []
    for ods, st, adm, registro in rows:
        tipo_servicio = str(ods.tipo_servicio or "")
        tipos_lista = [t.strip() for t in tipo_servicio.split("|") if t.strip()]
        if not _calcular_areas_aplicables(tipos_lista)["servtec"]:
            continue
        anulada = str(ods.estado or "").strip().lower() == "anulada"
        envio_instalacion_ok = _is_true(getattr(adm, "envio_solicitud_instalacion", False)) if adm else False
        recepcion_ts = (
            getattr(adm, "fecha_envio_solicitud_instalacion", None)
            or (getattr(st, "fecha_recepcion_solicitud_instalacion", None) if st else None)
        )
        if not envio_instalacion_ok:
            continue
        registro_estado = _normalize_text(getattr(registro, "estado", "") if registro else "")
        registro_derivacion = _normalize_text(getattr(registro, "derivacion", "") if registro else "")
        registro_finalizado = (
            registro_estado.startswith("termin")
            or registro_estado.startswith("final")
            or "finalizado" in registro_derivacion
            or bool(getattr(registro, "fecha_cierre", None) if registro else None)
        )
        instalacion_finalizada = (
            _is_true(getattr(st, "instalacion_finalizada", False))
            or _is_true(getattr(st, "finalizado", False))
            or registro_finalizado
        )
        out.append(
            {
                "codigo": ods.codigo or "",
                "fecha": _fmt_date(ods.created_at),
                "ejecutivo": _resolve_ejecutivo(email_to_name, ods.creado_por),
                "rutCliente": ods.rut_cliente or "",
                "razonSocial": ods.razon_social or "",
                "nombreSucursal": ods.nombre_sucursal or "",
                "direccionSucursal": ods.direccion_sucursal or "",
                "tipoServicio": ods.tipo_servicio or "",
                "materialesBase": ods.materiales or "",
                "odt": ods.codigo or "",
                "anulada": anulada,
                "estados": {
                    "recepcion_solicitud_instalacion": envio_instalacion_ok,
                    "instalacion_finalizada": instalacion_finalizada,
                    "finalizado": _is_true(getattr(st, "finalizado", False)),
                },
                "valores": {
                    "recepcion_solicitud_instalacion": _fmt_date(recepcion_ts),
                    "llamar_cliente": getattr(st, "llamar_cliente", "") if st else "",
                    "solicitud_materiales": getattr(st, "solicitud_materiales", "") if st else "",
                    "fecha_inicio_instalacion": getattr(st, "fecha_inicio_instalacion", "") if st else "",
                    "fecha_fin_instalacion": getattr(st, "fecha_fin_instalacion", "") if st else "",
                    "tecnico_a_cargo": getattr(st, "tecnico_a_cargo", "") if st else "",
                    "acompanante": getattr(st, "acompanante", "") if st else "",
                    "layout_final": getattr(st, "layout_final", "") if st else "",
                },
            }
        )
    return out


def get_servicio_tecnico_ventas_detail(db: Session, codigo: str) -> dict[str, str]:
    detail = get_ods_detail(db, codigo)
    return {
        "sucursal": detail.get("nombreSucursal") or detail.get("razonSocial") or "",
        "direccion": detail.get("direccionSucursal") or "",
        "observacion": detail.get("observacion") or "",
        "consideraciones": detail.get("consideraciones") or "",
        "camaras": detail.get("camInstalar") or detail.get("camVigilar") or "",
        "dias": detail.get("diasAdicional") or "",
        "layout": detail.get("layout") or "",
        "materiales": detail.get("materiales") or "",
    }


def get_servicio_tecnico_ventas_contacto(db: Session, direccion: str) -> dict[str, str]:
    direccion_limpia = str(direccion or "").strip()
    if not direccion_limpia:
        return {}
    sucursal = (
        db.query(SucursalBBDD)
        .filter(func.lower(func.trim(SucursalBBDD.direccion_sucursal)) == direccion_limpia.lower())
        .first()
    )
    if not sucursal:
        return {}
    contacto = (
        db.query(SucursalContactoEmergencia)
        .filter(SucursalContactoEmergencia.sucursal_id == sucursal.id)
        .order_by(SucursalContactoEmergencia.id.asc())
        .first()
    )
    if not contacto:
        return {}
    return {
        "nombre": contacto.nombre or "",
        "telefono": contacto.telefono or "",
        "correo": contacto.email or "",
    }


def update_servicio_tecnico_ventas_estado(db: Session, codigo: str, campo: str, valor: bool) -> dict[str, object]:
    codigo_limpio = str(codigo or "").strip()
    campo_limpio = str(campo or "").strip()
    if campo_limpio not in SERVICIO_TECNICO_VENTAS_ESTADO_FIELDS:
        raise HTTPException(status_code=400, detail="Campo de servicio tecnico invalido.")
    ods = (
        db.query(VentaODS)
        .filter(func.lower(func.trim(VentaODS.codigo)) == codigo_limpio.lower())
        .first()
    )
    if not ods:
        raise HTTPException(status_code=404, detail="ODS no encontrada.")
    if str(ods.estado or "").strip().lower() == "anulada":
        raise HTTPException(status_code=400, detail="La ODS esta anulada.")

    row = _get_or_create_servicio_tecnico_venta_row(db, codigo_limpio)
    bool_field, date_field = SERVICIO_TECNICO_VENTAS_ESTADO_FIELDS[campo_limpio]
    if campo_limpio == "finalizado" and bool(valor) and not _is_true(getattr(row, "instalacion_finalizada", False)):
        raise HTTPException(
            status_code=400,
            detail="Primero debe estar en Finalizado la columna Instalación Finalizada.",
        )
    previous_value = _is_true(getattr(row, bool_field, False))
    now = datetime.now(timezone.utc)
    setattr(row, bool_field, bool(valor))
    if date_field:
        setattr(row, date_field, now if valor else None)
    db.commit()
    if campo_limpio == "finalizado" and bool(valor) and not previous_value:
        _codigo_bg = codigo_limpio
        def _bg_inst():
            from ATC.app.core.db import SessionLocal
            _db = SessionLocal()
            try:
                notify_instalacion_finalizada(_db, _codigo_bg)
            except Exception as exc:
                _log.warning("notify_instalacion_finalizada %s falló: %s", _codigo_bg, exc)
            finally:
                _db.close()
        threading.Thread(target=_bg_inst, daemon=True).start()

        # Avisamos al comercial que la instalación en terreno ya quedó lista, sin
        # importar si el tipo de servicio requiere Soporte Técnico después: Servicio
        # Técnico instala primero, y solo entonces Soporte configura — por eso este
        # aviso siempre va antes que el de notify_servicio_operativo (que se manda
        # cuando Soporte marca su propio "Terminado", si aplica para esa ODS).
        def _bg_instalacion_terreno():
            from ATC.app.core.db import SessionLocal
            _db = SessionLocal()
            try:
                notify_instalacion_terreno_finalizada(_db, _codigo_bg)
            except Exception as exc:
                _log.warning("notify_instalacion_terreno_finalizada %s falló: %s", _codigo_bg, exc)
            finally:
                _db.close()
        threading.Thread(target=_bg_instalacion_terreno, daemon=True).start()
    return {
        "ok": True,
        "codigo": codigo_limpio,
        "campo": campo_limpio,
        "valor": bool(valor),
        "timestamp": _fmt_date(now) if valor else "",
        "email_sent": False, "email_to": [], "email_error": "",
    }


def update_servicio_tecnico_ventas_valor(db: Session, codigo: str, campo: str, valor: str | None) -> dict[str, object]:
    codigo_limpio = str(codigo or "").strip()
    campo_limpio = str(campo or "").strip()
    if campo_limpio not in SERVICIO_TECNICO_VENTAS_VALOR_FIELDS:
        raise HTTPException(status_code=400, detail="Campo de servicio tecnico invalido.")
    ods = (
        db.query(VentaODS)
        .filter(func.lower(func.trim(VentaODS.codigo)) == codigo_limpio.lower())
        .first()
    )
    if not ods:
        raise HTTPException(status_code=404, detail="ODS no encontrada.")
    if str(ods.estado or "").strip().lower() == "anulada":
        raise HTTPException(status_code=400, detail="La ODS esta anulada.")

    row = _get_or_create_servicio_tecnico_venta_row(db, codigo_limpio)
    valor_limpio = str(valor or "").strip()
    previous_value = str(getattr(row, campo_limpio, "") or "").strip()
    fechas_completas_antes = bool(
        str(getattr(row, "fecha_inicio_instalacion", "") or "").strip()
        and str(getattr(row, "fecha_fin_instalacion", "") or "").strip()
    )
    setattr(row, campo_limpio, valor_limpio)
    if campo_limpio == "solicitud_materiales" and valor_limpio != previous_value:
        ods.materiales = valor_limpio
    db.commit()

    fecha_inicio_final = str(getattr(row, "fecha_inicio_instalacion", "") or "").strip()
    fecha_fin_final = str(getattr(row, "fecha_fin_instalacion", "") or "").strip()
    fechas_completas_ahora = bool(fecha_inicio_final and fecha_fin_final)
    avisar_fechas_instalacion = (
        campo_limpio in {"fecha_inicio_instalacion", "fecha_fin_instalacion"}
        and fechas_completas_ahora
        and not fechas_completas_antes
    )

    if campo_limpio == "solicitud_materiales" and valor_limpio and valor_limpio != previous_value:
        _codigo_bg = codigo_limpio
        def _bg_mat():
            from ATC.app.core.db import SessionLocal
            _db = SessionLocal()
            try:
                notify_materiales_bodega(_db, _codigo_bg)
            except Exception as exc:
                _log.warning("notify_materiales_bodega %s falló: %s", _codigo_bg, exc)
            finally:
                _db.close()
        threading.Thread(target=_bg_mat, daemon=True).start()

    if avisar_fechas_instalacion:
        _codigo_bg = codigo_limpio
        _fecha_inicio_bg = fecha_inicio_final
        _fecha_fin_bg = fecha_fin_final
        def _bg_fechas():
            from ATC.app.core.db import SessionLocal
            _db = SessionLocal()
            try:
                notify_fechas_instalacion_definidas(_db, _codigo_bg, _fecha_inicio_bg, _fecha_fin_bg)
            except Exception as exc:
                _log.warning("notify_fechas_instalacion_definidas %s falló: %s", _codigo_bg, exc)
            finally:
                _db.close()
        threading.Thread(target=_bg_fechas, daemon=True).start()
    return {
        "ok": True,
        "codigo": codigo_limpio,
        "campo": campo_limpio,
        "valor": valor_limpio,
        "email_sent": False, "email_to": [], "email_error": "",
    }


def update_servicio_tecnico_layout_final(db: Session, codigo: str, nombre: str, data: str) -> dict[str, object]:
    codigo_limpio = str(codigo or "").strip()
    ods = (
        db.query(VentaODS)
        .filter(func.lower(func.trim(VentaODS.codigo)) == codigo_limpio.lower())
        .first()
    )
    if not ods:
        raise HTTPException(status_code=404, detail="ODS no encontrada.")
    if str(ods.estado or "").strip().lower() == "anulada":
        raise HTTPException(status_code=400, detail="La ODS esta anulada.")

    from ATC.app.schemas.venta import VentaODSArchivoRequest
    archivo = VentaODSArchivoRequest(nombre=nombre, data=data)
    folder = VENTA_UPLOADS_DIR / _safe_filename(codigo_limpio) / "layout_final"
    folder.mkdir(parents=True, exist_ok=True)
    content = _decode_base64_payload(archivo.data)
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacío o inválido.")
    file_name = _safe_filename(archivo.nombre or "layout_final.pdf")
    path = folder / file_name
    if path.exists():
        stem = path.stem or "layout_final"
        suffix = path.suffix
        counter = 2
        while path.exists():
            path = folder / f"{stem}_{counter}{suffix}"
            counter += 1
    path.write_bytes(content)
    ruta_rel = f"uploads/venta_ods/{_safe_filename(codigo_limpio)}/layout_final/{path.name}"

    row = _get_or_create_servicio_tecnico_venta_row(db, codigo_limpio)
    row.layout_final = ruta_rel
    db.commit()
    return {"ok": True, "codigo": codigo_limpio, "url": f"/{ruta_rel}"}


def update_ods(db: Session, payload, usuario_email: str) -> VentaODS:
    codigo = str(payload.selectorODS or "").strip()
    record = (
        db.query(VentaODS)
        .filter(func.lower(func.trim(VentaODS.codigo)) == codigo.lower())
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="ODS no encontrada.")

    tipos = [item.strip() for item in str(payload.tipoServicio or "").split(",") if item.strip()]

    record.rut_cliente = normalize_rut(payload.rut or record.rut_cliente)
    record.razon_social = str(payload.razonSocial or record.razon_social or "").strip()
    record.direccion_sucursal = str(payload.direccionSucursal or record.direccion_sucursal or "").strip()
    record.nombre_sucursal = _clean_text(payload.nombreSucursal)
    record.tipo_plan = _clean_text(payload.tipoPlan)
    record.agua_bano = _clean_text(getattr(payload, "aguaBano", None))
    record.tipo_cliente = _clean_text(payload.tipoCliente)
    record.numero_camaras_instalar = _to_optional_int(payload.camInstalar)
    record.numero_camaras_vigilar = _to_optional_int(payload.camVigilar)
    record.numero_camaras_desinstalar = _to_optional_int(getattr(payload, "camDesinstalar", None))
    record.montos_a_cobrar = _clean_text(payload.montoACobrar)
    record.dias_monitoreo_adicional = _clean_text(payload.diasAdicional)
    record.horario_monitoreo = _clean_text(payload.horario)
    previous_materiales = str(record.materiales or "").strip()
    record.materiales = _clean_text(payload.materiales)
    nuevo_materiales = str(record.materiales or "").strip()
    record.consideraciones = _clean_text(payload.consideraciones)
    record.observacion = _clean_text(payload.observacion)

    if nuevo_materiales and nuevo_materiales != previous_materiales:
        st_row_mat = _get_or_create_servicio_tecnico_venta_row(db, record.codigo)
        st_row_mat.solicitud_materiales = nuevo_materiales

    tipos_anteriores = [item.strip() for item in str(record.tipo_servicio or "").split("|") if item.strip()]
    areas_antes = _calcular_areas_aplicables(tipos_anteriores)
    areas_despues = _calcular_areas_aplicables(tipos)
    record.tipo_servicio = " | ".join(tipos) if tipos else ""
    record.creado_por = _clean_text(usuario_email) or record.creado_por

    # Si al editar el tipo de servicio un area que antes no aplicaba (y por eso
    # arranco auto-marcada "Terminado" al crear la ODT, ver _calcular_areas_aplicables
    # en la creacion) ahora si aplica, hay que sacarle el auto-completado — si no,
    # queda mostrando "Finalizado" sin que nadie haya hecho el trabajo real.
    if areas_despues["servtec"] and not areas_antes["servtec"]:
        st_row = _get_or_create_servicio_tecnico_venta_row(db, record.codigo)
        st_row.recepcion_solicitud_instalacion = False
        st_row.instalacion_finalizada = False
        st_row.finalizado = False
    if areas_despues["operaciones"] and not areas_antes["operaciones"]:
        op_row = _get_or_create_operaciones_row(db, record.codigo)
        op_row.fecha_coordinacion = False
        op_row.reunion_coordinacion = False
        op_row.coord_apertura_puesto = False
        op_row.coord_equipo = False

    archivos_nuevos: list[tuple[str, str, VentaODSArchivoRequest]] = []
    if payload.cotizacion:
        archivos_nuevos.append(("Cotizacion", "General", payload.cotizacion))
    if payload.layout:
        archivos_nuevos.append(("Layout", "Instalacion", payload.layout))
    if payload.oc:
        archivos_nuevos.append(("ODC", "Instalacion", payload.oc))
    for archivo in getattr(payload, "archivos", []) or []:
        tipo_doc = _clean_text(archivo.tipoDocumento) or "Archivo"
        servicio = _clean_text(archivo.servicio) or "General"
        archivos_nuevos.append((tipo_doc, servicio, archivo))

    drive_files: list[dict] = []
    for tipo_doc, servicio, archivo in archivos_nuevos:
        archivo_record, ruta = _add_ods_archivo(db, record.id, record.codigo, tipo_doc, servicio, archivo)
        if not ruta:
            continue
        tipo_base = _tipo_documento_base(tipo_doc).lower()
        if tipo_base == "cotizacion" and not record.cotizacion_path:
            record.cotizacion_path = ruta
        elif tipo_base == "odc" and not record.odc_path:
            record.odc_path = ruta
        elif tipo_base == "contrato" and not record.contrato_path:
            record.contrato_path = ruta
        drive_files.append({
            "path": ruta,
            "nombre": _clean_text(archivo.nombre) or (archivo_record.nombre_archivo if archivo_record else ""),
            "mime": _clean_text(archivo.tipo),
            "servicio": servicio or "General",
        })

    db.commit()
    db.refresh(record)

    if nuevo_materiales and nuevo_materiales != previous_materiales:
        _codigo_bg = record.codigo
        def _bg_mat_ods():
            from ATC.app.core.db import SessionLocal
            _db = SessionLocal()
            try:
                notify_materiales_bodega(_db, _codigo_bg)
            except Exception as exc:
                _log.warning("notify_materiales_bodega %s falló (edición ODS): %s", _codigo_bg, exc)
            finally:
                _db.close()
        threading.Thread(target=_bg_mat_ods, daemon=True).start()

    if drive_files:
        _codigo_u = record.codigo
        _rut_u = record.rut_cliente or ""
        _rs_u = record.razon_social or ""
        _record_id_u = record.id
        _drive_files_u = list(drive_files)
        def _bg_update_drive():
            from ATC.app.core.db import SessionLocal
            import sqlalchemy
            _db = SessionLocal()
            try:
                upload_ods_files_to_drive(codigo=_codigo_u, rut=_rut_u, razon_social=_rs_u, files=_drive_files_u)
            except Exception as exc:
                _log.warning("Drive upload ODS %s falló (edición): %s", _codigo_u, exc)
            try:
                folder_id = find_ods_drive_folder_id(_codigo_u, _rut_u, _rs_u)
                if folder_id:
                    _db.execute(
                        sqlalchemy.text("UPDATE venta_ods SET drive_folder_id=:fid, drive_folder_url=:url WHERE id=:id"),
                        {"fid": folder_id, "url": build_ods_folder_url(folder_id), "id": _record_id_u},
                    )
                    _db.commit()
            except Exception as exc:
                _db.rollback()
                _log.warning("drive_folder_url ODS %s falló (edición): %s", _codigo_u, exc)
            finally:
                _db.close()
        threading.Thread(target=_bg_update_drive, daemon=True).start()

    return record


def get_sucursales_table(db: Session) -> dict:
    headers = [
        "ID",
        "RUT",
        "Nombre Empresa",
        "Nombre Sucursal",
        "Direccion",
        "Region",
        "Comuna",
        "Referencia Ubicacion",
        "Proveedor Internet",
        "Proveedor Electricidad",
        "Nro Cliente Electricidad",
        "Horario Apertura",
        "Horario Cierre",
        "Dias Funcionamiento",
        "Latitud - Longitud",
        "Email Envio Facturas",
        "Nombre Emergencia",
    ]
    rows = db.query(SucursalBBDD).order_by(SucursalBBDD.id.asc()).all()

    # Sucursales que Bitácora marcó como "falta o está mal" y notificó a
    # Comercial (mismo criterio que get_sucursales_pendientes_bitacora_comercial,
    # pero sin filtrar por created_by: acá se ve independiente de quién la
    # registró) — se usa para resaltarlas arriba de la lista en bbdd_sucursal.html.
    pendientes_rows = db.execute(text("""
        SELECT s.id, e.campos_pendientes, e.campos_pendientes_obs,
               e.campos_pendientes_fecha, e.campos_pendientes_por
        FROM bbdd_sucursales s
        JOIN sucursal_info_extra e ON e.sucursal_id = s.id
        WHERE s.aceptada_bitacora = 0
          AND COALESCE(TRIM(e.campos_pendientes), '') <> ''
    """)).mappings().all()
    pendientes_bitacora: dict[str, dict] = {}
    for prow in pendientes_rows:
        campos_raw = str(prow.get("campos_pendientes") or "").strip()
        campos = [c.strip() for c in campos_raw.split(",") if c.strip()]
        if not campos:
            continue
        fecha = prow.get("campos_pendientes_fecha")
        pendientes_bitacora[str(prow.get("id"))] = {
            "campos": campos,
            "observacion": str(prow.get("campos_pendientes_obs") or "").strip(),
            "observado_en": fecha.isoformat(sep=" ", timespec="minutes") if isinstance(fecha, datetime) else "",
            "observado_por": str(prow.get("campos_pendientes_por") or "").strip(),
        }

    data_rows: list[list[str]] = []
    for row in rows:
        primer_contacto = (
            db.query(SucursalContactoEmergencia)
            .filter(SucursalContactoEmergencia.sucursal_id == row.id)
            .order_by(SucursalContactoEmergencia.id.asc())
            .first()
        )
        latlng = row.latitud_longitud or (
            f"{row.latitud}, {row.longitud}" if row.latitud and row.longitud else ""
        )
        data_rows.append([
            str(row.id),
            row.rut or "",
            row.nombre_empresa or "",
            row.nombre_sucursal or "",
            row.direccion_sucursal or "",
            _canonical_region(row.region),
            _canonical_comuna(row.region, row.comuna),
            row.referencia_ubicacion or "",
            row.proveedor_internet or "",
            row.proveedor_electricidad or "",
            row.nro_proveedor_electricidad or "",
            row.horario_apertura or "",
            row.horario_cierre or "",
            row.dias_funcionamiento or "",
            latlng or "",
            row.email_facturas or "",
            primer_contacto.nombre if primer_contacto and primer_contacto.nombre else "",
        ])
    return {"headers": headers, "rows": data_rows, "pendientes_bitacora": pendientes_bitacora}


def update_sucursal_row(db: Session, row_id: int, values: list[str]) -> None:
    if len(values) < 17:
        raise HTTPException(status_code=400, detail="Fila invalida: faltan columnas para actualizar.")

    record = db.query(SucursalBBDD).filter(SucursalBBDD.id == row_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")

    record.nombre_sucursal = (values[3] or "").strip()
    record.direccion_sucursal = (values[4] or "").strip()
    record.region = _clean_text(values[5])
    record.comuna = _clean_text(values[6])
    record.referencia_ubicacion = _clean_text(values[7])
    record.proveedor_internet = _clean_text(values[8])
    record.proveedor_electricidad = _clean_text(values[9])
    record.nro_proveedor_electricidad = _clean_text(values[10])
    record.horario_apertura = _clean_text(values[11])
    record.horario_cierre = _clean_text(values[12])
    record.dias_funcionamiento = _clean_text(values[13])
    record.email_facturas = _clean_text(values[15])

    lat, lng, latlng = _split_lat_lng(values[14], None, None)
    record.latitud = lat
    record.longitud = lng
    record.latitud_longitud = latlng

    nombre_emergencia = _clean_text(values[16])
    primer_contacto = (
        db.query(SucursalContactoEmergencia)
        .filter(SucursalContactoEmergencia.sucursal_id == record.id)
        .order_by(SucursalContactoEmergencia.id.asc())
        .first()
    )
    if primer_contacto:
        primer_contacto.nombre = nombre_emergencia
    elif nombre_emergencia:
        db.add(SucursalContactoEmergencia(
            sucursal_id=record.id,
            nombre=nombre_emergencia,
        ))

    db.commit()


def delete_sucursal_row(db: Session, row_id: int) -> None:
    """Borrado real (no soft-delete): las FK de bbdd_sucursales ya estan
    configuradas en la BBDD con ON DELETE CASCADE (contactos de
    emergencia, personas autorizadas, guardias, info extra, pruebas de
    sonido) y ON DELETE SET NULL (sucursal_camaras_monitoreo) — verificado
    contra sys.foreign_keys antes de implementar esto, no queda nada
    huerfano ni falla por constraint (pedido explicito, ago 2026)."""
    record = db.query(SucursalBBDD).filter(SucursalBBDD.id == row_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")
    db.delete(record)
    db.commit()


# Mismas claves/etiquetas que PEND_NOTIFICAR_CAMPOS en bitacora.html — el checklist
# de "Notificar a Comercial" manda estas claves, y acá se usan para resaltar los
# campos correspondientes en Venta y armar el resumen de "lo que rellenó".
CAMPOS_BITACORA_LABELS: dict[str, str] = {
    "direccion_sucursal": "Dirección",
    "latitud_longitud": "Latitud, Longitud",
    "referencia_ubicacion": "Referencia ubicación",
    "contacto": "Contacto",
    "email_facturas": "Correo",
    "horario_apertura": "Horario de apertura",
    "horario_cierre": "Horario de cierre",
    "horario_habil": "Días hábiles",
    "plan_cuadrante": "Plan cuadrante",
    "carabineros": "Carabineros",
    "bomberos": "Bomberos",
    "seguridad_ciudadana": "Seguridad ciudadana",
    "camaras_contratadas": "Cámaras a instalar",
    "camaras_televigiladas": "Cámaras televigiladas",
    "codigo_p2p": "Código P2P",
    "codigo_dss": "Código DSS",
    "telefono_porton": "Teléfono portón",
    "telefono_recepcion": "Teléfono recepción",
    "compania_electricidad": "Compañía electricidad",
    "numero_cliente_electricidad": "N° cliente electricidad",
    "proveedor_internet_cliente": "Proveedor internet cliente",
    "internet_atc": "Internet ATC",
    "contactos_emergencia": "Contacto de emergencia",
    "personas_autorizadas": "Personas autorizadas",
}


def get_sucursal_revision_bitacora(db: Session, sucursal_id: int) -> dict[str, Any]:
    """Qué marcó Bitácora como "falta o está mal" la última vez que notificó a
    Comercial sobre esta sucursal, y si sigue pendiente de aceptación."""
    row = db.execute(text("""
        SELECT s.aceptada_bitacora,
               e.campos_pendientes, e.campos_pendientes_obs,
               e.campos_pendientes_fecha, e.campos_pendientes_por
        FROM bbdd_sucursales s
        LEFT JOIN sucursal_info_extra e ON e.sucursal_id = s.id
        WHERE s.id = :sid
    """), {"sid": sucursal_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")
    campos_raw = str(row.get("campos_pendientes") or "").strip()
    campos = [c.strip() for c in campos_raw.split(",") if c.strip()]
    fecha = row.get("campos_pendientes_fecha")
    return {
        "pendiente": not bool(row.get("aceptada_bitacora")),
        "campos": campos,
        "observacion": str(row.get("campos_pendientes_obs") or "").strip(),
        "observado_en": fecha.isoformat(sep=" ", timespec="minutes") if isinstance(fecha, datetime) else "",
        "observado_por": str(row.get("campos_pendientes_por") or "").strip(),
    }


def get_sucursales_pendientes_bitacora_comercial(
    db: Session,
    comercial_nombre: str = "",
    *,
    todos: bool = False,
) -> dict[str, Any]:
    """Sucursales que Bitácora marcó como "falta o está mal" y siguen sin aceptar,
    filtradas a las que registró este comercial (bbdd_sucursales.created_by) — para
    el banner de avisos en Tabla Sucursal. Mismo criterio de "sigue pendiente" que
    get_sucursal_revision_bitacora (aceptada_bitacora = 0), más exigir que Bitácora
    haya dejado algo cargado en campos_pendientes (si no, no hay nada que avisar)."""
    nombre = (comercial_nombre or "").strip()
    if not nombre and not todos:
        return {"pendientes": []}
    filtro_comercial = "" if todos else "AND LOWER(TRIM(s.created_by)) = LOWER(TRIM(:nombre))"
    rows = db.execute(text("""
        SELECT s.id, s.nombre_sucursal, s.nombre_empresa,
               s.created_by,
               e.campos_pendientes, e.campos_pendientes_obs, e.campos_pendientes_fecha
        FROM bbdd_sucursales s
        JOIN sucursal_info_extra e ON e.sucursal_id = s.id
        WHERE s.aceptada_bitacora = 0
          """ + filtro_comercial + """
          AND COALESCE(TRIM(e.campos_pendientes), '') <> ''
        ORDER BY e.campos_pendientes_fecha DESC
    """), {"nombre": nombre}).mappings().all()
    pendientes = []
    for row in rows:
        campos_raw = str(row.get("campos_pendientes") or "").strip()
        campos = [c.strip() for c in campos_raw.split(",") if c.strip()]
        if not campos:
            continue
        fecha = row.get("campos_pendientes_fecha")
        pendientes.append({
            "sucursal_id": row.get("id"),
            "nombre_sucursal": row.get("nombre_sucursal") or "",
            "nombre_empresa": row.get("nombre_empresa") or "",
            "created_by": row.get("created_by") or "",
            "campos": campos,
            "observacion": str(row.get("campos_pendientes_obs") or "").strip(),
            "observado_en": fecha.isoformat(sep=" ", timespec="minutes") if isinstance(fecha, datetime) else "",
        })
    return {"pendientes": pendientes}


def _valores_actuales_para_resumen(db: Session, sucursal_id: int, campos: list[str]) -> list[tuple[str, str]]:
    """Valor actual de cada campo marcado, para mostrar en el correo de "avisar
    que está listo" qué quedó cargado — sin replicar el detalle de fallback fino
    que usa la ficha de Bitácora, solo un resumen informativo."""
    if not campos:
        return []
    row = db.execute(text("""
        SELECT
            b.direccion_sucursal, b.latitud_longitud, b.email_facturas,
            b.horario_apertura, b.horario_cierre, b.nombre_empresa, b.rut,
            COALESCE(NULLIF(TRIM(b.referencia_ubicacion), ''), e.referencia_ubicacion) AS referencia_ubicacion,
            COALESCE(NULLIF(TRIM(b.dias_funcionamiento), ''), e.horario_habil) AS horario_habil,
            e.plan_cuadrante, e.carabineros, e.bomberos, e.seguridad_ciudadana,
            e.camaras_contratadas, e.camaras_televigiladas, e.codigo_p2p, e.codigo_dss,
            e.telefono_porton, e.telefono_recepcion, e.internet_atc, e.contacto,
            b.proveedor_electricidad, b.nro_proveedor_electricidad, b.proveedor_internet
        FROM bbdd_sucursales b
        LEFT JOIN sucursal_info_extra e ON e.sucursal_id = b.id
        WHERE b.id = :sid
    """), {"sid": sucursal_id}).mappings().first()
    if not row:
        return []

    alias = {
        "compania_electricidad": "proveedor_electricidad",
        "numero_cliente_electricidad": "nro_proveedor_electricidad",
        "proveedor_internet_cliente": "proveedor_internet",
    }
    contactos = db.execute(text(
        "SELECT COUNT(*) FROM sucursal_contactos_emergencia WHERE sucursal_id = :sid"
    ), {"sid": sucursal_id}).scalar() or 0
    personas = db.execute(text(
        "SELECT COUNT(*) FROM sucursal_personas_autorizadas WHERE sucursal_id = :sid"
    ), {"sid": sucursal_id}).scalar() or 0
    # Mismo criterio de prioridad que la ficha de Bitácora (teléfono del cliente
    # antes que el override de sucursal_info_extra.contacto) — ver "contacto" en
    # bitacora.py _informacion_cliente_data.
    cliente_telefono = db.execute(text("""
        SELECT TOP 1 telefono FROM bbdd_clientes
        WHERE LOWER(TRIM(cliente)) = LOWER(TRIM(:empresa)) OR LOWER(TRIM(rut)) = LOWER(TRIM(:rut))
        ORDER BY id DESC
    """), {"empresa": str(row.get("nombre_empresa") or ""), "rut": str(row.get("rut") or "")}).scalar()

    resumen: list[tuple[str, str]] = []
    for campo in campos:
        label = CAMPOS_BITACORA_LABELS.get(campo, campo)
        if campo == "contactos_emergencia":
            resumen.append((label, f"{contactos} contacto(s) registrado(s)"))
            continue
        if campo == "personas_autorizadas":
            resumen.append((label, f"{personas} persona(s) registrada(s)"))
            continue
        if campo == "contacto":
            valor = str(cliente_telefono or row.get("contacto") or "").strip() or "-"
            resumen.append((label, valor))
            continue
        columna = alias.get(campo, campo)
        valor = str(row.get(columna) or "").strip() or "-"
        resumen.append((label, valor))
    return resumen


def avisar_sucursal_lista_bitacora(
    db: Session,
    sucursal_id: int,
    usuario: str,
    mensaje: str,
    campos_seleccionados: list[str] | None = None,
) -> dict[str, Any]:
    """Comercial ya corrigió (algunos o todos) los campos que Bitácora había marcado
    como pendientes en una sucursal (desde BBDD Sucursales o Información Clientes) y
    avisa al equipo de Bitácora para que la revise de nuevo. campos_seleccionados es
    lo que Comercial tildó como "esto sí lo corregí" en el popup — no
    necesariamente todo lo que estaba marcado, así que solo esos se limpian del
    flag y solo esos entran en el resumen; el resto sigue pendiente para la
    próxima vez."""
    revision = get_sucursal_revision_bitacora(db, sucursal_id)
    todos_los_marcados = revision["campos"]
    seleccionados = (
        [c for c in campos_seleccionados if c in todos_los_marcados]
        if campos_seleccionados is not None
        else todos_los_marcados
    )
    resumen = _valores_actuales_para_resumen(db, sucursal_id, seleccionados)
    resultado = notify_sucursal_lista_para_bitacora(db, sucursal_id, usuario, mensaje, resumen)
    if resultado.get("email_sent"):
        restantes = [c for c in todos_los_marcados if c not in seleccionados]
        db.execute(text("""
            UPDATE sucursal_info_extra SET campos_pendientes = :campos, campos_pendientes_obs = :obs
            WHERE sucursal_id = :sid
        """), {
            "sid": sucursal_id,
            "campos": ",".join(restantes),
            # Si queda algo pendiente se conserva la observación original de Bitácora
            # como contexto; si ya se resolvió todo, se limpia.
            "obs": revision["observacion"] if restantes else "",
        })
        db.commit()
    return resultado


_CHILE_REGIONES_COMUNAS: dict[str, list[str]] = {
    "Arica y Parinacota": ["Arica", "Camarones", "Putre", "General Lagos"],
    "Tarapac\u00e1": ["Iquique", "Alto Hospicio", "Pozo Almonte", "Cami\u00f1a", "Colchane", "Huara", "Pica"],
    "Antofagasta": ["Antofagasta", "Mejillones", "Sierra Gorda", "Taltal", "Calama", "Ollag\u00fce", "San Pedro de Atacama", "Tocopilla", "Mar\u00eda Elena"],
    "Atacama": ["Copiap\u00f3", "Caldera", "Tierra Amarilla", "Cha\u00f1aral", "Diego de Almagro", "Vallenar", "Alto del Carmen", "Freirina", "Huasco"],
    "Coquimbo": ["La Serena", "Coquimbo", "Andacollo", "La Higuera", "Paiguano", "Vicu\u00f1a", "Illapel", "Canela", "Los Vilos", "Salamanca", "Ovalle", "Combarbal\u00e1", "Monte Patria", "Punitaqui", "R\u00edo Hurtado"],
    "Valpara\u00edso": ["Valpara\u00edso", "Casablanca", "Conc\u00f3n", "Juan Fern\u00e1ndez", "Puchuncav\u00ed", "Quintero", "Vi\u00f1a del Mar", "Isla de Pascua", "Los Andes", "Calle Larga", "Rinconada", "San Esteban", "La Ligua", "Cabildo", "Papudo", "Petorca", "Zapallar", "Quillota", "Calera", "Hijuelas", "La Cruz", "Nogales", "San Antonio", "Algarrobo", "Cartagena", "El Quisco", "El Tabo", "Santo Domingo", "San Felipe", "Catemu", "Llaillay", "Panquehue", "Putaendo", "Santa Mar\u00eda", "Quilpu\u00e9", "Limache", "Olmu\u00e9", "Villa Alemana"],
    "Metropolitana de Santiago": ["Santiago", "Cerrillos", "Cerro Navia", "Conchal\u00ed", "El Bosque", "Estaci\u00f3n Central", "Huechuraba", "Independencia", "La Cisterna", "La Florida", "La Granja", "La Pintana", "La Reina", "Las Condes", "Lo Barnechea", "Lo Espejo", "Lo Prado", "Macul", "Maip\u00fa", "\u00d1u\u00f1oa", "Pedro Aguirre Cerda", "Pe\u00f1alol\u00e9n", "Providencia", "Pudahuel", "Quilicura", "Quinta Normal", "Recoleta", "Renca", "San Joaqu\u00edn", "San Miguel", "San Ram\u00f3n", "Vitacura", "Puente Alto", "Pirque", "San Jos\u00e9 de Maipo", "Colina", "Lampa", "Tiltil", "San Bernardo", "Buin", "Calera de Tango", "Paine", "Melipilla", "Alhu\u00e9", "Curacav\u00ed", "Mar\u00eda Pinto", "San Pedro", "Talagante", "El Monte", "Isla de Maipo", "Padre Hurtado", "Pe\u00f1aflor"],
    "O'Higgins": ["Rancagua", "Codegua", "Coinco", "Coltauco", "Do\u00f1ihue", "Graneros", "Las Cabras", "Machal\u00ed", "Malloa", "Mostazal", "Olivar", "Peumo", "Pichidegua", "Quinta de Tilcoco", "Rengo", "Requ\u00ednoa", "San Vicente", "Pichilemu", "La Estrella", "Litueche", "Marchihue", "Navidad", "Paredones", "San Fernando", "Ch\u00e9pica", "Chimbarongo", "Lolol", "Nancagua", "Palmilla", "Peralillo", "Placilla", "Pumanque", "Santa Cruz"],
    "Maule": ["Talca", "Constituci\u00f3n", "Curepto", "Empedrado", "Maule", "Pelarco", "Pencahue", "R\u00edo Claro", "San Clemente", "San Rafael", "Cauquenes", "Chanco", "Pelluhue", "Curic\u00f3", "Huala\u00f1\u00e9", "Licant\u00e9n", "Molina", "Rauco", "Romeral", "Sagrada Familia", "Teno", "Vichuqu\u00e9n", "Linares", "Colb\u00fan", "Longav\u00ed", "Parral", "Retiro", "San Javier", "Villa Alegre", "Yerbas Buenas"],
    "\u00d1uble": ["Chill\u00e1n", "Bulnes", "Chill\u00e1n Viejo", "El Carmen", "Pemuco", "Pinto", "Quill\u00f3n", "San Ignacio", "Yungay", "Cobquecura", "Coelemu", "Ninhue", "Portezuelo", "Quirihue", "R\u00e1nquil", "Trehuaco", "Coihueco", "\u00d1iqu\u00e9n", "San Carlos", "San Fabi\u00e1n", "San Nicol\u00e1s"],
    "Biob\u00edo": ["Concepci\u00f3n", "Coronel", "Chiguayante", "Florida", "Hualqui", "Lota", "Penco", "San Pedro de la Paz", "Santa Juana", "Talcahuano", "Tom\u00e9", "Hualp\u00e9n", "Lebu", "Arauco", "Ca\u00f1ete", "Contulmo", "Curanilahue", "Los \u00c1lamos", "Tir\u00faa", "Los \u00c1ngeles", "Antuco", "Cabrero", "Laja", "Mulch\u00e9n", "Nacimiento", "Negrete", "Quilaco", "Quilleco", "San Rosendo", "Santa B\u00e1rbara", "Tucapel", "Yumbel", "Alto Biob\u00edo"],
    "La Araucan\u00eda": ["Temuco", "Carahue", "Cunco", "Curarrehue", "Freire", "Galvarino", "Gorbea", "Lautaro", "Loncoche", "Melipeuco", "Nueva Imperial", "Padre Las Casas", "Perquenco", "Pitrufqu\u00e9n", "Puc\u00f3n", "Saavedra", "Teodoro Schmidt", "Tolt\u00e9n", "Vilc\u00fan", "Villarrica", "Cholchol", "Angol", "Collipulli", "Curacaut\u00edn", "Ercilla", "Lonquimay", "Los Sauces", "Lumaco", "Pur\u00e9n", "Renaico", "Traigu\u00e9n", "Victoria"],
    "Los R\u00edos": ["Valdivia", "Corral", "Futrono", "La Uni\u00f3n", "Lago Ranco", "Lanco", "Los Lagos", "M\u00e1fil", "Mariquina", "Paillaco", "Panguipulli", "R\u00edo Bueno"],
    "Los Lagos": ["Puerto Montt", "Calbuco", "Cocham\u00f3", "Fresia", "Frutillar", "Los Muermos", "Llanquihue", "Maull\u00edn", "Puerto Varas", "Castro", "Ancud", "Chonchi", "Curaco de V\u00e9lez", "Dalcahue", "Puqueld\u00f3n", "Queil\u00e9n", "Quell\u00f3n", "Quemchi", "Quinchao", "Osorno", "Puerto Octay", "Purranque", "Puyehue", "R\u00edo Negro", "San Juan de la Costa", "San Pablo", "Chait\u00e9n", "Futaleuf\u00fa", "Hualaihu\u00e9", "Palena"],
    "Ays\u00e9n": ["Coyhaique", "Lago Verde", "Ays\u00e9n", "Cisnes", "Guaitecas", "Cochrane", "O'Higgins", "Tortel", "Chile Chico", "R\u00edo Ib\u00e1\u00f1ez"],
    "Magallanes": ["Punta Arenas", "Laguna Blanca", "R\u00edo Verde", "San Gregorio", "Cabo de Hornos", "Ant\u00e1rtica", "Porvenir", "Primavera", "Timaukel", "Natales", "Torres del Paine"],
}


def fetch_regiones() -> list[str]:
    return sorted(_CHILE_REGIONES_COMUNAS.keys())


def _canonical_region(value: str | None) -> str:
    value_clean = _repair_text_encoding(value or "")
    value_norm = _normalize_text(value_clean)
    region_aliases = {
        "valparaso": "Valpara\u00edso",
        "valpara so": "Valpara\u00edso",
        "nuble": "\u00d1uble",
        "uble": "\u00d1uble",
        "tarapac": "Tarapac\u00e1",
        "aysen": "Ays\u00e9n",
        "biobo": "Biob\u00edo",
        "la araucana": "La Araucan\u00eda",
        "los ros": "Los R\u00edos",
    }
    if value_norm in region_aliases:
        return region_aliases[value_norm]
    for region in _CHILE_REGIONES_COMUNAS:
        if _normalize_text(region) == value_norm:
            return region
    return value_clean


def _canonical_comuna(region: str | None, value: str | None) -> str:
    value_clean = _repair_text_encoding(value or "")
    value_norm = _normalize_text(value_clean)
    region_name = _canonical_region(region)
    for comuna in _CHILE_REGIONES_COMUNAS.get(region_name, []):
        if _normalize_text(comuna) == value_norm:
            return comuna
    return value_clean


def fetch_comunas(region: str) -> list[str]:
    region_name = _canonical_region(region)
    region_norm = _normalize_text(region_name)
    for key, comunas in _CHILE_REGIONES_COMUNAS.items():
        if _normalize_text(key) == region_norm:
            return sorted(comunas)
    raise HTTPException(status_code=404, detail=f"Region '{region_name}' no encontrada.")


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
            _canonical_region(row.region),
            _canonical_comuna(row.region, row.comuna),
            row.email_facturas or "",
            row.nombre_representante or "",
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
    db.commit()


def update_cliente_telefono(db: Session, rut: str, telefono: str) -> None:
    """Edita solo el teléfono del cliente (bbdd_clientes.telefono) — es el mismo
    valor que la ficha de Bitácora muestra como "Contacto" de la sucursal cuando
    el cliente tiene teléfono cargado (ver _first_non_empty en bitacora.py), así
    que hay que poder corregirlo desde Información Clientes, no solo desde la
    tabla de clientes completa."""
    rut_normalizado = normalize_rut(rut)
    if not rut_normalizado:
        raise HTTPException(status_code=400, detail="Falta el RUT del cliente.")
    record = (
        db.query(ClienteBBDD)
        .filter(func.lower(func.trim(ClienteBBDD.rut)) == rut_normalizado.lower())
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    record.telefono = (telefono or "").strip() or None
    db.commit()


# â”€â”€â”€ Operaciones â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

OPERACIONES_BOOL_FIELDS: dict[str, str] = {
    "fecha_coordinacion": "ts_fecha_coordinacion",
    "reunion_coordinacion": "ts_reunion_coordinacion",
    "coord_apertura_puesto": "ts_coord_apertura_puesto",
    "coord_equipo": "ts_coord_equipo",
    "terminado": "ts_terminado",
}


def _get_or_create_operaciones_row(db: Session, codigo: str):
    codigo_limpio = str(codigo or "").strip()
    row = (
        db.query(OperacionesVentaODT)
        .filter(func.lower(func.trim(OperacionesVentaODT.odt)) == codigo_limpio.lower())
        .first()
    )
    if not row:
        row = OperacionesVentaODT(odt=codigo_limpio)
        db.add(row)
        db.flush()
    return row


def get_operaciones_ods_rows(db: Session) -> dict:
    email_to_name = _email_to_name_map(db)
    rows = (
        db.query(VentaODS, OperacionesVentaODT, ServicioTecnicoVentaODT, SoporteTecnicoVentaODT)
        .outerjoin(
            OperacionesVentaODT,
            func.lower(func.trim(OperacionesVentaODT.odt)) == func.lower(func.trim(VentaODS.codigo)),
        )
        .outerjoin(
            ServicioTecnicoVentaODT,
            func.lower(func.trim(ServicioTecnicoVentaODT.odt)) == func.lower(func.trim(VentaODS.codigo)),
        )
        .outerjoin(
            SoporteTecnicoVentaODT,
            func.lower(func.trim(SoporteTecnicoVentaODT.odt)) == func.lower(func.trim(VentaODS.codigo)),
        )
        .order_by(VentaODS.created_at.desc(), VentaODS.id.desc())
        .all()
    )
    out: list[dict] = []
    total_anuladas = 0
    for ods, op, st, sop in rows:
        tipo_servicio_op = str(ods.tipo_servicio or "")
        tipos_lista_op = [t.strip() for t in tipo_servicio_op.split("|") if t.strip()]
        if not _calcular_areas_aplicables(tipos_lista_op)["operaciones"]:
            continue
        estado_ods = str(ods.estado or "").strip()
        anulada = estado_ods.lower() == "anulada"
        if anulada:
            total_anuladas += 1
        terminado_soporte = _is_true(getattr(st, "finalizado", False))
        requiere_puesto = getattr(sop, "requiere_puesto_nuevo", "") if sop else ""
        numero_central = getattr(sop, "numero_central_asignado", "") if sop else ""
        # Guardia no pasa por Servicio Tecnico/Soporte (solo aplica a
        # Operaciones, ver _calcular_areas_aplicables arriba) — exigirle
        # TERM. SOPORTE, Req. Puesto y N. Central nunca se cumple para este
        # tipo y la fila queda bloqueada para siempre. Se marca aparte para
        # que el frontend no exija esos 3 campos cuando es Guardia (pedido
        # explicito, ago 2026).
        es_guardia = "guardia" in {_normalize_text(t) for t in tipos_lista_op}
        out.append({
            "codigo": ods.codigo or "",
            "fecha": _fmt_date(ods.created_at),
            "ejecutivo": _resolve_ejecutivo(email_to_name, ods.creado_por),
            "rutCliente": ods.rut_cliente or "",
            "razonSocial": ods.razon_social or "",
            "nombreSucursal": ods.nombre_sucursal or "",
            "direccionSucursal": ods.direccion_sucursal or "",
            "tipoServicio": ods.tipo_servicio or "",
            "tipoPlan": ods.tipo_plan or "",
            "terminadoSoporte": terminado_soporte,
            "terminadoSoporteFecha": _fmt_date_only(getattr(st, "fecha_cierre", None)) if terminado_soporte else "",
            "requierePuestoNuevo": requiere_puesto or "",
            "numeroCentralAsignado": numero_central or "",
            "esGuardia": es_guardia,
            "fechaInicioServicio": getattr(op, "fecha_inicio_servicio", "") if op else "",
            "estados": {
                "fecha_coordinacion": _is_true(getattr(op, "fecha_coordinacion", False)),
                "reunion_coordinacion": _is_true(getattr(op, "reunion_coordinacion", False)),
                "coord_apertura_puesto": _is_true(getattr(op, "coord_apertura_puesto", False)),
                "coord_equipo": _is_true(getattr(op, "coord_equipo", False)),
                "terminado": _is_true(getattr(op, "terminado", False)),
            },
            "anulada": anulada,
        })
    return {"rows": out, "totalAnuladas": total_anuladas}


def update_operaciones_ods_estado(db: Session, codigo: str, campo: str, valor: bool) -> dict:
    codigo_limpio = str(codigo or "").strip()
    campo_limpio = str(campo or "").strip()
    if campo_limpio not in OPERACIONES_BOOL_FIELDS:
        raise HTTPException(status_code=400, detail="Campo operaciones invalido.")
    ods = (
        db.query(VentaODS)
        .filter(func.lower(func.trim(VentaODS.codigo)) == codigo_limpio.lower())
        .first()
    )
    if not ods:
        raise HTTPException(status_code=404, detail="ODS no encontrada.")
    if str(ods.estado or "").strip().lower() == "anulada":
        raise HTTPException(status_code=400, detail="La ODS esta anulada.")
    row = _get_or_create_operaciones_row(db, codigo_limpio)
    ts_field = OPERACIONES_BOOL_FIELDS[campo_limpio]
    now = datetime.now(timezone.utc)
    setattr(row, campo_limpio, bool(valor))
    setattr(row, ts_field, now if valor else None)
    db.commit()
    return {
        "ok": True,
        "codigo": codigo_limpio,
        "campo": campo_limpio,
        "valor": bool(valor),
        "estado": "Completado" if valor else "Pendiente",
        "timestamp": _fmt_date(now) if valor else "",
    }


def update_operaciones_ods_fecha(db: Session, codigo: str, fecha: str) -> dict:
    codigo_limpio = str(codigo or "").strip()
    ods = (
        db.query(VentaODS)
        .filter(func.lower(func.trim(VentaODS.codigo)) == codigo_limpio.lower())
        .first()
    )
    if not ods:
        raise HTTPException(status_code=404, detail="ODS no encontrada.")
    row = _get_or_create_operaciones_row(db, codigo_limpio)
    previous_fecha = str(row.fecha_inicio_servicio or "").strip()
    row.fecha_inicio_servicio = str(fecha or "").strip()[:40]
    db.commit()
    if row.fecha_inicio_servicio and row.fecha_inicio_servicio != previous_fecha:
        _codigo_bg = codigo_limpio
        _fecha_bg = row.fecha_inicio_servicio
        def _bg_inicio():
            from ATC.app.core.db import SessionLocal
            _db = SessionLocal()
            try:
                notify_inicio_servicio(_db, _codigo_bg, _fecha_bg)
            except Exception as exc:
                _log.warning("notify_inicio_servicio %s falló: %s", _codigo_bg, exc)
            finally:
                _db.close()
        threading.Thread(target=_bg_inicio, daemon=True).start()
    return {
        "ok": True,
        "codigo": codigo_limpio,
        "fechaInicioServicio": row.fecha_inicio_servicio,
        "email_sent": False, "email_to": [], "email_error": "",
    }


# â”€â”€â”€ Comercial (vista general) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _fecha_corta(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    return str(value)


def _evaluar_exclusion_carta(tipo_servicio: str) -> bool:
    ts = (tipo_servicio or "").lower()
    return any(k in ts for k in (
        "solo instalacion", "servicio tecnico", "servicio técnico", "servicio tÃ©cnico",
        "desinstalacion", "desinstalación", "desinstalaciÃ³n", "monitoreo adicional",
        "upgrade", "downgrade",
    ))


def _contrato_no_requerido(tipos: list[str]) -> bool:
    """Servicio Tecnico o Instalacion (solos) no requieren contrato firmado."""
    if len(tipos) != 1:
        return False
    return _normalize_text(tipos[0]) in {"servicio tecnico", "instalacion"}


def _calcular_areas_aplicables(tipos: list[str]) -> dict[str, bool]:
    """Determina quÃ© Ã¡reas corresponden segÃºn los tipos de servicio seleccionados.

    Tabla de referencia (imagen TablaAdministracion):
      ServTec aplica  â†’ Instalacion | Servicio Tecnico | Alarma | Upgrade | Downgrade | Desinstalacion
      Soporte aplica  â†’ Televigilancia | Alarma | Instalacion | Servicio Tecnico | Upgrade | Downgrade | Monitoreo Adicional
      Operaciones aplica â†’ Televigilancia | Guardia | Upgrade | Downgrade | Monitoreo Adicional
    """
    n = {_normalize_text(t) for t in tipos}
    tv      = "televigilancia" in n
    inst    = "instalacion" in n
    st      = "servicio tecnico" in n
    alarma  = "alarma" in n
    guardia = "guardia" in n
    upg     = "upgrade" in n
    dwn     = "downgrade" in n
    desinst = "desinstalacion" in n
    mon     = "monitoreo adicional" in n
    return {
        "servtec":      inst or st or alarma or upg or dwn or desinst,
        "soporte":      tv or alarma or inst or st or upg or dwn or mon,
        "operaciones":  tv or guardia or upg or dwn or mon,
    }


def _area_estado(checks: list[tuple]) -> dict:
    """checks: cada item es (nombre, hecho) o (nombre, hecho, fecha) — fecha es la
    datetime en que se marcó ese paso, si se tiene registrada (ver AdministracionODT).

    El ultimo item de la lista es el cierre formal del area ("Finalizado"/"Terminado"/
    "VB final servicio"): si ya esta marcado, el area cuenta como Terminada aunque
    queden pasos intermedios sin tildar — el cierre formal manda por sobre el detalle."""
    normalizados = [item if len(item) == 3 else (*item, None) for item in checks]
    detalles = [name for name, done, _fecha in normalizados if not done]
    completados = [
        {"nombre": name, "fecha": fecha.strftime("%d/%m/%Y") if fecha else None}
        for name, done, fecha in normalizados if done
    ]
    cierre_formal_hecho = bool(normalizados) and normalizados[-1][1]
    if not detalles or cierre_formal_hecho:
        estado = "Terminado"
    elif not completados:
        estado = "Pendiente"
    else:
        estado = "En proceso"
    return {"estado": estado, "detalles": detalles, "completados": completados}


def _area_no_aplica() -> dict:
    """Estado propio para un area que no corresponde al tipo de servicio contratado
    de esta ODS (ver _calcular_areas_aplicables) — distinto de "Terminado", para no
    confundir "no aplica" con "ya se hizo"."""
    return {"estado": "No aplica", "detalles": [], "completados": []}


def get_comercial_todo(db: Session) -> dict:
    email_to_name = _email_to_name_map(db)
    # ODS creadas por estos ejecutivos no deben verse en la Tabla Comercial
    # (a pedido explicito; sus ODS si siguen visibles en el resto de tablas).
    # creado_por guarda el nombre tal cual (no el email) para estas filas.
    _EJECUTIVOS_OCULTOS_COMERCIAL = {"maryorie alegria espinoza", "gianpiero lubiano"}
    rows = (
        db.query(
            VentaODS,
            AdministracionODT,
            ServicioTecnicoVentaODT,
            FinanzasODT,
            OperacionesVentaODT,
            SoporteTecnicoVentaODT,
        )
        .outerjoin(AdministracionODT, func.lower(func.trim(AdministracionODT.odt)) == func.lower(func.trim(VentaODS.codigo)))
        .outerjoin(ServicioTecnicoVentaODT, func.lower(func.trim(ServicioTecnicoVentaODT.odt)) == func.lower(func.trim(VentaODS.codigo)))
        .outerjoin(FinanzasODT, func.lower(func.trim(FinanzasODT.odt)) == func.lower(func.trim(VentaODS.codigo)))
        .outerjoin(OperacionesVentaODT, func.lower(func.trim(OperacionesVentaODT.odt)) == func.lower(func.trim(VentaODS.codigo)))
        .outerjoin(SoporteTecnicoVentaODT, func.lower(func.trim(SoporteTecnicoVentaODT.odt)) == func.lower(func.trim(VentaODS.codigo)))
        .filter(func.lower(func.trim(func.coalesce(VentaODS.creado_por, ""))).notin_(_EJECUTIVOS_OCULTOS_COMERCIAL))
        .order_by(VentaODS.created_at.desc(), VentaODS.id.desc())
        .all()
    )
    out: list[dict] = []
    dirty = False
    for ods, adm, st, fin, op, sop in rows:
        estado_ods = str(ods.estado or "").strip()
        anulada = estado_ods.lower() == "anulada"
        tipo_servicio = str(ods.tipo_servicio or "")
        excluir_carta = _evaluar_exclusion_carta(tipo_servicio)

        tipos_lista = [t.strip() for t in tipo_servicio.split("|") if t.strip()]
        areas_aplica = _calcular_areas_aplicables(tipos_lista)
        contrato_no_requerido = _contrato_no_requerido(tipos_lista)

        comercial_checks = [
            ("Cotizacion", bool(ods.cotizacion_path)),
        ]
        if not contrato_no_requerido:
            comercial_checks.append(("Contrato Firmado", bool(ods.contrato_path)))
        if str(ods.requiere_oc or "").strip().lower() == "si":
            comercial_checks.append(("Orden de Compra", bool(ods.odc_path)))
        area_comercial = _area_estado(comercial_checks)

        # Servicio Tecnico o Instalacion solos no requieren registro en Alpha3 / Intranet
        # (mismo criterio que tabla_administracion_venta.html: debeOcultarBoton).
        omitir_registros = (
            len(tipos_lista) == 1
            and _normalize_text(tipos_lista[0]) in {"servicio tecnico", "instalacion"}
        )

        admin_checks: list[tuple] = [
            ("Recepcion info", _is_true(getattr(adm, "recepcion_info", False)), getattr(adm, "fecha_recepcion_info", None)),
        ]
        if not omitir_registros:
            admin_checks.append(("Registro Alpha3", _is_true(getattr(adm, "registro_alpha3", False)), getattr(adm, "fecha_registro_alpha3", None)))
            admin_checks.append(("Registro Intranet", _is_true(getattr(adm, "registro_intranet", False)), getattr(adm, "fecha_registro_intranet", None)))
        admin_checks.append(("Envio solicitud instalacion", _is_true(getattr(adm, "envio_solicitud_instalacion", False)), getattr(adm, "fecha_envio_solicitud_instalacion", None)))
        admin_checks.append(("Envio datos facturacion", _is_true(getattr(adm, "envio_datos_facturacion", False)), getattr(adm, "fecha_envio_datos_facturacion", None)))
        if not excluir_carta:
            admin_checks.append(("Carta de bienvenida", _is_true(getattr(adm, "envio_carta_bienvenida", False)), getattr(adm, "fecha_envio_carta_bienvenida", None)))
        admin_checks.append(("Finalizado", _is_true(getattr(adm, "finalizado", False)), getattr(adm, "fecha_cierre", None)))
        area_admin = _area_estado(admin_checks)

        if areas_aplica["servtec"]:
            servicio_finalizado = _is_true(getattr(st, "finalizado", False))
            area_servicio = _area_estado([
                ("Recepcion solicitud instalacion", _is_true(getattr(st, "recepcion_solicitud_instalacion", False)), getattr(st, "fecha_recepcion_solicitud_instalacion", None)),
                ("Llamar cliente", bool(_clean_text(getattr(st, "llamar_cliente", None)))),
                ("Solicitud materiales", bool(_clean_text(getattr(st, "solicitud_materiales", None)))),
                # En la vista comercial, los datos de agenda/equipo no bloquean
                # el termino del area: fecha inicio, fecha fin, tecnico y acompanante.
                (
                    "Instalacion finalizada",
                    _is_true(getattr(st, "instalacion_finalizada", False)) or servicio_finalizado,
                    getattr(st, "fecha_instalacion_finalizada", None) or getattr(st, "fecha_cierre", None),
                ),
                ("Finalizado", servicio_finalizado, getattr(st, "fecha_cierre", None)),
            ])
        else:
            area_servicio = _area_no_aplica()

        if areas_aplica["soporte"]:
            area_soporte = _area_estado([
                ("Configuracion camaras", _is_true(getattr(sop, "configuracion_camaras", False)), getattr(sop, "fecha_configuracion_camaras", None)),
                ("Posicionamiento imagen", _is_true(getattr(sop, "posicionamiento_imagen", False)), getattr(sop, "fecha_posicionamiento_imagen", None)),
                ("Enlace servidor", _is_true(getattr(sop, "enlace_servidor", False)), getattr(sop, "fecha_enlace_servidor", None)),
                ("Configuracion IVS", _is_true(getattr(sop, "configuracion_ivs", False)), getattr(sop, "fecha_configuracion_ivs", None)),
                ("Plan grabacion", _is_true(getattr(sop, "plan_grabacion", False)), getattr(sop, "fecha_plan_grabacion", None)),
                ("Requiere puesto nuevo", bool(_clean_text(getattr(sop, "requiere_puesto_nuevo", None)))),
                ("Numero central asignado", bool(_clean_text(getattr(sop, "numero_central_asignado", None)))),
                ("Configuracion cliente", _is_true(getattr(sop, "configuracion_cliente", False)), getattr(sop, "fecha_configuracion_cliente", None)),
                ("VB final servicio", _is_true(getattr(sop, "vb_final_servicio", False)), getattr(sop, "fecha_vb_final_servicio", None)),
            ])
        else:
            area_soporte = _area_no_aplica()

        if areas_aplica["operaciones"]:
            area_operaciones = _area_estado([
                ("Fecha inicio servicio", bool(_clean_text(getattr(op, "fecha_inicio_servicio", None)))),
                ("Fecha coordinacion", _is_true(getattr(op, "fecha_coordinacion", False)), getattr(op, "ts_fecha_coordinacion", None)),
                ("Reunion coordinacion", _is_true(getattr(op, "reunion_coordinacion", False)), getattr(op, "ts_reunion_coordinacion", None)),
                ("Coord. apertura puesto", _is_true(getattr(op, "coord_apertura_puesto", False)), getattr(op, "ts_coord_apertura_puesto", None)),
                ("Coord. equipo", _is_true(getattr(op, "coord_equipo", False)), getattr(op, "ts_coord_equipo", None)),
                ("Terminado", _is_true(getattr(op, "terminado", False)), getattr(op, "ts_terminado", None)),
            ])
        else:
            area_operaciones = _area_no_aplica()

        area_finanzas = _area_estado([
            ("Recepcion datos facturacion", _is_true(getattr(fin, "recepcion_datos_facturacion", False)), getattr(fin, "fecha_recepcion_datos_facturacion", None)),
            ("Creacion clientes Piriod", _is_true(getattr(fin, "creacion_clientes_piriod", False)), getattr(fin, "fecha_creacion_clientes_piriod", None)),
            ("Facturacion instalacion", _is_true(getattr(fin, "facturacion_instalacion", False)), getattr(fin, "fecha_facturacion_instalacion", None)),
            ("Facturacion servicio", _is_true(getattr(fin, "facturacion_servicio", False)), getattr(fin, "fecha_facturacion_servicio", None)),
            ("Finalizado", _is_true(getattr(fin, "finalizado", False)), getattr(fin, "fecha_cierre", None)),
        ])

        drive_folder_url = (ods.drive_folder_url or "").strip()
        if not drive_folder_url and (ods.drive_folder_id or "").strip():
            drive_folder_url = build_ods_folder_url(ods.drive_folder_id)
        if not drive_folder_url:
            try:
                folder_id = find_ods_drive_folder_id(ods.codigo or "", ods.rut_cliente or "", ods.razon_social or "")
            except Exception as exc:
                _log.warning("No se pudo buscar carpeta Drive para %s: %s", ods.codigo, exc)
                folder_id = ""
            if folder_id:
                drive_folder_url = build_ods_folder_url(folder_id)
                ods.drive_folder_id = folder_id
                ods.drive_folder_url = drive_folder_url
                dirty = True

        out.append({
            "codigo": ods.codigo or "",
            "fecha": _fecha_corta(ods.created_at),
            "ejecutivo": _resolve_ejecutivo(email_to_name, ods.creado_por),
            "rutCliente": ods.rut_cliente or "",
            "razonSocial": ods.razon_social or "",
            "nombreSucursal": ods.nombre_sucursal or "",
            "direccionSucursal": ods.direccion_sucursal or "",
            "tipoServicio": tipo_servicio,
            "tipoPlan": ods.tipo_plan or "",
            "carpeta": ods.cotizacion_path or ods.odc_path or ods.desglose_path or ods.contrato_path or "",
            "driveFolderUrl": drive_folder_url,
            "contratoPath": ods.contrato_path or "",
            "contratoRequerido": not contrato_no_requerido,
            "requiereOC": ods.requiere_oc or "",
            "anulada": anulada,
            "areaComercial": area_comercial,
            "areaAdmin": area_admin,
            "areaServicio": area_servicio,
            "areaSoporte": area_soporte,
            "areaOperaciones": area_operaciones,
            "areaFinanzas": area_finanzas,
        })
    if dirty:
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            _log.warning("No se pudo persistir drive_folder_url cacheado en comercial: %s", exc)
    return {"rows": out}


def anular_ods_venta(db: Session, codigo: str) -> dict:
    codigo_limpio = str(codigo or "").strip()
    ods = (
        db.query(VentaODS)
        .filter(func.lower(func.trim(VentaODS.codigo)) == codigo_limpio.lower())
        .first()
    )
    if not ods:
        raise HTTPException(status_code=404, detail="ODS no encontrada.")
    if str(ods.estado or "").strip().lower() == "anulada":
        raise HTTPException(status_code=400, detail="La ODS ya esta anulada.")
    ods.estado = "Anulada"
    db.commit()
    return {"ok": True, "codigo": codigo_limpio}


def subir_contrato_venta(db: Session, codigo: str, nombre: str, data_base64: str) -> dict:
    codigo_limpio = str(codigo or "").strip()
    nombre_limpio = str(nombre or "").strip()
    if not codigo_limpio or not nombre_limpio or not data_base64:
        raise HTTPException(status_code=400, detail="Datos incompletos.")
    ods = (
        db.query(VentaODS)
        .filter(func.lower(func.trim(VentaODS.codigo)) == codigo_limpio.lower())
        .first()
    )
    if not ods:
        raise HTTPException(status_code=404, detail="ODS no encontrada.")
    try:
        contenido = base64.b64decode(data_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="Datos base64 invalidos.")
    directorio = VENTA_UPLOADS_DIR / codigo_limpio / "contrato"
    directorio.mkdir(parents=True, exist_ok=True)
    ruta = directorio / nombre_limpio
    ruta.write_bytes(contenido)
    ruta_rel = f"uploads/venta_ods/{codigo_limpio}/contrato/{nombre_limpio}"
    ods.contrato_path = ruta_rel
    existing = (
        db.query(VentaODSArchivo)
        .filter(
            VentaODSArchivo.ods_id == ods.id,
            func.lower(func.trim(VentaODSArchivo.tipo_documento)) == "contrato",
        )
        .first()
    )
    if existing:
        existing.nombre_archivo = nombre_limpio
        existing.ruta_archivo = ruta_rel
    else:
        db.add(VentaODSArchivo(
            ods_id=ods.id,
            codigo_ods=codigo_limpio,
            tipo_documento="Contrato",
            servicio="General",
            nombre_archivo=nombre_limpio,
            mime_type="",
            ruta_archivo=ruta_rel,
        ))
    db.commit()
    return {"ok": True, "codigo": codigo_limpio, "nombre": nombre_limpio}
