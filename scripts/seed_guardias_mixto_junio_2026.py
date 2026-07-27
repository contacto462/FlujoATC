from __future__ import annotations

import argparse
import calendar
import random
from collections import Counter
from datetime import date, datetime, time

from actualizar_departamento_guardias import connect


TURNOS = ("Dia", "Noche", "Extra", "Contrato Diario")
TURNO_HORA = {
    "Dia": time(7, 45),
    "Noche": time(19, 45),
    "Extra": time(10, 0),
    "Contrato Diario": time(9, 0),
}
PRIVADOS_SUCURSAL_IDS = (411, 545, 529, 650, 533, 674)
SEED_AGENT = "seed_guardias_mixto_junio_2026.py"
SEED_IP = "seed-mixto-guardias"
SEED_NOTA_PREFIX = "Seed mixto guardias junio 2026"


def _normalizar_rut(value: object) -> str:
    text = str(value or "").strip().upper()
    return "".join(ch for ch in text if ch not in ". ")


def _label(row: tuple) -> str:
    _id, empresa, sucursal, direccion = row
    parts = [str(empresa or "").strip(), str(sucursal or "").strip()]
    label = " - ".join(part for part in parts if part)
    return label or str(direccion or "").strip()


def _load_guardias(cursor, limit: int) -> list[dict]:
    cursor.execute(
        """
        SELECT TOP (%d) rut, nombre
        FROM dbo.bbdd_guardias
        WHERE NULLIF(LTRIM(RTRIM(rut)), '') IS NOT NULL
          AND NULLIF(LTRIM(RTRIM(nombre)), '') IS NOT NULL
        ORDER BY nombre, rut
        """
        % max(1, min(limit, 300))
    )
    rows = [{"rut": _normalizar_rut(row[0]), "nombre": str(row[1] or "").strip()} for row in cursor.fetchall()]
    if len(rows) < 8:
        raise RuntimeError("No hay suficientes guardias en dbo.bbdd_guardias para generar datos mixtos.")
    return rows


def _load_recintos(cursor) -> list[dict]:
    cursor.execute(
        """
        SELECT id, nombre_empresa, nombre_sucursal, direccion_sucursal
        FROM dbo.bbdd_sucursales
        WHERE latitud IS NOT NULL
          AND longitud IS NOT NULL
          AND LOWER(COALESCE(nombre_empresa, '')) LIKE 'municipalidad de quintero%%'
        ORDER BY nombre_empresa, nombre_sucursal
        """
    )
    quintero = [{"id": int(row[0]), "label": _label(row)} for row in cursor.fetchall() if _label(row)]

    placeholders = ",".join(str(int(x)) for x in PRIVADOS_SUCURSAL_IDS)
    cursor.execute(
        f"""
        SELECT id, nombre_empresa, nombre_sucursal, direccion_sucursal
        FROM dbo.bbdd_sucursales
        WHERE id IN ({placeholders})
        ORDER BY nombre_empresa, nombre_sucursal
        """
    )
    privados = [{"id": int(row[0]), "label": _label(row)} for row in cursor.fetchall() if _label(row)]
    recintos = quintero + [row for row in privados if row["label"] not in {r["label"] for r in quintero}]
    if len(recintos) < 4:
        raise RuntimeError("No hay suficientes recintos para generar datos mixtos.")
    return recintos


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, end


def _delete_previous_seed(cursor, year: int, month: int) -> dict[str, int]:
    start, end = _month_bounds(year, month)
    cursor.execute(
        """
        DELETE FROM dbo.inicio_turno_registros
        WHERE registrado_at >= %s
          AND registrado_at < %s
          AND (
            user_agent IN (%s, 'seed_guardias_junio_2026.py')
            OR ip_origen IN (%s, 'seed-junio-2026')
          )
        """,
        (start, end, SEED_AGENT, SEED_IP),
    )
    deleted_qr = int(cursor.rowcount or 0)
    cursor.execute(
        """
        DELETE FROM dbo.supervisor_registros
        WHERE fecha >= %s
          AND fecha < %s
          AND (
            notas LIKE %s
            OR notas = 'Carga aleatoria junio 2026'
          )
        """,
        (start.date(), end.date(), f"{SEED_NOTA_PREFIX}%"),
    )
    deleted_sv = int(cursor.rowcount or 0)
    return {"deleted_qr": deleted_qr, "deleted_sv": deleted_sv}


def _insert_qr(cursor, guardia: dict, recinto: dict, turno: str, current_date: date, caso: str) -> None:
    registrado_at = datetime.combine(current_date, TURNO_HORA[turno])
    cursor.execute(
        """
        INSERT INTO dbo.inicio_turno_registros
            (rut, nombre_guardia, tipo_turno, recinto, sucursal_id, latitud, longitud,
             precision_metros, ubicacion_estado, ip_origen, user_agent, registrado_at)
        VALUES (%s, %s, %s, %s, %s, NULL, NULL, %s, %s, %s, %s, %s)
        """,
        (
            guardia["rut"],
            guardia["nombre"],
            turno,
            recinto["label"],
            recinto["id"],
            12.0,
            f"seed-{caso}",
            SEED_IP,
            SEED_AGENT,
            registrado_at,
        ),
    )


