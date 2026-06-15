---
tipo: tabla
modulo: plataforma
db: postgres
tags:
  - db/tabla
actualizado: 2026-06-14
---

# Tabla `areas`

> [!abstract] Responsabilidad
> Áreas funcionales (soporte, servicio técnico, incidencias, venta, finanzas, etc.). Definen a qué panel entra cada usuario vía `[[user_areas]]`.

## Columnas
| Columna | Tipo | Notas |
|---|---|---|
| `id` | int PK |  |
| `code` | varchar UK | clave de fusión en la [[Unificación BBDD]] |
| `name` | varchar |  |
| `department` | varchar |  |

> [!todo] Pendiente
> Confirmar columnas reales contra el modelo en `ATC/`.

## Relaciones
- **← referenciada por** `[[user_areas]]` (`area_id`)
