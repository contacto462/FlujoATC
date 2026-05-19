# Validacion previa para foreign keys reales

## Alcance

Fecha de validacion: 2026-05-19.

Base inspeccionada: `incidencias`.

Conexion usada: `postgresql://***:***@localhost:5432/incidencias`.

Schema: `public`.

Version reportada: `PostgreSQL 18.2 on x86_64-windows`.

Restriccion respetada: se ejecutaron solo consultas `SELECT`. No se ejecuto `ALTER TABLE`, `DROP`, `DELETE`, `UPDATE`, `INSERT` ni migraciones.

## Tablas verificadas

Todas las tablas necesarias existen:

| Tabla | Existe |
|---|---:|
| `registro` | Si |
| `incidencias_cierres` | Si |
| `incidencias_imagenes_odt` | Si |
| `registros_correos_cliente` | Si |

## Volumen actual

| Tabla | Registros |
|---|---:|
| `registro` | 37 |
| `incidencias_cierres` | 0 |
| `incidencias_imagenes_odt` | 66 |
| `registros_correos_cliente` | 24 |

## Validacion de tipos de datos

| Campo | Tipo PostgreSQL | Nullable | Observacion |
|---|---|---:|---|
| `incidencias_cierres.incidencia_id` | `bigint` / `int8` | NO | Apunta logicamente a `registro.id`, pero el tipo no es identico. |
| `registro.id` | `integer` / `int4` | NO | Primary key real. |
| `incidencias_imagenes_odt.odt` | `varchar(80)` | NO | Mas largo que `registro.odt`. |
| `registro.odt` | `varchar(30)` | NO | Identificador funcional unico. |
| `registros_correos_cliente.odt` | `varchar(30)` | NO | Compatible en largo con `registro.odt`. |

Dictamen de tipos:

- `incidencias_cierres.incidencia_id` y `registro.id` no tienen el mismo tipo exacto (`bigint` vs `integer`). Esto no invalida automaticamente una FK en PostgreSQL si existe operador de igualdad compatible, pero no es ideal para un modelo profesional. A futuro conviene alinear tipos.
- `incidencias_imagenes_odt.odt` puede contener hasta 80 caracteres, mientras `registro.odt` solo 30. Esto permite valores hijos que nunca podrian existir en el padre. Antes de una FK, hay que validar longitud y limpiar huerfanos.
- `registros_correos_cliente.odt` y `registro.odt` son compatibles (`varchar(30)`).

## Validacion de unicidad de `registro.odt`

Resultado:

- `registro.odt` tiene indice unico real: `ix_registro_odt`.
- Duplicados encontrados en `registro.odt`: 0.
- Valores NULL en `registro.odt`: 0.

Dictamen:

`registro.odt` es apto como columna referenciada para foreign keys por ODT.

## Indices utiles existentes

### `registro`

- `registro_pkey`: unique primary key sobre `id`.
- `ix_registro_odt`: unique sobre `odt`.
- `ix_registro_cliente`: indice sobre `cliente`.
- `ix_registro_estado`: indice sobre `estado`.

### `incidencias_cierres`

- `incidencias_cierres_pkey`: primary key sobre `id`.

Faltan indices utiles para las FKs propuestas:

- Falta indice sobre `incidencias_cierres.incidencia_id`.
- Falta indice sobre `incidencias_cierres.odt` si se consulta por ODT.

### `incidencias_imagenes_odt`

- `incidencias_imagenes_odt_pkey`: primary key sobre `id`.
- `idx_incidencias_imagenes_odt_odt`: indice no unico sobre `odt`.
- `ix_incidencias_imagenes_odt_odt`: indice unico sobre `odt`.
- `uq_incidencias_imagenes_odt_odt`: indice unico sobre `odt`.

Observacion:

Hay indices redundantes sobre `incidencias_imagenes_odt.odt`: un indice no unico y dos unicos. No conviene eliminarlos en esta fase porque la restriccion indica no modificar PostgreSQL, pero deberian revisarse en una migracion futura.

