# Unificacion de BBDD helpdesk + incidencias

## Objetivo

Unificar `helpdesk` e `incidencias` en una sola base PostgreSQL sin cambiar el
comportamiento visible de las aplicaciones:

- Helpdesk sigue manejando tickets, mensajes, solicitantes, SLA, correo y panel.
- Incidencias sigue manejando `registro`, venta ODS, clientes, sucursales,
  protocolos, rendiciones y tareas.
- Ambas apps comparten la misma base fisica.
- Las tablas compartidas se fusionan con cuidado, especialmente `users`.

## Diagnostico actual

Segun la configuracion actual:

- `ATC/.env`
  - `DATABASE_URL` apunta a `helpdesk`.
  - `INCIDENCIAS_DATABASE_URL` apunta a `incidencias`.
- `Incidencias/.env`
  - `DATABASE_URL` apunta a `incidencias`.
  - `SUPPORT_DB_URL` apunta a `helpdesk`.
  - `SUPPORT_SYNC_MODE=off`.

Eso significa que hoy las apps no estan realmente unificadas: ATC lee/escribe
en helpdesk y abre una segunda conexion a incidencias para algunas pantallas.
Incidencias trabaja en su propia base y tiene configuracion preparada para
sincronizar hacia soporte.

## Estrategia recomendada

La forma mas segura es crear una base nueva, por ejemplo `atc_unificada`, y
copiar alli ambas bases. No conviene mezclar directamente sobre produccion.

Orden recomendado:

1. Crear respaldo de `helpdesk` e `incidencias`.
2. Crear base nueva `atc_unificada`.
3. Restaurar primero `helpdesk` como base inicial.
4. Agregar las tablas de `incidencias` que no existan en `helpdesk`.
5. Fusionar tablas comunes:
   - `users`: fusionar por `username`.
   - `areas`: fusionar por `code`.
   - `user_areas`: remapear `user_id` y `area_id`.
   - `login_sessions`: preferible no migrarla; son sesiones temporales.
6. Revisar tablas con colision de nombre y forma:
   - `incidencias_imagenes` puede existir con formatos distintos. Antes de
     fusionar, confirmar columnas reales en ambas bases.
7. Apuntar ambas apps a la base nueva.
8. Probar flujos criticos antes de apagar las bases antiguas.

## Configuracion final esperada

En `ATC/.env`:

```env
DATABASE_URL=postgresql+psycopg2://USER:PASS@HOST:5432/atc_unificada
INCIDENCIAS_DATABASE_URL=postgresql+psycopg2://USER:PASS@HOST:5432/atc_unificada
```

En `Incidencias/.env`:

```env
DATABASE_URL=postgresql+psycopg://USER:PASS@HOST:5432/atc_unificada
SUPPORT_SYNC_MODE=off
SUPPORT_DB_URL=
```

`SUPPORT_SYNC_MODE` queda apagado porque ya no hace falta sincronizar entre dos
bases. Ambas aplicaciones leen y escriben en la misma base.

## Flujos que se deben validar

Antes de dar por terminada la migracion:

- Login en Helpdesk.
- Login en Incidencias.
- Crear ticket desde correo/manual.
- Responder ticket.
- Crear incidencia nueva desde Incidencias.
- Ver esa incidencia en el radar/panel de Helpdesk.
- Cerrar ODT/incidencia con imagenes.
- Crear cliente/sucursal.
- Crear flujo Venta ODS.
- Ver paneles por area: Soporte, Servicio Tecnico, Incidencias, Venta,
  Finanzas, Administracion y Operaciones.

## Auditoria previa

Ejecutar el auditor de esquemas antes de migrar:

```powershell
python tools/audit_db_unification.py
```

El script no escribe en las bases. Solo compara tablas, columnas y cantidad de
filas para detectar choques antes de hacer una migracion real.

## Resultado de auditoria local

Ejecutado el 2026-05-20:

- Tablas solo en `helpdesk`: 15.
- Tablas solo en `incidencias`: 25.
- Tablas comunes: 2.

Tablas comunes detectadas:

- `users`
  - `helpdesk`: 6 filas.
  - `incidencias`: 24 filas.
  - Diferencia: `incidencias` agrega `department`, `created_at`,
    `updated_at`.
  - Accion: usar una tabla `users` superset y fusionar por `username`.
- `incidencias_imagenes_odt`
  - `helpdesk`: 0 filas, columna `imagenes` tipo `JSONB`.
  - `incidencias`: 66 filas, columna `imagenes` tipo `TEXT`.
  - Accion: mantener `JSONB` en la base unificada y convertir el texto de
    incidencias a JSON durante la copia.

Conclusion: el riesgo principal esta acotado. La mayor parte de la migracion
es copiar tablas completas; solo `users` e `incidencias_imagenes_odt` necesitan
tratamiento especial.

## Ejecucion aplicada

El 2026-05-20 se ejecuto la unificacion real:

```powershell
python tools\unify_postgres_databases_to_atc.py --execute --drop-old
```

Resultado:

- Se creo la base PostgreSQL `ATC`.
- `ATC` se creo inicialmente desde `helpdesk`.
- Se copiaron las tablas y datos de `incidencias`.
- Se fusiono `users`.
- Se migro `incidencias_imagenes_odt` convirtiendo `imagenes` a JSONB.
- Se vacio `login_sessions` porque son sesiones temporales.
- Se actualizaron `ATC/.env` e `Incidencias/.env` para apuntar a `ATC`.
- Se eliminaron las bases antiguas `helpdesk` e `incidencias`.

Respaldo previo:

```text
backups/db_unification/20260520_123315
```

Ese respaldo queda ignorado por Git porque contiene datos reales.
