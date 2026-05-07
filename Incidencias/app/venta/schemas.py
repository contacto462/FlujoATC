from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class VentaClienteCreateRequest(BaseModel):
    rut: str = Field(min_length=3, max_length=32)
    razonSocial: str = Field(min_length=2, max_length=255)
    giro: str | None = Field(default=None, max_length=255)
    direccion: str = Field(min_length=2, max_length=255)
    region: str | None = Field(default=None, max_length=120)
    comuna: str | None = Field(default=None, max_length=120)
    emailFacturas: EmailStr
    nombreRepresentante: str | None = Field(default=None, max_length=255)
    rutRepresentante: str | None = Field(default=None, max_length=32)
    telefono: str | None = Field(default=None, max_length=32)
    emailRepresentante: EmailStr | None = None
    ejecutivo: str | None = None


class VentaClienteCreateResponse(BaseModel):
    ok: bool
    cliente_id: int
    message: str


class VentaClienteTableUpdateRequest(BaseModel):
    row_id: int
    values: list[str]


class VentaSucursalContactoEmergenciaRequest(BaseModel):
    nombre: str | None = Field(default=None, max_length=255)
    rut: str | None = Field(default=None, max_length=32)
    telefono: str | None = Field(default=None, max_length=32)
    email: EmailStr | None = None


class VentaSucursalPersonaAutorizadaRequest(BaseModel):
    nombre: str | None = Field(default=None, max_length=255)
    rut: str | None = Field(default=None, max_length=32)
    telefono: str | None = Field(default=None, max_length=32)
    email: EmailStr | None = None
    claveVerde: str | None = Field(default=None, max_length=255)
    claveRoja: str | None = Field(default=None, max_length=255)


class VentaSucursalGuardiaRequest(BaseModel):
    nombre: str | None = Field(default=None, max_length=255)
    rut: str | None = Field(default=None, max_length=32)
    telefono: str | None = Field(default=None, max_length=32)
    horarioDesde: str | None = Field(default=None, max_length=20)
    horarioHasta: str | None = Field(default=None, max_length=20)


class VentaSucursalCreateRequest(BaseModel):
    rut: str = Field(min_length=3, max_length=32)
    nombreEmpresa: str | None = Field(default=None, max_length=255)
    nombreSucursal: str = Field(min_length=2, max_length=255)
    direccionSucursal: str = Field(min_length=2, max_length=255)
    region: str = Field(min_length=2, max_length=120)
    comuna: str = Field(min_length=2, max_length=120)
    referenciaUbicacion: str | None = Field(default=None, max_length=500)
    proveedorInternet: str | None = Field(default=None, max_length=120)
    proveedorElectricidad: str | None = Field(default=None, max_length=120)
    nroProveedorElectricidad: str | None = Field(default=None, max_length=120)
    horarioApertura: str | None = Field(default=None, max_length=20)
    horarioCierre: str | None = Field(default=None, max_length=20)
    diasFuncionamiento: str | None = Field(default=None, max_length=255)
    latitudLongitud: str | None = Field(default=None, max_length=120)
    latitud: str | None = Field(default=None, max_length=40)
    longitud: str | None = Field(default=None, max_length=40)
    emailFacturas: EmailStr
    contactosEmergencia: list[VentaSucursalContactoEmergenciaRequest] = Field(default_factory=list)
    personasAutorizadas: list[VentaSucursalPersonaAutorizadaRequest] = Field(default_factory=list)
    guardias: list[VentaSucursalGuardiaRequest] = Field(default_factory=list)


class VentaSucursalCreateResponse(BaseModel):
    ok: bool
    sucursal_id: int
    codigo: str
    message: str
