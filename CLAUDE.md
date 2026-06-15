# PROYECTO-ATC

Monorepo **FastAPI** de Alguien Te Cuida. Una sola app (`ATC.app.main:app`) con tres módulos —
Helpdesk, Incidencias y Venta— sobre una sola BBDD PostgreSQL (`ATC`), con login único / SSO.

## 📚 Documentación primero (ahorra exploración)

Antes de explorar el código a ciegas, **revisa el vault `Documentación/`** (notas Obsidian). Suele
tener ya la respuesta y evita leer muchos archivos. Mapa:

- **Arquitectura** → `Documentación/10-Arquitectura/`
  - `Visión General.md` — los 3 módulos y cómo se unifican
  - `App Unificada.md` — entry point, rutas, imports namespaced
  - `Login Único y SSO.md` — redirección por área, SSO
  - `Módulos/` — fichas de Helpdesk, Incidencias, Venta
- **Base de datos** → `Documentación/20-Base-de-Datos/`
  - `MER ATC.md` — modelo de datos (Mermaid)
  - `Unificación BBDD.md` — historia de la unificación de bases
  - `Tablas/` — ficha por tabla (columnas, FKs)
- **Operaciones / runbooks** → `Documentación/30-Operaciones/`
  - `Levantar Servidor.md`, `Email IMAP-SMTP.md`, `Celery y Workers.md`
- **Decisiones (ADR)** → `Documentación/40-Decisiones/`

> Si documentas algo nuevo o cambias arquitectura, actualiza la nota correspondiente en
> `Documentación/` (usa las plantillas de `Documentación/_templates/`).

## Código

Todo el código vive en **`ATC/app/`** (ya no hay carpeta `ATC/incidencias/`):

- Rutas: `ATC/app/routes/` — `web.py` (Helpdesk), `incidencias.py`, `venta.py`, `tickets.py`, etc.
- Modelos: `ATC/app/models/` — `user.py`, `incidencias.py`, `venta.py`, etc.
- Servicios: `ATC/app/services/` — `incidencias_service.py`, `venta_service.py`, etc.
- Config única: `ATC/app/core/config.py` · DB única: `ATC/app/core/db.py`
- Templates: `ATC/app/templates/` (unificado) · Static: `ATC/app/static/`
- Workers Celery: `ATC/app/workers/` · Integraciones (correo/WhatsApp): `ATC/app/integrations/`

## Levantar el server (macOS)

Ver `Documentación/30-Operaciones/Levantar Servidor.md`. Resumen: **no** usar los venvs de Windows;
usar `.venv-backend` con Python **3.14.5** y `uvicorn ATC.app.main:app --host 127.0.0.1 --port 8000` (sin `--reload`).

## No tocar

- `Documentación/` es el vault de Obsidian del usuario (notas, no código).
- `ATC/`, venvs, `uploads/`, `docs/` (doc original, ya migrada) — no son parte del vault.
