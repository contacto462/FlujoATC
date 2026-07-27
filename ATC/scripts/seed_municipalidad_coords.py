"""
Actualiza coordenadas GPS de los recintos de Municipalidad de Quintero y
divide "Estadio Municipal y Cancha aledañas" en 3 registros independientes.

Fuente: tabla entregada por cliente (imagen con lat/lon DMS).
Ejecutar una sola vez:

    .venv314/bin/python ATC/scripts/seed_municipalidad_coords.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ATC.app.core.db import SessionLocal
from ATC.app.models.incidencias import SucursalBBDD


def dms(deg: float, min_: float, sec: float, *, south: bool = False, west: bool = False) -> str:
    """Convierte DMS → decimal y devuelve string con 6 decimales."""
    decimal = deg + min_ / 60 + sec / 3600
    if south or west:
        decimal = -decimal
    return f"{decimal:.6f}"


# ---------------------------------------------------------------------------
# Tabla de coordenadas (fuente: imagen provista por cliente)
# clave: id de SucursalBBDD existente  →  (lat_str, lon_str)
# ---------------------------------------------------------------------------
COORDS_POR_ID: dict[int, tuple[str, str]] = {
    # APARCADERO MUNICIPAL
    35: (dms(32, 47, 31.99, south=True), dms(71, 31, 57.49, west=True)),
    # CEMCO
    136: (dms(32, 47, 10.28, south=True), dms(71, 31, 40.71, west=True)),
    # CEMENTERIO MUNICIPAL
    137: (dms(32, 47, 47.69, south=True), dms(71, 30, 34.25, west=True)),
    # DIDECO (registro 1)
    217: (dms(32, 47,  2.33, south=True), dms(71, 31, 39.20, west=True)),
    # DIDECO NUEVO (registro 2 — mismas coords)
    219: (dms(32, 47,  2.33, south=True), dms(71, 31, 39.20, west=True)),
    # EDIFICIO CONSISTORIAL BASE
    234: (dms(32, 47,  9.17, south=True), dms(71, 31, 42.82, west=True)),
    # EDIFICIO DE ADMINISTRACIÓN DESAM
    236: (dms(32, 47,  5.19, south=True), dms(71, 31, 51.33, west=True)),
    # EDIFICIO MEDIO AMBIENTE
    238: (dms(32, 46, 55.23, south=True), dms(71, 31, 38.58, west=True)),
    # ESCOMBRERA MUNICIPAL
    251: (dms(32, 47, 58.18, south=True), dms(71, 30,  7.63, west=True)),
    # ESTADIO MUNICIPAL (entrada principal) — antes era "y Cancha aledañas"
    255: (dms(32, 47, 55.11, south=True), dms(71, 31, 46.89, west=True)),
    # FARMACIA MUNICIPAL
    257: (dms(32, 47,  1.53, south=True), dms(71, 31, 44.21, west=True)),
    # JUZGADO DE POLICÍA LOCAL (duplicado 1)
    379: (dms(32, 47,  4.72, south=True), dms(71, 31, 35.32, west=True)),
    # JUZGADO POLICÍA LOCAL (duplicado 2)
    380: (dms(32, 47,  4.72, south=True), dms(71, 31, 35.32, west=True)),
    # NUEVO CESFAM QUINTERO
    438: (dms(32, 47, 44.06, south=True), dms(71, 32,  5.58, west=True)),
    # NUEVO TERMINAL DE BUSES
    439: (dms(32, 47, 50.62, south=True), dms(71, 31, 40.29, west=True)),
    # PARQUE MUNICIPAL
    459: (dms(32, 46, 15.51, south=True), dms(71, 31, 47.24, west=True)),
    # POSTA DE SALUD LONCURA
    478: (dms(32, 47, 18.70, south=True), dms(71, 30, 13.12, west=True)),
}

# ---------------------------------------------------------------------------
# Registros nuevos que no existen todavía en la BD
# ---------------------------------------------------------------------------
RUT_MUNICIPALIDAD = "69060700-K"

NUEVOS: list[dict] = [
    {
        "rut": RUT_MUNICIPALIDAD,
        "nombre_empresa": "Municipalidad De Quintero",
        "nombre_sucursal": "Estadio Municipal Cancha Sintética 1",
        "direccion_sucursal": "Av. Freire s/n, Quintero",
        "comuna": "Quintero",
        "latitud":  dms(32, 47, 51.97, south=True),
        "longitud": dms(71, 31, 50.70, west=True),
    },
    {
        "rut": RUT_MUNICIPALIDAD,
        "nombre_empresa": "Municipalidad De Quintero",
        "nombre_sucursal": "Estadio Municipal Cancha Sintética 2",
        "direccion_sucursal": "Av. Freire s/n, Quintero",
        "comuna": "Quintero",
        "latitud":  dms(32, 47, 58.05, south=True),
        "longitud": dms(71, 31, 50.28, west=True),
    },
    {
        "rut": RUT_MUNICIPALIDAD,
        "nombre_empresa": "Municipalidad De Quintero",
        "nombre_sucursal": "Nueva Biblioteca Municipal",
        "direccion_sucursal": "Quintero",
        "comuna": "Quintero",
        "latitud":  dms(32, 47, 12.07, south=True),
        "longitud": dms(71, 31, 43.88, west=True),
    },
    {
        "rut": RUT_MUNICIPALIDAD,
        "nombre_empresa": "Municipalidad De Quintero",
        "nombre_sucursal": "Albergue Municipal",
        "direccion_sucursal": "Quintero",
        "comuna": "Quintero",
        "latitud":  dms(32, 46, 34.21, south=True),
        "longitud": dms(71, 31, 48.76, west=True),
    },
]


def main() -> None:
    db = SessionLocal()
    try:
        updated = 0
        for sucursal_id, (lat, lon) in COORDS_POR_ID.items():
            row: SucursalBBDD | None = db.get(SucursalBBDD, sucursal_id)
            if row is None:
                print(f"  AVISO: id={sucursal_id} no encontrado — omitido")
                continue
            row.latitud = lat
            row.longitud = lon
            row.latitud_longitud = f"{lat}, {lon}"
            # Normalizar nombre del estadio principal
            if sucursal_id == 255:
                row.nombre_sucursal = "Estadio Municipal"
            updated += 1
            print(f"  OK  id={sucursal_id:4}  {row.nombre_sucursal!r}  lat={lat}  lon={lon}")

        created = 0
        for spec in NUEVOS:
            exists = (
                db.query(SucursalBBDD)
                .filter(
                    SucursalBBDD.nombre_empresa == spec["nombre_empresa"],
                    SucursalBBDD.nombre_sucursal == spec["nombre_sucursal"],
                )
                .first()
            )
            if exists:
                exists.latitud = spec["latitud"]
                exists.longitud = spec["longitud"]
                exists.latitud_longitud = f"{spec['latitud']}, {spec['longitud']}"
                print(f"  UPD id={exists.id:4}  {exists.nombre_sucursal!r}  (ya existía, coords actualizadas)")
                updated += 1
            else:
                nuevo = SucursalBBDD(
                    rut=spec["rut"],
                    nombre_empresa=spec["nombre_empresa"],
                    nombre_sucursal=spec["nombre_sucursal"],
                    direccion_sucursal=spec["direccion_sucursal"],
                    comuna=spec.get("comuna"),
                    latitud=spec["latitud"],
                    longitud=spec["longitud"],
                    latitud_longitud=f"{spec['latitud']}, {spec['longitud']}",
                )
                db.add(nuevo)
                created += 1
                print(f"  NEW  {spec['nombre_sucursal']!r}  lat={spec['latitud']}  lon={spec['longitud']}")

        db.commit()
        print(f"\nListo: {updated} actualizados, {created} creados.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
