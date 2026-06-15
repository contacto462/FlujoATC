---
tipo: decision
estado: aceptada
fecha: 2026-05-20
modulo: plataforma
tags:
  - decision
  - db
---

# ADR 0001 · Unificar helpdesk e incidencias en una sola BBDD

> [!success] Estado: **aceptada** · 2026-05-20

## Contexto
Helpdesk e Incidencias corrían sobre dos bases PostgreSQL separadas (`helpdesk` e `incidencias`), con `users` duplicada y sincronización parcial. La [[App Unificada]] necesita una sola fuente de datos.

## Decisión
Crear una base única (`ATC`) que fusione ambas, con `users` como tabla superset (fusión por `username`), y apuntar todas las apps a esa base.

## Alternativas consideradas
- **Mantener dos bases + sincronización** — más frágil, doble conexión, `users` desincronizada.
- **Fusionar directo sobre producción** — riesgoso, sin rollback limpio. Descartado.

## Consecuencias
- ✅ Una sola conexión, sin sincronización, `users`/`areas` unificadas.
- ⚠️ `incidencias_imagenes_odt` requirió convertir `TEXT` → `JSONB`.
- ⚠️ `login_sessions` se vació (sesiones temporales).
- Respaldo previo en `backups/db_unification/20260520_123315`.

Detalle completo y pasos en [[Unificación BBDD]].
