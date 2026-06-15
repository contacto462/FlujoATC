---
tipo: tabla
modulo: plataforma
db: postgres
tags:
  - db/tabla
actualizado: 2026-06-14
---

# Tabla `users`

> [!abstract] Responsabilidad
> Usuarios del sistema (agentes, técnicos, administración). Entidad raíz del [[MER ATC]]. Es tabla **superset** tras la [[Unificación BBDD]] (fusionada por `username`).

## Columnas
| Columna | Tipo | Nulo | Notas |
|---|---|---|---|
| `id` | int PK | no |  |
| `name` | varchar(100) | no |  |
| `username` | varchar UK | no | clave de fusión en la unificación |
| `hashed_password` | varchar | no |  |
| `role` | varchar | no |  |
| `is_active` | bool | no |  |
| `department` | varchar | sí | agregado desde incidencias |
| `created_at` | datetime | no | server default |
| `updated_at` | datetime | no | on update |

Fuente: `ATC/app/models/user.py`.

## Relaciones
- **← referenciada por** `[[tickets]]` (`assigned_to_id`), `[[messages]]` (`sender_id`), `[[user_areas]]` (`user_id`)
- Relaciones funcionales (sin FK): `venta_ods.creado_por`, `bbdd_sucursales.created_by`, `registro.tecnicos`

## Módulos que la usan
- [[Helpdesk]], [[Incidencias]], [[Venta]] (compartida)
