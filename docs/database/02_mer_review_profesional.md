# Revision profesional del modelo entidad-relacion

## Resumen ejecutivo
El modelo es funcional y tiene relaciones centrales correctamente declaradas, especialmente en tickets y en venta/ODT. No obstante, no esta al 100% alineado con produccion: hay tablas reales no modeladas, una tabla modelada que no existe en `helpdesk`, relaciones logicas sin FK y falta de migraciones versionadas.

Veredicto: presentable como MER revisado con salvedades, pero no como modelo fisico final de produccion hasta resolver las diferencias criticas.

## Diagnostico general
- Normalizacion: parcial; soporte y venta estan mejor estructurados que incidencias/catalogos legacy.
- Integridad referencial: buena en relaciones principales declaradas; incompleta en cierres, imagenes y relaciones por ODT.
- Produccion: requiere migraciones, constraints y decision canonica sobre la entidad de incidencia.

## Inconsistencias entre codigo y PostgreSQL
### `ATC/helpdesk`
- Tablas en PostgreSQL sin modelo: `incidencias_cierres`, `incidencias_imagenes_odt`, `incidencias_tecnicos`.
- Tablas en modelo sin tabla real: `incidencias`.
- Diferencias de columnas:
  - `tickets`: en modelo/no DB: ninguna; en DB/no modelo: ['closed_at'].
- FKs del modelo no presentes en DB: ninguna.
- FKs reales no presentes en modelo: ninguna.

### `Incidencias`
- Tablas en PostgreSQL sin modelo: `incidencias_cierres`, `incidencias_data`, `sesiones_tecnico`, `users`.
- Tablas en modelo sin tabla real: `odt_ventas`.
- Diferencias de columnas:
  - `catalogo_clientes`: en modelo/no DB: ['activo', 'cliente']; en DB/no modelo: ['celular', 'direccion_sucursal', 'email', 'nombre_cliente', 'nombre_empleado', 'nombre_sucursal', 'nro_emergencia', 'rut_cliente', 'rut_empleado'].
  - `servicio_tecnico_ventas_odt`: en modelo/no DB: ninguna; en DB/no modelo: ['camaras_registradas', 'configuracion_camaras', 'configuracion_cliente', 'configuracion_ivs', 'enlace_servidor', 'fecha_configuracion_camaras', 'fecha_configuracion_cliente', 'fecha_configuracion_ivs', 'fecha_enlace_servidor', 'fecha_plan_grabacion', 'fecha_posicionamiento_imagen', 'fecha_vb_final_servicio', 'plan_grabacion', 'posicionamiento_imagen', 'vb_final_servicio'].
  - `venta_ods`: en modelo/no DB: ninguna; en DB/no modelo: ['ejecutivo_venta'].
- FKs del modelo no presentes en DB: ninguna.
- FKs reales no presentes en modelo: ninguna.

## Problemas encontrados y recomendaciones
- Prioridad Alta: Desalineacion ATC: SQLAlchemy declara `incidencias`, pero `helpdesk` no tiene esa tabla; existen `incidencias_cierres`, `incidencias_tecnicos` e `incidencias_imagenes_odt` sin modelo ATC.
- Prioridad Alta: `incidencias_cierres.incidencia_id` no tiene FK real. En `incidencias` apunta logicamente a `incidencias_data.id`, pero no esta declarado.
- Prioridad Alta: No se detecto Alembic/migraciones versionadas; esto aumenta la deriva entre codigo y PostgreSQL.
- Prioridad Media: `catalogo_clientes` en `incidencias` esta desalineada: el modelo espera `cliente`/`activo`, la base contiene datos de cliente, sucursal y contacto.
- Prioridad Media: Hay tablas productivas no modeladas en `incidencias`: `incidencias_data`, `incidencias_cierres`, `sesiones_tecnico`, `users`.
- Prioridad Media: Estados, roles, canales y prioridades estan como texto sin CHECK/ENUM en varias tablas.
- Prioridad Media: Varias fechas y banderas se almacenan como VARCHAR/TEXT, especialmente en incidencias y seguimiento de venta.
- Prioridad Media: Existe duplicidad semantica entre imagenes por ODT y fotos embebidas, y entre `registro`/`incidencias_data`.
- Prioridad Baja: Los nombres mezclan estilos (`bbdd_*`, `registro`, `venta_ods`, `odt_ventas`, `incidencias_data`).

## Indices faltantes o recomendados
- Prioridad Alta: indice en `incidencias_cierres.incidencia_id` para joins con la incidencia base.
- Prioridad Alta: indice en `incidencias_cierres.odt` si el cierre se consulta por ODT.
- Prioridad Media: indices compuestos `tickets(status, assigned_to_id, created_at)`, `messages(ticket_id, created_at)`, `registro(estado, fecha_registro)` y `venta_ods(estado, created_at)`.
- Prioridad Media: indices por `odt` en tablas de evidencia, correos y rendiciones.

## Restricciones faltantes sugeridas
- CHECK para estados, roles, canales y banderas textuales.
- FK para `incidencias_cierres.incidencia_id` hacia la tabla canonica de incidencias.
- UNIQUE canonico sobre ODT si el negocio confirma que identifica una incidencia u ODT de forma unica.
- NOT NULL en campos obligatorios, despues de revisar datos historicos.

## Veredicto profesional final
El MER actual esta razonablemente correcto en sus dominios centrales, pero incompleto como representacion fisica de produccion. Primero debe corregirse la deriva modelo-base; luego normalizacion/tipos; finalmente nombres y claridad visual.
