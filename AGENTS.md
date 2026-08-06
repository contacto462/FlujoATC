# AGENTS.md

This file provides guidance to Codex when working with code in this repository.
It mirrors the operational context from `CLAUDE.md`, adapted for Codex workflows.

# PROYECTO-ATC

Monorepo **FastAPI** de Alguien Te Cuida. Una sola app (`ATC.app.main:app`) con varios módulos:
Helpdesk, Incidencias, Venta, Compras y Portal Cliente, con login único / SSO. Compras y Portal
Cliente son los más recientes (jul 2026) y todavía no tienen ficha en `Documentación/`; antes de
tocarlos lee directamente `ATC/app/routes/compras.py` / `portal_cliente.py`. En producción (Windows)
la app se levanta vía `server_watchdog.py` -> `run_server.py` -> `unified_main:app`, en
`0.0.0.0:8000`, bajo la tarea
programada "ATC Server Watchdog" (ONSTART). **`run_server.py` es obligatorio**: uvicorn 0.49
hardcodea el ProactorEventLoop en Windows y con ese loop un error transitorio de `accept()` cierra
el socket de escucha para siempre ("Accept failed on a socket" / WinError 64 -> caídas en loop,
jul 2026); el script crea un `SelectorEventLoop` propio y corre `server.serve()` encima. No volver
a lanzar con `python -m uvicorn` directo.

**BBDD activa: SQL Server**, no PostgreSQL. `ATC/.env` -> `DATABASE_URL=mssql+pyodbc://...` con
`SERVER=SERVER\SQLEXPRESS;DATABASE=PROYECTO_ATC;Trusted_Connection=yes`: conexión local por auth
integrada de Windows porque la app corre en el mismo Windows Server que la base, no una conexión TCP
con usuario/contraseña. `10.20.30.8,14330` es la dirección para conectarse a esa misma instancia SQL
Server desde afuera, por ejemplo una herramienta externa, no lo que usa la app. Este repo vive
montado por SMB en `/Volumes/PROYECTO-ATC-SERVIDOR` desde
`C:\Users\Administrador\Downloads\proyectos\PROYECTO-ATC-SERVIDOR` en ese mismo Windows Server, y
la app corre en `http://10.20.30.8:8000`. También se accede externamente por `200.75.22.35:8000`.
Existe `INCIDENCIAS_DATABASE_URL` (postgresql) en `.env`, pero **no se usa**:
`ATC/app/core/db.py` arma el engine único desde `settings.DATABASE_URL` exclusivamente. Es un resabio
de un intento de migración a PostgreSQL que no quedó activo. No trabajar contra PostgreSQL salvo que
se pida explícitamente.

## Documentación Primero

Antes de explorar el código a ciegas, revisa el vault `Documentación/` (notas Obsidian). Suele tener
ya la respuesta y evita leer muchos archivos. Mapa:

- **Arquitectura** -> `Documentación/10-Arquitectura/`
  - `Visión General.md` - los módulos y cómo se unifican
  - `App Unificada.md` - entry point, rutas, imports namespaced
  - `Login Único y SSO.md` - redirección por área, SSO
  - `Módulos/` - fichas de Helpdesk, Incidencias, Venta
- **Base de datos** -> `Documentación/20-Base-de-Datos/`
  - `MER ATC.md` - modelo de datos
  - `Unificación BBDD.md` - historia de la unificación de bases
  - `Tablas/` - ficha por tabla (columnas, FKs)
- **Operaciones / runbooks** -> `Documentación/30-Operaciones/`
  - `Levantar Servidor.md`, `Email IMAP-SMTP.md`, `Celery y Workers.md`
- **Decisiones (ADR)** -> `Documentación/40-Decisiones/`

Si documentas algo nuevo o cambias arquitectura, actualiza la nota correspondiente en
`Documentación/` usando las plantillas de `Documentación/_templates/`.

En este montaje SMB tambien existe documentacion resumida en `docs/`. Para cualquier cambio en
`ATC/app/routes/incidencias.py`, lee primero `docs/incidencias_mapa_rapido.md`: contiene el indice
por rangos y las rutas principales para evitar cargar las ~4k lineas completas. Despues usa `rg` y
`sed` sobre rangos chicos. Si agregas, borras o mueves bloques grandes/rutas en ese archivo,
actualiza tambien ese mapa.

## Código

Todo el código vive en **`ATC/app/`**. Ya no hay carpeta `ATC/incidencias/`.

