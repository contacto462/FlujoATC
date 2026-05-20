from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import SQLAlchemyError


ROOT = Path(__file__).resolve().parents[1]
ATC_ENV = ROOT / "ATC" / ".env"
INC_ENV = ROOT / "Incidencias" / ".env"
BACKUP_ROOT = ROOT / "backups" / "db_unification"
TARGET_DB = "ATC"
SKIP_RAW_TABLES = {"users", "user_areas", "login_sessions", "incidencias_imagenes_odt"}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    found = False
    updated: list[str] = []
    for line in lines:
        if re.match(rf"^\s*{re.escape(key)}\s*=", line):
            updated.append(f"{key}={value}")
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f"{key}={value}")
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def url_with_database(url: str, database: str) -> str:
    parsed = make_url(url)
    return str(parsed.set(database=database))


def admin_url(url: str) -> URL:
    return make_url(url).set(database="postgres")


def make_engine(url: str | URL, database: str | None = None, autocommit: bool = False) -> Engine:
    parsed = make_url(str(url))
    if database is not None:
        parsed = parsed.set(database=database)
    connect_args: dict[str, Any] = {}
    if parsed.drivername.startswith("postgresql"):
        connect_args["connect_timeout"] = 5
    engine = create_engine(parsed, future=True, pool_pre_ping=True, connect_args=connect_args)
    if autocommit:
        return engine.execution_options(isolation_level="AUTOCOMMIT")
    return engine


def qident(name: str) -> str:
    if not name or "\x00" in name:
        raise ValueError("invalid identifier")
    return '"' + name.replace('"', '""') + '"'


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def backup_database(label: str, engine: Engine, backup_dir: Path) -> None:
    target = backup_dir / label
    target.mkdir(parents=True, exist_ok=True)
    inspector = inspect(engine)
    schema: dict[str, Any] = {}
    metadata = MetaData()

    for table_name in sorted(inspector.get_table_names()):
        table = Table(table_name, metadata, autoload_with=engine)
        columns = inspector.get_columns(table_name)
        schema[table_name] = [
            {
                "name": str(col.get("name")),
                "type": str(col.get("type")),
                "nullable": bool(col.get("nullable")),
                "default": str(col.get("default")) if col.get("default") is not None else None,
            }
            for col in columns
        ]
        out_file = target / f"{table_name}.jsonl"
        with engine.connect() as conn, out_file.open("w", encoding="utf-8") as fh:
            for row in conn.execute(select(table)).mappings():
                fh.write(json.dumps(dict(row), ensure_ascii=False, default=json_default) + "\n")

    (target / "_schema.json").write_text(
        json.dumps(schema, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def terminate_connections(admin: Engine, database: str) -> None:
    with admin.connect() as conn:
        conn.execute(
            text(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = :database
                  AND pid <> pg_backend_pid()
                """
            ),
            {"database": database},
        )


def database_exists(admin: Engine, database: str) -> bool:
    with admin.connect() as conn:
        return bool(conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :database"), {"database": database}).first())


def create_target_from_helpdesk(admin: Engine, helpdesk_db: str, replace: bool) -> None:
    if database_exists(admin, TARGET_DB):
        if not replace:
            raise RuntimeError(f"La base {TARGET_DB!r} ya existe. Ejecuta con --replace-target para reemplazarla.")
        terminate_connections(admin, TARGET_DB)
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE {qident(TARGET_DB)}"))

    terminate_connections(admin, helpdesk_db)
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {qident(TARGET_DB)} WITH TEMPLATE {qident(helpdesk_db)}"))


def ensure_target_user_columns(target: Engine) -> None:
    with target.begin() as conn:
        conn.execute(text('ALTER TABLE "users" ADD COLUMN IF NOT EXISTS department VARCHAR(80)'))
        conn.execute(text('ALTER TABLE "users" ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()'))
        conn.execute(text('ALTER TABLE "users" ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()'))
        conn.execute(text('UPDATE "users" SET created_at = NOW() WHERE created_at IS NULL'))
        conn.execute(text('UPDATE "users" SET updated_at = NOW() WHERE updated_at IS NULL'))


def reflect_tables(engine: Engine) -> dict[str, Table]:
    metadata = MetaData()
    metadata.reflect(bind=engine)
    return {table.name: table for table in metadata.sorted_tables}


def create_missing_incidencias_tables(source: Engine, target: Engine) -> set[str]:
    source_tables = reflect_tables(source)
    target_names = set(inspect(target).get_table_names())
    to_create = [
        table
        for name, table in source_tables.items()
        if name not in target_names and name not in {"users"}
    ]
    if to_create:
        source_tables_metadata = next(iter(source_tables.values())).metadata
        source_tables_metadata.create_all(bind=target, tables=to_create, checkfirst=True)
    return {table.name for table in to_create}


def fetch_all(engine: Engine, table: Table) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(table)).mappings()]


def insert_rows(target: Engine, table_name: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    metadata = MetaData()
    table = Table(table_name, metadata, autoload_with=target)
    target_cols = set(table.c.keys())
    cleaned = [{key: value for key, value in row.items() if key in target_cols} for row in rows]
    with target.begin() as conn:
        conn.execute(table.insert(), cleaned)


def copy_regular_tables(source: Engine, target: Engine) -> None:
    source_tables = reflect_tables(source)
    target_names = set(inspect(target).get_table_names())
    for table_name, table in source_tables.items():
        if table_name in SKIP_RAW_TABLES:
            continue
        if table_name not in target_names:
            continue
        if table_name in {"areas"}:
            # areas se copia con ids originales para remapear user_areas despues.
            pass
        rows = fetch_all(source, table)
        insert_rows(target, table_name, rows)


def merge_users(source: Engine, target: Engine) -> None:
    source_users = Table("users", MetaData(), autoload_with=source)
    rows = fetch_all(source, source_users)
    if not rows:
        return

    with target.begin() as conn:
        for row in rows:
            username = (row.get("username") or "").strip()
            if not username:
                continue
            existing = conn.execute(
                text('SELECT id, department FROM "users" WHERE username = :username'),
                {"username": username},
            ).mappings().first()
            if existing:
                if row.get("department") and not existing.get("department"):
                    conn.execute(
                        text('UPDATE "users" SET department = :department, updated_at = NOW() WHERE username = :username'),
                        {"department": row.get("department"), "username": username},
                    )
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO "users"
                        (name, username, hashed_password, role, is_active, department, created_at, updated_at)
                    VALUES
                        (:name, :username, :hashed_password, :role, :is_active, :department,
                         COALESCE(:created_at, NOW()), COALESCE(:updated_at, NOW()))
                    """
                ),
                {
                    "name": row.get("name") or username,
                    "username": username,
                    "hashed_password": row.get("hashed_password") or "",
                    "role": row.get("role") or "agent",
                    "is_active": bool(row.get("is_active", True)),
                    "department": row.get("department"),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                },
            )


def merge_user_areas(source: Engine, target: Engine) -> None:
    inspector = inspect(source)
    if not inspector.has_table("user_areas"):
        return

    with source.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    u.username,
                    a.code AS area_code,
                    ua.department,
                    ua.is_primary
                FROM user_areas ua
                JOIN users u ON u.id = ua.user_id
                JOIN areas a ON a.id = ua.area_id
                """
            )
        ).mappings().all()

    if not rows:
        return

    with target.begin() as conn:
        for row in rows:
            target_user = conn.execute(
                text('SELECT id FROM "users" WHERE username = :username'),
                {"username": row["username"]},
            ).scalar()
            target_area = conn.execute(
                text('SELECT id FROM "areas" WHERE code = :code'),
                {"code": row["area_code"]},
            ).scalar()
            if not target_user or not target_area:
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO user_areas (user_id, area_id, department, is_primary)
                    VALUES (:user_id, :area_id, :department, :is_primary)
                    ON CONFLICT (user_id, area_id) DO UPDATE
                    SET department = EXCLUDED.department,
                        is_primary = user_areas.is_primary OR EXCLUDED.is_primary
                    """
                ),
                {
                    "user_id": target_user,
                    "area_id": target_area,
                    "department": row["department"],
                    "is_primary": bool(row["is_primary"]),
                },
            )


