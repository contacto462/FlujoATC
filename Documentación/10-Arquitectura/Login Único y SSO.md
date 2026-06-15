---
tipo: arquitectura
area: plataforma
estado: activo
tags:
  - arquitectura
  - modulo/helpdesk
  - seguridad
actualizado: 2026-06-14
---

# Login Único y SSO

> [!abstract] Objetivo
> Una sola puerta de entrada para todos los usuarios, que redirige al `panel_selector` correcto según el área principal (`user_areas.is_primary`). Ver [[App Unificada]].

## Regla de redirección

| Área principal | Destino |
|---|---|
| `soporte` | Helpdesk `/panel` |
| `servicio_tecnico` / `tecnicos` | `panelSelectorServicio` |
| `incidencias` | `panelSelector` |
| `coordinacion` / `protocolos` | `panelSelectorCoordinacion` |
| `venta` | `/venta/panel-selector` |
| `finanzas` | `/venta/finanzas` |
| `administracion` | `/venta/administracion` |
| `operaciones` | `/venta/operaciones` |

El área principal vive en `[[user_areas]]` (`is_primary`).

## Configuración si las apps corren en puertos/dominios distintos

En `Incidencias/.env`:

```env
HELPDESK_BASE_URL=http://127.0.0.1:8000
```

En `ATC/.env`:

```env
INCIDENCIAS_PUBLIC_BASE_URL=http://127.0.0.1:8001
```

En la app unificada ambas pueden quedar vacías porque todo corre en el mismo puerto y se usan rutas relativas.

## SSO hacia Helpdesk

Helpdesk expone:

```text
/sso/login?token=...
```

Ese endpoint valida `login_sessions.token`, crea la cookie web de Helpdesk y redirige a `/panel`.

---
> Migrado de `docs/login_unico.md`.
