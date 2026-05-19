# Plan de correccion tecnica del MER

## Problemas criticos actuales

1. Hay dos entidades candidatas para incidencias: `registro` e `incidencias_data`.
   - `registro` existe en PostgreSQL, tiene modelo SQLAlchemy (`Registro`) y es usada por rutas/servicios de la aplicacion.
   - `incidencias_data` existe en PostgreSQL, pero no tiene modelo SQLAlchemy cargado en el proyecto.
   - Mantener ambas como entidades principales en el MER genera ambiguedad tecnica.

2. `incidencias_cierres.incidencia_id` no tiene foreign key real.
   - El campo es obligatorio (`NOT NULL`), pero PostgreSQL no garantiza que apunte a una incidencia existente.
   - Esto permite cierres huerfanos.

3. Varias relaciones por `odt` son solo logicas.
   - `incidencias_imagenes_odt.odt`
   - `registros_correos_cliente.odt`
   - `rendiciones.odt`
   - `incidencias_cierres.odt`

4. Hay relaciones por `odt` que ya estan correctamente formalizadas.
   - `administracion_odt.odt -> venta_ods.codigo`
   - `finanzas_odt.odt -> venta_ods.codigo`
   - `servicio_tecnico_ventas_odt.odt -> venta_ods.codigo`
   - `operaciones_venta_odt.odt -> venta_ods.codigo`

5. La base `ATC/helpdesk` contiene tablas de incidencias sin tabla padre local.
   - `incidencias_cierres`
   - `incidencias_imagenes_odt`
   - `incidencias_tecnicos`
   - No conviene crear foreign keys desde `helpdesk` hacia `incidencias`, porque PostgreSQL no soporta foreign keys nativas entre bases de datos distintas.

## Decision recomendada para incidencias

La tabla canonica recomendada es `registro`.

Motivos:

- Es la tabla declarada en los modelos SQLAlchemy del proyecto `Incidencias`.
- Es la tabla usada por servicios y rutas para crear, listar, actualizar y cerrar incidencias.
- Tiene `odt` con indice unico (`ix_registro_odt`), lo que permite usar ODT como identificador funcional.
- Contiene mas campos operativos actuales que `incidencias_data`: cierre, avance, observaciones, prioridad, materiales, soporte, servicio, drive y seguimiento.
- Evita crear una nueva tabla `incidencias`, lo que obligaria a migrar datos, reescribir codigo y duplicar temporalmente el dominio.

Decision explicita:

- Mantener `registro` como entidad principal del MER.
- Tratar `incidencias_data` como tabla legacy/importada o historica hasta confirmar su uso real.
- No crear una nueva tabla `incidencias` en esta fase.
- En una fase posterior, si se quiere un nombre mas profesional, crear una vista o migracion controlada desde `registro` hacia `incidencias`, pero no como primer paso.

## Foreign keys que deberian agregarse

### Alta prioridad

1. `incidencias_cierres.incidencia_id -> registro.id`
   - Justificacion: el cierre pertenece a una incidencia.
   - Cardinalidad: `registro 1:N incidencias_cierres`.
   - Accion recomendada: agregar FK `NOT VALID`, validar datos, luego `VALIDATE CONSTRAINT`.
   - `ON DELETE`: `RESTRICT` o sin accion. No conviene borrar cierres automaticamente si se borra una incidencia por error.

2. `incidencias_imagenes_odt.odt -> registro.odt`
   - Justificacion: la evidencia por ODT debe pertenecer a una incidencia registrada.
   - Cardinalidad: `registro 1:0..1 incidencias_imagenes_odt`, porque `incidencias_imagenes_odt.odt` es unico.
   - Accion recomendada: agregar FK solo si no existen imagenes huerfanas.
   - `ON DELETE`: `RESTRICT`. La evidencia no debe desaparecer por cascada accidental.

3. `registros_correos_cliente.odt -> registro.odt`
   - Justificacion: los correos enviados al cliente deben quedar asociados a la incidencia/ODT.
   - Cardinalidad: `registro 1:N registros_correos_cliente`.
   - Accion recomendada: agregar FK si todos los correos tienen ODT existente en `registro`.
   - `ON DELETE`: `RESTRICT`.

### Media prioridad

4. `rendiciones.odt -> registro.odt`
   - Justificacion: las rendiciones se registran contra una ODT.
   - Riesgo: puede haber rendiciones asociadas a ODT de venta, mantencion u otro flujo no registrado en `registro`.
   - Accion recomendada: no aplicar de inmediato. Primero medir huerfanos y confirmar regla de negocio.

5. `venta_ods_archivos.codigo_ods -> venta_ods.codigo`
   - Justificacion: existe relacion textual redundante por codigo.
   - Riesgo: ya existe FK real por `ods_id -> venta_ods.id`; agregar otra FK puede duplicar integridad y fallar si `codigo_ods` historico no coincide.
   - Accion recomendada: no agregar FK por ahora. Si se requiere consistencia entre `ods_id` y `codigo_ods`, usar validacion de aplicacion o trigger, no una FK redundante simple.