def parse_images(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    raw = str(value).strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
        if isinstance(parsed, str) and parsed.strip():
            return [parsed.strip()]
    except json.JSONDecodeError:
        pass
    return [raw]


def merge_images_odt(source: Engine, target: Engine) -> None:
    if not inspect(source).has_table("incidencias_imagenes_odt"):
        return
    table = Table("incidencias_imagenes_odt", MetaData(), autoload_with=source)
    rows = fetch_all(source, table)
    if not rows:
        return
    with target.begin() as conn:
        for row in rows:
            conn.execute(
                text(
                    """
                    INSERT INTO incidencias_imagenes_odt
                        (odt, sucursal, imagenes, created_by, created_at, updated_at)
                    VALUES
                        (:odt, :sucursal, CAST(:imagenes AS JSONB), :created_by,
                         COALESCE(:created_at, NOW()), COALESCE(:updated_at, NOW()))
                    ON CONFLICT (odt) DO UPDATE
                    SET sucursal = COALESCE(EXCLUDED.sucursal, incidencias_imagenes_odt.sucursal),
                        imagenes = EXCLUDED.imagenes,
                        created_by = COALESCE(EXCLUDED.created_by, incidencias_imagenes_odt.created_by),
                        updated_at = NOW()
                    """
                ),
                {
                    "odt": row.get("odt"),
                    "sucursal": row.get("sucursal"),
                    "imagenes": json.dumps(parse_images(row.get("imagenes")), ensure_ascii=False),
                    "created_by": row.get("created_by"),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                },
            )


def empty_login_sessions_if_exists(target: Engine) -> None:
    if inspect(target).has_table("login_sessions"):
        with target.begin() as conn:
            conn.execute(text("DELETE FROM login_sessions"))


def reset_sequences(engine: Engine) -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table_name in inspector.get_table_names():
            columns = {col["name"] for col in inspector.get_columns(table_name)}
            if "id" not in columns:
                continue
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
                {"table_name": f"public.{table_name}"},
            ).scalar()
            if not seq:
                continue
            conn.execute(
                text(
                    f"""
                    SELECT setval(
                        :seq,
                        COALESCE((SELECT MAX(id) FROM {qident(table_name)}), 1),
                        COALESCE((SELECT MAX(id) FROM {qident(table_name)}), 0) > 0
                    )
                    """
                ),
                {"seq": seq},
            )


