# Análisis del proyecto PROYECTO-ATC

Fecha: 2026-05-22
Alcance: `C:\Users\ATC\Desktop\PROYECTO-ATC`
Stack detectado: FastAPI + SQLAlchemy 2.x + PostgreSQL + Jinja2 + threading workers (Celery/Redis importados pero no usados realmente).

---

## 🔴 Hallazgos críticos (acción inmediata)

### C1. Claves API reales filtradas en `ATC/incidencias/.env`
- `ANTHROPIC_API_KEY=sk-ant-api03-X6OqKH7N...` (línea 54)
- `OPENAI_API_KEY=sk-proj-Pnq0OBV_Ut0Co98...` (línea 58)
- `SMTP_PASSWORD=ppnzjvkasaucnzcm` (app password de Gmail, línea 26)

**Bueno:** verifiqué con `git log --all --full-history -- ATC/incidencias/.env` y los `.env` **no están en el historial de git** — `.gitignore` los protege correctamente.

**Malo:** las claves están en disco en claro y un `git add -f` o un cambio en `.gitignore` las expondría. Y como el repo apunta a `https://github.com/contacto462/FlujoATC.git`, si en algún momento se pierde el patrón `.env` en gitignore, se filtran.

**Acción:**
1. **Rotar las 3 credenciales ya** (Anthropic console, OpenAI dashboard, Gmail → quitar app password).
2. Confirmar que `*.env` y `**/.env` están en `.gitignore` (hoy solo está `.env` que sí funciona en cualquier nivel — OK).
3. Migrar a un gestor de secretos (Windows Credential Manager, archivo cifrado, o variables de entorno del SO) si esto va a producción.

### C2. `auto_git.py` es una bomba de tiempo
```python
# auto_git.py:42
subprocess.run("git add .", cwd=RUTA_REPO, shell=True)
subprocess.run('git commit -m "Auto update"', ...)
subprocess.run("git push", ...)
```
Hace `git add .` + commit + push automáticos cada 10 min cuando detecta cambios. Hoy `.env` está en `.gitignore`, pero:
- Si alguien borra una línea del `.gitignore`, en el siguiente cambio se sube todo.
- Sube `__pycache__`, `secrets/` si la regla falla, archivos temporales, código a medio terminar.
- Los commits llevan mensaje "Auto update" — el log de git que veo (`8900254 Auto update`, `a82a3b2 Auto update`, ...) lo confirma. No es revisable.
- `shell=True` con cwd controlado no es injection, pero la práctica es mala.

**Acción:** desactivar este script. Si quieres backup automático, usa un cron que haga `git bundle` a una carpeta local, no push a un remoto.

### C3. Contraseña JWT débil y comprometida si el `.env` se filtra
`JWT_SECRET=SoporteATC1180` (`ATC/.env:12`) — 16 caracteres, claramente derivado del nombre del proyecto. Cualquiera que vea ese string puede falsificar tokens.

**Acción:** generar uno con `python -c "import secrets; print(secrets.token_urlsafe(64))"` y rotarlo. Los tokens emitidos quedarán inválidos (efecto deseado para la rotación de secreto).

### C4. Contraseñas en texto plano soportadas en login
```python
# ATC/app/routes/web.py:875
def _verify_web_password(plain_password: str, stored_password: str) -> bool:
    stored = str(stored_password or "")
    incoming = str(plain_password or "")
    if stored.startswith("plain:"):
        return stored.removeprefix("plain:") == incoming   # ← compara texto plano
    ...
```
Y en `ATC/app/main.py:166` la migración añade columna `password` con `DEFAULT 'plain:123456'`. Cualquier usuario creado por esa migración tiene literalmente `plain:123456` como contraseña verificable.

**Acción:**
- Eliminar el branch `plain:` o como mínimo dejarlo solo bajo un flag explícito de desarrollo.
- Forzar reset de contraseñas para usuarios con prefijo `plain:` al primer login.
- Cambiar el seed por defecto (`hash_password("123456")` en `main.py:407`) por una contraseña aleatoria que se imprima en consola la primera vez.