### `registros_correos_cliente`

- `registros_correos_cliente_pkey`: primary key sobre `id`.
- `ix_registros_correos_cliente_odt`: indice sobre `odt`.

## Validacion 1: `incidencias_cierres.incidencia_id -> registro.id`

Consulta validada:

- Cierres cuyo `incidencia_id` no existe en `registro.id`.

Resultado:

- Registros en `incidencias_cierres`: 0.
- Huerfanos por `incidencia_id`: 0.
- Ejemplos problematicos: ninguno.
- Inconsistencias entre `incidencias_cierres.odt` y `registro.odt`: 0.

Dictamen:

Desde el punto de vista de datos actuales, es seguro agregar la FK `incidencias_cierres.incidencia_id -> registro.id`, porque no hay datos hijos que la bloqueen.

Riesgo pendiente:

- El tipo no es identico (`bigint` contra `integer`).
- Falta indice en `incidencias_cierres.incidencia_id`.

Recomendacion:

1. Crear indice sobre `incidencias_cierres.incidencia_id`.
2. Agregar FK con `NOT VALID`.
3. Validar constraint.
4. En una fase posterior, alinear tipos si se busca maxima prolijidad fisica.

## Validacion 2: `incidencias_imagenes_odt.odt -> registro.odt`

Consulta validada:

- Imagenes cuyo `odt` no existe en `registro.odt`.

Resultado:

- Registros en `incidencias_imagenes_odt`: 66.
- Huerfanos por `odt`: 47.
- Valores NULL en `incidencias_imagenes_odt.odt`: 0.

Ejemplos de registros problematicos:

| id | odt | sucursal | created_by | created_at |
|---:|---|---|---|---|
| 1 | `M70` | `Imq Consistorial Nuevo` | `auto_mantencion_programada` | `2026-05-04 10:12:01.932207` |
| 6 | `M8` | `Imq Derecho - Carozzi 3` | `auto_mantencion_programada` | `2026-05-04 10:12:08.129989` |
| 7 | `M9` | `Imq Oficina Niñez` | `auto_mantencion_programada` | `2026-05-04 10:12:09.659840` |
| 8 | `M10` | `Imq Estadio V. Olimpica` | `Fernando Andrés Lubiano Moraga` | `2026-05-04 13:27:11.331607` |
| 9 | `M11` | `Imq Piscina V. Olimpica` | `Fernando Andrés Lubiano Moraga` | `2026-05-04 13:27:40.662503` |
| 10 | `M12` | `Imq Pisc. Bto. Sur` | `Fernando Andrés Lubiano Moraga` | `2026-05-04 13:27:51.570437` |
| 11 | `M13` | `Imq Unco` | `Fernando Andrés Lubiano Moraga` | `2026-05-04 13:28:09.236448` |
| 12 | `M14` | `Imq Oficina persona mayores` | `Fernando Andrés Lubiano Moraga` | `2026-05-04 13:29:28.114464` |
| 13 | `M16` | `Imq Operaciones` | `Fernando Andrés Lubiano Moraga` | `2026-05-04 13:29:58.067643` |
| 14 | `M17` | `Imq Pisc. Bto. Norte` | `Fernando Andrés Lubiano Moraga` | `2026-05-04 13:30:22.538305` |

Dictamen:

No es seguro agregar esta FK ahora. PostgreSQL rechazaria la validacion porque existen 47 imagenes con ODT que no existe en `registro`.

Riesgo funcional:

Los ejemplos muestran ODT de mantencion (`M...`). Es posible que `incidencias_imagenes_odt` este mezclando evidencias de incidencias y mantenciones programadas. Si eso es correcto para negocio, no corresponde forzar toda la tabla contra `registro`.

Datos a limpiar o decision requerida:

- Confirmar si las ODT `M...` deben existir tambien en `registro`.
- Si deben existir, crear los registros padre faltantes antes de agregar FK.
- Si son mantenciones y no incidencias, separar evidencias de mantencion en otra tabla o no aplicar esta FK global.

