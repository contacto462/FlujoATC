# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# PROYECTO-ATC

Monorepo **FastAPI** de Alguien Te Cuida. Una sola app (`ATC.app.main:app`) con varios módulos —
Helpdesk, Incidencias, Venta, Compras y Portal Cliente—, con login único / SSO. Compras y Portal
Cliente son los más recientes (jul 2026) y todavía no tienen ficha en `Documentación/`; antes de
tocarlos lee directamente `ATC/app/routes/compras.py` / `portal_cliente.py`. En producción (Windows)
la app se levanta
vía `server_watchdog.py` → `run_server.py` → `unified_main:app`, en `0.0.0.0:8000`, bajo la tarea
programada "ATC Server Watchdog" (ONSTART). **`run_server.py` es obligatorio**: uvicorn 0.49
hardcodea el ProactorEventLoop en Windows y con ese loop un error transitorio de `accept()` cierra
el socket de escucha para siempre ("Accept failed on a socket" / WinError 64 → caídas en loop,
jul 2026); el script crea un `SelectorEventLoop` propio y corre `server.serve()` encima. No volver
a lanzar con `python -m uvicorn` directo.

**BBDD activa: SQL Server**, no PostgreSQL. `ATC/.env` → `DATABASE_URL=mssql+pyodbc://...` con
`SERVER=SERVER\SQLEXPRESS;DATABASE=PROYECTO_ATC;Trusted_Connection=yes` — conexión local por auth
integrada de Windows (la app corre en el mismo Windows Server que la base), no una conexión TCP con
usuario/contraseña. `10.20.30.8,14330` es la dirección para conectarse a esa misma instancia SQL
Server **desde afuera** (p.ej. una herramienta externa), no lo que usa la app. Este repo vive montado por
SMB en `/Volumes/PROYECTO-ATC-SERVIDOR` desde
`C:\Users\Administrador\Downloads\proyectos\PROYECTO-ATC-SERVIDOR` en ese mismo Windows Server, y
la app corre en `http://10.20.30.8:8000`. Existe también `INCIDENCIAS_DATABASE_URL` (postgresql)
en `.env`, pero **no se usa**: `ATC/app/core/db.py` arma el engine único desde `settings.DATABASE_URL`
exclusivamente — es un resabio de un intento de migración a PostgreSQL que no quedó activo. No
trabajar contra PostgreSQL salvo que se pida explícitamente.

Para **consultas/escrituras normales** (SELECT/INSERT/UPDATE/DELETE) contra `PROYECTO_ATC` desde el
Mac hay un usuario SQL ad-hoc de solo trabajo que apunta directo a `10.20.30.8,14330`
(`TrustServerCertificate=yes`) — sin permisos de `ALTER`/`DBCC`. Para operaciones que sí requieren
esos permisos (p. ej. `DBCC CHECKIDENT` para reindexar un identity), no alcanza ese usuario: hay que
correr el script **en el propio Windows Server vía WinRM**, usando la misma auth integrada que usa
la app (`Trusted_Connection=yes` contra `SERVER\SQLEXPRESS`, igual que `DATABASE_URL` arriba). Las
credenciales de ambos (usuario SQL ad-hoc y Windows) no viven en este archivo por la misma razón que
las de WinRM — ver "Levantar el server".

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

En este montaje SMB tambien existe documentacion resumida en `docs/`. Para cualquier cambio en
`ATC/app/routes/incidencias.py`, lee primero `docs/incidencias_mapa_rapido.md`: contiene el indice
por rangos y las rutas principales para evitar cargar las ~4k lineas completas. Despues usa grep/`rg`
y rangos chicos con `sed`. Si agregas, borras o mueves bloques grandes/rutas en ese archivo,
actualiza tambien ese mapa.

## Código

Todo el código vive en **`ATC/app/`** (ya no hay carpeta `ATC/incidencias/`):

- Rutas: `ATC/app/routes/` — `web.py` (Helpdesk, ~9k líneas), `incidencias.py` (~4.1k líneas),
  `venta.py`, `compras.py` (módulo Compras, prefix `/compras`), `portal_cliente.py` (login de
  clientes), `tickets.py`, `bitacora.py`, `inicio_turno.py`, `messages.py`, `requesters.py`,
  `whatsapp_webhook.py`, `public.py`. `bitacora_access.py` no es un router — es un helper de
  control de acceso compartido que importan `bitacora.py`, `incidencias.py` y `web.py`. También hay
  routers en `ATC/app/modules/` (`client_notes.py`, `unified_access.py`).
  Antes de tocar `incidencias.py`, partir por `docs/incidencias_mapa_rapido.md` y busquedas
  dirigidas; no leer el archivo completo salvo que sea imprescindible.