### C5. Cookie de sesión sin `secure=True`
```python
# ATC/app/routes/web.py:1052
secure=False,
```
Sobre HTTP la cookie viaja sin TLS. Aceptable en `localhost`, **inaceptable** cuando se exponga al LAN o internet.

**Acción:** parametrizar con `secure=settings.COOKIE_SECURE` y poner `True` en cualquier entorno != dev.

---

## 🟠 Errores de código (bugs reales o latentes)

### B1. Router de `/auth/login` y `/auth/register` NO está montado
`ATC/app/routes/auth.py` define `router = APIRouter(prefix="/auth")` con endpoints de login/register, pero en `ATC/app/main.py` (líneas 325–338) **no se hace `app.include_router(auth_router)`**. El módulo se importa pero el router queda muerto.

Consecuencia: el endpoint declarado en `oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")` (`ATC/app/core/auth.py:11`) apunta a una URL que **devuelve 404**. La API JSON (Swagger) no se puede usar para autenticarse; solo funciona el login HTML por cookie en `/web/login`.

**Acción:** o se monta `from ATC.app.routes.auth import router as auth_router; app.include_router(auth_router)`, o se borra `auth.py` y se reemplaza `tokenUrl` por `/web/login`.

### B2. `oauth2_scheme` + cookie incoherentes
`get_current_user` (en `core/auth.py`) lee el token del header `Authorization: Bearer ...`. Pero todo el resto del sistema usa cookie `access_token`. Si algún endpoint usa `Depends(get_current_user)`, va a fallar para usuarios web; si usa `Depends(get_current_user_web)`, va a fallar para clientes API.

No usar dos esquemas paralelos. Unificar a uno (recomiendo cookie con CSRF para web, header bearer solo para API explícita).

### B3. `endpoint duplicado` para `public_router`
```python
# ATC/app/main.py:329-330
app.include_router(public_router, prefix="/api")
app.include_router(public_router)
```
Se monta dos veces: con prefijo `/api` y sin él. Esto crea **rutas duplicadas** (`/api/public/tickets` y `/public/tickets` apuntan al mismo handler) y rompe la documentación OpenAPI con operationIds repetidos. FastAPI no lanza error pero loguea warnings.

### B4. `ensure_users_unified_columns` y similares son frágiles
`ATC/app/main.py:134–170` y todas las funciones `ensure_*` ejecutan `ALTER TABLE` al startup, dentro de cada arranque del proceso. Problemas:
- Race conditions si arrancan dos workers a la vez (uvicorn `--workers 2+`).
- `Base.metadata.create_all()` + ALTERs manuales mezclados → ningún sistema controla qué esquema es "oficial".
- Las defaults son string-interpoladas en el SQL: `f'ALTER TABLE users ADD COLUMN "{column_name}" {column_type}{nullable_default}'`. No es inyectable porque la fuente es un dict literal en el código, pero el patrón es peligroso si alguien añade un valor desde input.

**Acción:** introducir Alembic. Las migraciones automágicas en `startup` no escalan.

### B5. Workers IMAP en threads daemon, sin reintentos exponenciales
```python
# ATC/app/main.py:481-504
def email_loop():
    while True:
        try: _poll_imap()
        except Exception as e: print(...)
        try: _poll_imap(IMAP2...)
        except Exception as e: print(...)
        time.sleep(5)
```
- Polling cada 5 segundos a IMAP es agresivo; servidores tipo Gmail rate-limitean.
- `print()` en vez de logging.
- Si la conexión IMAP queda colgada (no excepción, solo bloqueo), el thread se atasca para siempre.
- `daemon=True` significa que al matar el proceso no hay cierre limpio (mensajes en vuelo se pierden).

