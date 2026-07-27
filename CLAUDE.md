# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# PROYECTO-ATC

Monorepo **FastAPI** de Alguien Te Cuida. Una sola app (`ATC.app.main:app`) con tres módulos —
Helpdesk, Incidencias y Venta—, con login único / SSO. En producción (Windows) la app se levanta
vía `server_watchdog.py` → `run_server.py` → `unified_main:app`, en `0.0.0.0:8000`, bajo la tarea
programada "ATC Server Watchdog" (ONSTART). **`run_server.py` es obligatorio**: uvicorn 0.49
hardcodea el ProactorEventLoop en Windows y con ese loop un error transitorio de `accept()` cierra
el socket de escucha para siempre ("Accept failed on a socket" / WinError 64 → caídas en loop,
jul 2026); el script crea un `SelectorEventLoop` propio y corre `server.serve()` encima. No volver
a lanzar con `python -m uvicorn` directo.

**BBDD activa: SQL Server**, no PostgreSQL. `ATC/.env` → `DATABASE_URL=mssql+pyodbc://...`
apuntando al Windows Server (`10.20.30.8,14330`, base `PROYECTO_ATC`). Este repo vive montado por
SMB en `/Volumes/PROYECTO-ATC-SERVIDOR` desde
`C:\Users\Administrador\Downloads\proyectos\PROYECTO-ATC-SERVIDOR` en ese mismo Windows Server, y
la app corre en `http://10.20.30.8:8000`. Existe también `INCIDENCIAS_DATABASE_URL` (postgresql)
en `.env`, pero **no se usa**: `ATC/app/core/db.py` arma el engine único desde `settings.DATABASE_URL`
exclusivamente — es un resabio de un intento de migración a PostgreSQL que no quedó activo. No
trabajar contra PostgreSQL salvo que se pida explícitamente.

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

- Rutas: `ATC/app/routes/` — `web.py` (Helpdesk, ~7.5k líneas), `incidencias.py` (~3.5k líneas),
  `venta.py`, `tickets.py`, `bitacora.py`, `inicio_turno.py`, `messages.py`, `requesters.py`,
  `whatsapp_webhook.py`, `public.py`. También hay routers en `ATC/app/modules/`
  (`client_notes.py`, `unified_access.py`).
- Modelos: `ATC/app/models/` — `user.py`, `ticket.py`, `requester.py`, `incidencias.py`,
  `venta.py`, etc. **Todos los modelos se importan en `ATC/app/main.py`**; un modelo nuevo que no
  se importe ahí no participa de `Base.metadata.create_all`.
- Servicios: `ATC/app/services/` — `incidencias_service.py` (~8.8k líneas, el más grande del
  repo), `venta_service.py` (~2.4k), `protocolos_service.py`, `email_service.py`,
  `automation_service.py`, etc. Por el tamaño de estos archivos, usa grep/búsqueda dirigida en vez
  de leerlos completos.
- Config única: `ATC/app/core/config.py` (Pydantic `Settings`, lee `ATC/.env`) · DB única:
  `ATC/app/core/db.py` (engine SQL Server vía `mssql+pyodbc`, ver nota arriba).
- Templates: `ATC/app/templates/` (unificado, HTML+CSS+JS inline por página, sin framework
  frontend) · Static: `ATC/app/static/` (y `ATC/static/`, servido también vía `/shared-static`).
- Workers: `ATC/app/workers/` (Celery, ver abajo) · Integraciones (correo/WhatsApp):
  `ATC/app/integrations/`.
- Scripts puntuales (no forman parte del flujo normal de la app): `ATC/scripts/` (seeds, import
  CSV, reset de sync IMAP) y `tools/` en la raíz (auditoría/unificación de BBDD, ya ejecutados).

## Arquitectura — lo que no es obvio leyendo un solo archivo

- **Una sola BBDD, dos nombres de sesión por compat histórica**: `ATC/app/core/db.py` define
  `get_db()` y luego `get_incidencias_db = get_db` (alias literal, mismo engine SQL Server). Como
  FastAPI cachea las dependencias por identidad de función dentro de un mismo request, dos
  parámetros `Depends(get_db)` y `Depends(get_incidencias_db)` en la misma ruta **son la misma
  `Session`**: si una query sobre uno de los dos falla y no se hace `rollback()`, la conexión queda
  con la transacción abierta en mal estado y las queries siguientes sobre esa misma sesión pueden
  fallar o devolver datos inconsistentes. Si agregas un `try/except` alrededor de una query, haz
  `rollback()` en el `except` si la ruta sigue usando la sesión después.
