from __future__ import annotations

import getpass
import os

import pymssql


HOST = os.getenv("ATC_SQL_HOST", "10.20.30.8")
PORT = int(os.getenv("ATC_SQL_PORT", "14330"))
DATABASE = os.getenv("ATC_SQL_DATABASE", "PROYECTO_ATC")
USER = os.getenv("ATC_SQL_USER", "atc_vscode")


DELETE_SQL = """
SET NOCOUNT ON;

DECLARE @incidencias_borradas INT = 0;
DECLARE @cierres_borrados INT = 0;
DECLARE @imagenes_odt_borradas INT = 0;
DECLARE @imagenes_legacy_borradas INT = 0;
DECLARE @correos_borrados INT = 0;

BEGIN TRANSACTION;

IF OBJECT_ID('dbo.incidencias_cierres', 'U') IS NOT NULL
BEGIN
    EXEC sp_executesql N'
        DELETE c
        FROM dbo.incidencias_cierres AS c
        INNER JOIN dbo.incidencias AS i ON i.id = c.incidencia_id;
    ';
    SET @cierres_borrados = @@ROWCOUNT;
END;

IF OBJECT_ID('dbo.incidencias_imagenes_odt', 'U') IS NOT NULL
BEGIN
    EXEC sp_executesql N'
        DELETE img
        FROM dbo.incidencias_imagenes_odt AS img
        INNER JOIN dbo.incidencias AS i ON LTRIM(RTRIM(i.odt)) = LTRIM(RTRIM(img.odt));
    ';
    SET @imagenes_odt_borradas = @@ROWCOUNT;
END;

IF OBJECT_ID('dbo.incidencias_imagenes', 'U') IS NOT NULL
BEGIN
    EXEC sp_executesql N'
        DELETE img
        FROM dbo.incidencias_imagenes AS img
        INNER JOIN dbo.incidencias AS i ON LTRIM(RTRIM(i.odt)) = LTRIM(RTRIM(img.odt));
    ';
    SET @imagenes_legacy_borradas = @@ROWCOUNT;
END;

IF OBJECT_ID('dbo.registros_correos_cliente', 'U') IS NOT NULL
BEGIN
    EXEC sp_executesql N'
        DELETE mail
        FROM dbo.registros_correos_cliente AS mail
        INNER JOIN dbo.incidencias AS i ON LTRIM(RTRIM(i.odt)) = LTRIM(RTRIM(mail.odt));
    ';
    SET @correos_borrados = @@ROWCOUNT;
END;

DELETE FROM dbo.incidencias;
SET @incidencias_borradas = @@ROWCOUNT;

COMMIT TRANSACTION;

SELECT
    @incidencias_borradas AS incidencias_borradas,
    @cierres_borrados AS cierres_borrados,
    @imagenes_odt_borradas AS imagenes_odt_borradas,
    @imagenes_legacy_borradas AS imagenes_legacy_borradas,
    @correos_borrados AS correos_borrados;
"""


def fetch_one_dict(cursor) -> dict[str, int]:
    row = cursor.fetchone()
    if row is None:
        return {}
    return {desc[0]: int(row[index] or 0) for index, desc in enumerate(cursor.description or [])}


def print_dict(title: str, values: dict[str, int]) -> None:
    print(title)
    for key, value in values.items():
        print(f"{key}: {value}")


def table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SELECT CASE WHEN OBJECT_ID(%s, 'U') IS NULL THEN 0 ELSE 1 END", (table_name,))
    row = cursor.fetchone()
    return bool(row and row[0])


def count_table(cursor, table_name: str) -> int:
    if not table_exists(cursor, table_name):
        return 0
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    row = cursor.fetchone()
    return int(row[0] or 0) if row else 0


def counts(cursor) -> dict[str, int]:
    return {
        "incidencias": count_table(cursor, "dbo.incidencias"),
        "cierres": count_table(cursor, "dbo.incidencias_cierres"),
        "imagenes_odt": count_table(cursor, "dbo.incidencias_imagenes_odt"),
        "imagenes_legacy": count_table(cursor, "dbo.incidencias_imagenes"),
        "correos_cliente": count_table(cursor, "dbo.registros_correos_cliente"),
    }


def main() -> None:
    password = os.getenv("ATC_SQL_PASSWORD") or getpass.getpass("SQL password: ")
    conn = pymssql.connect(
        server=HOST,
        port=PORT,
        user=USER,
        password=password,
        database=DATABASE,
        login_timeout=10,
        timeout=60,
        as_dict=False,
    )
    try:
        with conn.cursor() as cursor:
            before = counts(cursor)
            print_dict("ANTES", before)

            cursor.execute(DELETE_SQL)
            deleted = fetch_one_dict(cursor)
            conn.commit()
            print_dict("BORRADO", deleted)

            after = counts(cursor)
            print_dict("DESPUES", after)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