## Foreign keys que NO conviene agregar ahora

1. `helpdesk.incidencias_cierres -> incidencias.registro`
   - Motivo: son bases distintas (`helpdesk` e `incidencias`). PostgreSQL no permite FK nativa entre bases.
   - Alternativa: documentar la relacion como sincronizacion logica o consolidar las tablas en una sola base.

2. `registro.cliente -> bbdd_clientes.cliente`
   - Motivo: `registro.cliente` parece texto operativo/importado, no clave estable.
   - Riesgo: diferencias de escritura, tildes, abreviaciones o nombres historicos romperian la FK.
   - Alternativa: agregar `cliente_id` en una migracion futura, poblarlo y recien ahi declarar FK.

3. `registro.tecnico -> users` o `incidencias_tecnicos`
   - Motivo: el tecnico esta guardado como nombre, no como identificador estable.
   - Alternativa: crear tabla canonica de tecnicos y columna `tecnico_id` antes de declarar FK.

4. `rendiciones.tecnico -> users`
   - Motivo: actualmente es texto operativo. Requiere normalizacion previa de tecnicos.

5. `incidencias_cierres.odt -> registro.odt` como unica FK de cierre.
   - Motivo: el campo fuerte del cierre es `incidencia_id`. `odt` funciona como copia legible.
   - Alternativa: si se quiere maxima consistencia, usar FK compuesta `(incidencia_id, odt) -> registro(id, odt)` despues de validar y hacer `odt NOT NULL`.

## Riesgos antes de modificar la base

- Datos huerfanos: registros en tablas hijas cuyo `odt` o `incidencia_id` no existe en `registro`.
- Tipos no identicos: `incidencias_cierres.incidencia_id` es `BIGINT`, mientras `registro.id` es `INTEGER`.
- ODT con formatos inconsistentes: espacios, mayusculas, prefijos o ceros a la izquierda.
- Tablas duplicadas entre `helpdesk` e `incidencias`.
- Codigo que crea tablas o constraints en startup mediante `create_all` y funciones `_ensure_*`.
- Ausencia de migraciones versionadas; aplicar cambios manuales aumentaria la deriva.

## Orden correcto de implementacion

1. Congelar decision de modelo: `registro` es la entidad canonica de incidencias.
2. Agregar o corregir modelos SQLAlchemy faltantes para tablas reales que se mantendran.
3. Crear una migracion versionada, no aplicar SQL manual directo en produccion.
4. Ejecutar validaciones de datos en staging:
   - duplicados de `registro.odt`;
   - cierres sin `registro.id`;
   - imagenes sin `registro.odt`;
   - correos sin `registro.odt`;
   - rendiciones sin `registro.odt`.
5. Corregir datos huerfanos con aprobacion de negocio.
6. Crear indices faltantes de forma concurrente cuando aplique.
7. Agregar foreign keys con `NOT VALID`.
8. Validar constraints con `VALIDATE CONSTRAINT`.
9. Actualizar el MER final para mostrar solo `registro` como incidencia canonica.
10. Documentar `incidencias_data` como legacy, archivo historico o tabla pendiente de retiro.

## Que validar antes de aplicar SQL

- Que `registro.odt` sea unico y no nulo en los registros activos.
- Que todo `incidencias_cierres.incidencia_id` exista en `registro.id`.
- Que todo `incidencias_imagenes_odt.odt` exista en `registro.odt`.
- Que todo `registros_correos_cliente.odt` exista en `registro.odt`.
- Que `rendiciones.odt` pertenezca siempre a `registro.odt`; si no, no agregar FK.
- Que el codigo de la aplicacion no cree registros hijos antes de crear el registro principal.
- Que no existan procesos externos escribiendo directamente en tablas hijas.

## Cambios seguros y cambios que requieren respaldo

### Seguros, con bajo riesgo

- Crear indices no unicos sobre columnas existentes.
- Ejecutar consultas `SELECT` de validacion.
- Agregar constraints `NOT VALID` en staging.
- Documentar `incidencias_data` como legacy.

### Requieren respaldo y ventana controlada

- Agregar foreign keys en produccion.
- Validar constraints sobre tablas con volumen alto.
- Cambiar tipos de datos.
- Hacer `NOT NULL` sobre columnas existentes.
- Eliminar duplicidad entre `registro` e `incidencias_data`.
- Renombrar tablas o columnas.

## Resultado esperado

Despues de aplicar estas correcciones mediante migraciones y con datos validados, el MER podria considerarse profesional y tecnicamente consistente para presentacion. La condicion clave es que `registro` quede como unica entidad canonica de incidencias y que las relaciones por `odt` criticas pasen de logicas a foreign keys reales o queden justificadas formalmente como relaciones no obligatorias.
