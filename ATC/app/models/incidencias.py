from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ATC.app.core.db import Base
from ATC.app.models.user import User  # noqa: F401 — re-export; incidencias_service lo importa desde aquí

class Registro(Base):
    __tablename__ = "incidencias"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    odt: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    fecha_registro: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now())
    puesto: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    cliente: Mapped[str] = mapped_column(String(255), index=True)
    problema: Mapped[str] = mapped_column(String(255))
    detalle_problema: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    derivacion: Mapped[str] = mapped_column(String(120), default="Servicio TÃ©cnico")
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
    causa_cierre: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    accion_cierre: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resultado_cierre: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
    rut: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, unique=True)
    email_facturas: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    nombre_representante: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rut_representante: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    telefono: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    email_representante: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ejecutivo_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    fecha_creacion: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.now())

    sucursales: Mapped[list["SucursalBBDD"]] = relationship(back_populates="cliente_ref")
    venta_ods: Mapped[list["VentaODS"]] = relationship(back_populates="cliente_ref")


class SucursalBBDD(Base):
    __tablename__ = "bbdd_sucursales"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rut: Mapped[str] = mapped_column(String(40), ForeignKey("bbdd_clientes.rut"), index=True)
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
    habilitada: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    estado: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    cliente_ref: Mapped[Optional["ClienteBBDD"]] = relationship(back_populates="sucursales")
    contactos_emergencia: Mapped[list["SucursalContactoEmergencia"]] = relationship(
        back_populates="sucursal",
        cascade="all, delete-orphan",
    )
    personas_autorizadas: Mapped[list["SucursalPersonaAutorizada"]] = relationship(
        back_populates="sucursal",
        cascade="all, delete-orphan",
    )
    guardias: Mapped[list["SucursalGuardia"]] = relationship(
        back_populates="sucursal",
        cascade="all, delete-orphan",
    )
    info_extra: Mapped[Optional["SucursalInfoExtra"]] = relationship(
        back_populates="sucursal",
        uselist=False,
        cascade="all, delete-orphan",
    )


class SucursalInfoExtra(Base):
    __tablename__ = "sucursal_info_extra"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sucursal_id: Mapped[int] = mapped_column(ForeignKey("bbdd_sucursales.id", ondelete="CASCADE"), unique=True, index=True)
    plan_cuadrante: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    carabineros: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    bomberos: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    seguridad_ciudadana: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    codigo_p2p: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    codigo_dss: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    telefono_porton: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    telefono_recepcion: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    internet_atc: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    sucursal: Mapped["SucursalBBDD"] = relationship(back_populates="info_extra")


class SucursalContactoEmergencia(Base):
    __tablename__ = "sucursal_contactos_emergencia"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sucursal_id: Mapped[int] = mapped_column(ForeignKey("bbdd_sucursales.id", ondelete="CASCADE"), index=True)
    nombre: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rut: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    telefono: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    sucursal: Mapped["SucursalBBDD"] = relationship(back_populates="contactos_emergencia")


class SucursalPersonaAutorizada(Base):
    __tablename__ = "sucursal_personas_autorizadas"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sucursal_id: Mapped[int] = mapped_column(ForeignKey("bbdd_sucursales.id", ondelete="CASCADE"), index=True)
    nombre: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rut: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    telefono: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    clave_verde: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    clave_roja: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    habilitado: Mapped[bool] = mapped_column(default=True, server_default="1")

    sucursal: Mapped["SucursalBBDD"] = relationship(back_populates="personas_autorizadas")


class SucursalGuardia(Base):
    __tablename__ = "sucursal_guardias"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sucursal_id: Mapped[int] = mapped_column(ForeignKey("bbdd_sucursales.id", ondelete="CASCADE"), index=True)
    nombre: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rut: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    telefono: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    horario_desde: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    horario_hasta: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    sucursal: Mapped["SucursalBBDD"] = relationship(back_populates="guardias")


