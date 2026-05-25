from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


ROOT = Path(__file__).resolve().parents[1]
ATC_ENV = ROOT / "ATC" / ".env"
INC_ENV = ROOT / "ATC" / "incidencias" / ".env"


@dataclass(frozen=True)
class DbConfig:
    label: str
    url: str

    @property
    def masked(self) -> str:
        return mask_url(self.url)


@dataclass(frozen=True)
class TableInfo:
    name: str
    columns: dict[str, str]
    row_count: int | None


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


def mask_url(url: str) -> str:
    if not url:
        return "<empty>"
    masked = re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", url)
    masked = re.sub(r"://([^@/:]+)@", r"://***@", masked)
    return masked


def build_engine(url: str) -> Engine:
    connect_args: dict[str, object] = {}
    if url.startswith("postgresql"):
        connect_args["connect_timeout"] = 3
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)


def load_configs() -> tuple[DbConfig, DbConfig]:
    atc_env = parse_env(ATC_ENV)
    inc_env = parse_env(INC_ENV)

    helpdesk_url = atc_env.get("DATABASE_URL", "").strip()
    incidencias_url = inc_env.get("DATABASE_URL", "").strip()

    if not helpdesk_url:
        raise RuntimeError(f"No DATABASE_URL found in {ATC_ENV}")
    if not incidencias_url:
        raise RuntimeError(f"No DATABASE_URL found in {INC_ENV}")

    return (
        DbConfig("helpdesk", helpdesk_url),
        DbConfig("incidencias", incidencias_url),
    )


def quoted_table(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_count(engine: Engine, table_name: str) -> int | None:
    try:
        with engine.connect() as conn:
            return int(conn.execute(text(f"SELECT COUNT(*) FROM {quoted_table(table_name)}")).scalar() or 0)
    except SQLAlchemyError:
        return None


def inspect_tables(config: DbConfig) -> dict[str, TableInfo]:
    engine = build_engine(config.url)
    inspector = inspect(engine)
    tables: dict[str, TableInfo] = {}
    for table_name in sorted(inspector.get_table_names()):
        columns = {
            str(col.get("name")): str(col.get("type"))
            for col in inspector.get_columns(table_name)
            if col.get("name")
        }
        tables[table_name] = TableInfo(
            name=table_name,
            columns=columns,
            row_count=table_count(engine, table_name),
        )
    engine.dispose()
    return tables


def fmt_count(value: int | None) -> str:
    return "?" if value is None else str(value)


def render_table_list(title: str, names: Iterable[str], tables: dict[str, TableInfo]) -> None:
    print()
    print(title)
    print("-" * len(title))
    for name in sorted(names):
        info = tables[name]
        print(f"{name}: {len(info.columns)} columns, {fmt_count(info.row_count)} rows")


def main() -> int:
    helpdesk, incidencias = load_configs()
    print("Comparando bases para unificacion")
    print(f"- {helpdesk.label}: {helpdesk.masked}")
    print(f"- {incidencias.label}: {incidencias.masked}")

    helpdesk_tables = inspect_tables(helpdesk)
    incidencias_tables = inspect_tables(incidencias)

    helpdesk_names = set(helpdesk_tables)
    incidencias_names = set(incidencias_tables)
    only_helpdesk = helpdesk_names - incidencias_names
    only_incidencias = incidencias_names - helpdesk_names
    common = helpdesk_names & incidencias_names

    render_table_list("Solo en helpdesk", only_helpdesk, helpdesk_tables)
    render_table_list("Solo en incidencias", only_incidencias, incidencias_tables)

    print()
    print("Tablas comunes")
    print("--------------")
    for name in sorted(common):
        left = helpdesk_tables[name]
        right = incidencias_tables[name]
        left_cols = set(left.columns)
        right_cols = set(right.columns)
        only_left_cols = sorted(left_cols - right_cols)
        only_right_cols = sorted(right_cols - left_cols)
        shared_cols = left_cols & right_cols
        changed_types = sorted(
            col
            for col in shared_cols
            if normalize_type(left.columns[col]) != normalize_type(right.columns[col])
        )

        status = "compatible"
        if only_left_cols or only_right_cols or changed_types:
            status = "revisar"
        print(
            f"{name}: {status}; "
            f"helpdesk={fmt_count(left.row_count)} rows, "
            f"incidencias={fmt_count(right.row_count)} rows"
        )
        if only_left_cols:
            print(f"  columnas solo helpdesk: {', '.join(only_left_cols)}")
        if only_right_cols:
            print(f"  columnas solo incidencias: {', '.join(only_right_cols)}")
        if changed_types:
            print(f"  tipos distintos: {', '.join(changed_types)}")

    print()
    print("Recomendacion")
    print("-------------")
    print("Crear una base nueva, restaurar helpdesk como base inicial y migrar incidencias encima.")
    print("Fusionar users por username; areas por code; user_areas con remapeo de IDs.")
    print("Revisar manualmente cualquier tabla comun marcada como 'revisar'.")
    return 0


def normalize_type(value: str) -> str:
    text_value = value.lower().strip()
    aliases = {
        "integer": "int",
        "bigint": "bigint",
        "character varying": "varchar",
        "timestamp without time zone": "timestamp",
        "timestamp with time zone": "timestamptz",
    }
    for source, target in aliases.items():
        text_value = text_value.replace(source, target)
    return text_value


if __name__ == "__main__":
    raise SystemExit(main())