- Rutas: `ATC/app/routes/` - `web.py` (Helpdesk, ~9k líneas), `incidencias.py` (~4.1k líneas),
  `venta.py`, `compras.py` (módulo Compras, prefix `/compras`), `portal_cliente.py` (login de
  clientes), `tickets.py`, `bitacora.py`, `inicio_turno.py`, `messages.py`, `requesters.py`,
  `whatsapp_webhook.py`, `public.py`. `bitacora_access.py` no es un router: es un helper de control
  de acceso compartido que importan `bitacora.py`, `incidencias.py` y `web.py`. También hay routers
  en `ATC/app/modules/` (`client_notes.py`, `unified_access.py`).
  Antes de tocar `incidencias.py`, partir por `docs/incidencias_mapa_rapido.md` y busquedas
  dirigidas; no leer el archivo completo salvo que sea imprescindible.
- Modelos: `ATC/app/models/` - `user.py`, `ticket.py`, `requester.py`, `incidencias.py`,
  `venta.py`, `compras.py`, `portal_cliente.py`, `prevencion.py`, etc. **Todos los modelos se
  importan en `ATC/app/main.py`**; un modelo nuevo que no se importe ahí no participa de
  `Base.metadata.create_all`.
- Servicios: `ATC/app/services/` - `incidencias_service.py` (~10.7k líneas, el más grande del
  repo), `venta_service.py` (~2.7k), `compras_service.py`, `protocolos_service.py`,
  `email_service.py`, `automation_service.py`, `ticket_service.py`, `user_service.py`,
  `analytics_service.py`, y varios más de reportes/Drive (`*_drive_report_service.py`,
  `informe_cliente_service.py`, `sla_feedback_service.py`, `lavados_service.py`,
  `ley_karin_service.py`, `contrato_diario_service.py`), son ~20 archivos en total. Por el tamaño de
  estos archivos, usa `rg` y búsquedas dirigidas en vez de leerlos completos.
- Config única: `ATC/app/core/config.py` (Pydantic `Settings`, lee `ATC/.env`). DB única:
  `ATC/app/core/db.py` (engine SQL Server vía `mssql+pyodbc`).
- Templates: `ATC/app/templates/` (unificado, HTML+CSS+JS inline por página, sin framework
  frontend). Static: `ATC/app/static/` y `ATC/static/`, servido también vía `/shared-static`.
- Workers: `ATC/app/workers/` (Celery). Integraciones de correo/WhatsApp:
  `ATC/app/integrations/`.
- Scripts puntuales: `ATC/scripts/` (seeds, import CSV, reset de sync IMAP) y `tools/` en la raíz
  (auditoría/unificación de BBDD, ya ejecutados).

## Arquitectura

- **Una sola BBDD, dos nombres de sesión por compat histórica**: `ATC/app/core/db.py` define
  `get_db()` y luego `get_incidencias_db = get_db` (alias literal, mismo engine SQL Server). Como
  FastAPI cachea las dependencias por identidad de función dentro de un mismo request, dos
  parámetros `Depends(get_db)` y `Depends(get_incidencias_db)` en la misma ruta son la misma
  `Session`. Si una query falla y no se hace `rollback()`, la conexión queda con la transacción
  abierta en mal estado y las queries siguientes sobre esa misma sesión pueden fallar o devolver
  datos inconsistentes. Si agregas un `try/except` alrededor de una query, haz `rollback()` en el
  `except` si la ruta sigue usando la sesión después.
- **Arranque sin Celery por defecto**: `main.py` (`startup_tasks`, evento `on_event("startup")`)
  levanta dos `threading.Thread` daemon (`email_loop`, `automation_loop`) que pollean IMAP cada
  `EMAIL_POLL_SECONDS` (mín. 60s; sube a 300s si el buzón rechaza por exceso de conexiones) y
  ejecutan auto-cierre cada `AUTOMATION_POLL_SECONDS` (mín. 60s). Celery (`ATC/app/workers/`,
  broker/backend en Redis `localhost:6379`) es una vía alternativa/asíncrona para importación de
  correo y notificaciones; no es necesario para que el server funcione en local.
- **Login único y redirección por área**: cada usuario tiene un área principal en `user_areas`
  (`is_primary`) que decide a qué panel entra (`soporte` -> Helpdesk `/panel`, `venta` ->
  `/venta/panel-selector`, `compras_control` -> `/compras/panel-control`, `compras_solicitud` ->
  `/compras/solicitud`, etc.). La tabla completa vive en `_redirect_for_user_area` en
  `routes/web.py` (~18 area-codes a jul 2026: soporte, materiales, compras_control,
  compras_solicitud, servicio_tecnico, tecnicos, coordinacion, protocolos, venta, finanzas,
  administracion, operaciones, guardia, supervisores, rrhh, prevencion, bitacora, incidencias).
  Confirma ahí antes de asumir que `Login Único y SSO.md` está completo, porque puede no reflejar
  las áreas de Compras. El bridge entre módulos es `/sso/login?token=...`.
