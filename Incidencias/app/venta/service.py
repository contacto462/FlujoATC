from __future__ import annotations

import base64
import json
import re
import ssl
import unicodedata
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    ClienteBBDD,
    SucursalBBDD,
    SucursalContactoEmergencia,
    SucursalGuardia,
    SucursalPersonaAutorizada,
    VentaODS,
    VentaODSArchivo,
)
from app.services import IncidenciasService
from app.venta.schemas import VentaClienteCreateRequest, VentaODSArchivoRequest, VentaODSCreateRequest, VentaSucursalCreateRequest


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

VENTA_EJECUTIVOS = [
    "Sebastian Storm",
    "Teodoro Storm",
    "Lucas Cortes",
    "Gianpiero Lubiano",
]

VENTA_UPLOADS_DIR = Path(__file__).resolve().parents[2] / "uploads" / "venta_ods"


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


def get_ejecutivos_venta() -> list[str]:
    return VENTA_EJECUTIVOS[:]


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
        "nombreRepresentante": cliente.nombre_representante or cliente.contacto or "",
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
    return f"{prefijo}{max_number + 1:04d}"


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
    )
    db.add(record)
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
        db.add(VentaODSArchivo(
            ods_id=record.id,
            codigo_ods=codigo,
            tipo_documento=_clean_text(archivo.tipoDocumento),
            servicio=_clean_text(archivo.servicio),
            nombre_archivo=_clean_text(archivo.nombre),
            mime_type=_clean_text(archivo.tipo),
            ruta_archivo=ruta,
        ))

    db.commit()
    db.refresh(record)
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
    for archivo in archivos:
        tipo = str(archivo.tipo_documento or "").strip().lower()
        if tipo == "layout" and not layout:
            layout = archivo.ruta_archivo or ""

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
    }


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
            row.region or "",
            row.comuna or "",
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
        .filter(SucursalContactoEmergencia.sucursal_id == row.id)
        .order_by(SucursalContactoEmergencia.id.asc())
        .first()
    )
    if primer_contacto:
        primer_contacto.nombre = nombre_emergencia
    elif nombre_emergencia:
        db.add(SucursalContactoEmergencia(
            sucursal_id=row.id,
            nombre=nombre_emergencia,
        ))

    db.commit()


def _fetch_catalog(path: str) -> dict:
    base_url = (settings.venta_catalogo_base_url or "https://apis.digital.gob.cl/dpa").rstrip("/")
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
        verify_ssl = settings.venta_catalogo_verify_ssl
        if "apis.digital.gob.cl" in base_url:
            verify_ssl = False
        if base_url.startswith("https://") and not verify_ssl:
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