def count_tables(engine: Engine) -> dict[str, int]:
    counts: dict[str, int] = {}
    inspector = inspect(engine)
    with engine.connect() as conn:
        for table_name in sorted(inspector.get_table_names()):
            counts[table_name] = int(conn.execute(text(f"SELECT COUNT(*) FROM {qident(table_name)}")).scalar() or 0)
    return counts


def drop_database(admin: Engine, database: str) -> None:
    if not database_exists(admin, database):
        return
    terminate_connections(admin, database)
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE {qident(database)}"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Unifica helpdesk + incidencias en una unica BBDD PostgreSQL ATC.")
    parser.add_argument("--execute", action="store_true", help="Ejecuta cambios reales. Sin esto, solo muestra configuracion.")
    parser.add_argument("--replace-target", action="store_true", help="Borra y recrea ATC si ya existe.")
    parser.add_argument("--drop-old", action="store_true", help="Elimina las BBDD originales luego de migrar correctamente.")
    args = parser.parse_args()

    atc_env = parse_env(ATC_ENV)
    inc_env = parse_env(INC_ENV)
    helpdesk_url = atc_env["DATABASE_URL"]
    incidencias_url = inc_env["DATABASE_URL"]
    helpdesk_db = make_url(helpdesk_url).database or "helpdesk"
    incidencias_db = make_url(incidencias_url).database or "incidencias"

    print(f"Origen helpdesk: {helpdesk_db}")
    print(f"Origen incidencias: {incidencias_db}")
    print(f"Destino final: {TARGET_DB}")
    if not args.execute:
        print("Dry-run. Usa --execute para aplicar.")
        return 0

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_ROOT / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)

    helpdesk_engine = make_engine(helpdesk_url)
    incidencias_engine = make_engine(incidencias_url)
    admin = make_engine(admin_url(helpdesk_url), autocommit=True)

    print(f"Respaldando datos en {backup_dir} ...")
    backup_database("helpdesk", helpdesk_engine, backup_dir)
    backup_database("incidencias", incidencias_engine, backup_dir)

    print(f"Creando {TARGET_DB} desde plantilla {helpdesk_db} ...")
    create_target_from_helpdesk(admin, helpdesk_db, replace=args.replace_target)

    target_url_atc = url_with_database(helpdesk_url, TARGET_DB)
    target_url_inc = url_with_database(incidencias_url, TARGET_DB)
    target_engine = make_engine(target_url_atc)

    print("Preparando users y tablas faltantes ...")
    ensure_target_user_columns(target_engine)
    create_missing_incidencias_tables(incidencias_engine, target_engine)

    print("Copiando tablas de incidencias ...")
    copy_regular_tables(incidencias_engine, target_engine)

    print("Fusionando users, user_areas e imagenes ODT ...")
    merge_users(incidencias_engine, target_engine)
    merge_user_areas(incidencias_engine, target_engine)
    merge_images_odt(incidencias_engine, target_engine)
    empty_login_sessions_if_exists(target_engine)
    reset_sequences(target_engine)

    counts_file = backup_dir / "atc_final_counts.json"
    counts_file.write_text(json.dumps(count_tables(target_engine), indent=2, ensure_ascii=False), encoding="utf-8")

    print("Actualizando .env ...")
    write_env_value(ATC_ENV, "DATABASE_URL", target_url_atc)
    write_env_value(ATC_ENV, "INCIDENCIAS_DATABASE_URL", target_url_atc)
    write_env_value(INC_ENV, "DATABASE_URL", target_url_inc)
    write_env_value(INC_ENV, "SUPPORT_SYNC_MODE", "off")
    write_env_value(INC_ENV, "SUPPORT_DB_URL", "")

    if args.drop_old:
        print(f"Eliminando bases antiguas: {helpdesk_db}, {incidencias_db} ...")
        drop_database(admin, helpdesk_db)
        drop_database(admin, incidencias_db)

    print("Unificacion terminada.")
    print(f"Backup JSONL: {backup_dir}")
    print(f"Conteos finales: {counts_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