- **Routers se registran todos en `main.py`** vía `app.include_router(...)`; para ubicar dónde vive
  una ruta HTTP, busca el prefijo en ese archivo antes de adivinar por nombre de módulo.
  **El orden de registro importa**: si dos routers declaran el mismo path, gana el que se registró
  primero y el otro queda muerto sin aviso. Verifica con `rg` antes de agregar paths nuevos.
- **`/api/client-notes` es un despachador**: la ruta vive solo en `modules/client_notes.py`, que
  según haya token de Incidencias o cookie de Helpdesk llama a funciones sin decorador en
  `routes/incidencias.py` o en `routes/web.py`. Esas funciones parecen handlers huérfanos pero no
  son código muerto; no las borres ni les agregues `@router` de nuevo.
- **Dependencias**: hay `requirements.txt` en la raíz. Los venvs que existían en el volumen no están
  visibles por SMB; en la práctica el Python que corre la app vive en el Windows Server. Si agregas
  una dependencia, añádela también a `requirements.txt`.
- **Mojibake histórico**: varios archivos tienen comentarios/strings con doble codificación UTF-8
  (`Ã³`, `ÃƒÆ'...`). Los casos que afectaban lógica (parsers de "sí", marcadores del webhook SLA,
  regex de correos) se corrigieron en jul 2026, pero quedan comentarios corruptos y datos en la BBDD
  pueden venir con mojibake. Por eso existe `_repair_text_encoding` en `venta_service.py` y
  comparaciones con variantes corruptas a propósito; no las limpies sin revisar los datos.
- **No hay tests ni linter configurados** en este repo (sin `pytest`, `ruff`, etc.). Verifica cambios
  levantando el server y probando la ruta/endpoint a mano, o con `TestClient` ad-hoc si aplica.

## Levantar El Server

Ver `Documentación/30-Operaciones/Levantar Servidor.md`. En **producción (Windows Server)** corre
la tarea programada "ATC Server Watchdog" -> `server_watchdog.py` -> `run_server.py`
(SelectorEventLoop) en `0.0.0.0:8000` (logs en `logs/`). Para reiniciar remotamente hay WinRM
(NTLM, puerto 5985) vía `pywinrm`; para reiniciar solo el server basta matar el proceso
`run_server.py` y el watchdog lo relanza en ~5s. El watchdog limpia el puerto 8000 antes de arrancar.

En **macOS** ya no hay venv utilizable en el volumen. Para verificar cambios desde el Mac usa:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/atc_compile_cache python3 -m compileall ATC/app
```

Si necesitas correr localmente, crea un venv nuevo con `uv` (Python 3.12+) e instala desde
`requirements.txt`.

## Validación

No hay tests ni linter configurados en este repo. Para cambios Python, valida al menos con
`compileall` usando `PYTHONPYCACHEPREFIX` para evitar errores de permisos de cache en macOS.

Para templates con JavaScript inline, extrae el bloque `<script>` y revisa con `node --check` cuando
la edición toque lógica JS. Para cambios funcionales, prueba la ruta real en `http://10.20.30.8:8000`
o `http://200.75.22.35:8000` después del reinicio si aplica.

## Git Y Publicación

No ejecutes comandos git dentro de `ATC/`: si aparece `ATC/.git`, es un repo anidado/obsoleto.

En este montaje SMB puede no estar visible el `.git` de la raíz. Antes de guardar o publicar cambios,
verifica:

```bash
git rev-parse --show-toplevel
git status --short
git remote -v
```

Si la raíz no es repo git, no inventes estado. Usa un clon temporal limpio del remoto
`contacto462/FlujoATC` o restaura el `.git` real antes de commitear/pushear. Al publicar, confirma
branch, commit y `git status` limpio.

### Guardar En GitHub Cuando El Usuario Lo Pida

Cuando el usuario diga "guárdalo en GitHub", "sube esto", "deja el proyecto en git" o equivalente,
hazlo tú mismo de punta a punta. No le devuelvas solo instrucciones salvo que falten credenciales o
el remoto rechace el push.

Flujo correcto:

1. Verifica si el montaje actual es repo git:

   ```bash
   git rev-parse --show-toplevel
   git status --short --branch
   git remote -v
   ```

