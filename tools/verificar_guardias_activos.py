"""Diagnostico de solo lectura: detecta guardias que NO resuelven a una
cuenta activa en `users` (department=GuardiaFulltime/GuardiaParttime, is_activate=1).

Reproduce exactamente la logica de `_usuario_guardia_activo_por_nombre` /
`_es_departamento_guardia` en ATC/app/routes/inicio_turno.py, para poder
listar (sin tocar nada) que nombres de `supervisor_registros` y `bbdd_guardias`
fallarian hoy con "Selecciona un guardia valido desde la lista." al intentar
registrarlos.

Ademas separa los que fallan en dos grupos:
  - ya existen como fila en `users` (algun otro rut/nombre/departamento/estado
    no calza) -> se pueden corregir con un UPDATE
  - no existen ninguna fila en `users` con ese nombre -> hace falta un INSERT
    nuevo (rut, password, departamento Full/Part time)

Uso (en el Windows Server, con el mismo Python/venv que corre la app):
    python tools/verificar_guardias_activos.py
    python tools/verificar_guardias_activos.py --dias 90
"""

from __future__ import annotations

import argparse
import unicodedata
from collections import defaultdict
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
ATC_ENV = ROOT / "ATC" / ".env"

DEPARTAMENTOS_GUARDIA = {
    "guardia",
    "guardiafulltime", "guardiasfulltime",
    "guardiaparttime", "guardiasparttime",
}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def normalizar_texto(value: object) -> str:
    text_ = unicodedata.normalize("NFD", str(value or "").casefold().strip())
    return "".join(ch for ch in text_ if unicodedata.category(ch) != "Mn")


def normalizar_rut(value: object) -> str:
    txt = str(value or "").strip().upper()
    return "".join(ch for ch in txt if ch not in ". ")