class VentaODS(Base):
    __tablename__ = "venta_comercial"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    codigo: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    creado_por: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rut_cliente: Mapped[str] = mapped_column(String(40), ForeignKey("bbdd_clientes.rut"), index=True)
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
    contrato_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    drive_folder_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    drive_folder_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    piriod_customer_id: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    estado: Mapped[str] = mapped_column(String(80), default="Registrada", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    cliente_ref: Mapped[Optional["ClienteBBDD"]] = relationship(back_populates="venta_ods")
    archivos: Mapped[list["VentaODSArchivo"]] = relationship(
        back_populates="ods",
        cascade="all, delete-orphan",
    )
    administracion: Mapped[Optional["AdministracionODT"]] = relationship(back_populates="ods_ref")
    finanzas: Mapped[Optional["FinanzasODT"]] = relationship(back_populates="ods_ref")
    servicio_tecnico: Mapped[Optional["ServicioTecnicoVentaODT"]] = relationship(back_populates="ods_ref")
    soporte_tecnico: Mapped[Optional["SoporteTecnicoVentaODT"]] = relationship(back_populates="ods_ref")
    operaciones: Mapped[Optional["OperacionesVentaODT"]] = relationship(back_populates="ods_ref")


class VentaODSArchivo(Base):
    __tablename__ = "venta_ods_archivos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ods_id: Mapped[int] = mapped_column(ForeignKey("venta_comercial.id", ondelete="CASCADE"), index=True)
    codigo_ods: Mapped[str] = mapped_column(String(30), index=True)
    tipo_documento: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    servicio: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    nombre_archivo: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    ruta_archivo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    ods: Mapped["VentaODS"] = relationship(back_populates="archivos")


class AdministracionODT(Base):
    __tablename__ = "venta_administracion"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    odt: Mapped[str] = mapped_column(String(30), ForeignKey("venta_comercial.codigo", ondelete="CASCADE"), unique=True, index=True)
    tecnico: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    acompanante: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    fecha_derivacion: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    recepcion_info: Mapped[bool] = mapped_column(default=False)
    fecha_recepcion_info: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    registro_alpha3: Mapped[bool] = mapped_column(default=False)
    fecha_registro_alpha3: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    registro_intranet: Mapped[bool] = mapped_column(default=False)
    fecha_registro_intranet: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    envio_solicitud_instalacion: Mapped[bool] = mapped_column(default=False)
    fecha_envio_solicitud_instalacion: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    envio_datos_facturacion: Mapped[bool] = mapped_column(default=False)
    fecha_envio_datos_facturacion: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    envio_carta_bienvenida: Mapped[bool] = mapped_column(default=False)
    fecha_envio_carta_bienvenida: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finalizado: Mapped[bool] = mapped_column(default=False)
    fecha_cierre: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    ods_ref: Mapped["VentaODS"] = relationship(back_populates="administracion")


class FinanzasODT(Base):
    __tablename__ = "venta_finanzas"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    odt: Mapped[str] = mapped_column(String(30), ForeignKey("venta_comercial.codigo", ondelete="CASCADE"), unique=True, index=True)
    fecha_inicio_servicio: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    recepcion_datos_facturacion: Mapped[bool] = mapped_column(default=False)
    fecha_recepcion_datos_facturacion: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    creacion_clientes_piriod: Mapped[bool] = mapped_column(default=False)
    fecha_creacion_clientes_piriod: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    facturacion_instalacion: Mapped[bool] = mapped_column(default=False)
    fecha_facturacion_instalacion: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    facturacion_servicio: Mapped[bool] = mapped_column(default=False)
    fecha_facturacion_servicio: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    finalizado: Mapped[bool] = mapped_column(default=False)
    fecha_cierre: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    ods_ref: Mapped["VentaODS"] = relationship(back_populates="finanzas")


class ServicioTecnicoVentaODT(Base):
    __tablename__ = "venta_servicio_tecnico"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    odt: Mapped[str] = mapped_column(String(30), ForeignKey("venta_comercial.codigo", ondelete="CASCADE"), unique=True, index=True)

    recepcion_solicitud_instalacion: Mapped[bool] = mapped_column(default=False)
    fecha_recepcion_solicitud_instalacion: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    llamar_cliente: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    solicitud_materiales: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fecha_inicio_instalacion: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    fecha_fin_instalacion: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    tecnico_a_cargo: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    acompanante: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    instalacion_finalizada: Mapped[bool] = mapped_column(default=False)
    fecha_instalacion_finalizada: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finalizado: Mapped[bool] = mapped_column(default=False)
    fecha_cierre: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    layout_final: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    ods_ref: Mapped["VentaODS"] = relationship(back_populates="servicio_tecnico")


class SoporteTecnicoVentaODT(Base):
    __tablename__ = "venta_soporte_tecnico"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    odt: Mapped[str] = mapped_column(String(30), ForeignKey("venta_comercial.codigo", ondelete="CASCADE"), unique=True, index=True)

    configuracion_camaras: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    fecha_configuracion_camaras: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    posicionamiento_imagen: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    fecha_posicionamiento_imagen: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    enlace_servidor: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    fecha_enlace_servidor: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    configuracion_ivs: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    fecha_configuracion_ivs: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    plan_grabacion: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    fecha_plan_grabacion: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    configuracion_cliente: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    fecha_configuracion_cliente: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    vb_final_servicio: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    fecha_vb_final_servicio: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    terminado: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    fecha_terminado: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    requiere_puesto_nuevo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    numero_central_asignado: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    camaras_registradas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    ods_ref: Mapped["VentaODS"] = relationship(back_populates="soporte_tecnico")


class RegistroCorreoCliente(Base):
    __tablename__ = "registros_correos_cliente"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    odt: Mapped[str] = mapped_column(String(30), index=True)
    sucursal: Mapped[str] = mapped_column(String(255))
    fecha_envio: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now())
    observacion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    estado: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


