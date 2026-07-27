from __future__ import annotations

from sqlalchemy import text

from ATC.app.core.db import SessionLocal


SQL = """
SET NOCOUNT ON;

DECLARE @incidencias_borradas INT = 0;
DECLARE @cierres_borrados INT = 0;
DECLARE @imagenes_odt_borradas INT = 0;
DECLARE @imagenes_legacy_borradas INT = 0;
DECLARE @correos_borrados INT = 0;

BEGIN TRANSACTION;

IF OBJECT_ID('dbo.incidencias_cierres', 'U') IS NOT NULL
BEGIN
    DELETE c
    FROM dbo.incidencias_cierres AS c
    INNER JOIN dbo.incidencias AS i ON i.id = c.incidencia_id;
    SET @cierres_borrados = @@ROWCOUNT;
END;

IF OBJECT_ID('dbo.incidencias_imagenes_odt', 'U') IS NOT NULL
BEGIN
    DELETE img
    FROM dbo.incidencias_imagenes_odt AS img
    INNER JOIN dbo.incidencias AS i ON LTRIM(RTRIM(i.odt)) = LTRIM(RTRIM(img.odt));
    SET @imagenes_odt_borradas = @@ROWCOUNT;
END;

IF OBJECT_ID('dbo.incidencias_imagenes', 'U') IS NOT NULL
BEGIN
    DELETE img
    FROM dbo.incidencias_imagenes AS img
    INNER JOIN dbo.incidencias AS i ON LTRIM(RTRIM(i.odt)) = LTRIM(RTRIM(img.odt));
    SET @imagenes_legacy_borradas = @@ROWCOUNT;
END;

IF OBJECT_ID('dbo.registros_correos_cliente', 'U') IS NOT NULL
BEGIN
    DELETE mail
    FROM dbo.registros_correos_cliente AS mail
    INNER JOIN dbo.incidencias AS i ON LTRIM(RTRIM(i.odt)) = LTRIM(RTRIM(mail.odt));
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


def main() -> None:
    db = SessionLocal()
    try:
        row = db.execute(text(SQL)).mappings().first()
        db.commit()
        print("Borrado completado.")
        if row:
            for key, value in row.items():
                print(f"{key}: {value}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
