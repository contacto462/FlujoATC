from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Registro(Base):
    __tablename__ = "registro"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    odt: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    fecha_registro: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    puesto: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    cliente: Mapped[str] = mapped_column(String(255), index=True)
    problema: Mapped[str] = mapped_column(String(255))
    detalle_problema: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    derivacion: Mapped[str] = mapped_column(String(120), default="Servicio Técnico")
    observacion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    observacion_soporte: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    observacion_servicio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tecnicos: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    acompanante: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    estado: Mapped[str] = mapped_column(String(100), default="Pendiente", index=True)
    dias_ejecucion: Mapped[Optional[int]] = mapped_column(nullable=True)
    fecha_cierre: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    fecha_derivacion_area: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    fecha_derivacion_tecnico: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    direccion: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    observacion_final: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    observacion_pendiente: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    prioridad: Mapped[Optional[int]] = mapped_column(nullable=True)
    materiales: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    responsable_cierre: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    causa_cierre: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    accion_cierre: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    resultado_cierre: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    pruebas_cierre: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requiere_seguimiento: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    porcentaje_avance: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    foto_1: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    foto_2: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    foto_3: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    drive_cierre_folder_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    drive_cierre_folder_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pdf_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ClienteBBDD(Base):
    __tablename__ = "bbdd_clientes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cliente: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    giro: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    direccion: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    comuna: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    contacto: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    correo: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rut: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    email_facturas: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    nombre_representante: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rut_representante: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    telefono: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    email_representante: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ejecutivo_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    fecha_creacion: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.now())


class SucursalBBDD(Base):
    __tablename__ = "bbdd_sucursales"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rut: Mapped[str] = mapped_column(String(40), index=True)
    nombre_empresa: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    nombre_sucursal: Mapped[str] = mapped_column(String(255), index=True)
    direccion_sucursal: Mapped[str] = mapped_column(String(255), index=True)
    region: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    comuna: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    referencia_ubicacion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latitud: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    longitud: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    latitud_longitud: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    email_facturas: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    proveedor_internet: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    proveedor_electricidad: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    nro_proveedor_electricidad: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    horario_apertura: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    horario_cierre: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    dias_funcionamiento: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SucursalContactoEmergencia(Base):
    __tablename__ = "sucursal_contactos_emergencia"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sucursal_id: Mapped[int] = mapped_column(index=True)
    nombre: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rut: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    telefono: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class SucursalPersonaAutorizada(Base):
    __tablename__ = "sucursal_personas_autorizadas"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sucursal_id: Mapped[int] = mapped_column(index=True)
    nombre: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rut: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    telefono: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    clave_verde: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    clave_roja: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class SucursalGuardia(Base):
    __tablename__ = "sucursal_guardias"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sucursal_id: Mapped[int] = mapped_column(index=True)
    nombre: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rut: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    telefono: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    horario_desde: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    horario_hasta: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)


class VentaODS(Base):
    __tablename__ = "venta_ods"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    codigo: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    ejecutivo_venta: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    creado_por: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rut_cliente: Mapped[str] = mapped_column(String(40), index=True)
    razon_social: Mapped[str] = mapped_column(String(255), index=True)
    direccion_sucursal: Mapped[str] = mapped_column(String(255), index=True)
    nombre_sucursal: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tipo_cliente: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    tipo_servicio: Mapped[str] = mapped_column(Text)
    tipo_plan: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    observacion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    numero_camaras_instalar: Mapped[Optional[int]] = mapped_column(nullable=True)
    numero_camaras_desinstalar: Mapped[Optional[int]] = mapped_column(nullable=True)
    numero_camaras_vigilar: Mapped[Optional[int]] = mapped_column(nullable=True)
    dias_grabacion: Mapped[Optional[int]] = mapped_column(nullable=True)
    dias_monitoreo_desde: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    dias_monitoreo_hasta: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    dias_monitoreo_adicional: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    horario_monitoreo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    materiales: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    consideraciones: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    agua_bano: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    requiere_oc: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    montos_a_cobrar: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cotizacion_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    odc_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    desglose_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    estado: Mapped[str] = mapped_column(String(80), default="Registrada", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class VentaODSArchivo(Base):
    __tablename__ = "venta_ods_archivos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ods_id: Mapped[int] = mapped_column(index=True)
    codigo_ods: Mapped[str] = mapped_column(String(30), index=True)
    tipo_documento: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    servicio: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    nombre_archivo: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    ruta_archivo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CatalogoCliente(Base):
    __tablename__ = "catalogo_clientes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cliente: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OdtVenta(Base):
    __tablename__ = "odt_ventas"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    odt: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    observacion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    servicio: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tecnico: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    fecha: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cliente: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    direccion: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    estado: Mapped[str] = mapped_column(String(100), default="En Proceso")
    observacion_final: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avance: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    fecha_cierre: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    foto_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    acompanante: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    prioridad: Mapped[Optional[int]] = mapped_column(nullable=True)


class AdministracionODT(Base):
    __tablename__ = "administracion_odt"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    odt: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    tecnico: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    acompanante: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    fecha_derivacion: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finalizado: Mapped[bool] = mapped_column(default=False)
    fecha_cierre: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ContactoEmergencia(Base):
    __tablename__ = "contactos_emergencia"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sucursal: Mapped[str] = mapped_column(String(255), index=True)
    nombre: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    celular: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    prioridad: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)


