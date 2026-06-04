# Helpdesk Receiver (API)

Este archivo describe como levantar un receptor en el servidor del sistema Helpdesk para recibir incidencias desde la app "Incidencias".

## 1) Ejecutar receptor

Copiar `integration/helpdesk_sync_receiver.py` al servidor/app de Helpdesk y ejecutar:

```powershell
python -m uvicorn helpdesk_sync_receiver:app --host 0.0.0.0 --port 9000
```

## 2) Variables de entorno en Helpdesk

```env
DATABASE_URL=postgresql+psycopg://postgres:TU_CLAVE@localhost:5432/helpdesk
DB_SCHEMA=public
DB_TABLE=incidencias
INCIDENCIAS_SYNC_TOKEN=tu_token_compartido
```

## 3) Configurar app Incidencias (emisor)

En `.env` del proyecto Incidencias:

```env
SUPPORT_SYNC_MODE=api
SUPPORT_SYNC_API_URL=http://127.0.0.1:9000/api/incidencias/sync
SUPPORT_SYNC_API_TOKEN=tu_token_compartido
SUPPORT_SYNC_TIMEOUT_SEC=10
```

## 4) Verificar

1. Crear incidencia en app Incidencias.
2. Revisar outbox:
   - `GET /api/sync/soporte/outbox`
3. Debe quedar en `status = sent`.
4. Confirmar en SQL Helpdesk:

```sql
SELECT * FROM public.incidencias ORDER BY id DESC LIMIT 20;
```

