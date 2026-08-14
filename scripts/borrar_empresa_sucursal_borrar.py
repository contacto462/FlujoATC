from __future__ import annotations

import argparse
import getpass
import os
from typing import Iterable


TARGET_EMPRESA = "BORRAR"
TARGET_SUCURSAL = "BORRAR"


def _sql_literal(value: str) -> str:
    return "N'" + str(value or "").replace("'", "''") + "'"


def _connect():
    password = os.getenv("ATC_SQL_PASSWORD")
    user = os.getenv("ATC_SQL_USER", "atc_vscode")
    host = os.getenv("ATC_SQL_HOST", "10.20.30.8")
    port = int(os.getenv("ATC_SQL_PORT", "14330"))
    database = os.getenv("ATC_SQL_DATABASE", "PROYECTO_ATC")

    if password:
        import pymssql

        return pymssql.connect(
            server=host,
            port=port,
            user=user,
            password=password,
            database=database,
            login_timeout=10,
            timeout=60,
            as_dict=True,
        )

    try:
        import pyodbc
    except Exception as exc:
        raise RuntimeError(
            "Sin ATC_SQL_PASSWORD y pyodbc no está disponible. "
            "Ejecuta esto en el Windows Server o exporta ATC_SQL_PASSWORD."
        ) from exc

    conn_str = os.getenv(
        "ATC_SQL_ODBC_CONNECT",
        "DRIVER={ODBC Driver 13 for SQL Server};"
        "SERVER=SERVER\\SQLEXPRESS;"
        "DATABASE=PROYECTO_ATC;"
        "Trusted_Connection=yes;",
    )
    return pyodbc.connect(conn_str, timeout=10)


def _fetch_current_rows(cursor) -> list[dict[str, object]]:
    columns = [col[0] for col in (cursor.description or [])]
    result = []
    for row in cursor.fetchall():
        if isinstance(row, dict):
            result.append(row)
        else:
            result.append({name: row[index] for index, name in enumerate(columns)})
    return result


def _execute_all_resultsets(cursor, sql: str) -> list[tuple[str, list[dict[str, object]]]]:
    cursor.execute(sql)
    resultsets: list[tuple[str, list[dict[str, object]]]] = []
    index = 1
    while True:
        if cursor.description:
            resultsets.append((f"RESULTSET {index}", _fetch_current_rows(cursor)))
            index += 1
        try:
            has_next = cursor.nextset()
        except Exception:
            has_next = False
        if not has_next:
            break
    return resultsets


def _print_rows(title: str, rows: Iterable[dict[str, object]]) -> None:
    print(title)
    count = 0
    for row in rows:
        count += 1
        print("  " + ", ".join(f"{key}={value}" for key, value in row.items()))
    if count == 0:
        print("  (sin filas)")


