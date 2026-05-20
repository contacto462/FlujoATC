# Aplicacion unificada ATC

## Entry point

La aplicacion unificada se levanta desde la raiz del repo:

```powershell
ATC\.venv\Scripts\python.exe -m uvicorn unified_main:app --host 127.0.0.1 --port 8000
```

URL local:

```text
http://127.0.0.1:8000/
```

## Que hace

`unified_main.py` corre Helpdesk e Incidencias en el mismo proceso y puerto.
El despachador envia cada ruta al modulo correcto:

- `/`, `/venta/*`, `/servicio/*` y APIs operativas -> Incidencias/Venta.
- `/panel`, `/dashboard`, `/soporte`, `/tabla-soporte` y tickets -> Helpdesk.
- `/static/*` se resuelve entre ambos proyectos segun exista el archivo.
- `/login` redirige al login unico `/?form=login&next=auto`.

## Importante

Los paquetes ya no deben depender del nombre generico `app`.
Los imports ahora son namespaced:

- `ATC.app...`
- `Incidencias.app...`

Esto permite que ambos proyectos convivan dentro del mismo interprete Python.

