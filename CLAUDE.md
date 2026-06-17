# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

- Rutas: `ATC/app/routes/` — `web.py` (Helpdesk, ~6.6k líneas), `incidencias.py` (~2.4k líneas),
  `venta.py`, `tickets.py`, `bitacora.py`, `inicio_turno.py`, `messages.py`, `requesters.py`,
  `whatsapp_webhook.py`, `public.py`. También hay routers en `ATC/app/modules/`
  (`client_notes.py`, `unified_access.py`).
- Modelos: `ATC/app/models/` — `user.py`, `ticket.py`, `requester.py`, `incidencias.py`,
  `venta.py`, etc. **Todos los modelos se importan en `ATC/app/main.py`**; un modelo nuevo que no
  se importe ahí no participa de `Base.metadata.create_all`.
- Servicios: `ATC/app/services/` — `incidencias_service.py` (~7.3k líneas, el más grande del
  repo), `venta_service.py` (~1.9k), `protocolos_service.py`, `email_service.py`,
  `automation_service.py`, etc. Por el tamaño de estos archivos, usa grep/búsqueda dirigida en vez
  de leerlos completos.
- Config única: `ATC/app/core/config.py` (Pydantic `Settings`, lee `ATC/.env`) · DB única:
  `ATC/app/core/db.py`.
- Templates: `ATC/app/templates/` (unificado, HTML+CSS+JS inline por página, sin framework
  frontend) · Static: `ATC/app/static/` (y `ATC/static/`, servido también vía `/shared-static`).
- Workers: `ATC/app/workers/` (Celery, ver abajo) · Integraciones (correo/WhatsApp):
  `ATC/app/integrations/`.
- Scripts puntuales (no forman parte del flujo normal de la app): `ATC/scripts/` (seeds, import
  CSV, reset de sync IMAP) y `tools/` en la raíz (auditoría/unificación de BBDD, ya ejecutados).

## Arquitectura — lo que no es obvio leyendo un solo archivo

- **Una sola BBDD, dos nombres de sesión por compat histórica**: `ATC/app/core/db.py` define
  `get_db()` y luego `get_incidencias_db = get_db` (alias literal, mismo engine). Como FastAPI
  cachea las dependencias por identidad de función dentro de un mismo request, dos parámetros
  `Depends(get_db)` y `Depends(get_incidencias_db)` en la misma ruta **son la misma `Session`**:
  si una query sobre uno de los dos falla y no se hace `rollback()`, la siguiente query sobre el
  otro también falla con `InFailedSqlTransaction`. Si agregas un `try/except` alrededor de una
  query, haz `rollback()` en el `except` si la ruta sigue usando la sesión después.
- **Arranque sin Celery por defecto**: `main.py` (`startup_tasks`, evento `on_event("startup")`)
  levanta dos `threading.Thread` daemon (`email_loop`, `automation_loop`) que pollean IMAP cada 5s
  y ejecutan auto-cierre cada `AUTOMATION_POLL_SECONDS` (mín. 60s). Celery (`ATC/app/workers/`,
  broker/backend en Redis `localhost:6379`) es una vía **alternativa/asíncrona** para
  importación de correo y notificaciones — no es necesario para que el server funcione en local.
- **Login único y redirección por área**: cada usuario tiene un área principal en `user_areas`
  (`is_primary`) que decide a qué panel entra (`soporte` → Helpdesk `/panel`, `venta` →
  `/venta/panel-selector`, etc. — tabla completa en `Login Único y SSO.md`). El bridge entre
  módulos es `/sso/login?token=...`.
- **Routers se registran todos en `main.py`** vía `app.include_router(...)`; para ubicar dónde
  vive una ruta HTTP, busca el prefijo en ese archivo antes de adivinar por nombre de módulo.
- **Sin manifiesto de dependencias**: no hay `requirements.txt`/`pyproject.toml`/`uv.lock` en el
  repo. Los venvs (`.venv-backend`, `.venv314`) se instalaron a mano con `uv pip install <paquete>`.
  Si agregas una dependencia nueva, instálala directo en el venv que estés usando.
- **No hay tests ni linter configurados** en este repo (sin `pytest`, `ruff`, etc.). Verifica
  cambios levantando el server y probando la ruta/endpoint a mano (o con `TestClient` ad-hoc).

## Levantar el server (macOS)

Ver `Documentación/30-Operaciones/Levantar Servidor.md`. Resumen: **no** usar los venvs de Windows
(`.venv`, trae `Scripts\python.exe`); usar `.venv-backend` o `.venv314` (ambos Python **3.14.5** vía
`uv`) y `uvicorn ATC.app.main:app --host 127.0.0.1 --port 8000` (sin `--reload`).

## No tocar

- `Documentación/` es el vault de Obsidian del usuario (notas, no código).
- `ATC/`, venvs, `uploads/`, `docs/` (doc original, ya migrada) — no son parte del vault.
- `ATC/.git` es un repo git anidado y obsoleto (sin remoto, commits viejos de antes de la
  unificación). El repo real es el de la raíz (`origin` → `contacto462/FlujoATC`). No ejecutes
  comandos git dentro de `ATC/` pensando que es el mismo repo.
