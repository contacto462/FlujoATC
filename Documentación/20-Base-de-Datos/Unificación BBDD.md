---
tipo: decision
estado: aceptada
area: datos
tags:
  - db
  - decision
actualizado: 2026-06-14
fecha: 2026-05-20
---

# Unificación de BBDD (helpdesk + incidencias)

> [!success] Estado: **ejecutada** el 2026-05-20
> Helpdesk e Incidencias ahora comparten una sola base PostgreSQL llamada `ATC`. Resumen también como ADR en [[0001 Unificación de BBDD]].

## Objetivo
Unificar `helpdesk` e `incidencias` en una sola base sin cambiar el comportamiento visible:
- Helpdesk: tickets, mensajes, solicitantes, SLA, correo y panel.
- Incidencias: `registro`, venta ODS, clientes, sucursales, protocolos, rendiciones y tareas.
- Ambas apps comparten la misma base física; las tablas comunes se fusionan con cuidado (sobre todo `[[users]]`).

## Diagnóstico previo
- `ATC/.env`: `DATABASE_URL` → `helpdesk`; `INCIDENCIAS_DATABASE_URL` → `incidencias`.
- `Incidencias/.env`: `DATABASE_URL` → `incidencias`; `SUPPORT_DB_URL` → `helpdesk`; `SUPPORT_SYNC_MODE=off`.

Conclusión: no estaban realmente unificadas. ATC abría una segunda conexión a incidencias para algunas pantallas.

## Estrategia aplicada
1. Respaldo de `helpdesk` e `incidencias`.
2. Crear base nueva (resultó llamarse `ATC`).
3. Restaurar `helpdesk` como base inicial.
4. Copiar tablas de `incidencias` que no existían.
5. Fusionar comunes: `users` por `username`; `areas` por `code`; remapear `user_areas`; no migrar `login_sessions` (sesiones temporales).
6. `incidencias_imagenes_odt`: mantener `JSONB` y convertir el `TEXT` de incidencias a JSON.
7. Apuntar ambas apps a la base nueva.
8. Validar flujos críticos.

## Auditoría previa (2026-05-20)
Ejecutada con `python tools/audit_db_unification.py` (solo lectura):
- Tablas solo en `helpdesk`: 15 · solo en `incidencias`: 25 · comunes: 2.
- `users` — helpdesk 6 filas, incidencias 24; incidencias agrega `department`, `created_at`, `updated_at`. Acción: superset fusionado por `username`.
- `incidencias_imagenes_odt` — helpdesk 0 filas `JSONB`; incidencias 66 filas `TEXT`. Acción: mantener `JSONB`, convertir texto a JSON.

## Ejecución (2026-05-20)
```bash
python tools/unify_postgres_databases_to_atc.py --execute --drop-old
```
- Se creó la base `ATC` desde `helpdesk`, se copió `incidencias`, se fusionó `users`, se migró `incidencias_imagenes_odt` a JSONB, se vació `login_sessions`.
- Se actualizaron `ATC/.env` e `Incidencias/.env` para apuntar a `ATC`.
- Se eliminaron las bases antiguas `helpdesk` e `incidencias`.
- Respaldo previo: `backups/db_unification/20260520_123315` (ignorado por Git).

## Configuración final
```env
# ATC/.env  y  Incidencias/.env
DATABASE_URL=postgresql+psycopg://USER:PASS@HOST:5432/ATC
SUPPORT_SYNC_MODE=off
SUPPORT_DB_URL=
```

## Flujos a validar tras migrar
Login Helpdesk e Incidencias · crear/responder ticket · crear incidencia y verla en panel Helpdesk · cerrar ODT con imágenes · crear cliente/sucursal · flujo Venta ODS · paneles por área (Soporte, Servicio Técnico, Incidencias, Venta, Finanzas, Administración, Operaciones).

---
> Migrado de `docs/unificacion_bbdd.md`.
