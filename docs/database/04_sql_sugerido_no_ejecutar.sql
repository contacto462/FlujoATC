-- ============================================================
-- SQL SUGERIDO - NO EJECUTAR SIN REVISION, BACKUP Y MIGRACION
-- ============================================================
-- Este archivo documenta mejoras recomendadas. No fue aplicado a PostgreSQL.
-- Antes de usarlo: validar datos existentes, crear migracion Alembic, probar en staging.

-- 1) Integridad de cierres de incidencias en base incidencias.
-- Supuesto: public.incidencias_data es la tabla canonica de incidencias.
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidencias_cierres_incidencia_id
--     ON public.incidencias_cierres (incidencia_id);
-- ALTER TABLE public.incidencias_cierres
--     ADD CONSTRAINT fk_incidencias_cierres_incidencias_data
--     FOREIGN KEY (incidencia_id) REFERENCES public.incidencias_data(id);

-- 2) Consultas por ODT en cierres e imagenes.
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidencias_cierres_odt
--     ON public.incidencias_cierres (odt);
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidencias_imagenes_odt_odt
--     ON public.incidencias_imagenes_odt (odt);

-- 3) Bandejas de soporte.
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tickets_status_assigned_created
--     ON public.tickets (status, assigned_to_id, created_at DESC);
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_messages_ticket_created
--     ON public.messages (ticket_id, created_at DESC);

-- 4) Dominios controlados en helpdesk.
-- ALTER TABLE public.users
--     ADD CONSTRAINT ck_users_role CHECK (role IN ('admin', 'agent'));
-- ALTER TABLE public.tickets
--     ADD CONSTRAINT ck_tickets_status CHECK (status IN ('open', 'pending', 'resolved', 'closed'));
-- ALTER TABLE public.tickets
--     ADD CONSTRAINT ck_tickets_source CHECK (source IN ('email', 'whatsapp', 'internal'));
-- ALTER TABLE public.messages
--     ADD CONSTRAINT ck_messages_sender_type CHECK (sender_type IN ('requester', 'agent', 'system'));
-- ALTER TABLE public.messages
--     ADD CONSTRAINT ck_messages_channel CHECK (channel IN ('email', 'whatsapp', 'internal'));

-- 5) Ventas/ODT.
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_venta_ods_estado_created
--     ON public.venta_ods (estado, created_at DESC);
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_registro_estado_fecha
--     ON public.registro (estado, fecha_registro DESC);

-- 6) Tipos de datos recomendados, requieren limpieza previa.
-- Ejemplo conceptual. No ejecutar sin revisar valores invalidos:
-- ALTER TABLE public.servicio_tecnico_ventas_odt
--     ALTER COLUMN fecha_inicio_instalacion TYPE timestamp USING fecha_inicio_instalacion::timestamp;