def _base_sql(empresa: str, sucursal: str) -> str:
    empresa_lit = _sql_literal(empresa)
    sucursal_lit = _sql_literal(sucursal)
    return f"""
SET NOCOUNT ON;

DECLARE @empresa NVARCHAR(255) = {empresa_lit};
DECLARE @sucursal NVARCHAR(255) = {sucursal_lit};

IF OBJECT_ID('tempdb..#targets') IS NOT NULL DROP TABLE #targets;
IF OBJECT_ID('tempdb..#ods') IS NOT NULL DROP TABLE #ods;
IF OBJECT_ID('tempdb..#inc') IS NOT NULL DROP TABLE #inc;
IF OBJECT_ID('tempdb..#qr') IS NOT NULL DROP TABLE #qr;

CREATE TABLE #targets (
    id BIGINT NOT NULL PRIMARY KEY,
    rut NVARCHAR(40) NULL,
    nombre_empresa NVARCHAR(255) NULL,
    nombre_sucursal NVARCHAR(255) NULL
);

INSERT INTO #targets (id, rut, nombre_empresa, nombre_sucursal)
SELECT id, rut, nombre_empresa, nombre_sucursal
FROM dbo.bbdd_sucursales
WHERE LOWER(LTRIM(RTRIM(CAST(nombre_empresa AS NVARCHAR(MAX))))) = LOWER(@empresa)
  AND LOWER(LTRIM(RTRIM(CAST(nombre_sucursal AS NVARCHAR(MAX))))) = LOWER(@sucursal);

CREATE TABLE #ods (
    id BIGINT NOT NULL PRIMARY KEY,
    codigo NVARCHAR(30) NOT NULL UNIQUE
);

IF OBJECT_ID('dbo.venta_comercial', 'U') IS NOT NULL
BEGIN
    INSERT INTO #ods (id, codigo)
    SELECT DISTINCT v.id, v.codigo
    FROM dbo.venta_comercial AS v
    WHERE LOWER(LTRIM(RTRIM(CAST(v.razon_social AS NVARCHAR(MAX))))) = LOWER(@empresa)
      AND LOWER(LTRIM(RTRIM(COALESCE(CAST(v.nombre_sucursal AS NVARCHAR(MAX)), N'')))) = LOWER(@sucursal);
END;

CREATE TABLE #inc (
    id BIGINT NOT NULL PRIMARY KEY,
    odt NVARCHAR(30) NULL
);

IF OBJECT_ID('dbo.incidencias', 'U') IS NOT NULL
BEGIN
    INSERT INTO #inc (id, odt)
    SELECT DISTINCT i.id, i.odt
    FROM dbo.incidencias AS i
    WHERE LOWER(LTRIM(RTRIM(CAST(i.cliente AS NVARCHAR(MAX))))) = LOWER(@sucursal)
       OR LTRIM(RTRIM(CAST(i.odt AS NVARCHAR(30)))) IN (SELECT codigo FROM #ods);
END;

CREATE TABLE #qr (
    id BIGINT NOT NULL PRIMARY KEY
);

IF OBJECT_ID('dbo.inicio_turno_qr_generados', 'U') IS NOT NULL
BEGIN
    INSERT INTO #qr (id)
    SELECT DISTINCT q.id
    FROM dbo.inicio_turno_qr_generados AS q
    WHERE LTRIM(RTRIM(CAST(q.recinto_id AS NVARCHAR(80)))) IN (SELECT CAST(id AS NVARCHAR(80)) FROM #targets)
       OR LOWER(LTRIM(RTRIM(CAST(q.recinto_label AS NVARCHAR(MAX))))) = LOWER(@sucursal);
END;
"""


PLAN_SQL = """
SELECT 'bbdd_sucursales_objetivo' AS tabla, COUNT(*) AS filas FROM #targets
UNION ALL SELECT 'venta_comercial_objetivo', COUNT(*) FROM #ods
UNION ALL SELECT 'incidencias_objetivo', COUNT(*) FROM #inc
UNION ALL SELECT 'inicio_turno_qr_generados_objetivo', COUNT(*) FROM #qr;

SELECT id, rut, nombre_empresa, nombre_sucursal
FROM #targets
ORDER BY id;
"""