Está bien para dev. Para producción local conviene Celery + Redis (ya están las dependencias) o al menos `apscheduler`.

### B6. SQL injection por interpolación con `text(f"...")` — bajo riesgo, pero presente
Veredicto general: la mayoría de los `text(f"...")` usan valores hardcodeados o de configuración. **Sin exploit directo desde un usuario**. Pero hay casos a tener en cuenta:

- `ATC/app/main.py:150, 167` → renames/adds de columna, dict literal. OK pero patrón malo.
- `ATC/incidencias/app/main.py:483, 553, 571` → `f"... FROM {_support_requesters_table_name()} ..."`. La función devuelve un valor de `settings.support_db_table` que viene de variable de entorno (`SUPPORT_DB_TABLE`, default `"registro"`). No es input de usuario, pero **si alguien controla el `.env` controla la query**. Bajo riesgo en local; en multitenant sería crítico.
- `ATC/incidencias/app/main.py:612, 642, 671, 703, 722, 750, 789, 795` → mismo patrón en migraciones automáticas.

**Acción:** validar nombres de tablas/columnas contra una whitelist antes de interpolarlos, o usar `sqlalchemy.schema.quoted_name` + reflection.

### B7. Endpoint público sin rate limit
```python
# ATC/app/routes/public.py:40
@router.post("/tickets")
def create_public_ticket(data: PublicTicketCreate, db: Session = Depends(get_db)):
```
Cualquier persona puede crear tickets sin captcha, sin rate limit, sin verificación de email. Spam guaranteed cuando se exponga.

**Acción:** `slowapi` (FastAPI rate limit) + captcha o token de un solo uso en el formulario.

### B8. `time.sleep(5)` y `time.sleep(max(int(settings.AUTOMATION_POLL_SECONDS or 300), 60))` 
Funciona, pero todo el sistema corre en threads dentro de uvicorn. Si lanzas `uvicorn --workers 4`, **cada worker** arranca un loop IMAP — múltiples conexiones leyendo el mismo buzón → duplicados de tickets.

**Acción:** un sentinel/lock en BD para que solo un worker procese IMAP, o ejecutar los loops fuera de uvicorn (servicio aparte / Celery worker).

### B9. Sin manejo de transacciones consistente
En varios sitios (`web.py`, `incidencias/app/main.py`) se mezclan `db.execute(...)` + `db.commit()` con bloques `try/except` que hacen rollback solo a veces. Una sesión que falla a mitad puede dejar la BD en estado raro.

### B10. `incidencias/app/main.py` define **su propia `FastAPI()`** que después se "monta" copiando rutas
```python
# ATC/app/modules/incidencias.py:18
for route in incidencias_app.router.routes:
    if not isinstance(route, APIRoute):
        continue
    if route.path in SKIPPED_PATHS:
        continue
    app.router.routes.append(route)
```
- Salta middlewares de `incidencias_app` (exception handler global definido en `incidencias/app/main.py:61` **no aplica**).
- Salta los `app.mount("/static")` del sub-app.
- Cualquier dependencia que el sub-app espere (config, lifecycle) tampoco se mueve.

Es un hack que ya está rompiendo el manejo unificado de errores 401. Mejor: convertir incidencias en un `APIRouter` puro, no en `FastAPI()`.

### B11. `BBDD vacía / huérfana`
`ATC/ticketing.db` existe como archivo de 0 bytes. La app real usa PostgreSQL (según `.env`). Es residuo. Borrarlo evita confusión.

### B12. CSV de 4.8 MB en el repo
`ATC/Registro Incidencias - Registro.csv` (4854733 bytes) está versionado. No es código, no debería estar en el repo — usar Git LFS o sacarlo.

### B13. `__init__.py` vacío en la raíz del proyecto
`PROYECTO-ATC/ATC/__init__.py` (48 bytes) existe para hacer del directorio un paquete. OK pero combinado con `unified_main.py` y el `auto_git.py` en la raíz, la estructura está confusa: no hay un solo entrypoint claro.

