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
