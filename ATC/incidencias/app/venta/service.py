from __future__ import annotations

import base64
import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ATC.incidencias.app.config import settings
from ATC.incidencias.app.drive_report_service import (
    DriveReportError,
    build_ods_folder_url,
    find_ods_drive_folder_id,
    upload_ods_files_to_drive,
)
from ATC.incidencias.app.models import (
    AdministracionODT,
    ClienteBBDD,
    FinanzasODT,
    OperacionesVentaODT,
    ServicioTecnicoVentaODT,
    SucursalBBDD,
    SucursalContactoEmergencia,
    SucursalGuardia,
    SucursalPersonaAutorizada,
    VentaODS,
    VentaODSArchivo,
)
from ATC.incidencias.app.services import IncidenciasService
from ATC.incidencias.app.venta.schemas import VentaClienteCreateRequest, VentaODSArchivoRequest, VentaODSCreateRequest, VentaSucursalCreateRequest

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

VENTA_UPLOADS_DIR = Path(__file__).resolve().parents[2] / "uploads" / "venta_ods"


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

    return {
        "nombreCliente": cliente.cliente or "",
        "direccionCasaMatriz": cliente.direccion or "",
        "comunaCasaMatriz": cliente.comuna or "",
        "nombreRepresentante": cliente.nombre_representante or "",
        "rutRepresentante": cliente.rut_representante or "",
        "emailRepresentante": cliente.email_representante or "",
        "sucursales": [
            {
                "id": sucursal.id,
                "nombre": sucursal.nombre_sucursal or "",
                "direccion": sucursal.direccion_sucursal or "",
                "label": f"{sucursal.nombre_sucursal or 'Sucursal'} - {sucursal.direccion_sucursal or ''}".strip(" -"),
            }
            for sucursal in sucursales
        ],
    }