---

## 🛡 Problemas de seguridad

| # | Issue | Severidad | Archivo |
|---|---|---|---|
| S1 | API keys reales en `.env` (Anthropic, OpenAI, SMTP) | Crítica | `ATC/incidencias/.env` |
| S2 | JWT secret débil y filtrado | Crítica | `ATC/.env:12` |
| S3 | `auto_git.py` con `git add .` automático | Alta | `auto_git.py` |
| S4 | Soporte de `plain:` en contraseñas | Alta | `ATC/app/routes/web.py:875` |
| S5 | Seed con password `123456` para 6 usuarios admin/agent | Alta | `ATC/app/main.py:407` |
| S6 | Cookie `secure=False` | Media (Alta si sale de localhost) | `ATC/app/routes/web.py:1052` |
| S7 | Sin CSRF en formularios HTML (cookies + state-changing POSTs) | Media | varios |
| S8 | Sin CORS configurado (default permisivo de FastAPI = vacío) | Baja | `ATC/app/main.py` |
| S9 | Endpoint público sin rate limit/captcha | Media | `ATC/app/routes/public.py:40` |
| S10 | `SLA_WEBHOOK_TOKEN=ATC_SLA_WEBHOOK_2026` predecible | Media | `ATC/.env:54` |
| S11 | `MAX_EMAIL_ATTACHMENT_BYTES = 25 MB` por archivo, sin validar mimetype real | Baja | `web.py:114` |
| S12 | `pwd_context.verify` envuelto en `except Exception: return False` (silencia errores reales) | Baja | `web.py:882` |
| S13 | `JWT_EXPIRES_MIN=1440` (24 h) sin refresh tokens — robar la cookie da 24h de acceso | Baja-Media | `.env:14` |
| S14 | Carpeta `ATC/secrets/` contiene `gdrive_service_account.json`, `google_oauth_*.json`. En `.gitignore` ✅, pero sin permisos OS restringidos | Baja | `ATC/secrets/` |
| S15 | `VENTA_CATALOGO_VERIFY_SSL=false` en `.env.example` (`incidencias/.env`) | Baja | `incidencias/.env:19` |
| S16 | `print("Error IMAP1:", e)` filtra detalles (passwords IMAP) si la excepción los incluye | Baja | `main.py:490` |

---

## 🚚 Factibilidad de deployment en servidor local

**Viabilidad: factible, con preparación. ~1–2 días de trabajo si quieres algo correcto.**

### ✅ Lo que ya está bien para deploy local
- `pydantic-settings` para configuración por entorno
- `psycopg[binary]` (driver PostgreSQL embebido, sin libpq externa)
- `requirements.txt` con versiones acotadas (`fastapi>=0.116,<1.0`)
- Uvicorn como ASGI server
- Modelos SQLAlchemy 2.x con tipado moderno

### ❌ Lo que falta o está mal para producción local

**Estructura/build:**
- No hay `requirements.txt` en la raíz, solo en `ATC/incidencias/`. Hay módulos (Celery, Redis, anthropic, google-api-python-client) que se usan en `ATC/app/...` y no están listados allí.
- No hay `pyproject.toml`.
- No hay `Dockerfile`, ni `docker-compose.yml`.
- No hay archivo de servicio (`systemd` unit, `nssm`, o servicio Windows).
- No hay script de bootstrap (crear BD, ejecutar migraciones, seedear).
- `.venv/` está dentro del repo — debería estar fuera o en `.gitignore` (ya está implícito por `__pycache__/` pero `.venv` no aparece en `.gitignore`).

**Base de datos:**
- `Base.metadata.create_all()` + funciones `ensure_*` cada arranque = sin control de versiones de esquema. Adoptar Alembic.
- `ATC/incidencias/sql/schema.sql` y `20260520_users_areas.sql` sugieren que parte del esquema se maneja manualmente y otra parte por ORM. Híbrido frágil.
- Sin script para backup automático.