## Validacion 3: `registros_correos_cliente.odt -> registro.odt`

Consulta validada:

- Correos cuyo `odt` no existe en `registro.odt`.

Resultado:

- Registros en `registros_correos_cliente`: 24.
- Huerfanos por `odt`: 23.
- Valores NULL en `registros_correos_cliente.odt`: 0.

Ejemplos de registros problematicos:

| id | odt | sucursal | fecha_envio | estado |
|---:|---|---|---|---|
| 1 | `I2` | `P Lub` | `2026-04-15 15:51:38.563608` | `En Proceso` |
| 2 | `I2` | `P Lub` | `2026-04-15 15:53:44.190489` | `En Proceso` |
| 3 | `I2` | `P Lub` | `2026-04-15 15:59:16.723018` | `En Proceso` |
| 4 | `I2` | `P Lub` | `2026-04-15 16:10:10.073286` | `En Proceso` |
| 5 | `I2` | `P Lub` | `2026-04-15 16:16:48.594450` | `En Proceso` |
| 6 | `I2` | `P Lub` | `2026-04-15 16:18:10.747777` | `En Proceso` |
| 7 | `I2` | `P Lub` | `2026-04-15 16:23:02.706440` | `En Proceso` |
| 8 | `I2` | `P Lub` | `2026-04-15 16:27:58.130635` | `En Proceso` |
| 9 | `I2` | `P Lub` | `2026-04-15 16:32:05.209311` | `En Proceso` |
| 10 | `I2` | `P Lub` | `2026-04-15 16:33:01.106645` | `En Proceso` |

Dictamen:

No es seguro agregar esta FK ahora. PostgreSQL rechazaria la validacion porque existen 23 correos con ODT que no existe en `registro`.

Datos a limpiar:

- Revisar por que `I2` no existe en `registro.odt`.
- Confirmar si esos correos son historicos, pruebas, datos migrados o registros que perdieron su padre.
- Crear el registro padre si corresponde, o reasignar/eliminar historicos solo con aprobacion explicita.

## Resumen de seguridad para aplicar FKs

| FK propuesta | Estado | Motivo |
|---|---|---|
| `incidencias_cierres.incidencia_id -> registro.id` | Segura con precaucion | No hay huerfanos; la tabla hija esta vacia. Falta indice y los tipos no son identicos. |
| `incidencias_imagenes_odt.odt -> registro.odt` | No segura | Hay 47 huerfanos de 66 registros. |
| `registros_correos_cliente.odt -> registro.odt` | No segura | Hay 23 huerfanos de 24 registros. |

## Riesgos detectados

1. `incidencias_imagenes_odt` parece contener evidencias de mantenciones (`M...`) ademas de incidencias. Una FK directa a `registro.odt` podria modelar mal el negocio.
2. `registros_correos_cliente` tiene casi todos sus registros sin padre en `registro`.
3. Hay diferencia de tipo entre `incidencias_cierres.incidencia_id` (`bigint`) y `registro.id` (`integer`).
4. Hay diferencia de largo entre `incidencias_imagenes_odt.odt` (`varchar(80)`) y `registro.odt` (`varchar(30)`).
5. Falta indice sobre `incidencias_cierres.incidencia_id`, que seria necesario para una FK eficiente.

## Recomendacion final

No aplicar las tres foreign keys juntas.

Aplicable primero:

- `incidencias_cierres.incidencia_id -> registro.id`, despues de crear indice sobre `incidencias_cierres.incidencia_id` y preferiblemente mediante constraint `NOT VALID` + `VALIDATE CONSTRAINT`.

No aplicar todavia:

- `incidencias_imagenes_odt.odt -> registro.odt`.
- `registros_correos_cliente.odt -> registro.odt`.

Antes de aplicar esas dos FKs por `odt`, se deben limpiar o justificar los huerfanos. Si las ODT de mantencion o historicas son validas fuera de `registro`, entonces el MER debe modelarlas con una entidad padre distinta o mantener esas relaciones como logicas, no como FKs globales hacia `registro`.
