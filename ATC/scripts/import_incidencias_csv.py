from __future__ import annotations

import argparse
import csv
from pathlib import Path

from sqlalchemy import delete, text

from app.core.db_compat import quote_ident
from app.core.db import Base, SessionLocal, engine
from app.models.incidencia import Incidencia


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    clean_value = str(value).strip()
    return clean_value or None


def _pick(row: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        if key in row:
            return _clean(row.get(key))
    return None


def import_csv(csv_path: Path, truncate_first: bool = False) -> tuple[int, int]:
    Base.metadata.create_all(bind=engine)

    if not csv_path.exists():
        raise FileNotFoundError(f"No existe el archivo: {csv_path}")

    source_file = csv_path.name
    inserted = 0
    skipped = 0

    db = SessionLocal()
    try:
        # Ajuste de compatibilidad si la tabla fue creada con prioridad VARCHAR corto.
        dialect = db.get_bind().dialect.name
        table_name = Incidencia.__tablename__
        table = quote_ident(table_name, dialect)
        column = quote_ident("prioridad", dialect)
        if dialect == "mssql":
            db.execute(text(f"ALTER TABLE {table} ALTER COLUMN {column} NVARCHAR(MAX) NULL"))
        else:
            db.execute(text(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE TEXT"))
        db.commit()

        if truncate_first:
            db.execute(delete(Incidencia))
            db.commit()

        existing_rows = {
            row[0]
            for row in db.query(Incidencia.source_row)
            .filter(Incidencia.source_file == source_file)
            .all()
        }

        with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            batch: list[dict] = []

            for line_num, row in enumerate(reader, start=2):
                if line_num in existing_rows:
                    skipped += 1
                    continue

                batch.append(
                    {
                        "odt": _pick(row, "ODT"),
                        "fecha": _pick(row, "Fecha"),
                        "puesto": _pick(row, "Puesto"),
                        "sucursal": _pick(row, "Sucursal"),
                        "problema": _pick(row, "Problema"),
                        "derivacion": _pick(row, "Derivación", "Derivacion"),
                        "observacion": _pick(row, "Observación", "Observacion"),
                        "tecnico": _pick(row, "Técnico", "Tecnico"),
                        "estado": _pick(row, "Estado"),
                        "cantidad_dias_ejecucion": _pick(
                            row,
                            "Cantidad de dias de Ejecución",
                            "Cantidad de dias de Ejecucion",
                        ),
                        "fecha_cierre": _pick(row, "Fecha de Cierre"),
                        "fecha_derivacion_area": _pick(
                            row,
                            "Fecha derivación Area",
                            "Fecha derivacion Area",
                        ),
                        "fecha_derivacion_tecnico": _pick(
                            row,
                            "Fecha derivación técnico",
                            "Fecha derivacion tecnico",
                        ),
                        "direccion": _pick(row, "Dirección", "Direccion"),
                        "observacion_final": _pick(row, "Observacion Final"),
                        "foto": _pick(row, "Foto"),
                        "foto_2": _pick(row, "Foto 2"),
                        "informe": _pick(row, "Informe"),
                        "prioridad": _pick(row, "Prioridad"),
                        "materiales": _pick(row, "Materiales"),
                        "acompanante": _pick(row, "Acompañante", "Acompanante"),
                        "estado_avance": _pick(row, "Estado de Avance"),
                        "observaciones_avance": _pick(row, "Observaciones de Avance"),
                        "imagen_1": _pick(row, "Imzgen 1", "Imagen 1"),
                        "imagen_2": _pick(row, "Imzgen 2", "Imagen 2"),
                        "imagen_3": _pick(row, "Imzgen 3", "Imagen 3"),
                        "estado_agrupado": _pick(row, "Estado Agrupado"),
                        "categoria": _pick(row, "Categoría", "Categoria"),
                        "source_file": source_file,
                        "source_row": line_num,
                    }
                )

                if len(batch) >= 1000:
                    db.bulk_insert_mappings(Incidencia, batch)
                    db.commit()
                    inserted += len(batch)
                    batch.clear()

            if batch:
                db.bulk_insert_mappings(Incidencia, batch)
                db.commit()
                inserted += len(batch)

    finally:
        db.close()

    return inserted, skipped


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa 'Registro Incidencias - Registro.csv' a la tabla incidencias."
    )
    parser.add_argument(
        "--file",
        default="Registro Incidencias - Registro.csv",
        help="Ruta del CSV a importar.",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Vaciar tabla incidencias antes de importar.",
    )
    args = parser.parse_args()

    csv_path = Path(args.file)
    inserted, skipped = import_csv(csv_path=csv_path, truncate_first=args.truncate)
    print(f"Importacion completada. Insertados: {inserted}. Omitidos: {skipped}.")


if __name__ == "__main__":
    main()