def _insert_sv(cursor, guardia: dict, recinto: dict, turno: str, current_date: date, caso: str) -> None:
    cursor.execute(
        """
        INSERT INTO dbo.supervisor_registros
            (recinto, fecha, nombre_guardia, tipo_turno, supervisor, notas)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            recinto["label"],
            current_date,
            guardia["nombre"],
            turno,
            "Supervisor ATC",
            f"{SEED_NOTA_PREFIX} - {caso}",
        ),
    )


def _different_turno(rng: random.Random, turno: str) -> str:
    choices = [item for item in TURNOS if item != turno]
    return rng.choice(choices)


def _different_recinto(rng: random.Random, recintos: list[dict], recinto: dict) -> dict:
    choices = [item for item in recintos if item["label"] != recinto["label"]]
    return rng.choice(choices)


def seed(year: int, month: int, guardias_limit: int, dry_run: bool) -> dict:
    rng = random.Random(year * 100 + month + 77)
    days_in_month = calendar.monthrange(year, month)[1]
    summary = Counter()

    with connect() as conn:
        cursor = conn.cursor()
        deleted = _delete_previous_seed(cursor, year, month)
        guardias = _load_guardias(cursor, guardias_limit)
        recintos = _load_recintos(cursor)

        for day in range(1, days_in_month + 1):
            current_date = date(year, month, day)
            recintos_dia = rng.sample(recintos, k=min(len(recintos), rng.randint(3, 6)))
            guardias_dia = rng.sample(guardias, k=min(len(guardias), rng.randint(9, 15)))
            guardia_idx = 0

            for recinto in recintos_dia:
                base_turnos = ["Dia", "Noche"]
                if rng.random() < 0.55:
                    base_turnos.append("Extra")
                if rng.random() < 0.35:
                    base_turnos.append("Contrato Diario")

                for turno in base_turnos:
                    guardia = guardias_dia[guardia_idx % len(guardias_dia)]
                    guardia_idx += 1
                    roll = rng.random()

                    if roll < 0.70:
                        _insert_qr(cursor, guardia, recinto, turno, current_date, "coincidencia")
                        _insert_sv(cursor, guardia, recinto, turno, current_date, "coincidencia")
                        summary["coincidencias"] += 1
                    elif roll < 0.80:
                        _insert_sv(cursor, guardia, recinto, turno, current_date, "inasistencia")
                        summary["inasistencias_solo_supervisor"] += 1
                    elif roll < 0.89:
                        _insert_qr(cursor, guardia, recinto, turno, current_date, "solo_sistema")
                        summary["solo_sistema"] += 1
                    elif roll < 0.96:
                        _insert_qr(cursor, guardia, recinto, turno, current_date, "discrepancia_turno")
                        _insert_sv(cursor, guardia, recinto, _different_turno(rng, turno), current_date, "discrepancia_turno")
                        summary["discrepancia_turno"] += 1
                    else:
                        recinto_sv = _different_recinto(rng, recintos, recinto)
                        _insert_qr(cursor, guardia, recinto, turno, current_date, "discrepancia_recinto")
                        _insert_sv(cursor, guardia, recinto_sv, turno, current_date, "discrepancia_recinto")
                        summary["discrepancia_recinto"] += 1

            if day % 5 == 0:
                guardia = rng.choice(guardias_dia)
                recinto = rng.choice(recintos_dia)
                turno = rng.choice(("Dia", "Noche"))
                _insert_sv(cursor, guardia, recinto, turno, current_date, "solo_supervisor_extra")
                summary["solo_supervisor_extra"] += 1

            if day % 6 == 0:
                guardia = rng.choice(guardias_dia)
                recinto = rng.choice(recintos_dia)
                turno = rng.choice(("Extra", "Contrato Diario"))
                _insert_qr(cursor, guardia, recinto, turno, current_date, "solo_sistema_extra")
                summary["solo_sistema_extra"] += 1

        start, end = _month_bounds(year, month)
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM dbo.inicio_turno_registros
            WHERE registrado_at >= %s AND registrado_at < %s AND user_agent = %s
            """,
            (start, end, SEED_AGENT),
        )
        inserted_qr = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM dbo.supervisor_registros
            WHERE fecha >= %s AND fecha < %s AND notas LIKE %s
            """,
            (start.date(), end.date(), f"{SEED_NOTA_PREFIX}%"),
        )
        inserted_sv = int(cursor.fetchone()[0])

        if dry_run:
            conn.rollback()
            action = "DRY RUN"
        else:
            conn.commit()
            action = "COMMIT"

    return {
        "action": action,
        "year": year,
        "month": month,
        **deleted,
        "inserted_qr": inserted_qr,
        "inserted_supervisor": inserted_sv,
        "cases": dict(summary),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera registros mixtos de guardias/supervisor para junio 2026.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--month", type=int, default=6)
    parser.add_argument("--guardias", type=int, default=80)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.month != 6:
        raise SystemExit("Este script esta pensado para junio. Usa --month 6.")
    print(seed(args.year, args.month, args.guardias, args.dry_run))


if __name__ == "__main__":
    main()
