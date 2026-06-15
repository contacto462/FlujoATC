---
tipo: modulo
area: incidencias
estado: activo
tags:
  - modulo
  - modulo/incidencias
actualizado: 2026-06-14
---

# Módulo · Incidencias

> [!abstract] Responsabilidad
> Operación de incidencias y servicio técnico, migrado de Google Apps Script (Sheets → SQL). Código en `ATC/incidencias/app`. Incluye también el flujo de [[Venta]].

## Stack
- Python + FastAPI · SQLAlchemy ORM · PostgreSQL (base `ATC`)

## Estructura del código
- `app/main.py` — router principal (equivalente a `doGet` + endpoints API)
- `app/services.py` — lógica de negocio migrada desde GAS
- `app/models.py` — tablas SQL (reemplazo de hojas)
- `app/protocolos_service.py`, `app/drive_report_service.py`
- `sql/schema.sql` — DDL explícito

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
