-- ============================================================
-- SQL DE CORRECCION MER - NO EJECUTAR SIN REVISION
-- ============================================================
-- Objetivo:
--   Convertir relaciones logicas del MER en integridad referencial real.
--
-- Base objetivo principal:
--   Base PostgreSQL "incidencias", schema public.
--
-- Decision de modelo:
--   La tabla canonica de incidencias debe ser public.registro.
--
-- Importante:
--   Este archivo es una propuesta. No fue ejecutado.
--   Antes de aplicarlo, crear migracion versionada, probar en staging,
--   revisar huerfanos y tomar respaldo.

-- ============================================================
-- 1) VALIDACIONES PREVIAS DE DATOS
-- ============================================================

-- 1.1 Verificar que registro.odt sea unico.
-- SELECT odt, COUNT(*) AS total
-- FROM public.registro
-- WHERE odt IS NOT NULL
-- GROUP BY odt
-- HAVING COUNT(*) > 1
-- ORDER BY total DESC, odt;

-- 1.2 Verificar cierres sin incidencia padre por ID.
-- SELECT c.*
-- FROM public.incidencias_cierres c
-- LEFT JOIN public.registro r ON r.id = c.incidencia_id
-- WHERE r.id IS NULL;

-- 1.3 Verificar cierres cuyo odt no coincide con el registro padre.
-- SELECT c.id AS cierre_id, c.incidencia_id, c.odt AS cierre_odt, r.odt AS registro_odt
-- FROM public.incidencias_cierres c
-- JOIN public.registro r ON r.id = c.incidencia_id
-- WHERE c.odt IS NOT NULL
--   AND r.odt IS NOT NULL
--   AND btrim(c.odt) <> btrim(r.odt);

-- 1.4 Verificar imagenes por ODT sin registro padre.
-- SELECT i.*
-- FROM public.incidencias_imagenes_odt i
-- LEFT JOIN public.registro r ON r.odt = i.odt
-- WHERE r.odt IS NULL;

-- 1.5 Verificar correos al cliente sin registro padre.
-- SELECT e.*
-- FROM public.registros_correos_cliente e
-- LEFT JOIN public.registro r ON r.odt = e.odt
-- WHERE r.odt IS NULL;

-- 1.6 Verificar rendiciones sin registro padre.
-- Esta FK es opcional; no aplicar si existen ODT validas fuera de registro.
-- SELECT rd.*
-- FROM public.rendiciones rd
-- LEFT JOIN public.registro r ON r.odt = rd.odt
-- WHERE r.odt IS NULL;

-- ============================================================
-- 2) INDICES RECOMENDADOS
-- ============================================================

-- 2.1 Soporte para FK y consultas de cierres por incidencia.
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidencias_cierres_incidencia_id
--     ON public.incidencias_cierres (incidencia_id);

-- 2.2 Soporte para busqueda de cierres por ODT.
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidencias_cierres_odt
--     ON public.incidencias_cierres (odt);

-- 2.3 Indice compuesto para bandejas/listados de incidencias.
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_registro_estado_fecha_registro
--     ON public.registro (estado, fecha_registro DESC);

-- ============================================================
-- 3) FOREIGN KEYS DE ALTA PRIORIDAD
-- ============================================================

-- 3.1 Cierres -> incidencia canonica.
-- Recomendado: ON DELETE RESTRICT para no borrar trazabilidad por accidente.
-- Aplicar primero como NOT VALID; luego validar cuando no existan huerfanos.
-- ALTER TABLE public.incidencias_cierres
--     ADD CONSTRAINT fk_incidencias_cierres_registro
--     FOREIGN KEY (incidencia_id)
--     REFERENCES public.registro (id)
--     ON DELETE RESTRICT
--     NOT VALID;

-- ALTER TABLE public.incidencias_cierres
--     VALIDATE CONSTRAINT fk_incidencias_cierres_registro;

-- 3.2 Imagenes por ODT -> registro canonico.
-- Aplicar solo si la validacion 1.4 no retorna filas.
-- ALTER TABLE public.incidencias_imagenes_odt
--     ADD CONSTRAINT fk_incidencias_imagenes_odt_registro_odt
--     FOREIGN KEY (odt)
--     REFERENCES public.registro (odt)
--     ON UPDATE CASCADE
--     ON DELETE RESTRICT
--     NOT VALID;

-- ALTER TABLE public.incidencias_imagenes_odt
--     VALIDATE CONSTRAINT fk_incidencias_imagenes_odt_registro_odt;

-- 3.3 Correos al cliente -> registro canonico.
-- Aplicar solo si la validacion 1.5 no retorna filas.
-- ALTER TABLE public.registros_correos_cliente
--     ADD CONSTRAINT fk_registros_correos_cliente_registro_odt
--     FOREIGN KEY (odt)
--     REFERENCES public.registro (odt)
--     ON UPDATE CASCADE
--     ON DELETE RESTRICT
--     NOT VALID;

-- ALTER TABLE public.registros_correos_cliente
--     VALIDATE CONSTRAINT fk_registros_correos_cliente_registro_odt;

-- ============================================================
-- 4) FOREIGN KEY OPCIONAL: RENDICIONES
-- ============================================================
-- No aplicar automaticamente.
-- Solo corresponde si negocio confirma que toda rendicion pertenece a una
-- incidencia registrada en public.registro.

-- ALTER TABLE public.rendiciones
--     ADD CONSTRAINT fk_rendiciones_registro_odt
--     FOREIGN KEY (odt)
--     REFERENCES public.registro (odt)
--     ON UPDATE CASCADE
--     ON DELETE RESTRICT
--     NOT VALID;

-- ALTER TABLE public.rendiciones
--     VALIDATE CONSTRAINT fk_rendiciones_registro_odt;

-- ============================================================
-- 5) OPCION AVANZADA: CONSISTENCIA ENTRE incidencia_id Y odt
-- ============================================================
-- Si se quiere garantizar que incidencias_cierres.odt coincida con el odt
-- del registro padre, se puede usar una FK compuesta.
-- Requiere que incidencias_cierres.odt no sea NULL o aceptar que la FK no
-- valide filas con odt NULL.
-- No aplicar en primera fase.

-- CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_registro_id_odt
--     ON public.registro (id, odt);

-- ALTER TABLE public.incidencias_cierres
--     ADD CONSTRAINT fk_incidencias_cierres_registro_id_odt
--     FOREIGN KEY (incidencia_id, odt)
--     REFERENCES public.registro (id, odt)
--     ON UPDATE CASCADE
--     ON DELETE RESTRICT
--     NOT VALID;

-- ALTER TABLE public.incidencias_cierres
--     VALIDATE CONSTRAINT fk_incidencias_cierres_registro_id_odt;

-- ============================================================
-- 6) RELACIONES QUE NO DEBEN FORMALIZARSE AUN
-- ============================================================

-- No agregar FK entre bases distintas:
--   helpdesk.incidencias_cierres -> incidencias.registro
-- PostgreSQL no soporta foreign keys nativas cross-database.

-- No agregar todavia:
--   public.registro.cliente -> public.bbdd_clientes.cliente
--   public.registro.tecnico -> public.users.name
--   public.rendiciones.tecnico -> public.users.name
-- Primero se requieren columnas *_id normalizadas y limpieza de datos.
