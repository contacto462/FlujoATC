---
tipo: runbook
modulo: helpdesk
criticidad: alta
tags:
  - op/runbook
  - modulo/helpdesk
actualizado: 2026-06-14
---

# Runbook · Email IMAP / SMTP

> [!abstract] Objetivo
> Correo entrante (IMAP → tickets) y saliente (SMTP) del [[Helpdesk]]. Código en `ATC/app/integrations/email_imap.py` y `email_smtp.py`. Disparado por [[Celery y Workers]].

## Flujo
- **Entrante**: `fetch_unseen_emails(limit=100)` lee correos no leídos y crea `[[tickets]]` / `[[messages]]`.
- **Saliente**: `email_smtp.py` envía respuestas.
- **Auto-reply**: controlado por `AUTOMATION_EMAIL_AUTO_REPLY_ENABLED` (default `True`).

## Configuración (`.env`, ver `ATC/app/core/config.py`)
```env
# IMAP — cuenta principal
IMAP_HOST=
IMAP_PORT=993
IMAP_USER=
IMAP_PASSWORD=
IMAP_FOLDER=INBOX

# Segunda cuenta IMAP (opcional)
IMAP2_HOST=
IMAP2_PORT=993
IMAP2_USER=
IMAP2_PASSWORD=
IMAP2_FOLDER=INBOX

# SMTP
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
```

## Verificación
- Enviar un correo a la casilla configurada y confirmar que aparece un ticket nuevo en el panel (importación cada 60 s vía beat).

## Si falla
- Revisar credenciales IMAP/SMTP en `.env`.
- Confirmar que el worker y el beat de [[Celery y Workers]] están corriendo y que Redis está arriba.