def es_departamento_guardia(value: object) -> bool:
    partes = [
        normalizar_texto(parte).replace(" ", "")
        for parte in str(value or "").split(";")
        if str(parte or "").strip()
    ]
    return any(parte in DEPARTAMENTOS_GUARDIA for parte in partes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dias", type=int, default=60, help="Ventana de dias hacia atras a revisar en supervisor_registros (default 60)")
    args = parser.parse_args()

    atc_env = parse_env(ATC_ENV)
    url = atc_env.get("DATABASE_URL", "").strip()
    if not url:
        print(f"No se encontro DATABASE_URL en {ATC_ENV}")
        return 2

    engine = create_engine(url, future=True, pool_pre_ping=True)

    with engine.connect() as conn:
        distintos = conn.execute(
            text(
                "SELECT departament, COUNT(*) AS n, SUM(CASE WHEN is_activate = 1 THEN 1 ELSE 0 END) AS activos "
                "FROM users WHERE departament IS NOT NULL GROUP BY departament ORDER BY n DESC"
            )
        ).all()
        print("=== Valores distintos de `departament` en users ===")
        for row in distintos:
            print(f"  {row.departament!r}  total={row.n}  activos={row.activos}")
        print()

        usuarios_guardia = conn.execute(
            text(
                "SELECT id, name, [user] AS username, departament AS department, is_activate "
                "FROM users WHERE departament IS NOT NULL"
            )
        ).all()

        todos_usuarios = conn.execute(
            text("SELECT id, name, [user] AS username, departament AS department, is_activate FROM users")
        ).all()

        recientes = conn.execute(
            text(
                "SELECT DISTINCT nombre_guardia FROM supervisor_registros "
                "WHERE fecha >= DATEADD(day, :dias, CAST(GETDATE() AS date))"
            ),
            {"dias": -abs(args.dias)},
        ).all()

        bbdd_guardias = conn.execute(
            text("SELECT rut, nombre FROM bbdd_guardias")
        ).all()

    # Cuentas activas de guardia (misma logica que _usuarios_guardia_activos)
    activos = [
        u for u in usuarios_guardia
        if bool(u.is_activate) and es_departamento_guardia(u.department)
    ]
    por_nombre_activo: dict[str, list] = defaultdict(list)
    for u in activos:
        por_nombre_activo[normalizar_texto(u.name)].append(u)

    # Cualquier fila en users (activa o no, con cualquier departamento) por nombre
    por_nombre_cualquiera: dict[str, list] = defaultdict(list)
    for u in todos_usuarios:
        por_nombre_cualquiera[normalizar_texto(u.name)].append(u)

    # RUT conocido en el roster legacy (bbdd_guardias) por nombre normalizado
    rut_por_nombre_roster: dict[str, str] = {}
    for row in bbdd_guardias:
        key = normalizar_texto(row.nombre)
        if key and key not in rut_por_nombre_roster:
            rut_por_nombre_roster[key] = normalizar_rut(row.rut)

    print(f"Cuentas activas con departamento de guardia (Full/Part time): {len(activos)}")
    print(f"Total de filas en users (cualquier estado/departamento):      {len(todos_usuarios)}")
    print(f"Nombres distintos revisados en supervisor_registros (ultimos {args.dias} dias): {len(recientes)}")
    print(f"Nombres distintos en bbdd_guardias (tabla legacy de inicio de turno): {len(bbdd_guardias)}")
    print()

    nombres_a_revisar = {row.nombre_guardia for row in recientes if row.nombre_guardia}
    nombres_a_revisar |= {row.nombre for row in bbdd_guardias if row.nombre}

    ok, sin_cuenta_pero_existe_fila, sin_ninguna_fila, ambiguos = [], [], [], []
    for nombre in sorted(nombres_a_revisar, key=lambda s: s.casefold()):
        key = normalizar_texto(nombre)
        matches_activos = por_nombre_activo.get(key, [])
        if len(matches_activos) == 1:
            ok.append(nombre)
            continue
        if len(matches_activos) > 1:
            ambiguos.append((nombre, matches_activos))
            continue
        # no hay cuenta activa de guardia -> ver si existe ALGUNA fila en users
        cualquiera = por_nombre_cualquiera.get(key, [])
        rut_roster = rut_por_nombre_roster.get(key, "")
        if cualquiera:
            sin_cuenta_pero_existe_fila.append((nombre, cualquiera, rut_roster))
        else:
            sin_ninguna_fila.append((nombre, rut_roster))

    print(f"OK (resuelven a exactamente 1 cuenta activa): {len(ok)}")
    print(f"YA EXISTE fila en users pero no activa/depto correcto: {len(sin_cuenta_pero_existe_fila)}")
    print(f"NO EXISTE ninguna fila en users (hace falta INSERT nuevo): {len(sin_ninguna_fila)}")
    print(f"AMBIGUOS (mas de 1 cuenta activa con el mismo nombre): {len(ambiguos)}")
    print()

    if sin_cuenta_pero_existe_fila:
        print("=== Ya existen en `users` (solo hay que corregir is_activate / departament) ===")
        for nombre, filas, rut_roster in sin_cuenta_pero_existe_fila:
            detalle = "; ".join(
                f"id={u.id} user={u.username} is_activate={u.is_activate} departament={u.department!r}"
                for u in filas
            )
            print(f"  - {nombre}  (rut roster: {rut_roster or 's/d'})  ->  {detalle}")
        print()

    if sin_ninguna_fila:
        print("=== NO existen en `users` en absoluto (hace falta crear la cuenta desde cero) ===")
        for nombre, rut_roster in sin_ninguna_fila:
            print(f"  - {nombre}  (rut roster: {rut_roster or 'SIN RUT EN bbdd_guardias'})")
        print()

    if ambiguos:
        print("=== Guardias AMBIGUOS (mas de una cuenta activa con el mismo nombre normalizado) ===")
        for nombre, matches in ambiguos:
            detalle = ", ".join(f"id={u.id} rut={normalizar_rut(u.username)}" for u in matches)
            print(f"  - {nombre}  ->  {detalle}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
