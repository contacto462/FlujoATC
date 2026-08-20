"""Importa la cuota EXACTA de turnos mensuales por dependencia de Concon
desde "Cantidad de Guardias Concon.csv" (raiz del repo, entregado por el
usuario ago 2026) a la tabla `turnos_estipulados` (modelo TurnoEstipulado).

Solo importa las filas de la seccion "CONCON 2.0" del CSV (se detiene al
llegar a la seccion "QUINTERO"), y solo las dependencias con dotacion > 0
(las de "Sin Guardia" no tienen cuota que cumplir, se omiten).

El cruce dependencia -> sucursal_id es una lista fija verificada a mano
(_MAPEO_SUCURSAL, 15 dependencias = las 15 de _CONCON_ORDEN en
routes/inicio_turno.py) — no hay match automatico/fuzzy, para no arriesgar
una fila mal cruzada en un reporte de cumplimiento contractual.

Re-ejecutable: borra e importa de nuevo las filas grupo='concon' cada vez
(no hay edicion manual que preservar todavia sobre esta tabla).

Uso: python -m ATC.scripts._importar_turnos_estipulados_concon
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path = [p for p in sys.path if "PROYECTO-ATC-SERVIDOR" not in p]

from ATC.app.core.db import SessionLocal  # noqa: E402
from ATC.app.models.inicio_turno import TurnoEstipulado  # noqa: E402

# Cruce dependencia (texto normalizado, minusculas sin tildes) -> sucursal_id
# en bbdd_sucursales. Mismas 15 dependencias que _CONCON_ORDEN
# (routes/inicio_turno.py) — verificado a mano contra los comentarios de esa
# lista y el orden de la planilla.
_MAPEO_SUCURSAL: dict[str, int] = {
    "edificio municipal": 239,
    "parque la isla": 458,
    "museo": 434,
    "biblioteca municipal": 83,
    "oficina deporte, estadio atletico - dimao- operaciones": 445,
    "transito y transporte publico - daf": 704,
    "corrales municipales": 195,
    "direccion de obras municipales": 222,
    "avanzada cultural (carpa)": 75,
    "centro comunitario (juventus, adulto mayor y discapacidad)": 139,
    "juzgado de policia local": 705,
    "cesfam": 142,
    "dideco": 218,
    "sar concon": 779,
    "parque vista al mar": 707,
}


def _normalizar(texto: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFD", texto or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = t.replace("–", "-").replace("—", "-")
    return " ".join(t.lower().split())


def _parse_int(valor: str) -> int:
    valor = (valor or "").strip().replace(",", "")
    if not valor:
        return 0
    try:
        return int(float(valor))
    except ValueError:
        return 0


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    csv_path = repo_root / "Cantidad de Guardias Concon.csv"
    if not csv_path.exists():
        raise SystemExit(f"No se encontro el CSV en {csv_path}")

    filas: list[dict] = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)

    en_concon = False
    for row in rows:
        primera = (row[0] if row else "").strip()
        if primera.upper().startswith("CONCON"):
            en_concon = True
            continue
        if primera.upper() == "QUINTERO":
            break
        if not en_concon:
            continue
        if primera.lower() in ("dependendia", "dependencia") or not primera:
            continue
        if len(row) < 37:
            continue
        dependencia = primera
        horario = (row[1] or "").strip()
        cobertura = (row[2] or "").strip()
        dotacion = _parse_int(row[3])
        dias_valores = [_parse_int(v) for v in row[4:35] if (v or "").strip() != ""]
        turnos_dia = dias_valores[0] if dias_valores else 0
        turnos_30 = _parse_int(row[35])
        turnos_31 = _parse_int(row[36])
        if dotacion <= 0:
            continue
        if len(set(dias_valores)) > 1:
            print(f"AVISO: {dependencia!r} no tiene el mismo valor los 31 dias ({sorted(set(dias_valores))}) — se usa el del dia 1 ({turnos_dia}) para la columna diaria, revisar a mano")
        clave = _normalizar(dependencia)
        sucursal_id = _MAPEO_SUCURSAL.get(clave)
        if sucursal_id is None:
            print(f"AVISO: sin cruce de sucursal_id para {dependencia!r} (clave={clave!r}) — se importa igual con sucursal_id=NULL")
        filas.append({
            "dependencia": dependencia,
            "sucursal_id": sucursal_id,
            "horario": horario,
            "cobertura": cobertura,
            "dotacion": dotacion,
            "turnos_dia": turnos_dia,
            "turnos_mes_30": turnos_30,
            "turnos_mes_31": turnos_31,
        })

    db = SessionLocal()
    try:
        db.query(TurnoEstipulado).filter(TurnoEstipulado.grupo == "concon").delete()
        for fila in filas:
            db.add(TurnoEstipulado(grupo="concon", **fila))
        db.commit()
        print(f"Filas importadas (grupo=concon): {len(filas)}")
        for fila in filas:
            print(f"  {fila['dependencia']!r} -> sucursal_id={fila['sucursal_id']} | 30d={fila['turnos_mes_30']} 31d={fila['turnos_mes_31']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
