USE PROYECTO_ATC;
GO

SET XACT_ABORT ON;
SET NOCOUNT ON;

BEGIN TRANSACTION;

SELECT *
INTO #rendiciones_reindex
FROM dbo.rendiciones
WHERE id IN (1007, 1008);

IF (SELECT COUNT(*) FROM #rendiciones_reindex) <> 2
BEGIN
    THROW 51000, 'No se encontraron exactamente las rendiciones 1007 y 1008 para renumerar.', 1;
END;

DELETE p
FROM dbo.rendiciones_pagos AS p
INNER JOIN dbo.rendiciones AS r ON r.codigo_diario = p.codigo_diario
WHERE r.id IN (6, 1004, 1005, 1006);

DELETE FROM dbo.rendiciones
WHERE id IN (6, 1004, 1005, 1006, 1007, 1008);

IF EXISTS (SELECT 1 FROM dbo.rendiciones)
BEGIN
    THROW 51001, 'Quedaron rendiciones no previstas. Se cancela para no mezclar IDs.', 1;
END;

SET IDENTITY_INSERT dbo.rendiciones ON;

INSERT INTO dbo.rendiciones (
    id, codigo_diario, fecha_registro, tecnico, mail, odt, cliente, comuna,
    tipo_gasto, tipo_documento, nro_documento, fecha_documento, monto_total,
    descripcion, url_boleta, url_informe, documento, estado_revision
)
SELECT
    CASE WHEN id = 1007 THEN 1 WHEN id = 1008 THEN 2 ELSE id END,
    codigo_diario, fecha_registro, tecnico, mail, odt, cliente, comuna,
    tipo_gasto, tipo_documento, nro_documento, fecha_documento, monto_total,
    descripcion, url_boleta, url_informe, documento, estado_revision
FROM #rendiciones_reindex
ORDER BY id;

SET IDENTITY_INSERT dbo.rendiciones OFF;

DBCC CHECKIDENT ('dbo.rendiciones', RESEED, 2);

COMMIT TRANSACTION;

SELECT id, codigo_diario, tecnico, odt, cliente, tipo_gasto, nro_documento, monto_total, estado_revision
FROM dbo.rendiciones
ORDER BY id;
