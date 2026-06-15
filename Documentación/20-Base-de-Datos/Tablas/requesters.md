---
tipo: tabla
modulo: helpdesk
db: postgres
tags:
  - db/tabla
  - modulo/helpdesk
actualizado: 2026-06-14
---

# Tabla `requesters`

> [!abstract] Responsabilidad
> Solicitantes (quien abre tickets). Distinto de `[[users]]`: son contactos externos, no agentes.

## Columnas
| Columna | Tipo | Nulo | Notas |
|---|---|---|---|
| `id` | int PK | no |  |
| `email` | varchar | sí |  |
| `phone` | varchar | sí |  |
| `name` | varchar | no |  |
| `internal_name` | varchar | sí |  |
| `notes` | text | sí |  |

Fuente: `ATC/app/models/requester.py`.

## Relaciones
- **← referenciada por** `[[tickets]]` (`requester_id`)

## Módulos que la usan
- [[Helpdesk]]
