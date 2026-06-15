---
tipo: runbook
modulo: plataforma
criticidad: alta
tags:
  - op/runbook
actualizado: 2026-06-14
---

# Runbook · Levantar Servidor (macOS)

> [!abstract] Objetivo
> Arrancar la [[App Unificada]] localmente en este Mac.

> [!warning] Los venvs de Windows NO sirven
> El repo trae `.venv` de Windows (`Scripts\python.exe`). En macOS no funcionan. Usar **`.venv314`** (creado con `uv` / Python 3.14).

## Pasos
1. Desde la raíz del repo, levantar con uvicorn apuntando a la app unificada:
   ```bash
   uvicorn ATC.app.main:app --host 127.0.0.1 --port 8000
   ```
2. Abrir `http://127.0.0.1:8000/`.

> [!warning] Sin `--reload`
> No usar `--reload` en este equipo.

## Verificación
- La home `http://127.0.0.1:8000/` carga y `/login` redirige a `/?form=login&next=auto` (ver [[Login Único y SSO]]).

## Notas
- La app es una sola instancia FastAPI: `ATC.app.main:app` (`unified_main.py` es alias).
- Imports namespaced: `ATC.app...` y `ATC.incidencias.app...`.
