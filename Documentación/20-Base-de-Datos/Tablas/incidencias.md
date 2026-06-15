---
tipo: tabla
modulo: incidencias
db: postgres
tags:
  - db/tabla
  - modulo/incidencias
actualizado: 2026-06-14
---

# Tabla `incidencias`

> [!abstract] Responsabilidad
> Incidencias operativas / servicio técnico (ODT, sucursal, técnico, estado, avance). Cargada desde archivos de origen (`source_file` / `source_row`).

## Columnas (selección)
| Columna | Tipo | Notas |
|---|---|---|
| `id` | int PK |  |
| `odt` | varchar(50) | indexada |
| `fecha` | varchar(60) |  |
| `puesto` | varchar(40) |  |
| `sucursal` | varchar(255) | indexada |
| `problema` | varchar(255) | indexada |
| `derivacion` / `observacion` | text |  |
| `tecnico` | varchar(140) | indexada |
| `estado` | varchar(80) | indexada |
| `cantidad_dias_ejecucion` | varchar(40) |  |
| `fecha_cierre` / `fecha_derivacion_area` / `fecha_derivacion_tecnico` | varchar(60) |  |
| `direccion` / `observacion_final` / `prioridad` / `materiales` | text |  |
| `acompanante` | varchar(140) |  |
| `estado_avance` / `estado_agrupado` / `categoria` | varchar |  |
| `observaciones_avance` | text |  |
| `source_file` | varchar(255) | origen de la carga |
| `source_row` | int | fila de origen |
| `created_at` / `updated_at` | datetime |  |

Fuente: `ATC/app/models/incidencia.py`. Imágenes en `incidencias_imagenes_odt` (ver [[Unificación BBDD]]).

## Relaciones
- Funcional (sin FK): `tecnico` → `[[users]]`

## Módulos que la usan
- [[Incidencias]]