**Red/observabilidad:**
- No hay reverse proxy configurado (nginx/IIS/caddy). Uvicorn no debería exponerse directo en :8000 a la red, ni siquiera local.
- No hay TLS. Si vas a usarlo en LAN, al menos genera cert auto-firmado y ajusta `cookie secure=True`.
- Logs van a `print()`. Sin rotación, sin centralización.
- No hay healthcheck robusto (solo `/health` que devuelve 200 sin verificar BD).

**Concurrencia:**
- Lanzar con `uvicorn --workers >1` rompe los loops de email/automation (B8). Hoy solo funciona en 1 worker = throughput bajo.
- Threads daemon dentro de uvicorn = al recargar (`--reload`) quedan threads zombi.

### Plan recomendado para deployment local

1. **Día 1 — saneamiento**
   - Rotar las 3 claves API filtradas
   - Generar JWT_SECRET nuevo
   - Borrar/desactivar `auto_git.py`
   - Eliminar branch `plain:` y forzar reseteo
   - `secure=True` en cookies y ajustar dev vs prod por env var

2. **Día 2 — empaquetado**
   - Crear `requirements.txt` en raíz con todas las deps reales (hacer `pip freeze` filtrado)
   - Migrar `ensure_*` a Alembic
   - Bajar IMAP/automation a un proceso aparte (script `python -m ATC.app.workers.email_runner`)
   - Crear servicio Windows con `nssm` o tarea programada que arranque uvicorn detrás de IIS/nginx
   - Configurar TLS (mkcert para LAN, certbot si tiene DNS)

3. **Día 3 — operación**
   - Reemplazar `print()` por `logging` con archivo + rotación
   - Script de backup `pg_dump` programado
   - Healthcheck `/health` que verifique BD + IMAP

---

## 🔐 Estado de credenciales hardcodeadas

| Tipo | Lugar | Estado |
|---|---|---|
| Anthropic API key | `ATC/incidencias/.env:54` | 🔴 Real, rotar ya |
| OpenAI API key | `ATC/incidencias/.env:58` | 🔴 Real, rotar ya |
| Gmail SMTP password | `ATC/incidencias/.env:26` | 🔴 Real (app password), rotar ya |
| IMAP password `Soporte@soporteatc.cl` | `ATC/.env:22` | 🟠 Real, plano en disco |
| Postgres password | `ATC/.env:4,7` | 🟢 Enmascarado como `***` (asumo lo enmascaraste tú) |
| JWT secret | `ATC/.env:12` | 🔴 `SoporteATC1180` débil |
| SLA webhook token | `ATC/.env:54` | 🟠 `ATC_SLA_WEBHOOK_2026` predecible |
| Google service account JSON | `ATC/secrets/gdrive_service_account.json` | 🟡 Existe, no en git, pero sin permisos OS restringidos |
| Google OAuth client + token | `ATC/secrets/*.json` | 🟡 Igual que arriba |
| Default password seed users | `ATC/app/main.py:407` | 🔴 `"123456"` para 6 cuentas |
| Default password migración | `ATC/app/main.py:166` | 🔴 `'plain:123456'` |
| Hardcoded folder/template IDs Google | `incidencias/app/config.py:212–227` | 🟢 No son secretos, pero acoplan a Drive personal |

**Buenas prácticas que ya están:**
- `.env` y `.env.example` separados ✅
- `.gitignore` cubre `.env`, `*.json`, `*.db`, `**/secrets/**` ✅
- `passlib` con bcrypt para hashing nuevo ✅

---

## 🗄 Configuración de conexiones SQL

### Engines detectados

1. **`ATC/app/core/db.py:5`** — engine principal (`settings.DATABASE_URL`).
2. **`ATC/app/core/db.py:14`** — engine `incidencias` (`settings.INCIDENCIAS_DATABASE_URL or settings.DATABASE_URL`).
3. **`ATC/incidencias/app/database.py:34`** — un tercer engine separado dentro del sub-app `incidencias`, con su propia config y `Base`.

