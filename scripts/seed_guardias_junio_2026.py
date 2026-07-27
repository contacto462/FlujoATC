from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime, time
from pathlib import Path

from sqlalchemy import and_

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ATC.app.core.db import SessionLocal
from ATC.app.models.incidencias import SucursalBBDD
from ATC.app.models.inicio_turno import InicioTurnoGuardia, InicioTurnoRegistro, SupervisorRegistro
from ATC.app.routes.inicio_turno import _PRIVADOS_SUCURSAL_IDS, _recinto_label


TURNOS = ("Dia", "Noche", "Extra", "Contrato Diario")
TURNO_HORA = {
    "Dia": time(7, 45),
    "Noche": time(19, 45),
    "Extra": time(10, 0),
    "Contrato Diario": time(9, 0),
}


def _normalizar_rut(value: object) -> str:
    text = str(value or "").strip().upper()
    return "".join(ch for ch in text if ch not in ". ")


def _month_range(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    return start, end


def _load_recintos(db) -> list[str]:
    rows = (
        db.query(SucursalBBDD)
        .filter(
            SucursalBBDD.latitud.isnot(None),
            SucursalBBDD.longitud.isnot(None),
        )
        .order_by(SucursalBBDD.nombre_empresa.asc(), SucursalBBDD.nombre_sucursal.asc())
        .all()
    )
    quintero = [
        _recinto_label(row)
        for row in rows
        if _recinto_label(row).casefold().startswith("municipalidad de quintero")
    ]
    privados = [
        _recinto_label(row)
        for row in db.query(SucursalBBDD)
        .filter(SucursalBBDD.id.in_(_PRIVADOS_SUCURSAL_IDS))
        .order_by(SucursalBBDD.nombre_empresa.asc(), SucursalBBDD.nombre_sucursal.asc())
        .all()
        if _recinto_label(row)
    ]
    recintos = [r for r in quintero + privados if r]
    if recintos:
        return recintos
    return ["Municipalidad de Quintero - Registro General"]


def _load_guardias(db, limit: int) -> list[InicioTurnoGuardia]:
    guardias = (
        db.query(InicioTurnoGuardia)
        .order_by(InicioTurnoGuardia.nombre.asc(), InicioTurnoGuardia.rut.asc())
        .limit(limit)
        .all()
    )
    if not guardias:
        raise RuntimeError("No hay guardias en bbdd_guardias para generar los registros.")
    return guardias


def _delete_june(db, start: datetime, end: datetime) -> tuple[int, int]:
    qr_rows = (
        db.query(InicioTurnoRegistro)
        .filter(
            InicioTurnoRegistro.registrado_at >= start,
            InicioTurnoRegistro.registrado_at < end,
        )
        .all()
    )
    sv_rows = (
        db.query(SupervisorRegistro)
        .filter(
            SupervisorRegistro.fecha >= start.date(),
            SupervisorRegistro.fecha < end.date(),
        )
        .all()
    )
    for row in qr_rows:
        db.delete(row)
    for row in sv_rows:
        db.delete(row)
    return len(qr_rows), len(sv_rows)


def _existing_keys(db, start: datetime, end: datetime) -> tuple[set[tuple], set[tuple]]:
    qr_keys = {
        (
            _normalizar_rut(row.rut),
            row.registrado_at.date(),
            str(row.recinto or "").strip(),
            str(row.tipo_turno or "").strip(),
        )
        for row in db.query(InicioTurnoRegistro)
        .filter(
            InicioTurnoRegistro.registrado_at >= start,
            InicioTurnoRegistro.registrado_at < end,
        )
        .all()
    }
    sv_keys = {
        (
            str(row.nombre_guardia or "").strip().casefold(),
            row.fecha,
            str(row.recinto or "").strip(),
            str(row.tipo_turno or "").strip(),
        )
        for row in db.query(SupervisorRegistro)
        .filter(
            and_(
                SupervisorRegistro.fecha >= start.date(),
                SupervisorRegistro.fecha < end.date(),
            )
        )
        .all()
    }
    return qr_keys, sv_keys


def seed_junio(year: int, month: int, replace: bool, dry_run: bool, guardias_limit: int) -> None:
    rng = random.Random(year * 100 + month)
    start, end = _month_range(year, month)

    with SessionLocal() as db:
        guardias = _load_guardias(db, guardias_limit)
        recintos = _load_recintos(db)

        deleted_qr = deleted_sv = 0
        if replace:
            deleted_qr, deleted_sv = _delete_june(db, start, end)

        existing_qr, existing_sv = _existing_keys(db, start, end)
        insert_qr = 0
        insert_sv = 0

        days = range(1, 31)
        for day in days:
            current_date = date(year, month, day)
            recintos_dia = rng.sample(recintos, k=min(len(recintos), rng.randint(2, min(5, max(2, len(recintos))))))
            guardias_dia = rng.sample(guardias, k=min(len(guardias), rng.randint(6, min(12, max(6, len(guardias))))))

            assignments = []
            for recinto in recintos_dia:
                for turno in ("Dia", "Noche"):
                    guardia = rng.choice(guardias_dia)
                    assignments.append((guardia, recinto, turno))
                if rng.random() < 0.55:
                    assignments.append((rng.choice(guardias_dia), recinto, "Extra"))
                if rng.random() < 0.35:
                    assignments.append((rng.choice(guardias_dia), recinto, "Contrato Diario"))
            seen_day = set()
            for guardia, recinto, turno in assignments:
                rut = _normalizar_rut(guardia.rut)
                nombre = str(guardia.nombre or "").strip()
                if not rut or not nombre or not recinto or turno not in TURNOS:
                    continue

                key = (rut, current_date, recinto, turno)
                if key in seen_day:
                    continue
                seen_day.add(key)

                registro_at = datetime.combine(current_date, TURNO_HORA[turno])
                qr_key = (rut, current_date, recinto, turno)
                sv_key = (nombre.casefold(), current_date, recinto, turno)

                if qr_key not in existing_qr:
                    db.add(
                        InicioTurnoRegistro(
                            rut=rut,
                            nombre_guardia=nombre,
                            tipo_turno=turno,
                            recinto=recinto,
                            registrado_at=registro_at,
                            ubicacion_estado="confirmada",
                            ip_origen="seed-junio-2026",
                            user_agent="seed_guardias_junio_2026.py",
                        )
                    )
                    existing_qr.add(qr_key)
                    insert_qr += 1

                if sv_key not in existing_sv:
                    db.add(
                        SupervisorRegistro(
                            recinto=recinto,
                            fecha=current_date,
                            nombre_guardia=nombre,
                            tipo_turno=turno,
                            supervisor="Supervisor ATC",
                            notas="Carga aleatoria junio 2026",
                        )
                    )
                    existing_sv.add(sv_key)
                    insert_sv += 1

        if dry_run:
            db.rollback()
        else:
            db.commit()

    action = "DRY RUN" if dry_run else "COMMIT"
    print(
        {
            "action": action,
            "year": year,
            "month": month,
            "replace": replace,
            "deleted_inicio_turno_registros": deleted_qr,
            "deleted_supervisor_registros": deleted_sv,
            "inserted_inicio_turno_registros": insert_qr,
            "inserted_supervisor_registros": insert_sv,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Carga turnos aleatorios coincidentes para junio.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--month", type=int, default=6)
    parser.add_argument("--guardias", type=int, default=24)
    parser.add_argument("--replace", action="store_true", help="Elimina registros del mes antes de insertar.")
    parser.add_argument("--dry-run", action="store_true", help="Simula sin guardar cambios.")
    args = parser.parse_args()

    if args.month != 6:
        raise SystemExit("Este script esta pensado para junio. Usa --month 6.")
    seed_junio(args.year, args.month, args.replace, args.dry_run, args.guardias)


if __name__ == "__main__":
    main()