DELETE_SQL = """
SET XACT_ABORT ON;
BEGIN TRANSACTION;

IF OBJECT_ID('dbo.incidencias_cierres', 'U') IS NOT NULL
    DELETE c FROM dbo.incidencias_cierres AS c INNER JOIN #inc AS i ON i.id = c.incidencia_id;

IF OBJECT_ID('dbo.incidencias_imagenes_odt', 'U') IS NOT NULL
    DELETE img FROM dbo.incidencias_imagenes_odt AS img INNER JOIN #inc AS i ON LTRIM(RTRIM(img.odt)) = LTRIM(RTRIM(i.odt));

IF OBJECT_ID('dbo.incidencias_imagenes', 'U') IS NOT NULL
    DELETE img FROM dbo.incidencias_imagenes AS img INNER JOIN #inc AS i ON LTRIM(RTRIM(img.odt)) = LTRIM(RTRIM(i.odt));

IF OBJECT_ID('dbo.registros_correos_cliente', 'U') IS NOT NULL
    DELETE mail FROM dbo.registros_correos_cliente AS mail
    WHERE LTRIM(RTRIM(mail.odt)) IN (SELECT LTRIM(RTRIM(odt)) FROM #inc WHERE odt IS NOT NULL)
       OR LTRIM(RTRIM(mail.odt)) IN (SELECT LTRIM(RTRIM(codigo)) FROM #ods);

IF OBJECT_ID('dbo.incidencias', 'U') IS NOT NULL
    DELETE i FROM dbo.incidencias AS i INNER JOIN #inc AS x ON x.id = i.id;

IF OBJECT_ID('dbo.venta_ods_archivos', 'U') IS NOT NULL
    DELETE a FROM dbo.venta_ods_archivos AS a INNER JOIN #ods AS o ON o.id = a.ods_id OR LTRIM(RTRIM(o.codigo)) = LTRIM(RTRIM(a.codigo_ods));

IF OBJECT_ID('dbo.venta_administracion', 'U') IS NOT NULL
    DELETE a FROM dbo.venta_administracion AS a INNER JOIN #ods AS o ON LTRIM(RTRIM(o.codigo)) = LTRIM(RTRIM(a.odt));

IF OBJECT_ID('dbo.venta_finanzas', 'U') IS NOT NULL
    DELETE f FROM dbo.venta_finanzas AS f INNER JOIN #ods AS o ON LTRIM(RTRIM(o.codigo)) = LTRIM(RTRIM(f.odt));

IF OBJECT_ID('dbo.venta_servicio_tecnico', 'U') IS NOT NULL
    DELETE st FROM dbo.venta_servicio_tecnico AS st INNER JOIN #ods AS o ON LTRIM(RTRIM(o.codigo)) = LTRIM(RTRIM(st.odt));

IF OBJECT_ID('dbo.venta_soporte_tecnico', 'U') IS NOT NULL
    DELETE st FROM dbo.venta_soporte_tecnico AS st INNER JOIN #ods AS o ON LTRIM(RTRIM(o.codigo)) = LTRIM(RTRIM(st.odt));

IF OBJECT_ID('dbo.venta_operaciones', 'U') IS NOT NULL
    DELETE op FROM dbo.venta_operaciones AS op INNER JOIN #ods AS o ON LTRIM(RTRIM(o.codigo)) = LTRIM(RTRIM(op.odt));

IF OBJECT_ID('dbo.venta_comercial', 'U') IS NOT NULL
    DELETE v FROM dbo.venta_comercial AS v INNER JOIN #ods AS o ON o.id = v.id;

IF OBJECT_ID('dbo.protocolos_informes', 'U') IS NOT NULL
BEGIN
    DELETE pi
    FROM dbo.protocolos_informes AS pi
    INNER JOIN dbo.protocolos_registro AS pr ON pr.id = pi.registro_id
    WHERE LOWER(LTRIM(RTRIM(CAST(pr.cliente AS NVARCHAR(MAX))))) = LOWER(@empresa)
      AND LOWER(LTRIM(RTRIM(CAST(pr.sucursal AS NVARCHAR(MAX))))) = LOWER(@sucursal);

    DELETE pi
    FROM dbo.protocolos_informes AS pi
    WHERE LOWER(LTRIM(RTRIM(CAST(pi.cliente AS NVARCHAR(MAX))))) = LOWER(@empresa)
      AND LOWER(LTRIM(RTRIM(CAST(pi.sucursal AS NVARCHAR(MAX))))) = LOWER(@sucursal);
END;

IF OBJECT_ID('dbo.protocolos_registro', 'U') IS NOT NULL
    DELETE pr FROM dbo.protocolos_registro AS pr
    WHERE LOWER(LTRIM(RTRIM(CAST(pr.cliente AS NVARCHAR(MAX))))) = LOWER(@empresa)
      AND LOWER(LTRIM(RTRIM(CAST(pr.sucursal AS NVARCHAR(MAX))))) = LOWER(@sucursal);

IF OBJECT_ID('dbo.bitacora_noticias', 'U') IS NOT NULL
    DELETE n FROM dbo.bitacora_noticias AS n
    WHERE LOWER(LTRIM(RTRIM(CAST(n.nombre_empresa AS NVARCHAR(MAX))))) = LOWER(@empresa)
      AND LOWER(LTRIM(RTRIM(CAST(n.nombre_sucursal AS NVARCHAR(MAX))))) = LOWER(@sucursal);

IF OBJECT_ID('dbo.bitacora_registros', 'U') IS NOT NULL
    DELETE r FROM dbo.bitacora_registros AS r
    WHERE LOWER(LTRIM(RTRIM(CAST(r.nombre_empresa AS NVARCHAR(MAX))))) = LOWER(@empresa)
      AND LOWER(LTRIM(RTRIM(CAST(r.nombre_sucursal AS NVARCHAR(MAX))))) = LOWER(@sucursal);

IF OBJECT_ID('dbo.inicio_turno_ronda_registros', 'U') IS NOT NULL
    DELETE rr FROM dbo.inicio_turno_ronda_registros AS rr INNER JOIN #qr AS q ON q.id = rr.qr_generado_id;

IF OBJECT_ID('dbo.inicio_turno_qr_generados', 'U') IS NOT NULL
    DELETE qg FROM dbo.inicio_turno_qr_generados AS qg INNER JOIN #qr AS q ON q.id = qg.id;

IF OBJECT_ID('dbo.inicio_turno_registros', 'U') IS NOT NULL
    DELETE it FROM dbo.inicio_turno_registros AS it INNER JOIN #targets AS t ON t.id = it.sucursal_id;

IF OBJECT_ID('dbo.pruebas_sonido', 'U') IS NOT NULL
    DELETE ps FROM dbo.pruebas_sonido AS ps INNER JOIN #targets AS t ON t.id = ps.sucursal_id;

IF OBJECT_ID('dbo.sucursal_info_extra', 'U') IS NOT NULL
    DELETE e FROM dbo.sucursal_info_extra AS e INNER JOIN #targets AS t ON t.id = e.sucursal_id;

IF OBJECT_ID('dbo.sucursal_guardias', 'U') IS NOT NULL
    DELETE g FROM dbo.sucursal_guardias AS g INNER JOIN #targets AS t ON t.id = g.sucursal_id;

IF OBJECT_ID('dbo.sucursal_personas_autorizadas', 'U') IS NOT NULL
    DELETE p FROM dbo.sucursal_personas_autorizadas AS p INNER JOIN #targets AS t ON t.id = p.sucursal_id;

IF OBJECT_ID('dbo.sucursal_contactos_emergencia', 'U') IS NOT NULL
    DELETE c FROM dbo.sucursal_contactos_emergencia AS c INNER JOIN #targets AS t ON t.id = c.sucursal_id;

IF OBJECT_ID('dbo.contactos_emergencia', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.contactos_emergencia', 'sucursal') IS NOT NULL
    DELETE c FROM dbo.contactos_emergencia AS c
    WHERE LOWER(LTRIM(RTRIM(CAST(c.sucursal AS NVARCHAR(MAX))))) = LOWER(@sucursal);

IF OBJECT_ID('dbo.mantenciones_imagenes_sucursal', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.mantenciones_imagenes_sucursal', 'sucursal') IS NOT NULL
   AND COL_LENGTH('dbo.mantenciones_imagenes_sucursal', 'sucursal_key') IS NOT NULL
    DELETE m FROM dbo.mantenciones_imagenes_sucursal AS m
    WHERE LOWER(LTRIM(RTRIM(CAST(m.sucursal AS NVARCHAR(MAX))))) = LOWER(@sucursal)
       OR LOWER(LTRIM(RTRIM(CAST(m.sucursal_key AS NVARCHAR(MAX))))) = LOWER(@sucursal);

IF OBJECT_ID('dbo.cierre_apertura_imagenes', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.cierre_apertura_imagenes', 'client_id') IS NOT NULL
   AND COL_LENGTH('dbo.cierre_apertura_imagenes', 'client_name') IS NOT NULL
    DELETE ci FROM dbo.cierre_apertura_imagenes AS ci
    WHERE LOWER(LTRIM(RTRIM(CAST(ci.client_id AS NVARCHAR(MAX))))) = LOWER(@sucursal)
       OR LOWER(LTRIM(RTRIM(CAST(ci.client_name AS NVARCHAR(MAX))))) = LOWER(@sucursal);

DELETE s FROM dbo.bbdd_sucursales AS s INNER JOIN #targets AS t ON t.id = s.id;

IF OBJECT_ID('dbo.catalogo_clientes', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.catalogo_clientes', 'nombre_cliente') IS NOT NULL
   AND COL_LENGTH('dbo.catalogo_clientes', 'nombre_sucursal') IS NOT NULL
    DELETE cc FROM dbo.catalogo_clientes AS cc
    WHERE LOWER(LTRIM(RTRIM(CAST(cc.nombre_cliente AS NVARCHAR(MAX))))) = LOWER(@empresa)
      AND LOWER(LTRIM(RTRIM(CAST(cc.nombre_sucursal AS NVARCHAR(MAX))))) = LOWER(@sucursal);

DELETE c
FROM dbo.bbdd_clientes AS c
WHERE LOWER(LTRIM(RTRIM(CAST(c.cliente AS NVARCHAR(MAX))))) = LOWER(@empresa)
  AND NOT EXISTS (
      SELECT 1 FROM dbo.bbdd_sucursales AS s
      WHERE LOWER(LTRIM(RTRIM(CAST(s.rut AS NVARCHAR(MAX))))) = LOWER(LTRIM(RTRIM(CAST(c.rut AS NVARCHAR(MAX)))))
  )
  AND NOT EXISTS (
      SELECT 1 FROM dbo.venta_comercial AS v
      WHERE LOWER(LTRIM(RTRIM(CAST(v.rut_cliente AS NVARCHAR(MAX))))) = LOWER(LTRIM(RTRIM(CAST(c.rut AS NVARCHAR(MAX)))))
  );

COMMIT TRANSACTION;
"""