**Problema:** **dos `Base = DeclarativeBase`** distintas (`ATC/app/core/db.py:21` y `ATC/incidencias/app/database.py:9`), con modelos posiblemente apuntando a las **mismas tablas** desde clases distintas. Esto puede causar:
- Discrepancias de esquema entre los dos lados.
- `Base.metadata.create_all()` solo crea las tablas de su propio Base — el otro lado depende de `ensure_*` o de SQL manual.

**Acción:** unificar a un solo `Base` y un solo engine pool, o aislar las tablas de cada módulo con prefijos/schemas distintos.

### Pool & timeout
```python
create_engine(settings.DATABASE_URL, pool_pre_ping=True, connect_args={"connect_timeout": 2})
```
- `pool_pre_ping=True` ✅ (detecta conexiones muertas).
- `connect_timeout=2` segundos: **muy bajo** para PostgreSQL en arranque/carga. Si el servidor tarda más de 2s en aceptar, falla. Recomiendo 5–10s.
- No se especifica `pool_size`, `max_overflow`, `pool_recycle`. Defaults (5, 10, sin recycle) pueden ser insuficientes con el polling IMAP + automation_loop + requests web concurrentes.

### Driver `psycopg2` vs `psycopg`
- `ATC/.env` usa `postgresql+psycopg2://...`
- `ATC/incidencias/requirements.txt` declara `psycopg[binary]>=3.2,<4.0` (psycopg 3, no 2)
- `ATC/incidencias/app/config.py:237` normaliza `postgresql://` → `postgresql+psycopg://`

**Inconsistencia:** el app principal espera psycopg2 (que no está en requirements) y el sub-app espera psycopg3. Si pip instala solo lo de `incidencias/requirements.txt`, el engine principal **falla con `ModuleNotFoundError: No module named 'psycopg2'`**.

**Acción:** elegir uno. Recomiendo `psycopg[binary]` (v3) y cambiar `.env` a `postgresql+psycopg://`.

### Transacciones
`autocommit=False, autoflush=False` está bien. Pero el uso real es desigual: hay `db.commit()` esparcidos y `db.rollback()` solo en algunos `except`. Convendría un context manager / dependency que garantice rollback en error.

---

## 📋 Resumen accionable (orden de prioridad)

| Prioridad | Acción | Tiempo |
|---|---|---|
| 🔴 P0 | Rotar Anthropic/OpenAI/Gmail keys filtradas | 15 min |
| 🔴 P0 | Generar nuevo `JWT_SECRET` aleatorio | 5 min |
| 🔴 P0 | Desactivar `auto_git.py` | 1 min |
| 🔴 P0 | Eliminar branch `plain:` y resetear passwords default | 30 min |
| 🟠 P1 | Unificar driver psycopg2/psycopg3 | 15 min |
| 🟠 P1 | Montar router `/auth` o borrarlo (B1) | 10 min |
| 🟠 P1 | Quitar doble-mount de `public_router` (B3) | 2 min |
| 🟠 P1 | Cookie `secure=True` configurable por entorno | 20 min |
| 🟠 P1 | Rate-limit + captcha en `/public/tickets` | 1 h |
| 🟡 P2 | Adoptar Alembic, eliminar `ensure_*` ALTER al startup | 4 h |
| 🟡 P2 | Sacar email_loop/automation_loop de uvicorn (Celery o proceso aparte) | 4 h |
| 🟡 P2 | Unificar `Base` y engines | 3 h |
| 🟢 P3 | `requirements.txt` en raíz + Dockerfile o servicio Windows | 2 h |
| 🟢 P3 | Reemplazar `print` por logging con rotación | 1 h |
| 🟢 P3 | Sacar CSV de 4.8 MB del repo | 5 min |

---
