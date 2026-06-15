---
tipo: runbook
modulo: helpdesk
criticidad: media
tags:
  - op/runbook
  - modulo/helpdesk
actualizado: 2026-06-14
---

# Runbook · Celery y Workers

> [!abstract] Objetivo
> Procesamiento asíncrono del [[Helpdesk]]: importación de correo y notificaciones. Código en `ATC/app/workers/`.

## Componentes
- `celery_app.py` — app Celery `"helpdesk"`.
  - **Broker**: `redis://localhost:6379/0`
  - **Backend**: `redis://localhost:6379/1`
- `tasks_email.py` — importación de correo (`run_email_import`) → ver [[Email IMAP-SMTP]].
- `tasks_notifications.py` — notificaciones.
- `email_worker.py` — worker de correo.

> [!info] Beat schedule
> `import-emails-every-minute`: ejecuta `app.workers.tasks_email.import_emails_task` cada **60 s**.

## Requisitos previos
- Redis corriendo en `localhost:6379`.

## Pasos
1. Levantar Redis.
2. Worker:
   ```bash
   celery -A ATC.app.workers.celery_app worker --loglevel=info
   ```
3. Scheduler (beat) para el import cada minuto:
   ```bash
   celery -A ATC.app.workers.celery_app beat --loglevel=info
   ```

> [!warning] Nombre de tarea legacy
> El beat referencia `app.workers.tasks_email...` (prefijo `app.`, no `ATC.app.`). Si las tareas no se descubren tras el namespacing, revisar este nombre.

## Verificación
- Los logs del worker muestran la importación de correos cada minuto.
