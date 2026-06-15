---
tipo: arquitectura
area: plataforma
estado: activo
tags:
  - arquitectura
  - modulo/helpdesk
  - modulo/incidencias
  - modulo/venta
actualizado: 2026-06-14
---

# App Unificada ATC

> [!abstract] Resumen
> `ATC.app.main` es la **única** instancia FastAPI. Helpdesk, Incidencias y Venta se registran como módulos dentro de esa app y comparten una sola BBDD PostgreSQL (`ATC`). Ver [[Visión General]].

## Entry point

La aplicación modular se levanta desde la raíz del repo. En este equipo (macOS) usa el runbook [[Levantar Servidor]]:

```bash
uvicorn ATC.app.main:app --host 127.0.0.1 --port 8000
```

URL local: `http://127.0.0.1:8000/`

> [!warning] No usar venvs de Windows
> El comando original de la doc apuntaba a `ATC\.venv\Scripts\python.exe` (Windows). En macOS eso no sirve — ver [[Levantar Servidor]].

## Qué hace

Helpdesk, Incidencias y Venta se registran como módulos dentro de una sola app:

- `/`, `/venta/*`, `/servicio/*` y APIs operativas → [[Incidencias]] / [[Venta]].
- `/panel`, `/dashboard`, `/soporte`, `/tabla-soporte` y tickets → [[Helpdesk]].
- `/static/*` y `/uploads/*` se resuelven desde una capa común con fallback entre carpetas.
- `/login` redirige al login único `/?form=login&next=auto` — ver [[Login Único y SSO]].

`unified_main.py` queda como alias de compatibilidad y expone el mismo `app`.

## Importante: imports namespaced

Los paquetes ya no dependen del nombre genérico `app`. Los imports son:

- `ATC.app...`
- `ATC.incidencias.app...`

Esto permite que todo conviva como un solo proyecto Python modular.

---
> Migrado de `docs/app_unificada.md`.
