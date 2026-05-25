# Aplicacion unificada ATC

## Entry point

La aplicacion modular unificada se levanta desde la raiz del repo:

```powershell
ATC\.venv\Scripts\python.exe -m uvicorn ATC.app.main:app --host 127.0.0.1 --port 8000
```

URL local:

```text
http://127.0.0.1:8000/
```

## Que hace

`ATC.app.main` es ahora la unica instancia FastAPI.
Helpdesk, Incidencias y Venta se registran como modulos dentro de esa app:

- `/`, `/venta/*`, `/servicio/*` y APIs operativas -> Incidencias/Venta.
- `/panel`, `/dashboard`, `/soporte`, `/tabla-soporte` y tickets -> Helpdesk.
- `/static/*` y `/uploads/*` se resuelven desde una capa comun con fallback entre carpetas.
- `/login` redirige al login unico `/?form=login&next=auto`.

`unified_main.py` queda como alias de compatibilidad y expone el mismo `app`.

## Importante

Los paquetes ya no deben depender del nombre generico `app`.
Los imports ahora son namespaced:

- `ATC.app...`
- `ATC.incidencias.app...`

Esto permite que todo conviva como un solo proyecto Python modular.
