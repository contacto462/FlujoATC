"""
Fix puntual: la tabla rendiciones tenia un salto de ID (37 -> 1035), probablemente
por un INSERT manual con IDENTITY_INSERT que reseteo el contador. Renumera las
9 filas 1035..1043 a 38..46 (contiguas con las existentes 1..37) y reseedea el
IDENTITY para que el proximo insert continue en 47.

Verificado antes de escribir: ninguna otra tabla referencia rendiciones.id por
valor (rendiciones_pagos vincula por codigo_diario/tecnico, no por id), asi que
renumerar es seguro.
"""
from sqlalchemy import text

from ATC.app.core.db import SessionLocal
from ATC.app.models.incidencias import Rendicion

MAPEO = {1035: 38, 1036: 39, 1037: 40, 1038: 41, 1039: 42, 1040: 43, 1041: 44, 1042: 45, 1043: 46}

COLUMNAS = [
    "codigo_diario", "fecha_registro", "tecnico", "mail", "odt", "cliente", "comuna",
    "tipo_gasto", "tipo_documento", "nro_documento", "fecha_documento", "monto_total",
    "descripcion", "url_boleta", "url_informe", "documento", "estado_revision",
]

db = SessionLocal()
try:
    filas = db.query(Rendicion).filter(Rendicion.id.in_(MAPEO.keys())).all()
    if len(filas) != len(MAPEO):
        raise RuntimeError(f"Esperaba {len(MAPEO)} filas, encontre {len(filas)}. Abortando sin tocar nada.")

    datos = []
    for f in filas:
        fila = {"old_id": f.id, "new_id": MAPEO[f.id]}
        for col in COLUMNAS:
            fila[col] = getattr(f, col)
        datos.append(fila)

    for d in datos:
        print(f"MOVER: id={d['old_id']} -> id={d['new_id']} ({d['codigo_diario']})")

    for d in datos:
        db.query(Rendicion).filter(Rendicion.id == d["old_id"]).delete()
    db.flush()

    db.execute(text("SET IDENTITY_INSERT dbo.rendiciones ON"))
    insert_sql = text(
        "INSERT INTO dbo.rendiciones (id, " + ", ".join(COLUMNAS) + ") "
        "VALUES (:new_id, " + ", ".join(f":{c}" for c in COLUMNAS) + ")"
    )
    for d in datos:
        db.execute(insert_sql, d)
    db.execute(text("SET IDENTITY_INSERT dbo.rendiciones OFF"))
    db.commit()

    db.execute(text("DBCC CHECKIDENT ('dbo.rendiciones', RESEED, 46)"))
    db.commit()
    print("OK: renumerado 1035-1043 -> 38-46, identity reseed a 46 (proximo insert = 47).")
except Exception:
    db.rollback()
    raise
finally:
    db.close()
