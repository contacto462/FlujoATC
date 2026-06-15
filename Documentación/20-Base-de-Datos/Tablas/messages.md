---
tipo: tabla
modulo: helpdesk
db: postgres
tags:
  - db/tabla
  - modulo/helpdesk
actualizado: 2026-06-14
---

# Tabla `messages`

> [!abstract] Responsabilidad
> Mensajes de un [[tickets|ticket]]: respuestas de agente, del solicitante y notas internas, por canal.

## Columnas
| Columna | Tipo | Nulo | Notas |
|---|---|---|---|
| `id` | int PK | no |  |
| `ticket_id` | int FK | no | → `[[tickets]]` |
| `sender_type` | varchar | no | agente / solicitante / sistema |
| `sender_id` | int FK | sí | → `[[users]]` |
| `sender_name` | varchar | sí |  |
| `sender_email` | varchar | sí |  |
| `channel` | varchar | no | email / web / whatsapp |
| `content` | text | — |  |
| `external_id` | varchar | sí | id del mensaje en el canal externo |
| `is_internal_note` | bool | no |  |
| `created_at` | datetime | no |  |

Fuente: `ATC/app/models/message.py`.

## Relaciones
- **FK →** `[[tickets]]` (`ticket_id`), `[[users]]` (`sender_id`)

## Módulos que la usan
- [[Helpdesk]] · alimentado por [[Email IMAP-SMTP]]
