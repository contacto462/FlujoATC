from __future__ import annotations

"""
Modelos del modulo de venta.

Las tablas de venta estan declaradas en ``ATC.app.models.incidencias`` por
compatibilidad historica con el modulo operacional. Este archivo mantiene el
punto de importacion ``ATC.app.models.venta`` sin duplicar definiciones de
SQLAlchemy.
"""

from ATC.app.models.incidencias import (
    AdministracionODT,
    ClienteBBDD,
    FinanzasODT,
    OperacionesVentaODT,
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

__all__ = [
    "AdministracionODT",
    "ClienteBBDD",
    "FinanzasODT",
    "OperacionesVentaODT",
    "ServicioTecnicoVentaODT",
    "SoporteTecnicoVentaODT",
    "SucursalBBDD",
    "SucursalContactoEmergencia",
    "SucursalGuardia",
    "SucursalInfoExtra",
    "SucursalPersonaAutorizada",
    "VentaODS",
    "VentaODSArchivo",
]
