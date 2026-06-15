---
tipo: modulo
area: venta
estado: activo
tags:
  - modulo
  - modulo/venta
actualizado: 2026-06-14
---

# Módulo · Venta

> [!abstract] Responsabilidad
> Flujo de venta ODS y paneles por área (finanzas, administración, operaciones). Vive junto a [[Incidencias]] en `ATC/incidencias/app/venta`.

## Rutas principales
- `/venta/panel-selector`
- `/venta/finanzas`
- `/venta/administracion`
- `/venta/operaciones`

La redirección por área se define en [[Login Único y SSO]].

## Tablas que usa
- `venta_ods`, `administracion_odt`, `rendiciones`
- `[[bbdd_clientes]]` y `bbdd_sucursales`

## Relacionado
- [[Incidencias]] · [[Visión General]]
