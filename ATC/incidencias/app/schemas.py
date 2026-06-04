from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    nombre_tecnico: str
    clave: str
    token: str
    destino: Optional[str] = "pendientes"


class LoginResponse(BaseModel):
    success: bool
    message: Optional[str] = None
    redirect: Optional[str] = None
    token: Optional[str] = None


class IncidenciaNueva(BaseModel):
    puesto: Optional[str] = None
    cliente: str
    tipo_incidencia: str = Field(alias="tipoIncidencia")
    descripcion: str
    estado: str = "Pendiente"
    token: Optional[str] = ""


class FormularioRegistro(BaseModel):
    encargado_soporte: Optional[str] = None
    grupo: Optional[str] = None
    fecha: Optional[datetime] = None
    cliente: str
    problema: str
    detalle_problema: Optional[str] = None
    derivacion: str = "Servicio Técnico"
    observacion: Optional[str] = None
    odt: Optional[str] = None
    tecnicos: Optional[str] = None
    acompanante: Optional[str] = None
    estado: str = "Pendiente"
    dias_ejecucion: Optional[int] = None
    foto: Optional[str] = None
    observacion_final: Optional[str] = None
    fecha_cierre: Optional[datetime] = None


class CerrarIncidenciaRequest(BaseModel):
    odt: str
    observacion: str
    responsable_cierre: str = Field(alias="responsableCierre")
    causa_cierre: str = Field(alias="causaCierre")
    accion_cierre: str = Field(alias="accionCierre")
    resultado_cierre: str = Field(alias="resultadoCierre")
    pruebas_cierre: list[str] = Field(default_factory=list, alias="pruebasCierre")
    materiales: list[dict] = Field(default_factory=list)
    materiales_sin_uso: bool = Field(default=False, alias="materialesSinUso")
    requiere_seguimiento: bool = Field(default=False, alias="requiereSeguimiento")


class FinalizarIncidenciaRequest(BaseModel):
    odt: str
    observacion: str
    fotos_base64: list[str] = Field(default_factory=list, alias="fotosBase64")
    responsable_cierre: str = Field(alias="responsableCierre")
    causa_cierre: str = Field(alias="causaCierre")
    accion_cierre: str = Field(alias="accionCierre")
    resultado_cierre: str = Field(alias="resultadoCierre")
    pruebas_cierre: list[str] = Field(default_factory=list, alias="pruebasCierre")
    materiales: list[dict] = Field(default_factory=list)
    materiales_sin_uso: bool = Field(default=False, alias="materialesSinUso")
    requiere_seguimiento: bool = Field(default=False, alias="requiereSeguimiento")


class EnProcesoRequest(BaseModel):
    odt: str
    avance: int
    observacion: str
    token: Optional[str] = ""


class DerivarTecnicoRequest(BaseModel):
    odt: str
    tecnico: str
    acompanante: Optional[str] = ""
    derivacion: Optional[str] = "Servicio Técnico"
    estado: Optional[str] = "Pendiente"


class EditarIncidenciaTablaRequest(BaseModel):
    token: str
    odt: str
    derivacion: Optional[str] = None
    observacion: Optional[str] = None
    prioridad: Optional[str] = None
    observacion_servicio: Optional[str] = Field(default=None, alias="observacionServicio")
    observacion_final: Optional[str] = Field(default=None, alias="observacionFinal")
    repetida_odt_ref: Optional[str] = Field(default=None, alias="repetidaOdtRef")


class TareaManualRequest(BaseModel):
    token: str
    cliente: str
    tipo_tarea: str = Field(alias="tipoTarea")
    especificacion: str
    descripcion: str
    solicitante: Optional[str] = None
    estado: str = "Pendiente"


class ActualizarTareaRequest(BaseModel):
    fila_id: int
    columna: str
    valor: str
    token: str


class RendicionRequest(BaseModel):
    tecnico: str
    odt: str
    cliente: str
    tipo_gasto: str = Field(alias="tipoGasto")
    tipo_documento: str = Field(alias="tipoDocumento")
    nro_documento: str = Field(alias="nroDocumento")
    fecha_documento: datetime = Field(alias="fechaDocumento")
    monto_total: float = Field(alias="montoTotal")
    descripcion: Optional[str] = ""
    url_boleta: Optional[str] = Field(default="", alias="urlBoleta")


class ContactoDestinoRequest(BaseModel):
    nombre: Optional[str] = ""
    telefono: Optional[str] = ""
    email: Optional[str] = ""
    prioridad: Optional[str] = ""


class EnviarInformacionContactoRequest(BaseModel):
    token: Optional[str] = ""
    odt: str
    sucursal: str
    problema: Optional[str] = ""
    estado: Optional[str] = ""
    observacion: Optional[str] = ""
    tecnico: Optional[str] = ""
    acompanante: Optional[str] = ""
    fecha_visita: Optional[str] = ""
    destinos: list[ContactoDestinoRequest] = Field(default_factory=list)
    imagenes: list[dict[str, str]] = Field(default_factory=list)


class ProtocoloRegistroCreateRequest(BaseModel):
    token: Optional[str] = ""
    cliente: str
    sucursal: str
    tipo_protocolo: Optional[str] = Field(default="", alias="tipoProtocolo")
    detectado: Optional[str] = ""
    efectivo: Optional[str] = ""
    sirena: Optional[str] = ""
    voz: Optional[str] = ""
    carabineros: Optional[str] = ""
    alpha3: Optional[str] = ""
    informado: Optional[str] = ""
    bitacora: Optional[str] = ""
    protocolo_exitoso: Optional[str] = Field(default="", alias="protocoloExitoso")
    puesto: Optional[str] = ""
    operador: Optional[str] = ""
    observaciones: Optional[str] = ""
