-- Auditoría rápida (PostgreSQL)
-- 1) Filas aproximadas por tabla (rápido)
SELECT relname AS tabla, n_live_tup AS filas_aprox
FROM pg_stat_user_tables
ORDER BY n_live_tup ASC, relname ASC;

-- 2) Conteo exacto por tabla (más caro): reemplaza/ajusta esquema si aplica (public por defecto)
-- Nota: este bloque está escrito para las 41 tablas detectadas en el repo.
SELECT 'administracion_odt' AS tabla, COUNT(*)::bigint AS filas FROM public.administracion_odt
UNION ALL SELECT 'automation_logs', COUNT(*)::bigint FROM public.automation_logs
UNION ALL SELECT 'bbdd_clientes', COUNT(*)::bigint FROM public.bbdd_clientes
UNION ALL SELECT 'bbdd_sucursales', COUNT(*)::bigint FROM public.bbdd_sucursales
UNION ALL SELECT 'catalogo_clientes', COUNT(*)::bigint FROM public.catalogo_clientes
UNION ALL SELECT 'contactos_emergencia', COUNT(*)::bigint FROM public.contactos_emergencia
UNION ALL SELECT 'email_sync_states', COUNT(*)::bigint FROM public.email_sync_states
UNION ALL SELECT 'finanzas_odt', COUNT(*)::bigint FROM public.finanzas_odt
UNION ALL SELECT 'incidencias', COUNT(*)::bigint FROM public.incidencias
UNION ALL SELECT 'incidencias_imagenes', COUNT(*)::bigint FROM public.incidencias_imagenes
UNION ALL SELECT 'incidencias_imagenes_odt', COUNT(*)::bigint FROM public.incidencias_imagenes_odt
UNION ALL SELECT 'incidencias_tecnicos', COUNT(*)::bigint FROM public.incidencias_tecnicos
UNION ALL SELECT 'login_sessions', COUNT(*)::bigint FROM public.login_sessions
UNION ALL SELECT 'mantenciones_imagenes_sucursal', COUNT(*)::bigint FROM public.mantenciones_imagenes_sucursal
UNION ALL SELECT 'messages', COUNT(*)::bigint FROM public.messages
UNION ALL SELECT 'operaciones_venta_odt', COUNT(*)::bigint FROM public.operaciones_venta_odt
UNION ALL SELECT 'protocolos_informes', COUNT(*)::bigint FROM public.protocolos_informes
UNION ALL SELECT 'protocolos_registro', COUNT(*)::bigint FROM public.protocolos_registro
UNION ALL SELECT 'registro', COUNT(*)::bigint FROM public.registro
UNION ALL SELECT 'registros_correos_cliente', COUNT(*)::bigint FROM public.registros_correos_cliente
UNION ALL SELECT 'rendiciones', COUNT(*)::bigint FROM public.rendiciones
UNION ALL SELECT 'requester_internal_note_read_states', COUNT(*)::bigint FROM public.requester_internal_note_read_states
UNION ALL SELECT 'requesters', COUNT(*)::bigint FROM public.requesters
UNION ALL SELECT 'servicio_tecnico_ventas_odt', COUNT(*)::bigint FROM public.servicio_tecnico_ventas_odt
UNION ALL SELECT 'sucursal_contactos_emergencia', COUNT(*)::bigint FROM public.sucursal_contactos_emergencia
UNION ALL SELECT 'sucursal_guardias', COUNT(*)::bigint FROM public.sucursal_guardias
UNION ALL SELECT 'sucursal_personas_autorizadas', COUNT(*)::bigint FROM public.sucursal_personas_autorizadas
UNION ALL SELECT 'sync_outbox', COUNT(*)::bigint FROM public.sync_outbox
UNION ALL SELECT 'tareas', COUNT(*)::bigint FROM public.tareas
UNION ALL SELECT 'ticket_alert_read_states', COUNT(*)::bigint FROM public.ticket_alert_read_states
UNION ALL SELECT 'ticket_assignment_history', COUNT(*)::bigint FROM public.ticket_assignment_history
UNION ALL SELECT 'ticket_internal_note_read_states', COUNT(*)::bigint FROM public.ticket_internal_note_read_states
UNION ALL SELECT 'ticket_manual_unread', COUNT(*)::bigint FROM public.ticket_manual_unread
UNION ALL SELECT 'ticket_message_read_states', COUNT(*)::bigint FROM public.ticket_message_read_states
UNION ALL SELECT 'ticket_sla_feedback', COUNT(*)::bigint FROM public.ticket_sla_feedback
UNION ALL SELECT 'ticket_sla_feedback_events', COUNT(*)::bigint FROM public.ticket_sla_feedback_events
UNION ALL SELECT 'tickets', COUNT(*)::bigint FROM public.tickets
UNION ALL SELECT 'users', COUNT(*)::bigint FROM public.users
UNION ALL SELECT 'venta_clientes', COUNT(*)::bigint FROM public.venta_clientes
UNION ALL SELECT 'venta_ods', COUNT(*)::bigint FROM public.venta_ods
UNION ALL SELECT 'venta_ods_archivos', COUNT(*)::bigint FROM public.venta_ods_archivos
ORDER BY filas ASC, tabla ASC;

-- 3) Tablas sin FK (en PostgreSQL, según constraints reales del DB)
-- Esto detecta tablas que NO tienen FK salientes NI entrantes.
WITH fk_src AS (
  SELECT conrelid::regclass::text AS tabla
  FROM pg_constraint
  WHERE contype = 'f'
),
fk_tgt AS (
  SELECT confrelid::regclass::text AS tabla
  FROM pg_constraint
  WHERE contype = 'f'
),
all_tables AS (
  SELECT (schemaname || '.' || tablename) AS tabla
  FROM pg_tables
  WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
)
SELECT a.tabla
FROM all_tables a
LEFT JOIN fk_src s ON s.tabla = a.tabla
LEFT JOIN fk_tgt t ON t.tabla = a.tabla
WHERE s.tabla IS NULL AND t.tabla IS NULL
ORDER BY a.tabla;

