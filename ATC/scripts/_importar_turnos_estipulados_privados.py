"""Importa la cuota EXACTA de turnos mensuales por dependencia de "Guardias
Privados" desde "Guardias Privados.csv" (raiz del repo, exportado desde
"GUARDIAS PRIVADOS.numbers" entregado por el usuario, ago 2026) a la tabla
`turnos_estipulados` (modelo TurnoEstipulado, grupo="privados").

El CSV viene con columnas Dependencia;Horario;Cobertura;Dotacion;1..31;30
Turnos;31 Turnos, separadas por ";" (export de Numbers) — mismo layout que
Quintero salvo que aca son solo 2 filas de encabezado (ciclo semanal +
nombres de columna, sin fila de titulo de seccion) y la columna "Dotacion"
viene vacia en todas las filas (no se usa para el calculo, ver comentario
en el modelo TurnoEstipulado — el cumplimiento diario se calcula con
turnos_dia/dias_semana). El nombre de dependencia solo aparece en la
primera fila de cada grupo de tramos (ej. "MACH Camino I." tiene una fila
base Lun-Vier y una segunda fila de cobertura extra Sab/Dom+festivos con
dependencia en blanco) — se hereda con forward-fill.

El dia de la semana en que cada fila aplica se DERIVA comparando el patron
de las 31 columnas de dias contra el ciclo semanal real de esas columnas
(fila 1 del CSV: L,M,M,J,V,S,D repitiendo desde la columna "1" = Lunes),
igual tecnica que Quintero. Nota: la cobertura "F/s - Fes." (fines de
semana y festivos) de MACH Camino I. solo se pudo derivar como Sabado+
Domingo — los festivos entre semana no quedan representados porque el
modelo solo soporta reglas por dia de la semana, no por fecha calendario;
en la planilla de origen no habia ningun feriado entre semana en el mes
muestreado, asi que no se perdio informacion visible, pero si en el futuro
un feriado cae entre semana el conteo de ese dia puntual no lo va a
reflejar.

El cruce dependencia -> sucursal_id es una lista fija verificada a mano
contra bbdd_sucursales (6 dependencias, ago 2026).

Re-ejecutable: borra e importa de nuevo las filas grupo='privados' cada vez.

Uso: python -m ATC.scripts._importar_turnos_estipulados_privados
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path = [p for p in sys.path if "PROYECTO-ATC-SERVIDOR" not in p]

from ATC.app.core.db import SessionLocal  # noqa: E402
from ATC.app.models.inicio_turno import TurnoEstipulado  # noqa: E402

_MAPEO_SUCURSAL: dict[str, int] = {
    "atlas copco renca": 650,
    "edificio velazquez": 674,
    "mach camino i.": 411,
    "soprodi": 529,
    "storage vina": 533,
    "t transport placilla": 545,
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
    csv_path = repo_root / "Guardias Privados.csv"
    if not csv_path.exists():
        raise SystemExit(f"No se encontro el CSV en {csv_path}")

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f, delimiter=";"))

    filas: list[dict] = []
    ultimo_nombre: str | None = None
    for row in rows[2:]:
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
        clave = _normalizar(dependencia)
        sucursal_id = _MAPEO_SUCURSAL.get(clave)
        if sucursal_id is None:
            print(f"AVISO: sin cruce de sucursal_id para {dependencia!r} (clave={clave!r}) — se importa igual con sucursal_id=NULL")
        filas.append({
            "dependencia": dependencia,
            "sucursal_id": sucursal_id,
            "horario": (row[1] or "").strip(),
            "cobertura": (row[2] or "").strip(),
            "dotacion": _parse_int(row[3]),
            "turnos_dia": turnos_dia,
            "dias_semana": dias_semana,
            "turnos_mes_30": _parse_int(row[35]),
            "turnos_mes_31": _parse_int(row[36]),
        })

    db = SessionLocal()
    try:
        db.query(TurnoEstipulado).filter(TurnoEstipulado.grupo == "privados").delete()
        for fila in filas:
            db.add(TurnoEstipulado(grupo="privados", **fila))
        db.commit()
        print(f"Filas importadas (grupo=privados): {len(filas)}")
        for fila in filas:
            print(f"  {fila['dependencia']!r} -> sucursal_id={fila['sucursal_id']} | turnos_dia={fila['turnos_dia']} | dias_semana={fila['dias_semana']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
