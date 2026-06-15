# Migración GAS -> Python + SQL

Este proyecto adapta tu Google Apps Script a Python con FastAPI, reemplazando Google Sheets por SQL.

## Stack
- Python + FastAPI
- SQLAlchemy ORM
- PostgreSQL (o SQLite para pruebas rápidas)

## Estructura
- `app/main.py`: router principal (`doGet` equivalente + endpoints API)
- `app/services.py`: lógica de negocio migrada desde GAS
- `app/models.py`: tablas SQL (reemplazo de hojas)
- `sql/schema.sql`: DDL SQL explícito

## Ejecutar
1. Usar el entorno del repo con Python 3.14.5:
   - `./.venv-backend/bin/python --version`
2. Instalar dependencias:
   - `./.venv-backend/bin/python -m pip install --upgrade pip setuptools wheel`
   - `./.venv-backend/bin/python -m pip install --prefer-binary -r requirements.txt`
3. Opcional `.env`:
   - `DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/incidencias`
4. Iniciar API:
   - `./.venv-backend/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001`

## Nota Python
- Recomendado: Python `3.14.5`.
- Si recreas el entorno, usa la misma versión del repo para evitar diferencias en SQLAlchemy/Pydantic.

## Tablas (antes Sheets)
- `registro` (hoja `Registro`)
- `bbdd_clientes` (hoja `BBDD`)
- `venta_ods` (flujo actual ODS Venta)
- `administracion_odt` (hoja `Administración`)
- `rendiciones` (hoja `Rendición`)
- `tareas` (hoja `Tareas`)
- `contactos_emergencia` (hoja `ContactosEmergencia`)
- `registros_correos_cliente` (hoja `RegistrosCorreosCliente`)
- `login_sessions` (cache/token de sesión)

## Estado de la migración
- `doGet` y enrutamiento principal: migrado.
- Login por token: migrado a SQL.
- Registro, cierre y consulta de incidencias: migrado.
- Planificación consolidada (incidencias + ventas): migrado.
- Tareas soporte: migrado.
- Rendiciones base: migrado.
- Correo/Drive/Telegram: dejar por integración externa (SMTP/API) según credenciales del entorno.
