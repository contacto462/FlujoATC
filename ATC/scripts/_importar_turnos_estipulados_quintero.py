"""Importa la cuota EXACTA de turnos mensuales por dependencia de Quintero
desde "Guardias Quintero.csv" (raiz del repo, exportado desde "GUARDIAS
QUINTERO.numbers" entregado por el usuario, ago 2026) a la tabla
`turnos_estipulados` (modelo TurnoEstipulado, grupo="quintero").

El CSV viene con columnas Dependencia;Tipo;Dias;Horario;1..31;30 Turnos;31
Turnos, separadas por ";" (export de Numbers). El nombre de dependencia
solo aparece en la primera fila de cada grupo de tramos horarios — las
filas siguientes lo dejan en blanco (se hereda, "forward fill").

La columna "Dias" (Lun-Vier, Lunes, Dom - Fest, etc.) viene incompleta en
varias filas de la planilla original — en vez de confiar en ese texto, el
dia de la semana en que cada fila aplica se DERIVA comparando el patron de
las 31 columnas de dias contra el ciclo semanal real de esas columnas
(fila 3 del CSV: L,M,M,J,V,S,D repitiendo desde la columna "1" — es decir
columna "1" = Lunes, sea cual sea el mes/año real de donde salio la
planilla). Se verifico a mano que esto reproduce exactamente el texto de
"Dias" donde SI estaba, incluyendo las filas donde estaba vacio.

El cruce dependencia -> sucursal_id es una lista fija verificada a mano
contra bbdd_sucursales (incluye los casos "EX DIDECO" vs "DIDECO NUEVO",
que son sucursales DISTINTAS a la "217 DIDECO" generica).

Re-ejecutable: borra e importa de nuevo las filas grupo='quintero' cada vez.

Uso: python -m ATC.scripts._importar_turnos_estipulados_quintero
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path = [p for p in sys.path if "PROYECTO-ATC-SERVIDOR" not in p]

from ATC.app.core.db import SessionLocal  # noqa: E402
from ATC.app.models.inicio_turno import TurnoEstipulado  # noqa: E402

_MAPEO_SUCURSAL: dict[str, int] = {
    "edificio consistorial base": 234,
    "edificio medioambiente": 238,
    "escombrera municipal": 251,
    "aparcadero municipal": 35,
    "cementerio municipal": 137,
    "estadio municipal": 255,
    "estadio municipal cancha sintetica 2": 667,
    "estadio municipal cancha sintetica 1": 666,
    "mquin cemco-operadores de camaras de seguridad": 774,
    "juzgado de policia local": 380,
    "nueva biblioteca municipal": 668,
    "parque municipal": 459,
    "nuevo terminal de buses": 439,
    "alberge municipal": 669,  # sic — asi viene en la planilla ("Alberge", no "Albergue")
    # "EX DIDECO"(734)/"Dideco Nuevo"(219) se reemplazaron por un unico
    # "DIDECO"(217) en la planilla corregida entregada ago 2026 — cruzado
    # contra _QUINTERO_SUCURSAL_IDS (routes/inicio_turno.py) para que las 19
    # sucursales del sistema de marcaje de guardias tengan exactamente su
    # contraparte aca, ni una de mas ni de menos.
    "dideco": 217,
    "posta de salud loncura": 478,
    "desam": 236,
    "farmacia": 257,
    "cesfam": 438,
}


def _normalizar(texto: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFD", texto or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return " ".join(t.lower().split())


def _parse_int(valor: str) -> int:
    valor = (valor or "").strip().replace(",", ".")
    if not valor:
        return 0
    try:
        return int(float(valor))
    except ValueError:
        return 0


def _derivar_dias_semana(day_values: list[int]) -> tuple[int, str | None]:
    """day_values: 31 valores (0 si vacio), columna 0 = dia "1" = Lunes
    (ciclo semanal real de la planilla, ver docstring del modulo).
    Devuelve (turnos_dia, dias_semana) — dias_semana None = todos los dias,
    o string "0,1,2..." (date.weekday(), 0=Lunes) con los dias que aplican."""
    buckets: dict[int, list[int]] = {i: [] for i in range(7)}
    for col_idx, val in enumerate(day_values):
        buckets[col_idx % 7].append(val)
    activos: dict[int, int] = {}
    for wd, vals in buckets.items():
        nonzero = [v for v in vals if v]
        activos[wd] = max(set(nonzero), key=nonzero.count) if nonzero else 0
    turnos_dia = max(activos.values()) if activos else 0
    dias_activos = sorted(wd for wd, v in activos.items() if v == turnos_dia and v > 0)
    if len(dias_activos) == 7 or len(dias_activos) == 0:
        return turnos_dia, None
    return turnos_dia, ",".join(str(d) for d in dias_activos)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    csv_path = repo_root / "Guardias Quintero.csv"
    if not csv_path.exists():
        raise SystemExit(f"No se encontro el CSV en {csv_path}")

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f, delimiter=";"))

    filas: list[dict] = []
    ultimo_nombre: str | None = None
    for row in rows[4:]:
        if not row or all(not (c or "").strip() for c in row):
            continue
        dependencia_raw = (row[0] or "").strip()
        if dependencia_raw:
            ultimo_nombre = dependencia_raw
        dependencia = ultimo_nombre
        if not dependencia:
            continue
        if len(row) < 37:
            continue
        day_values = [_parse_int(v) for v in row[4:35]]
        if not any(day_values):
            continue
        turnos_dia, dias_semana = _derivar_dias_semana(day_values)
        horario_partes = [p for p in [(row[1] or "").strip(), (row[3] or "").strip()] if p]
        horario = " ".join(horario_partes)
        clave = _normalizar(dependencia)
        sucursal_id = _MAPEO_SUCURSAL.get(clave)
        if sucursal_id is None:
            print(f"AVISO: sin cruce de sucursal_id para {dependencia!r} (clave={clave!r}) — se importa igual con sucursal_id=NULL")
        filas.append({
            "dependencia": dependencia,
            "sucursal_id": sucursal_id,
            "horario": horario,
            "cobertura": (row[2] or "").strip(),
            "dotacion": turnos_dia,
            "turnos_dia": turnos_dia,
            "dias_semana": dias_semana,
            "turnos_mes_30": _parse_int(row[35]),
            "turnos_mes_31": _parse_int(row[36]),
        })

    db = SessionLocal()
    try:
        db.query(TurnoEstipulado).filter(TurnoEstipulado.grupo == "quintero").delete()
        for fila in filas:
            db.add(TurnoEstipulado(grupo="quintero", **fila))
        db.commit()
        print(f"Filas importadas (grupo=quintero): {len(filas)}")
        for fila in filas:
            print(f"  {fila['dependencia']!r} -> sucursal_id={fila['sucursal_id']} | turnos_dia={fila['turnos_dia']} | dias_semana={fila['dias_semana']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
