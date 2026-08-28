"""Importa el detalle DIARIO por Día/Noche desde "Turnos.xlsx - TURNOS.csv"
(raiz del repo, entregado por el usuario ago 2026, pestaña "TURNOS" separada
de los CSV "Cantidad de Guardias .../Guardias .../Guardias Privados.csv" que
usan los otros 3 scripts _importar_turnos_estipulados_*) a la tabla
`turnos_estipulados` — REEMPLAZA las filas de los 3 grupos (concon/quintero/
privados) por versiones granulares SIEMPRE separadas por cobertura='Dia' o
'Noche' (nunca "Dia y Noche" ni etiquetas de dia de semana como venian antes
en el grupo 'privados'), con `hora_inicio` poblado — necesario para las
alertas de cobertura incompleta por turno (ver routes/inicio_turno.py:
_verificar_alertas_turno_incompleto), que necesitan saber Día vs Noche por
separado y a qué hora empezó cada uno.

Compatible con el reporte mensual existente (_datos_cumplimiento_turnos):
esa función ya SUMA todas las filas de una misma dependencia, así que tener
2 filas (Día + Noche) en vez de 1 fila fusionada da el mismo total.

Formato del CSV: 3 secciones (CON CON / QUINTERO / PRIVADOS), cada una con
fila de título, fila en blanco, fila de ciclo semanal (L,M,M,J,V,S,D...) +
2 columnas de total mensual, fila de encabezado "Dependendia,Tipo,01..31,30
Dias,31 Dias", y luego una fila por (recinto, Tipo). La columna "01" es
SIEMPRE Lunes (weekday()==0) en la fila de ciclo semanal, así que el dia de
semana de la columna N es (N-1) % 7 — no hace falta comparar contra ningún
encabezado de texto.

hora_inicio: se busca en el texto `horario` de la fila YA IMPORTADA (por los
otros 3 scripts) para esa misma dependencia/grupo un patron "HH:MM" que caiga
en la franja de mañana (05:00-13:00, va a 'Dia') o de tarde/noche
(17:00-24:00 o 00:00-04:00, va a 'Noche'). Si no se encuentra, cae al
default confirmado por el usuario (ago 2026): Día 08:00, Noche 20:00.

Re-ejecutable: borra e importa de nuevo TODAS las filas de los 3 grupos cada
vez (reemplaza también lo que hayan dejado los otros 3 scripts).

Uso (en el Windows Server, D:\\PROYECTO-ATC-SERVIDOR):
    python -m ATC.scripts._importar_turnos_estipulados_turno
"""
from __future__ import annotations

import csv
import re
import sys
import unicodedata
from pathlib import Path

sys.path = [p for p in sys.path if "PROYECTO-ATC-SERVIDOR" not in p]

from ATC.app.core.db import SessionLocal  # noqa: E402
from ATC.app.models.inicio_turno import TurnoEstipulado  # noqa: E402
from ATC.scripts._importar_turnos_estipulados_concon import (  # noqa: E402
    _MAPEO_SUCURSAL as _MAPEO_CONCON,
)
from ATC.scripts._importar_turnos_estipulados_privados import (  # noqa: E402
    _MAPEO_SUCURSAL as _MAPEO_PRIVADOS,
)
from ATC.scripts._importar_turnos_estipulados_quintero import (  # noqa: E402
    _MAPEO_SUCURSAL as _MAPEO_QUINTERO,
)

_MAPEOS = {"concon": _MAPEO_CONCON, "quintero": _MAPEO_QUINTERO, "privados": _MAPEO_PRIVADOS}
_DEFAULT_HORA = {"dia": "08:00", "noche": "20:00"}
_HORA_RE = re.compile(r"(\d{1,2}):(\d{2})")

# La pestaña "TURNOS" (este CSV) nombra varias dependencias de Quintero
# distinto a como quedaron en _MAPEO_SUCURSAL de
# _importar_turnos_estipulados_quintero.py (verificado contra
# bbdd_sucursales en esa importación original) — mismo recinto, otro texto
# ("MQUIN Medio Ambiente" vs "Edificio Medioambiente", "Albergue" vs
# "Alberge", etc.). Alias -> clave normalizada del _MAPEO_SUCURSAL
# correspondiente, verificado a mano comparando ambas planillas.
_ALIAS_QUINTERO: dict[str, str] = {
    "mquin medio ambiente": "edificio medioambiente",
    "mquin escombrera municipal": "escombrera municipal",
    "mquin aparcadero municipal": "aparcadero municipal",
    "mquin cementerio municipal": "cementerio municipal",
    "mquin parque municipal": "parque municipal",
    "albergue municipal": "alberge municipal",
    "mquin posta de salud loncura": "posta de salud loncura",
    "mquin edificio de administracion desam": "desam",
    "farmacia municipal": "farmacia",
    "mquin cesfam quintero": "cesfam",
}
_ALIASES = {"quintero": _ALIAS_QUINTERO}


def _normalizar(texto: str) -> str:
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


def _tipo_normalizado(valor: str) -> str | None:
    t = _normalizar(valor)
    if t.startswith("dia") or t == "día":
        return "dia"
    if t.startswith("noche"):
        return "noche"
    return None


def _hora_desde_horario_viejo(horario_viejo: str, tipo: str) -> str:
    horas = _HORA_RE.findall(horario_viejo or "")
    for hh, mm in horas:
        h = int(hh)
        if tipo == "dia" and 5 <= h < 13:
            return f"{h:02d}:{mm}"
        if tipo == "noche" and (17 <= h < 24 or 0 <= h < 5):
            return f"{h:02d}:{mm}"
    return _DEFAULT_HORA[tipo]


