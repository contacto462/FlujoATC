from __future__ import annotations

import re
from typing import Any

from sqlalchemy import inspect, text


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def dialect_name(bind: Any) -> str:
    dialect = getattr(bind, "dialect", None)
    if dialect is None and hasattr(bind, "get_bind"):
        try:
            dialect = getattr(bind.get_bind(), "dialect", None)
        except Exception:
            dialect = None
    return str(getattr(dialect, "name", "") or "").lower()


def quote_ident(name: str, dialect: str) -> str:
    if not _IDENT_RE.match(name or ""):
        raise ValueError(f"Identificador SQL invalido: {name!r}")
    if dialect == "mssql":
        return "[" + name.replace("]", "]]") + "]"
    return '"' + name.replace('"', '""') + '"'


def sql_null_text(dialect: str) -> str:
    if dialect == "mssql":
        return "CAST(NULL AS NVARCHAR(MAX))"
    if dialect == "postgresql":
        return "NULL::text"
    return "NULL"


def _normalizar_tipo_columna(column_type: str, dialect: str) -> str:
    ctype = " ".join(str(column_type or "").strip().split())
    if not ctype:
        raise ValueError("Tipo de columna vacio")
    if dialect == "mssql":
        ctype = re.sub(r"\bTEXT\b", "NVARCHAR(MAX)", ctype, flags=re.IGNORECASE)
        ctype = re.sub(r"\bBOOLEAN\b", "BIT", ctype, flags=re.IGNORECASE)
        ctype = re.sub(r"\bTIMESTAMPTZ\b", "DATETIMEOFFSET", ctype, flags=re.IGNORECASE)
        ctype = re.sub(r"\bNOW\(\)", "GETDATE()", ctype, flags=re.IGNORECASE)
        ctype = re.sub(r"\bTRUE\b", "1", ctype, flags=re.IGNORECASE)
        ctype = re.sub(r"\bFALSE\b", "0", ctype, flags=re.IGNORECASE)
    elif dialect == "postgresql":
        ctype = re.sub(r"\bBIT\b", "BOOLEAN", ctype, flags=re.IGNORECASE)
        ctype = re.sub(r"\bDATETIMEOFFSET\b", "TIMESTAMPTZ", ctype, flags=re.IGNORECASE)
        ctype = re.sub(r"\bDATETIME2?\b", "TIMESTAMP", ctype, flags=re.IGNORECASE)
        ctype = re.sub(r"\bGETDATE\(\)", "CURRENT_TIMESTAMP", ctype, flags=re.IGNORECASE)
        ctype = re.sub(r"\bDEFAULT\s+0\b", "DEFAULT false", ctype, flags=re.IGNORECASE)
        ctype = re.sub(r"\bDEFAULT\s+1\b", "DEFAULT true", ctype, flags=re.IGNORECASE)
    return ctype


def add_column(conn: Any, table_name: str, column_name: str, column_type: str) -> None:
    dialect = dialect_name(conn)
    table = quote_ident(table_name, dialect)
    column = quote_ident(column_name, dialect)
    ctype = _normalizar_tipo_columna(column_type, dialect)
    if dialect == "mssql":
        conn.execute(text(f"ALTER TABLE {table} ADD {column} {ctype}"))
    else:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}"))


def rename_column(conn: Any, table_name: str, old_name: str, new_name: str) -> None:
    dialect = dialect_name(conn)
    if dialect == "mssql":
        objname = f"{quote_ident(table_name, dialect)}.{quote_ident(old_name, dialect)}"
        conn.execute(
            text("EXEC sp_rename :objname, :newname, 'COLUMN'"),
            {"objname": objname, "newname": new_name},
        )
    else:
        table = quote_ident(table_name, dialect)
        old = quote_ident(old_name, dialect)
        new = quote_ident(new_name, dialect)
        conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}"))


def drop_column(conn: Any, table_name: str, column_name: str) -> None:
    dialect = dialect_name(conn)
    table = quote_ident(table_name, dialect)
    column = quote_ident(column_name, dialect)
    conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))


def table_has_column(conn: Any, table_name: str, column_name: str) -> bool:
    inspector = inspect(conn)
    if not inspector.has_table(table_name):
        return False
    return column_name in {str(c.get("name", "")).strip() for c in inspector.get_columns(table_name)}
