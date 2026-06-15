---
tipo: modulo
area: soporte
estado: activo
tags:
  - modulo
  - modulo/helpdesk
actualizado: 2026-06-14
---

# Módulo · Helpdesk

> [!abstract] Responsabilidad
> Gestión de tickets de atención: solicitantes, mensajes, SLA y feedback, correo entrante/saliente y paneles. Código en `ATC/app`.

## Rutas principales
- `/panel`, `/dashboard`, `/soporte`, `/tabla-soporte`
- APIs de tickets, mensajes y solicitantes

## Servicios / lógica
- `ATC/app/services/ticket_service.py`, `ticket_status_service.py`
- `ATC/app/services/sla_feedback_service.py`
- `ATC/app/services/analytics_service.py`, `email_service.py`, `drive_report_service.py`

## Integraciones
- Correo: `ATC/app/integrations/email_imap.py`, `email_smtp.py` → ver [[Email IMAP-SMTP]]
- WhatsApp: `ATC/app/integrations/whatsapp_cloud.py`
- Workers Celery: `ATC/app/workers/` → ver [[Celery y Workers]]

## Tablas que usa
- `[[users]]`, `[[requesters]]`, `[[tickets]]`, `[[messages]]`
- Historiales, estados de lectura y SLA (`ticket_history`, `ticket_sla_feedback`, etc.)

## Relacionado
- [[App Unificada]] · [[Login Único y SSO]]
