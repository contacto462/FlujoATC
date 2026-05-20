# Login unico ATC

## Objetivo

Usar una sola puerta de entrada para todos los usuarios y redirigir al
`panel_selector` correcto segun el area principal (`user_areas.is_primary`).

## Regla de redireccion

- `soporte` -> Helpdesk `/panel`
- `servicio_tecnico` / `tecnicos` -> `panelSelectorServicio`
- `incidencias` -> `panelSelector`
- `coordinacion` / `protocolos` -> `panelSelectorCoordinacion`
- `venta` -> `/venta/panel-selector`
- `finanzas` -> `/venta/finanzas`
- `administracion` -> `/venta/administracion`
- `operaciones` -> `/venta/operaciones`

## Configuracion si las apps corren en puertos o dominios distintos

En `Incidencias/.env`:

```env
HELPDESK_BASE_URL=http://127.0.0.1:8000
```

En `ATC/.env`:

```env
INCIDENCIAS_PUBLIC_BASE_URL=http://127.0.0.1:8001
```

En la aplicacion unificada (`unified_main.py`) ambas variables pueden quedar
vacias porque todo corre en el mismo puerto y se usan rutas relativas.

## SSO hacia Helpdesk

Helpdesk expone:

```text
/sso/login?token=...
```

Ese endpoint valida `login_sessions.token`, crea la cookie web de Helpdesk y
redirige a `/panel`.
