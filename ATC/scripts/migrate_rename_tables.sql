-- Migración de esquema: renombrar, mantener y borrar tablas
-- Ejecutar sobre la base de datos ATC con psql o pgAdmin
-- Fecha: 2026-06-15

BEGIN;

-- ─────────────────────────────────────────────────────────────────
-- 1. Liberar el nombre "incidencias" (modelo de importaciones Excel)
--    antes de que "registro" lo tome
-- ─────────────────────────────────────────────────────────────────
ALTER TABLE IF EXISTS incidencias RENAME TO incidencias_importadas;

-- ─────────────────────────────────────────────────────────────────
-- 2. Renombrar tablas
-- ─────────────────────────────────────────────────────────────────
ALTER TABLE IF EXISTS registro                  RENAME TO incidencias;
ALTER TABLE IF EXISTS administracion_odt        RENAME TO venta_administracion;
ALTER TABLE IF EXISTS finanzas_odt              RENAME TO venta_finanzas;
ALTER TABLE IF EXISTS operaciones_venta_odt     RENAME TO venta_operaciones;
ALTER TABLE IF EXISTS requesters                RENAME TO clientes;
ALTER TABLE IF EXISTS servicio_tecnico_ventas_odt RENAME TO venta_servicio_tecnico;
ALTER TABLE IF EXISTS venta_ods                 RENAME TO venta_comercial;

-- ─────────────────────────────────────────────────────────────────
-- 3. Borrar tablas obsoletas
--    (CASCADE para eliminar también FK y secuencias dependientes)
-- ─────────────────────────────────────────────────────────────────
DROP TABLE IF EXISTS catalogo_clientes     CASCADE;
DROP TABLE IF EXISTS contactos_emergencia  CASCADE;
DROP TABLE IF EXISTS incidencias_imagenes  CASCADE;
DROP TABLE IF EXISTS sync_outbox           CASCADE;
DROP TABLE IF EXISTS tareas                CASCADE;
DROP TABLE IF EXISTS venta_clientes        CASCADE;

-- ─────────────────────────────────────────────────────────────────
-- 4. Actualizar restricciones de FK renombradas
--    PostgreSQL sigue las FKs por OID al renombrar tablas, pero
--    los índices/constraints conservan el nombre antiguo.
--    Renombramos los más visibles para mantener coherencia.
-- ─────────────────────────────────────────────────────────────────

-- FK de tickets → clientes (antes requesters)
DO $$
DECLARE
    c TEXT;
BEGIN
    SELECT constraint_name INTO c
    FROM information_schema.table_constraints
    WHERE table_name = 'tickets'
      AND constraint_type = 'FOREIGN KEY'
      AND constraint_name ILIKE '%requester%';
    IF c IS NOT NULL THEN
        EXECUTE format('ALTER TABLE tickets RENAME CONSTRAINT %I TO fk_tickets_clientes_id', c);
    END IF;
END $$;

-- FK de requester_internal_note_read_states → clientes
DO $$
DECLARE
    c TEXT;
BEGIN
    SELECT constraint_name INTO c
    FROM information_schema.table_constraints
    WHERE table_name = 'requester_internal_note_read_states'
      AND constraint_type = 'FOREIGN KEY'
      AND constraint_name ILIKE '%requester%';
    IF c IS NOT NULL THEN
        EXECUTE format('ALTER TABLE requester_internal_note_read_states RENAME CONSTRAINT %I TO fk_requester_note_read_clientes_id', c);
    END IF;
END $$;

COMMIT;