def _horarios_viejos_por_dependencia(db, grupo: str) -> dict[str, str]:
    """dependencia normalizada -> texto `horario` tal como quedó de la
    importación granular por dependencia (scripts _importar_..._concon/
    quintero/privados), ANTES de que este script la reemplace — se lee una
    sola vez al principio, antes del DELETE."""
    filas = db.query(TurnoEstipulado.dependencia, TurnoEstipulado.horario).filter(
        TurnoEstipulado.grupo == grupo
    ).all()
    out: dict[str, str] = {}
    for dependencia, horario in filas:
        clave = _normalizar(dependencia)
        if clave not in out and horario:
            out[clave] = horario
    return out


def _reglas_desde_dias(valores: list[int]) -> list[tuple[int, str | None]]:
    """valores: lista de 7 (Lunes..Domingo) con el requerido de ese dia de
    semana. Devuelve [(turnos_dia, dias_semana_csv_o_None), ...] — una regla
    por cada valor distinto >0, agrupando los dias de semana que comparten
    ese valor. Si los 7 dias tienen el mismo valor >0, una sola regla con
    dias_semana=None (aplica siempre)."""
    por_valor: dict[int, list[int]] = {}
    for weekday, valor in enumerate(valores):
        if valor <= 0:
            continue
        por_valor.setdefault(valor, []).append(weekday)
    if len(por_valor) == 1:
        (valor, dias), = por_valor.items()
        if len(dias) == 7:
            return [(valor, None)]
        return [(valor, ",".join(str(d) for d in sorted(dias)))]
    return [(valor, ",".join(str(d) for d in sorted(dias))) for valor, dias in por_valor.items()]


def _parse_seccion(rows: list[list[str]], inicio: int, fin: int) -> list[dict]:
    """Parsea las filas de recinto de una sección (entre la fila de
    encabezado 'Dependendia,Tipo,...' y el final de la sección)."""
    filas: list[dict] = []
    for row in rows[inicio:fin]:
        if len(row) < 33:
            continue
        dependencia = (row[0] or "").strip()
        tipo_raw = (row[1] or "").strip()
        if not dependencia or not tipo_raw:
            continue
        tipo = _tipo_normalizado(tipo_raw)
        if tipo is None:
            continue
        dias_valores = [_parse_int(v) for v in row[2:33]]
        if len(dias_valores) < 7:
            continue
        semana = dias_valores[:7]  # columnas 01-07 = Lunes..Domingo
        filas.append({"dependencia": dependencia, "tipo": tipo, "semana": semana})
    return filas


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    csv_path = repo_root / "Turnos.xlsx - TURNOS.csv"
    if not csv_path.exists():
        raise SystemExit(f"No se encontro el CSV en {csv_path}")

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    secciones: dict[str, tuple[int, int]] = {}
    grupo_actual = None
    inicio_encabezado = None
    for idx, row in enumerate(rows):
        primera = (row[0] if row else "").strip().upper()
        if primera in ("CON CON", "CONCON"):
            grupo_actual, inicio_encabezado = "concon", None
        elif primera == "QUINTERO":
            if grupo_actual and inicio_encabezado is not None:
                secciones[grupo_actual] = (inicio_encabezado, idx)
            grupo_actual, inicio_encabezado = "quintero", None
        elif primera == "PRIVADOS":
            if grupo_actual and inicio_encabezado is not None:
                secciones[grupo_actual] = (inicio_encabezado, idx)
            grupo_actual, inicio_encabezado = "privados", None
        elif primera.lower() in ("dependendia", "dependencia") and grupo_actual and inicio_encabezado is None:
            inicio_encabezado = idx + 1
    if grupo_actual and inicio_encabezado is not None:
        secciones[grupo_actual] = (inicio_encabezado, len(rows))

    db = SessionLocal()
    try:
        resumen: dict[str, int] = {}
        for grupo, (inicio, fin) in secciones.items():
            mapeo = _MAPEOS[grupo]
            alias = _ALIASES.get(grupo, {})
            horarios_viejos = _horarios_viejos_por_dependencia(db, grupo)
            filas_csv = _parse_seccion(rows, inicio, fin)

            db.query(TurnoEstipulado).filter(TurnoEstipulado.grupo == grupo).delete()

            nuevas = []
            for fila in filas_csv:
                clave = _normalizar(fila["dependencia"])
                clave = alias.get(clave, clave)
                sucursal_id = mapeo.get(clave)
                if sucursal_id is None:
                    print(f"AVISO [{grupo}]: sin cruce de sucursal_id para {fila['dependencia']!r} (clave={clave!r}) — se importa igual con sucursal_id=NULL")
                horario_viejo = horarios_viejos.get(clave, "")
                hora_inicio = _hora_desde_horario_viejo(horario_viejo, fila["tipo"])
                cobertura = "Dia" if fila["tipo"] == "dia" else "Noche"
                for turnos_dia, dias_semana in _reglas_desde_dias(fila["semana"]):
                    nuevas.append(TurnoEstipulado(
                        grupo=grupo,
                        dependencia=fila["dependencia"],
                        sucursal_id=sucursal_id,
                        horario=horario_viejo or None,
                        cobertura=cobertura,
                        dotacion=turnos_dia,
                        turnos_dia=turnos_dia,
                        dias_semana=dias_semana,
                        turnos_mes_30=0,
                        turnos_mes_31=0,
                        hora_inicio=hora_inicio,
                    ))
            db.add_all(nuevas)
            resumen[grupo] = len(nuevas)

        db.commit()
        for grupo, n in resumen.items():
            print(f"Filas importadas (grupo={grupo}): {n}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