def get_cliente_sucursal_resumen(db: Session, rut: str, sucursal_id: int) -> dict:
    safe_rut = normalize_rut(rut)
    sucursal = (
        db.query(SucursalBBDD)
        .filter(SucursalBBDD.id == sucursal_id, func.lower(func.trim(SucursalBBDD.rut)) == safe_rut.lower())
        .first()
    )
    if not sucursal:
        raise HTTPException(status_code=404, detail="No se encontro la sucursal seleccionada para ese cliente.")

    ultima_ods = (
        db.query(VentaODS)
        .filter(
            func.lower(func.trim(VentaODS.rut_cliente)) == safe_rut.lower(),
            func.lower(func.trim(VentaODS.direccion_sucursal)) == str(sucursal.direccion_sucursal or "").strip().lower(),
        )
        .order_by(VentaODS.id.desc())
        .first()
    )

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

    return {
        "comunaSucursal": sucursal.comuna or "",
        "latitudLongitud": sucursal.latitud_longitud or (
            f"{sucursal.latitud}, {sucursal.longitud}" if sucursal.latitud and sucursal.longitud else ""
        ),
        "referenciaUbicacion": sucursal.referencia_ubicacion or "",
        "cantidadCamaras": str(ultima_ods.numero_camaras_vigilar or ultima_ods.numero_camaras_instalar or "") if ultima_ods else "",
        "diasGrabacion": str(ultima_ods.dias_grabacion or "") if ultima_ods else "",
        "proveedorInternet": sucursal.proveedor_internet or "",
        "proveedorElectricidad": sucursal.proveedor_electricidad or "",
        "numeroClienteElectricidad": sucursal.nro_proveedor_electricidad or "",
        "diasApertura": sucursal.dias_funcionamiento or "",
        "horarioApertura": sucursal.horario_apertura or "",
        "horarioCierre": sucursal.horario_cierre or "",
        "tipoServicio": ultima_ods.tipo_servicio.replace(" | ", ", ") if ultima_ods and ultima_ods.tipo_servicio else "",
        "tipoPlan": ultima_ods.tipo_plan or "" if ultima_ods else "",
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
    }


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

    existing = (
        db.query(SucursalBBDD.id)
        .filter(
            func.lower(func.trim(SucursalBBDD.rut)) == rut.lower(),
            func.lower(func.trim(SucursalBBDD.nombre_sucursal)) == nombre_sucursal.lower(),
            func.lower(func.trim(SucursalBBDD.direccion_sucursal)) == direccion_sucursal.lower(),
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
    path.write_bytes(content)
    return str(path.relative_to(Path(__file__).resolve().parents[2])).replace("\\", "/")


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
        finalizado=not areas["soporte"],
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

    # Subir archivos adjuntos a Google Drive (best-effort, no bloquea el registro)
    if drive_files:
        try:
            upload_ods_files_to_drive(
                codigo=codigo,
                rut=rut,
                razon_social=razon_social,
                files=drive_files,
            )
        except (DriveReportError, Exception) as exc:
            _log.warning("Drive upload ODS %s fallÃ³ (el registro se guardÃ³ igual): %s", codigo, exc)

        try:
            folder_id = find_ods_drive_folder_id(codigo, rut, razon_social)
            if folder_id:
                record.drive_folder_id = folder_id
                record.drive_folder_url = build_ods_folder_url(folder_id)
                db.commit()
                db.refresh(record)
        except Exception as exc:
            db.rollback()
            _log.warning("No se pudo guardar drive_folder_url para %s: %s", codigo, exc)

    return record


def get_ods_codes(db: Session) -> list[str]:
    rows = db.query(VentaODS.codigo).order_by(VentaODS.codigo.asc()).all()
    return [str(row[0]).strip() for row in rows if row and row[0]]


def get_ods_detail(db: Session, codigo: str) -> dict[str, str]:
    record = (
        db.query(VentaODS)
        .filter(func.lower(func.trim(VentaODS.codigo)) == str(codigo or "").strip().lower())
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="ODS no encontrada.")

    archivos = db.query(VentaODSArchivo).filter(VentaODSArchivo.ods_id == record.id).all()
    archivo_por_tipo: dict[tuple[str, str], VentaODSArchivo] = {}
    for archivo in archivos:
        key = (
            str(archivo.tipo_documento or "").strip().lower(),
            str(archivo.servicio or "").strip().lower(),
        )
        archivo_por_tipo[key] = archivo

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
        "camInstalar": str(record.numero_camaras_instalar or ""),
        "camVigilar": str(record.numero_camaras_vigilar or ""),
        "montoACobrar": record.montos_a_cobrar or "",
        "diasAdicional": record.dias_monitoreo_adicional or "",
        "horario": record.horario_monitoreo or "",
        "materiales": record.materiales or "",
        "consideraciones": record.consideraciones or "",
        "cotizacion": cotizacion,
        "layout": layout,
        "oc": oc,
        "contrato": contrato,
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
    "creacion_clientes_bd": ("creacion_clientes_bd", "fecha_creacion_clientes_bd"),
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
    "requiere_puesto_nuevo",
    "numero_central_asignado",
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


def _fmt_date(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    return str(value)


def _is_true(value) -> bool:
    return bool(value)


def get_admin_ods_rows(db: Session) -> list[dict[str, object]]:
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
        carpeta = ods.cotizacion_path or ods.odc_path or ods.desglose_path or ods.contrato_path or ""
        out.append(
            {
                "codigo": ods.codigo or "",
                "fecha": _fmt_date(ods.created_at),
                "ejecutivo": ods.creado_por or "",
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
    now = datetime.utcnow()
    setattr(row, bool_field, bool(valor))
    if date_field:
        setattr(row, date_field, now if valor else None)
    if campo_limpio == "finalizado":
        st_row = _get_or_create_servicio_tecnico_venta_row(db, codigo_limpio)
        st_row.recepcion_solicitud_instalacion = bool(valor)
        st_row.fecha_recepcion_solicitud_instalacion = now if valor else None
    db.commit()
    return {
        "ok": True,
        "codigo": codigo_limpio,
        "campo": campo_limpio,
        "valor": bool(valor),
        "estado": "Completado" if valor else "Pendiente",
        "timestamp": _fmt_date(now) if valor else "",
        "notificacion": "pendiente_configuracion" if campo_limpio in {"recepcion_info", "envio_solicitud_instalacion"} and valor else "",
    }


def get_finanzas_ods_rows(db: Session) -> dict[str, object]:
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
                "ejecutivo": ods.creado_por or "",
                "rutCliente": ods.rut_cliente or "",
                "razonSocial": ods.razon_social or "",
                "nombreSucursal": ods.nombre_sucursal or "",
                "direccionSucursal": ods.direccion_sucursal or "",
                "tipoServicio": ods.tipo_servicio or "",
                "tipoPlan": ods.tipo_plan or "",
                "carpeta": carpeta_url,
                "fechaInicioServicio": getattr(fin, "fecha_inicio_servicio", "") if fin else "",
                "estados": {
                    "recepcion_datos_facturacion": _is_true(getattr(fin, "recepcion_datos_facturacion", False)),
                    "creacion_clientes_piriod": _is_true(getattr(fin, "creacion_clientes_piriod", False)),
                    "creacion_clientes_bd": _is_true(getattr(fin, "creacion_clientes_bd", False)),
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


def get_finanzas_ods_detail(db: Session, codigo: str) -> dict[str, str]:
    detail = get_ods_detail(db, codigo)
    comuna = ""
    direccion = detail.get("direccionSucursal") or ""
    if direccion:
        suc = (
            db.query(SucursalBBDD)
            .filter(func.lower(func.trim(SucursalBBDD.direccion)) == direccion.strip().lower())
            .first()
        )
        comuna = getattr(suc, "comuna", "") or ""
    return {
        "comuna": comuna,
        "numeroCamaras": detail.get("camInstalar") or "",
        "camarasVigilar": detail.get("camVigilar") or "",
        "montosACobrar": detail.get("montosACobrar") or "",
        "observacion": detail.get("observacion") or "",
        "requiereOC": detail.get("requiereOC") or "",
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
    now = datetime.utcnow()
    setattr(row, bool_field, bool(valor))
    if date_field:
        setattr(row, date_field, now if valor else None)
    db.commit()
    return {"ok": True, "codigo": codigo_limpio, "campo": campo_limpio, "valor": bool(valor), "estado": "Completado" if valor else "Pendiente", "timestamp": _fmt_date(now) if valor else ""}


def get_servicio_tecnico_ventas_rows(db: Session) -> list[dict[str, object]]:
    rows = (
        db.query(VentaODS, ServicioTecnicoVentaODT, AdministracionODT)
        .outerjoin(
            ServicioTecnicoVentaODT,
            func.lower(func.trim(ServicioTecnicoVentaODT.odt)) == func.lower(func.trim(VentaODS.codigo)),
        )
        .outerjoin(
            AdministracionODT,
            func.lower(func.trim(AdministracionODT.odt)) == func.lower(func.trim(VentaODS.codigo)),
        )
        .order_by(VentaODS.created_at.desc(), VentaODS.id.desc())
        .all()
    )
    out: list[dict[str, object]] = []
    for ods, st, adm in rows:
        tipo_servicio = str(ods.tipo_servicio or "")
        tipos_lista = [t.strip() for t in tipo_servicio.split("|") if t.strip()]
        if not _calcular_areas_aplicables(tipos_lista)["servtec"]:
            continue
        anulada = str(ods.estado or "").strip().lower() == "anulada"
        recepcion_ok = _is_true(getattr(st, "recepcion_solicitud_instalacion", False)) if st else False
        recepcion_ts = getattr(st, "fecha_recepcion_solicitud_instalacion", None) if st else None
        if not recepcion_ok:
            continue
        out.append(
            {
                "codigo": ods.codigo or "",
                "fecha": _fmt_date(ods.created_at),
                "ejecutivo": ods.creado_por or "",
                "rutCliente": ods.rut_cliente or "",
                "razonSocial": ods.razon_social or "",
                "nombreSucursal": ods.nombre_sucursal or "",
                "direccionSucursal": ods.direccion_sucursal or "",
                "tipoServicio": ods.tipo_servicio or "",
                "materialesBase": ods.materiales or "",
                "odt": ods.codigo or "",
                "anulada": anulada,
                "estados": {
                    "recepcion_solicitud_instalacion": recepcion_ok,
                    "instalacion_finalizada": _is_true(getattr(st, "instalacion_finalizada", False)),
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
    now = datetime.utcnow()
    setattr(row, bool_field, bool(valor))
    if date_field:
        setattr(row, date_field, now if valor else None)
    db.commit()
    return {"ok": True, "codigo": codigo_limpio, "campo": campo_limpio, "valor": bool(valor), "timestamp": _fmt_date(now) if valor else ""}


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
    setattr(row, campo_limpio, valor_limpio)
    db.commit()
    return {"ok": True, "codigo": codigo_limpio, "campo": campo_limpio, "valor": valor_limpio}


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
    record.tipo_cliente = _clean_text(payload.tipoCliente)
    record.numero_camaras_instalar = _to_optional_int(payload.camInstalar)
    record.numero_camaras_vigilar = _to_optional_int(payload.camVigilar)
    record.montos_a_cobrar = _clean_text(payload.montoACobrar)
    record.dias_monitoreo_adicional = _clean_text(payload.diasAdicional)
    record.horario_monitoreo = _clean_text(payload.horario)
    record.materiales = _clean_text(payload.materiales)
    record.consideraciones = _clean_text(payload.consideraciones)
    record.observacion = _clean_text(payload.observacion)
    record.tipo_servicio = " | ".join(tipos) if tipos else ""
    record.creado_por = _clean_text(usuario_email) or record.creado_por

    cotizacion_path = _upsert_ods_archivo(db, record.id, record.codigo, "Cotizacion", "General", payload.cotizacion)
    layout_path = _upsert_ods_archivo(db, record.id, record.codigo, "Layout", "Instalacion", payload.layout)
    oc_path = _upsert_ods_archivo(db, record.id, record.codigo, "ODC", "Instalacion", payload.oc)

    if cotizacion_path:
        record.cotizacion_path = cotizacion_path
    if oc_path:
        record.odc_path = oc_path
    if layout_path:
        existing_layout = (
            db.query(VentaODSArchivo)
            .filter(
                VentaODSArchivo.ods_id == record.id,
                func.lower(func.trim(VentaODSArchivo.tipo_documento)) == "layout",
            )
            .first()
        )
        if existing_layout:
            existing_layout.ruta_archivo = layout_path

    db.commit()
    db.refresh(record)
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
    return {"headers": headers, "rows": data_rows}


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
    rows = (
        db.query(VentaODS, OperacionesVentaODT, ServicioTecnicoVentaODT)
        .outerjoin(
            OperacionesVentaODT,
            func.lower(func.trim(OperacionesVentaODT.odt)) == func.lower(func.trim(VentaODS.codigo)),
        )
        .outerjoin(
            ServicioTecnicoVentaODT,
            func.lower(func.trim(ServicioTecnicoVentaODT.odt)) == func.lower(func.trim(VentaODS.codigo)),
        )
        .order_by(VentaODS.created_at.desc(), VentaODS.id.desc())
        .all()
    )
    out: list[dict] = []
    total_anuladas = 0
    for ods, op, st in rows:
        tipo_servicio_op = str(ods.tipo_servicio or "")
        tipos_lista_op = [t.strip() for t in tipo_servicio_op.split("|") if t.strip()]
        if not _calcular_areas_aplicables(tipos_lista_op)["operaciones"]:
            continue
        estado_ods = str(ods.estado or "").strip()
        anulada = estado_ods.lower() == "anulada"
        if anulada:
            total_anuladas += 1
        terminado_soporte = _is_true(getattr(st, "finalizado", False))
        requiere_puesto = getattr(st, "requiere_puesto_nuevo", "") if st else ""
        numero_central = getattr(st, "numero_central_asignado", "") if st else ""
        out.append({
            "codigo": ods.codigo or "",
            "fecha": _fmt_date(ods.created_at),
            "ejecutivo": ods.creado_por or "",
            "rutCliente": ods.rut_cliente or "",
            "razonSocial": ods.razon_social or "",
            "nombreSucursal": ods.nombre_sucursal or "",
            "direccionSucursal": ods.direccion_sucursal or "",
            "tipoServicio": ods.tipo_servicio or "",
            "tipoPlan": ods.tipo_plan or "",
            "terminadoSoporte": terminado_soporte,
            "requierePuestoNuevo": requiere_puesto or "",
            "numeroCentralAsignado": numero_central or "",
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
    now = datetime.utcnow()
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
    row.fecha_inicio_servicio = str(fecha or "").strip()[:40]
    db.commit()
    return {"ok": True, "codigo": codigo_limpio, "fechaInicioServicio": row.fecha_inicio_servicio}


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
        "solo instalacion", "servicio tecnico", "servicio tÃ©cnico",
        "desinstalacion", "desinstalaciÃ³n", "monitoreo adicional",
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


def _area_estado(checks: list[tuple[str, bool]]) -> dict:
    detalles = [name for name, done in checks if not done]
    completados = [name for name, done in checks if done]
    if not detalles:
        estado = "Terminado"
    elif not completados:
        estado = "Pendiente"
    else:
        estado = "En proceso"
    return {"estado": estado, "detalles": detalles, "completados": completados}


def get_comercial_todo(db: Session) -> dict:
    rows = (
        db.query(VentaODS, AdministracionODT, ServicioTecnicoVentaODT, FinanzasODT, OperacionesVentaODT)
        .outerjoin(AdministracionODT, func.lower(func.trim(AdministracionODT.odt)) == func.lower(func.trim(VentaODS.codigo)))
        .outerjoin(ServicioTecnicoVentaODT, func.lower(func.trim(ServicioTecnicoVentaODT.odt)) == func.lower(func.trim(VentaODS.codigo)))
        .outerjoin(FinanzasODT, func.lower(func.trim(FinanzasODT.odt)) == func.lower(func.trim(VentaODS.codigo)))
        .outerjoin(OperacionesVentaODT, func.lower(func.trim(OperacionesVentaODT.odt)) == func.lower(func.trim(VentaODS.codigo)))
        .order_by(VentaODS.created_at.desc(), VentaODS.id.desc())
        .all()
    )
    out: list[dict] = []
    for ods, adm, st, fin, op in rows:
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

        admin_checks: list[tuple[str, bool]] = [
            ("Recepcion info", _is_true(getattr(adm, "recepcion_info", False))),
        ]
        if not omitir_registros:
            admin_checks.append(("Registro Alpha3", _is_true(getattr(adm, "registro_alpha3", False))))
            admin_checks.append(("Registro Intranet", _is_true(getattr(adm, "registro_intranet", False))))
        admin_checks.append(("Envio solicitud instalacion", _is_true(getattr(adm, "envio_solicitud_instalacion", False))))
        admin_checks.append(("Envio datos facturacion", _is_true(getattr(adm, "envio_datos_facturacion", False))))
        if not excluir_carta:
            admin_checks.append(("Carta de bienvenida", _is_true(getattr(adm, "envio_carta_bienvenida", False))))
        area_admin = _area_estado(admin_checks)

        if areas_aplica["servtec"]:
            area_servicio = _area_estado([
                ("Llamar cliente", bool(_clean_text(getattr(st, "llamar_cliente", None)))),
                ("Instalacion finalizada", _is_true(getattr(st, "instalacion_finalizada", False))),
            ])
        else:
            area_servicio = _area_estado([("No aplica", True)])

        if areas_aplica["soporte"]:
            area_soporte = _area_estado([
                ("Soporte terminado", _is_true(getattr(st, "finalizado", False))),
            ])
        else:
            area_soporte = _area_estado([("No aplica", True)])

        if areas_aplica["operaciones"]:
            area_operaciones = _area_estado([
                ("Fecha coordinacion", _is_true(getattr(op, "fecha_coordinacion", False))),
                ("Reunion coordinacion", _is_true(getattr(op, "reunion_coordinacion", False))),
                ("Coord. apertura puesto", _is_true(getattr(op, "coord_apertura_puesto", False))),
                ("Coord. equipo", _is_true(getattr(op, "coord_equipo", False))),
            ])
        else:
            area_operaciones = _area_estado([("No aplica", True)])

        area_finanzas = _area_estado([
            ("Recepcion datos facturacion", _is_true(getattr(fin, "recepcion_datos_facturacion", False))),
            ("Creacion clientes Piriod", _is_true(getattr(fin, "creacion_clientes_piriod", False))),
            ("Creacion clientes BD", _is_true(getattr(fin, "creacion_clientes_bd", False))),
            ("Facturacion instalacion", _is_true(getattr(fin, "facturacion_instalacion", False))),
            ("Facturacion servicio", _is_true(getattr(fin, "facturacion_servicio", False))),
        ])

        out.append({
            "codigo": ods.codigo or "",
            "fecha": _fecha_corta(ods.created_at),
            "ejecutivo": ods.creado_por or "",
            "rutCliente": ods.rut_cliente or "",
            "razonSocial": ods.razon_social or "",
            "nombreSucursal": ods.nombre_sucursal or "",
            "direccionSucursal": ods.direccion_sucursal or "",
            "tipoServicio": tipo_servicio,
            "tipoPlan": ods.tipo_plan or "",
            "carpeta": ods.cotizacion_path or ods.odc_path or ods.desglose_path or ods.contrato_path or "",
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