- Modelos: `ATC/app/models/` — `user.py`, `ticket.py`, `requester.py`, `incidencias.py`,
  `venta.py`, `compras.py`, `portal_cliente.py`, `prevencion.py`, etc. **Todos los modelos se
  importan en `ATC/app/main.py`**; un modelo nuevo que no se importe ahí no participa de
  `Base.metadata.create_all`.
- Servicios: `ATC/app/services/` — `incidencias_service.py` (~10.7k líneas, el más grande del
  repo), `venta_service.py` (~2.7k), `compras_service.py`, `protocolos_service.py`,
  `email_service.py`, `automation_service.py`, `ticket_service.py`, `user_service.py`,
  `analytics_service.py`, y varios más de reportes/Drive (`*_drive_report_service.py`,
  `informe_cliente_service.py`, `sla_feedback_service.py`, `lavados_service.py`,
  `ley_karin_service.py`, `contrato_diario_service.py`) — son ~20 archivos en total. Por el tamaño
  de estos archivos, usa grep/búsqueda dirigida en vez de leerlos completos.
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
  `/venta/panel-selector`, `compras_control` → `/compras/panel-control`, `compras_solicitud` →
  `/compras/solicitud`, etc.). La tabla completa vive en `_redirect_for_user_area` en
  `routes/web.py` (~18 area-codes a jul 2026: soporte, materiales, compras_control,
  compras_solicitud, servicio_tecnico, tecnicos, coordinacion, protocolos, venta, finanzas,
  administracion, operaciones, guardia, supervisores, rrhh, prevencion, bitacora, incidencias) —
  confirma ahí antes de asumir que `Login Único y SSO.md` está completo, porque puede no reflejar
  las áreas de Compras. El bridge entre módulos es `/sso/login?token=...`.
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
ver arriba) en `0.0.0.0:8000` (logs en `logs/`). El watchdog limpia el puerto 8000 antes de arrancar
(en Windows dos procesos pueden quedar escuchando el mismo puerto y el server queda errático — pasó
en jul 2026 con 5 uvicorn zombis).

**Los cambios de código Python (rutas, servicios, modelos) requieren reiniciar el proceso** — a
diferencia de los templates Jinja/HTML/JS, que se recargan solos en cada request. Reinicio remoto vía
WinRM (HTTP, puerto 5985, auth NTLM; **SSH no está disponible**), usuario `Administrador` — las
credenciales NO viven en este archivo por estar `CLAUDE.md` versionado en git (ver `.gitignore`);
pídelas o consúltalas en el almacén de secretos que uses para este proyecto:

```python
import sys
sys.path = [p for p in sys.path if 'PROYECTO-ATC-SERVIDOR' not in p]  # evita timeout por el volumen SMB montado
import winrm
s = winrm.Session('http://10.20.30.8:5985/wsman', auth=('Administrador', '<password>'), transport='ntlm')
```

**Para matar el proceso, no uses `Get-Process run_server`** — corre como `python.exe run_server.py`,
su `ProcessName` real es `python`, así que ese comando nunca encuentra nada y falla en silencio
(causó varios "reinicios" que en realidad eran no-ops en jul 2026). Matar por línea de comando:

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*run_server*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

El watchdog relanza en ~5-15s. Verificar que el restart fue real (no un no-op) comparando
`CreationDate` del proceso antes/después con `Get-CimInstance Win32_Process | Where CommandLine
-like '*run_server*' | Select ProcessId,CreationDate`, y siempre confirmar con
`curl http://10.20.30.8:8000/health` (debe responder `{"status":"ok"}`).

Para probar una página autenticada sin credenciales de usuario, se puede tomar un token de sesión
vigente directo de la base (solo lectura): `SELECT TOP 1 token FROM dbo.login_sessions WHERE
expires_at > GETDATE() ORDER BY expires_at DESC`, y navegar a `?token=EL_TOKEN`.

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
