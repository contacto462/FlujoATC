---
tipo: arquitectura
area: plataforma
estado: activo
tags:
  - arquitectura
  - modulo/helpdesk
  - modulo/incidencias
  - modulo/venta
actualizado: 2026-06-15
---

# App Unificada ATC

> [!abstract] Resumen
> `ATC.app.main` es la **única** instancia FastAPI. Helpdesk, Incidencias y Venta viven en `ATC/app/` y comparten una sola BBDD PostgreSQL (`ATC`). Ya no existe carpeta `ATC/incidencias/`. Ver [[Visión General]].

## Entry point

La aplicación se levanta desde la raíz del repo. En este equipo (macOS) usa el runbook [[Levantar Servidor]]:

```bash
.venv314/bin/uvicorn ATC.app.main:app --host 127.0.0.1 --port 8000
```

URL local: `http://127.0.0.1:8000/`

> [!warning] No usar venvs de Windows
> El venv de producción es `.venv314` (Python 3.14.5, macOS). Ver [[Levantar Servidor]].

## Estructura del código

Todo el código vive en `ATC/app/`:

| Subcarpeta | Contenido |
|---|---|
| `routes/` | `web.py` (Helpdesk), `incidencias.py`, `venta.py`, `tickets.py`, … |
| `models/` | `user.py`, `incidencias.py`, `venta.py`, … |
| `services/` | `incidencias_service.py`, `venta_service.py`, … |
| `core/` | `config.py` (Settings único) · `db.py` (engine/Base/get_db único) |
| `templates/` | Todos los templates unificados (Helpdesk + Incidencias + Venta) |
| `static/` | Archivos estáticos |
| `workers/` | Workers Celery |
| `integrations/` | Correo, WhatsApp, etc. |

## Qué hace

- `/`, `/venta/*`, `/servicio/*` y APIs operativas → rutas de Incidencias / Venta.
- `/panel`, `/ticketera`, `/soporte`, `/tabla-soporte` y tickets → rutas de Helpdesk.
- `/static/*` → `ATC/app/static/` (con fallback a `ATC/static/`).
- `/uploads/*` → `ATC/uploads/`.
- `/login` redirige al login único `/?form=login&next=auto` — ver [[Login Único y SSO]].

## Imports

Todos los imports son `ATC.app.*`. No existe ya `ATC.incidencias.*`.

Los shims `core/incidencias_db.py` e `core/incidencias_config.py` re-exportan desde `core/db.py` y `core/config.py` para evitar editar código legacy puntual.

---
> Migrado de `docs/app_unificada.md`. Refactorizado en Fase 1-5 (2026-06-14/15).
