---
tipo: modulo
area: incidencias
estado: activo
tags:
  - modulo
  - modulo/incidencias
actualizado: 2026-06-15
---

# Módulo · Incidencias

> [!abstract] Responsabilidad
> Operación de incidencias y servicio técnico, migrado de Google Apps Script (Sheets → SQL). Código unificado en `ATC/app/` (ya no existe `ATC/incidencias/`). Incluye también el flujo de [[Venta]].

## Stack
- Python + FastAPI · SQLAlchemy ORM · PostgreSQL (base `ATC`)

## Estructura del código
- `ATC/app/routes/incidencias.py` — router principal (`APIRouter`, equivalente al antiguo `doGet` + endpoints API)
- `ATC/app/services/incidencias_service.py` — lógica de negocio migrada desde GAS
- `ATC/app/services/protocolos_service.py`, `ATC/app/services/incidencias_drive_report_service.py`
- `ATC/app/models/incidencias.py` — tablas SQL (reemplazo de hojas)
- `ATC/app/templates/` — templates de incidencias (unificados con Helpdesk y Venta)

## Tablas (antes Sheets)
- `[[registro]]` (hoja `Registro`)
- `bbdd_clientes` (hoja `BBDD`)
- `venta_ods` (flujo ODS Venta)
- `administracion_odt` (hoja `Administración`)
- `rendiciones` (hoja `Rendición`)
- `tareas` (hoja `Tareas`)
- `contactos_emergencia`, `registros_correos_cliente`
- `login_sessions` (cache/token de sesión)
- `[[incidencias]]` (operativas) e `incidencias_imagenes_odt`

## Estado de la migración
- `doGet` y enrutamiento principal: ✅ migrado
- Login por token: ✅ migrado a SQL → ver [[Login Único y SSO]]
- Registro, cierre y consulta de incidencias: ✅ migrado
- Planificación consolidada (incidencias + ventas): ✅ migrado
- Tareas soporte y rendiciones base: ✅ migrado
- Correo / Drive / Telegram: integración externa (SMTP/API) según credenciales del entorno

> [!note] Python en este equipo
> El README original asume Windows (`.venv\Scripts\activate`). En macOS se usa `.venv-backend` con Python **3.14.5** — ver [[Levantar Servidor]].

---
> Migrado de `ATC/incidencias/README.md` (limpiado de marcadores de conflicto de Git).
