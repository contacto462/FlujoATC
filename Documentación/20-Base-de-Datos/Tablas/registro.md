---
tipo: tabla
modulo: incidencias
db: postgres
tags:
  - db/tabla
  - modulo/incidencias
actualizado: 2026-06-14
---

# Tabla `registro`

> [!abstract] Responsabilidad
> Registro operativo (antes hoja `Registro` en Google Sheets). Núcleo del flujo de [[Incidencias]] por ODT.

## Columnas (según MER)
| Columna | Tipo | Notas |
|---|---|---|
| `id` | int PK |  |
| `odt` | varchar UK |  |
| `cliente` | varchar |  |
| `tecnicos` | varchar | funcional → `[[users]]` |
| `estado` | varchar |  |

> [!todo] Pendiente
> Completar columnas reales contra `ATC/incidencias/app/models.py` / `sql/schema.sql`.

## Relaciones
- Funcional (sin FK): `tecnicos` → `[[users]]`
