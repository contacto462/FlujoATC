from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


@dataclass
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "")
    db_schema: str = os.getenv("DB_SCHEMA", "public")
    db_table: str = os.getenv("DB_TABLE", "incidencias")
    sync_token: str = os.getenv("INCIDENCIAS_SYNC_TOKEN", "")


settings = Settings()

if not settings.database_url:
    raise RuntimeError("DATABASE_URL is required")

engine = create_engine(settings.database_url, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

app = FastAPI(title="Helpdesk Sync Receiver", version="1.0.0")


class SyncIncidenciaIn(BaseModel):
    odt: str
    fecha: str | None = None
    fecha_registro: str | None = None
    puesto: str | None = None
    sucursal: str | None = None
    cliente: str | None = None
    problema: str | None = None
    tipo_incidencia: str | None = None
    derivacion: str | None = None
    observacion: str | None = None
    descripcion: str | None = None
    estado: str | None = None
    origen: str | None = None


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _auth_or_401(authorization: str | None) -> None:
    token = (settings.sync_token or "").strip()
    if not token:
        return
    expected = f"Bearer {token}"
    if (authorization or "").strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _get_columns(db: Session, schema_name: str, table_name: str) -> set[str]:
    rows = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema_name
              AND table_name = :table_name
            """
        ),
        {"schema_name": schema_name, "table_name": table_name},
    ).all()
    return {str(r[0]).strip() for r in rows if r and r[0]}


def _pick(cols: set[str], options: list[str]) -> str | None:
    return next((c for c in options if c in cols), None)


def _field_map(cols: set[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    candidates = {
        "odt": ["odt", "codigo", "codigo_odt", "nro_odt"],
        "fecha": ["fecha", "fecha_registro", "created_at"],
        "puesto": ["puesto", "nro_puesto", "puesto_numero"],
        "sucursal": ["sucursal", "cliente", "nombre_sucursal", "nombre_cliente"],
        "problema": ["problema", "tipo_incidencia", "tipo"],
        "derivacion": ["derivacion", "servicio", "area"],
        "observacion": ["observacion", "descripcion", "detalle"],
        "estado": ["estado", "status", "situacion"],
    }
    for key, opts in candidates.items():
        c = _pick(cols, opts)
        if c:
            mapping[key] = c
    return mapping


def _to_fecha_text(payload: SyncIncidenciaIn) -> str:
    if payload.fecha:
        return payload.fecha
    if payload.fecha_registro:
        try:
            dt = datetime.fromisoformat(payload.fecha_registro.replace("Z", "+00:00"))
            return dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            return payload.fecha_registro
    return datetime.now().strftime("%d/%m/%Y %H:%M")


@app.post("/api/incidencias/sync")
def sync_incidencia(
    payload: SyncIncidenciaIn,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _auth_or_401(authorization)

    schema = settings.db_schema
    table = settings.db_table
    cols = _get_columns(db, schema, table)
    if not cols:
        raise HTTPException(status_code=500, detail=f"Table not found: {schema}.{table}")

    fmap = _field_map(cols)
    col_odt = fmap.get("odt")
    if not col_odt:
        raise HTTPException(status_code=500, detail="No ODT column found")

    values = {
        "odt": payload.odt.strip(),
        "fecha": _to_fecha_text(payload),
        "puesto": (payload.puesto or "").strip(),
        "sucursal": (payload.sucursal or payload.cliente or "").strip(),
        "problema": (payload.problema or payload.tipo_incidencia or "").strip(),
        "derivacion": (payload.derivacion or "Servicio Técnico").strip(),
        "observacion": (payload.observacion or payload.descripcion or "").strip(),
        "estado": (payload.estado or "Pendiente").strip(),
    }

    # 1) UPDATE por ODT (idempotente)
    update_set_cols = []
    update_params: dict[str, Any] = {"v_odt": values["odt"]}
    for k, v in values.items():
        col = fmap.get(k)
        if not col or k == "odt":
            continue
        p = f"v_{k}"
        update_set_cols.append(f'"{col}" = :{p}')
        update_params[p] = v

    if update_set_cols:
        upd_sql = text(
            f"""
            UPDATE "{schema}"."{table}"
            SET {", ".join(update_set_cols)}
            WHERE btrim(CAST("{col_odt}" AS text)) = :v_odt
            """
        )
        res = db.execute(upd_sql, update_params)
        if (res.rowcount or 0) > 0:
            db.commit()
            return {"ok": True, "action": "updated", "odt": values["odt"]}

    # 2) INSERT si no existe
    insert_cols: list[str] = []
    insert_vals: list[str] = []
    insert_params: dict[str, Any] = {}
    for k, v in values.items():
        col = fmap.get(k)
        if not col:
            continue
        pname = f"i_{k}"
        insert_cols.append(f'"{col}"')
        insert_vals.append(f":{pname}")
        insert_params[pname] = v

    if not insert_cols:
        raise HTTPException(status_code=500, detail="No compatible columns to insert")

    ins_sql = text(
        f"""
        INSERT INTO "{schema}"."{table}" ({", ".join(insert_cols)})
        VALUES ({", ".join(insert_vals)})
        """
    )
    db.execute(ins_sql, insert_params)
    db.commit()
    return {"ok": True, "action": "inserted", "odt": values["odt"]}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