- **Arranque sin Celery por defecto**: `main.py` (`startup_tasks`, evento `on_event("startup")`)
  levanta dos `threading.Thread` daemon (`email_loop`, `automation_loop`) que pollean IMAP cada
  `EMAIL_POLL_SECONDS` (mín. 60s; sube a 300s si el buzón rechaza por exceso de conexiones)
  y ejecutan auto-cierre cada `AUTOMATION_POLL_SECONDS` (mín. 60s). Celery (`ATC/app/workers/`,
  broker/backend en Redis `localhost:6379`) es una vía **alternativa/asíncrona** para
  importación de correo y notificaciones — no es necesario para que el server funcione en local.
- **Login único y redirección por área**: cada usuario tiene un área principal en `user_areas`
  (`is_primary`) que decide a qué panel entra (`soporte` → Helpdesk `/panel`, `venta` →
  `/venta/panel-selector`, etc. — tabla completa en `Login Único y SSO.md`). El bridge entre
  módulos es `/sso/login?token=...`.
- **Routers se registran todos en `main.py`** vía `app.include_router(...)`; para ubicar dónde
  vive una ruta HTTP, busca el prefijo en ese archivo antes de adivinar por nombre de módulo.
  **El orden de registro importa**: si dos routers declaran el mismo path, gana el que se registró
  primero y el otro queda muerto sin aviso (ya pasó y se limpió en jul 2026 — no declares una ruta
  que ya exista en otro router; verifica con grep antes de agregar paths "nuevos").
- **`/api/client-notes` es un despachador**: la ruta vive solo en `modules/client_notes.py`, que
  según haya token de Incidencias o cookie de Helpdesk llama a funciones *sin decorador* en
  `routes/incidencias.py` (`get_client_internal_notes`/`add_client_internal_note`) o en
  `routes/web.py` (`api_get_client_internal_notes`/`api_add_client_internal_note`). Esas funciones
  parecen handlers huérfanos pero **no son código muerto** — no las borres ni les agregues
  `@router` de nuevo.
- **Dependencias**: hay `requirements.txt` en la raíz (pip freeze completo; FastAPI 0.136,
  Celery, anthropic, google-api, etc.). Los venvs (`.venv-backend`, `.venv314`) que existían en el
  volumen ya no están visibles por SMB — en la práctica el Python que corre la app vive en el
  Windows Server. Si agregas una dependencia, añádela también a `requirements.txt`.
- **Mojibake histórico**: varios archivos tienen comentarios/strings con doble codificación UTF-8
  (`Ã³`, `ÃƒÆ'...`). Los casos que afectaban lógica (parsers de "sí", marcadores del webhook SLA,
  regex de correos) se corrigieron en jul 2026, pero quedan comentarios corruptos y **datos en la
  BBDD pueden venir con mojibake** (por eso existe `_repair_text_encoding` en `venta_service.py` y
  comparaciones con variantes corruptas a propósito — no las "limpies" sin revisar los datos).
- **No hay tests ni linter configurados** en este repo (sin `pytest`, `ruff`, etc.). Verifica
  cambios levantando el server y probando la ruta/endpoint a mano (o con `TestClient` ad-hoc).

## Levantar el server

Ver `Documentación/30-Operaciones/Levantar Servidor.md`. En **producción (Windows Server)** corre
la tarea programada "ATC Server Watchdog" → `server_watchdog.py` → `run_server.py` (SelectorEventLoop,
ver arriba) en `0.0.0.0:8000` (logs en `logs/`). Para reiniciar remotamente hay WinRM (NTLM, puerto
5985) vía `pywinrm`; para reiniciar solo el server basta matar el proceso `run_server.py` — el
watchdog lo relanza en ~5s. El watchdog limpia el puerto 8000 antes de arrancar (en Windows dos
procesos pueden quedar escuchando el mismo puerto y el server queda errático — pasó en jul 2026
con 5 uvicorn zombis).

En **macOS** ya no hay venv utilizable en el volumen (el Python del sistema es 3.9 y el código usa
sintaxis 3.10+, así que ni siquiera importa). Para verificar cambios desde el Mac: chequeo de
sintaxis con `python3 -m compileall ATC/app` y probar contra el server real en
`http://10.20.30.8:8000` tras reiniciar. Si necesitas correr localmente, crea un venv nuevo con
`uv` (Python 3.12+) e instala desde `requirements.txt`.

## No tocar

- `Documentación/` es el vault de Obsidian del usuario (notas, no código).
- `ATC/`, venvs, `uploads/`, `docs/` (doc original, ya migrada) — no son parte del vault.
- `ATC/.git` es un repo git anidado y obsoleto (sin remoto, commits viejos de antes de la
  unificación). El repo real es el de la raíz (`origin` → `contacto462/FlujoATC`). No ejecutes
  comandos git dentro de `ATC/` pensando que es el mismo repo.
