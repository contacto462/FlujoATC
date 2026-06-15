---
tipo: tabla
modulo: helpdesk
db: postgres
tags:
  - db/tabla
  - modulo/helpdesk
actualizado: 2026-06-14
---

# Tabla `tickets`

> [!abstract] Responsabilidad
> Tickets de atención del [[Helpdesk]]: estado, prioridad, origen, asignación y métricas de SLA.

## Columnas
| Columna | Tipo | Nulo | Notas |
|---|---|---|---|
| `id` | int PK | no |  |
| `subject` | varchar | no |  |
| `status` | varchar | no |  |
| `priority` | varchar | no |  |
| `source` | varchar | no | canal de origen |
| `is_deleted` | bool | no |  |
| `is_spam` | bool | no |  |
| `requester_id` | int FK | no | → `[[requesters]]` |
| `assigned_to_id` | int FK | sí | → `[[users]]` |
| `created_at` | datetime | no |  |
| `updated_at` | datetime | no |  |
| `first_agent_reply_at` | datetime | sí | métrica SLA |
| `resolved_at` | datetime | sí |  |
| `reopen_count` | int | no |  |

Fuente: `ATC/app/models/ticket.py`.

## Relaciones
- **FK →** `[[requesters]]` (`requester_id`), `[[users]]` (`assigned_to_id`)
- **← referenciada por** `[[messages]]` (`ticket_id`), historiales y estados de lectura/SLA

## Módulos que la usan
- [[Helpdesk]]
