---
tipo: tabla
modulo: plataforma
db: postgres
tags:
  - db/tabla
actualizado: 2026-06-14
---

# Tabla `user_areas`

> [!abstract] Responsabilidad
> Relación usuario ↔ área. El flag `is_primary` define el área principal y, con ella, la redirección de [[Login Único y SSO]].

## Columnas
| Columna | Tipo | Notas |
|---|---|---|
| `id` | int PK |  |
| `user_id` | int FK | → `[[users]]` |
| `area_id` | int FK | → `[[areas]]` |
| `is_primary` | bool | área principal del usuario |

## Relaciones
- **FK →** `[[users]]`, `[[areas]]`