2. Si `/Volumes/PROYECTO-ATC-SERVIDOR` no tiene `.git` visible, usa un clon temporal del remoto real.
   El clon utilizado en jul 2026 fue:

   ```bash
   /private/tmp/atc-github-publish-20260727103756
   ```

   Antes de usarlo, valida que existe, que está limpio, que apunta al remoto correcto y que está al día:

   ```bash
   git -C /private/tmp/atc-github-publish-20260727103756 remote -v
   git -C /private/tmp/atc-github-publish-20260727103756 status --short --branch
   git -C /private/tmp/atc-github-publish-20260727103756 fetch origin
   git -C /private/tmp/atc-github-publish-20260727103756 pull --ff-only origin main
   ```

   El remoto esperado es `https://github.com/contacto462/FlujoATC.git`, branch `main`.

3. Copia al clon solo los archivos realmente cambiados en el montaje SMB. Preferir copia puntual por
   path sobre sincronizar todo el árbol. No copiar `.env`, `uploads/`, `exports/`, `logs/`,
   `__pycache__/`, PDFs/backups de usuario ni runtime data salvo pedido explícito.

   Para copiar archivos puntuales preservando rutas:

   ```bash
   rsync -av --relative ./AGENTS.md /private/tmp/atc-github-publish-20260727103756/
   rsync -av --relative ./ATC/app/templates/archivo.html /private/tmp/atc-github-publish-20260727103756/
   ```

   Cambia los paths por los archivos tocados en la tarea. Si hay dudas, compara con `diff -u` o `rg`
   antes de copiar.

4. Valida en el clon, no solo en el montaje. Para cambios Python:

   ```bash
   cd /private/tmp/atc-github-publish-20260727103756
   PYTHONPYCACHEPREFIX=/private/tmp/atc_compile_cache_github python3 -m compileall ATC/app
   ```

   Para templates con JavaScript inline, ejecutar también un `node --check` del script extraído
   cuando corresponda.

5. Revisa exactamente qué se va a commitear:

   ```bash
   git status --short
   git diff --stat
   git diff -- <paths tocados>
   ```

   No uses `git add .` a ciegas si hay archivos ajenos o runtime data. Usa `git add <paths>`.

6. Commit y push:

   ```bash
   git add <paths tocados>
   git commit -m "Mensaje corto y específico"
   git push origin main
   git status --short --branch
   git log --oneline -3
   ```

7. En la respuesta final informa commit hash, branch, remoto, validaciones corridas y cualquier cosa
   que no se haya podido verificar. Si el push falla, informa el error exacto y deja el clon sin
   cambios parciales ocultos.

## Funcionalidades Recientes Y Bordes

- **Guardias / Informe mensual**: la funcionalidad `Informe mensual` fue eliminada. No reintroducir
  `guardia/informe-mensual`, `guardias_informe_service.py` ni `generar_informe_guardias_pdf`.
  **No tocar** `Descargar informes`: es una función distinta.
- **Resumen equipos técnicos**: `/resumen-equipos-tecnicos` tiene una columna admin-only llamada
  `Pendientes Prioritarios`. Al mover una incidencia hacia esa columna, debe quedar pendiente. Al
  mover una venta hacia esa columna, se borra técnico y acompañante.
- **Patentes visuales de equipos**: en `resumen_equipos_tecnicos.html`, las patentes se pueden editar
  **solo visualmente** mediante `localStorage`; no modificar SQL, asignaciones, técnicos ni
  `/api/resumen-equipos-tecnicos/mover` para esa edición visual.
- **Lavados**: `/lavados.html` es una página pública sin login y sin logo ATC. Debe ser cómoda en
  móvil. Usa Google Sheets/Drive mediante `lavados_service.py`. La columna `Lista` de la hoja BBDD se
  debe limpiar los lunes según la metadata del servicio.
- **Ticketera / notificaciones**: los badges de ticketera deben representar la cantidad de tickets
  que quedan abiertos o pendientes, no los "no abiertos".
- **Tabla soporte venta**: los pendientes que el usuario sí puede modificar deben aparecer arriba.

## No Tocar Sin Motivo

- `Documentación/` es el vault de Obsidian del usuario (notas, no código de runtime).
- `uploads/`, `exports/`, `docs/`, backups, logs y PDFs de usuario no se modifican salvo pedido
  explícito.
- No hacer refactors amplios ni limpieza estética fuera del alcance pedido.
- No tocar datos reales de SQL Server sin verificación antes/después cuando el cambio sea de cuentas,
  permisos, sucursales, empresas o registros productivos.