class IncidenciaImagenTabla(Base):
    __tablename__ = "incidencias_imagenes_odt"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    odt: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    sucursal: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    imagenes: Mapped[str] = mapped_column(Text, default="[]")
    created_by: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(), onupdate=lambda: datetime.now())


class MantencionImagenSucursal(Base):
    __tablename__ = "mantenciones_imagenes_sucursal"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sucursal_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    sucursal: Mapped[str] = mapped_column(String(255), index=True)
    imagenes: Mapped[str] = mapped_column(Text, default="[]")
    created_by: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(), onupdate=lambda: datetime.now())


class LoginSession(Base):
    __tablename__ = "login_sessions"

    token: Mapped[str] = mapped_column(String(120), primary_key=True)
    usuario: Mapped[str] = mapped_column(String(255), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    area_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    department: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class Rendicion(Base):
    __tablename__ = "rendiciones"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    codigo_diario: Mapped[str] = mapped_column(String(120), index=True)
    fecha_registro: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now())
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


class RendicionPago(Base):
    __tablename__ = "rendiciones_pagos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    codigo_diario: Mapped[str] = mapped_column(String(120), index=True)
    tecnico: Mapped[str] = mapped_column(String(255), index=True)
    rut_tecnico: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    tipo_pago: Mapped[str] = mapped_column(String(60), default="Transferencia")
    fecha_pago: Mapped[datetime] = mapped_column(DateTime)
    monto: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    creado_por: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    fecha_registro: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now())


class RendicionViaticoCap(Base):
    __tablename__ = "rendiciones_viatico_cap"

    codigo_diario: Mapped[str] = mapped_column(String(120), primary_key=True)
    viatico_max: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("10000"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(), onupdate=lambda: datetime.now())
    updated_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class ProtocoloRegistro(Base):
    __tablename__ = "protocolos_registro"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fecha_registro: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(), index=True)
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

    informes: Mapped[list["ProtocoloInforme"]] = relationship(
        back_populates="registro",
        cascade="all, delete-orphan",
    )


class MantencionVinaConfig(Base):
    """Config de una sola fila para 'Mantenciones Viña del Mar': cuantos
    puntos de camara estan vigentes actualmente (no siempre son 215)."""

    __tablename__ = "mantencion_vina_config"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    total_puntos: Mapped[int] = mapped_column(default=215)
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
    registro_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("protocolos_registro.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
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

    registro: Mapped[Optional["ProtocoloRegistro"]] = relationship(back_populates="informes")


class OperacionesVentaODT(Base):
    __tablename__ = "venta_operaciones"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    odt: Mapped[str] = mapped_column(String(30), ForeignKey("venta_comercial.codigo", ondelete="CASCADE"), unique=True, index=True)

    fecha_inicio_servicio: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    fecha_coordinacion: Mapped[bool] = mapped_column(Boolean, default=False)
    ts_fecha_coordinacion: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    reunion_coordinacion: Mapped[bool] = mapped_column(Boolean, default=False)
    ts_reunion_coordinacion: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    coord_apertura_puesto: Mapped[bool] = mapped_column(Boolean, default=False)
    ts_coord_apertura_puesto: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    coord_equipo: Mapped[bool] = mapped_column(Boolean, default=False)
    ts_coord_equipo: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    terminado: Mapped[bool] = mapped_column(Boolean, default=False)
    ts_terminado: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    ods_ref: Mapped["VentaODS"] = relationship(back_populates="operaciones")


class PruebaSonido(Base):
    __tablename__ = "pruebas_sonido"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sucursal_id: Mapped[int] = mapped_column(ForeignKey("bbdd_sucursales.id", ondelete="CASCADE"), index=True)
    anio: Mapped[int] = mapped_column(index=True)
    mes: Mapped[int] = mapped_column(index=True)
    resultado: Mapped[str] = mapped_column(String(20))  # exitoso | falla
    observacion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    operador: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    incidencia_odt: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())