class RegistroCorreoCliente(Base):
    __tablename__ = "registros_correos_cliente"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    odt: Mapped[str] = mapped_column(String(30), index=True)
    sucursal: Mapped[str] = mapped_column(String(255))
    fecha_envio: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    observacion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    estado: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


class IncidenciaImagenTabla(Base):
    __tablename__ = "incidencias_imagenes_odt"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    odt: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    sucursal: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    imagenes: Mapped[str] = mapped_column(Text, default="[]")
    created_by: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MantencionImagenSucursal(Base):
    __tablename__ = "mantenciones_imagenes_sucursal"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sucursal_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    sucursal: Mapped[str] = mapped_column(String(255), index=True)
    imagenes: Mapped[str] = mapped_column(Text, default="[]")
    created_by: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LoginSession(Base):
    __tablename__ = "login_sessions"

    token: Mapped[str] = mapped_column(String(120), primary_key=True)
    usuario: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class Tarea(Base):
    __tablename__ = "tareas"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    codigo: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    usuario_soporte: Mapped[str] = mapped_column(String(255))
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    cliente: Mapped[str] = mapped_column(String(255))
    tipo_tarea: Mapped[str] = mapped_column(String(255))
    especificacion: Mapped[str] = mapped_column(String(255))
    descripcion: Mapped[str] = mapped_column(Text)
    solicitante: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    estado: Mapped[str] = mapped_column(String(80), default="Pendiente")
    tecnico_cierre: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    fecha_cierre: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    dias_ejecucion: Mapped[Optional[int]] = mapped_column(nullable=True)


class SyncOutbox(Base):
    __tablename__ = "sync_outbox"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_key: Mapped[str] = mapped_column(String(80), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Rendicion(Base):
    __tablename__ = "rendiciones"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    folio: Mapped[int] = mapped_column(unique=True, index=True)
    codigo_diario: Mapped[str] = mapped_column(String(120), index=True)
    fecha_registro: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    tecnico: Mapped[str] = mapped_column(String(255), index=True)
    mail: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    odt: Mapped[str] = mapped_column(String(30), index=True)
    cliente: Mapped[str] = mapped_column(String(255), index=True)
    comuna: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tipo_gasto: Mapped[str] = mapped_column(String(120))
    tipo_documento: Mapped[str] = mapped_column(String(120))
    nro_documento: Mapped[str] = mapped_column(String(120), index=True)
    fecha_documento: Mapped[datetime] = mapped_column(DateTime)
    monto_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    descripcion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    url_boleta: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    url_informe: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    documento: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    estado_revision: Mapped[str] = mapped_column(String(30), default="Pendiente")


class ProtocoloRegistro(Base):
    __tablename__ = "protocolos_registro"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fecha_registro: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    encargado: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    grupo: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    cliente: Mapped[str] = mapped_column(String(255), index=True)
    sucursal: Mapped[str] = mapped_column(String(255), index=True)
    tipo_protocolo: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    detectado: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    efectivo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    sirena: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    voz: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    carabineros: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    alpha3: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    informado: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    bitacora: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    protocolo_exitoso: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    puesto: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    operador: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    observaciones_raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    observaciones_formal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ProtocoloInforme(Base):
    __tablename__ = "protocolos_informes"
    __table_args__ = (
        UniqueConstraint("tipo_informe", "registro_id", name="uq_protocolo_informe_individual"),
        UniqueConstraint(
            "tipo_informe",
            "cliente",
            "sucursal",
            "periodo_inicio",
            "periodo_fin",
            name="uq_protocolo_informe_semanal",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tipo_informe: Mapped[str] = mapped_column(String(20), index=True)  # INDIVIDUAL | SEMANAL
    estado: Mapped[str] = mapped_column(String(30), default="PENDIENTE", index=True)  # PENDIENTE | OK | ERROR
    registro_id: Mapped[Optional[int]] = mapped_column(nullable=True, index=True)
    cliente: Mapped[str] = mapped_column(String(255), index=True)
    sucursal: Mapped[str] = mapped_column(String(255), index=True)
    periodo_inicio: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    periodo_fin: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    titulo: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    pdf_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    drive_file_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    drive_folder_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    drive_folder_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error_detalle: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