VERIFY_SQL = """
SELECT 'bbdd_sucursales_restantes' AS tabla, COUNT(*) AS filas
FROM dbo.bbdd_sucursales
WHERE LOWER(LTRIM(RTRIM(CAST(nombre_empresa AS NVARCHAR(MAX))))) = LOWER(@empresa)
  AND LOWER(LTRIM(RTRIM(CAST(nombre_sucursal AS NVARCHAR(MAX))))) = LOWER(@sucursal)
UNION ALL
SELECT 'bbdd_clientes_empresa_restantes', COUNT(*)
FROM dbo.bbdd_clientes
WHERE LOWER(LTRIM(RTRIM(CAST(cliente AS NVARCHAR(MAX))))) = LOWER(@empresa)
UNION ALL
SELECT 'venta_comercial_restantes', COUNT(*)
FROM dbo.venta_comercial
WHERE LOWER(LTRIM(RTRIM(CAST(razon_social AS NVARCHAR(MAX))))) = LOWER(@empresa)
  AND LOWER(LTRIM(RTRIM(COALESCE(CAST(nombre_sucursal AS NVARCHAR(MAX)), N'')))) = LOWER(@sucursal);
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Borra la empresa/sucursal dummy BORRAR de SQL Server.")
    parser.add_argument("--empresa", default=TARGET_EMPRESA)
    parser.add_argument("--sucursal", default=TARGET_SUCURSAL)
    parser.add_argument("--execute", action="store_true", help="Ejecuta el borrado. Sin esto solo muestra el plan.")
    args = parser.parse_args()

    if os.getenv("ATC_SQL_USER") and not os.getenv("ATC_SQL_PASSWORD"):
        os.environ["ATC_SQL_PASSWORD"] = getpass.getpass("Password SQL Server: ")

    base = _base_sql(args.empresa, args.sucursal)
    conn = _connect()
    try:
        cursor = conn.cursor()
        for title, rows in _execute_all_resultsets(cursor, base + PLAN_SQL):
            _print_rows("PLAN" if title == "RESULTSET 1" else "DETALLE", rows)

        if not args.execute:
            print("Dry-run: no se borró nada. Repite con --execute para borrar.")
            return

        resultsets = _execute_all_resultsets(cursor, base + DELETE_SQL + VERIFY_SQL)
        conn.commit()
        for _title, rows in resultsets:
            _print_rows("VERIFICACION", rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
