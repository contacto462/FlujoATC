# Mapa rapido de `ATC/app/routes/incidencias.py`

Ultima revision: 2026-08-21. Archivo medido: 4492 lineas.

Objetivo: evitar leer completo `ATC/app/routes/incidencias.py` en cada cambio. Para trabajar sobre
este router, partir por este mapa y luego abrir solo rangos acotados.

## Regla de trabajo

1. Buscar primero por ruta, funcion o palabra clave:

   ```bash
   rg -n "texto_o_endpoint" ATC/app/routes/incidencias.py ATC/app/services/incidencias_service.py ATC/app/templates
   rg -n "^@router\\.|^def |^class " ATC/app/routes/incidencias.py
   ```

2. Leer solo el rango necesario:

   ```bash
   sed -n "LINEA_INICIO,LINEA_FINp" ATC/app/routes/incidencias.py
   ```

3. No abrir el archivo completo salvo que el mapa y las busquedas dirigidas no basten.
4. Si agregas, borras o mueves bloques grandes/rutas en este archivo, actualiza este mapa.

## Dependencias y limites importantes

- Router FastAPI: `router = APIRouter()` se define al inicio del archivo.
- Servicio principal: `IncidenciasService` desde `ATC/app/services/incidencias_service.py`.
- Servicio de protocolos: `ProtocolosService` desde `ATC/app/services/protocolos_service.py`.
- DB activa: SQL Server via `get_db`. `get_incidencias_db` es alias historico de la misma sesion.
- `get_client_internal_notes` y `add_client_internal_note` no tienen decorador `@router`: los usa
  `ATC/app/modules/client_notes.py`. No borrarlos ni agregar decoradores sin revisar ese despachador.
- El bloque de pruebas de sonido ocupa gran parte final del archivo; al tocarlo, buscar tambien en
  templates y servicios relacionados.
- Pruebas de sonido tiene 3 resultados posibles: `exitoso`, `falla` (crea incidencia, sin email) y
  `no_coordinacion` (no crea incidencia, envia email). Los emails de `exitoso` y `no_coordinacion`
  llevan copia oculta fija a `tahira.riquelme.atc@gmail.com` (`_CC_PRUEBAS_SONIDO`).
- `DELETE /api/pruebas-sonido/{id}` ("Deshacer" en el frontend): si la prueba era `falla` y la
  incidencia que generó sigue sin finalizar (mismo chequeo `finalizada` que usa el listado de
  sucursales — `fecha_cierre` o estado/derivacion con termin/final/solucion/resuelt), borra tambien
  esa incidencia. Si ya fue trabajada/cerrada, no se toca.

## Indice por rangos

| Rango aproximado | Tema |
| --- | --- |
| 1-161 | Imports, constantes, configuracion global, `router`, setup inicial. |
| 162-365 | Startup y helpers de bootstrap/schema SQL para relaciones base. |
| 366-585 | Helpers de notas internas de cliente para soporte/ticketera. |
| 586-877 | Helpers `_ensure_*` para columnas opcionales, identidad y vistas. |
| 878-885 | Dependencias FastAPI: `get_service`, `get_protocolos_service`. |
| 886-997 | Creacion de ticket oficina ATC y worker semanal de protocolos. |
| 998-1352 | Resolucion de token, pagina raiz `/`, login SSO/panel selector. |
| 1353-1385 | `/api/login` y `/api/logout`. |
| 1386-1459 | `/resumen-equipos-tecnicos` y helpers de mantenciones Vina. |
| 1460-1554 | Mantenciones Vina del Mar: pagina, tecnico, total, pendiente. |
| 1582-1669 | `/tabla-soporte`, listas, catalogos, registros, tecnico externo. |
| 1674-1735 | Consultas de incidencias por puesto, servicio tecnico, coordinacion, sucursal. |
| 1736-1924 | Imagenes, informes ODT cierre, Drive ODT y uploads de cierre/apertura. |
| 1932-2367 | Creacion/cierre/finalizacion/derivacion/edicion de incidencias. |
| 2371-2459 | Mantenciones, plantillas y contactos/sucursales. |
| 2465-2551 | Camaras por sucursal, contacto cliente, clientes soporte, tipos de tareas. |
| 2555-2664 | Protocolos: listas, registros, reportes, informes, envio/rechazo/borrado. |
| 2674-2927 | Derivaciones, coordinacion, rendiciones y finanzas. |
| 2931-2943 | Planificacion y debug DB. |
| 3005-3225 | Indicadores de servicio y generacion de informe. |
| 3256-4492 | Pruebas de sonido: sucursales, helpers internos, registro (exitoso/falla/no_coordinacion) y borrado (auto-borra incidencia de falla no finalizada). |

## Rutas principales

### Paginas

- `GET /` -> `do_get`
- `GET /resumen-equipos-tecnicos` -> `resumen_equipos_tecnicos_page`
- `GET /servicio-tecnico/mantenciones-vina-del-mar` -> `mantenciones_vina_del_mar_page`
- `GET /tabla-soporte` -> `tabla_soporte_local_page`
- `GET /tecnico-externo` -> `tecnico_externo_page`
- `GET /servicio/indicadores` -> `servicio_indicadores_page`

### Login

- `POST /api/login` -> `check_login`
- `POST /api/logout` -> `logout`

### Incidencias

- `GET /api/incidencias/puesto`
- `GET /api/incidencias/servicio-tecnico`
- `GET /api/incidencias/coordinacion`
- `GET /api/sucursal/detalle`
- `GET /api/sucursal/incidencias`
- `POST /api/formulario`
- `POST /api/incidencias/nueva`
- `POST /api/incidencias/multiples`
- `POST /api/incidencias/cerrar`
- `POST /api/incidencias/finalizar-completo`
- `POST /api/incidencias/finalizar-completo-archivos`
- `POST /api/incidencias/cierre-instalacion`
- `POST /api/incidencias/cierre-instalacion-archivos`
- `POST /api/incidencias/cierre-mantencion`
- `POST /api/incidencias/iniciar-trabajo`
- `POST /api/incidencias/en-proceso`
- `POST /api/incidencias/derivar-tecnico`
- `PATCH /api/incidencias/editar-tabla`
- `GET /api/incidencias/odt/{odt}/observacion-cierre`
- `PATCH /api/incidencias/regenerar-informe-cierre`
- `POST /api/incidencias/cerrar-encargado`

### Imagenes, Drive e informes

- `GET /api/incidencias/imagenes`
- `GET /api/incidencias/imagenes-tabla`
- `GET /api/incidencias/informes-odt-cierre`
- `GET /api/incidencias/drive-odt/listar`
- `GET /api/incidencias/drive-odt/buscar`
- `GET /api/incidencias/drive-image/{file_id}`
- `POST /api/incidencias/upload-image-tabla`
- `POST /api/incidencias/cierre-apertura/imagen`
- `GET /api/incidencias/cierre-apertura/imagenes`

### Mantenciones y servicio tecnico

- `PATCH /api/servicio-tecnico/mantenciones-vina-del-mar/{odt}/tecnico`
- `PATCH /api/servicio-tecnico/mantenciones-vina-del-mar/total`
- `POST /api/servicio-tecnico/mantenciones-vina-del-mar/{odt}/pendiente`
- `GET /api/mantencion/sucursales`
- `POST /api/mantencion/correctiva`
- `GET /api/mantencion/programada/plantilla`
- `POST /api/mantencion/programada/plantilla`
- `POST /api/mantencion/programada/plantilla-desde-odt`

### Listas y catalogos

- `GET /api/login/usuarios`
- `GET /api/listas/bbdd`
- `GET /api/materiales/buscar`
- `GET /api/listas/incidencias`
- `GET /api/catalogo-clientes`
- `GET /api/registros`
- `GET /api/registros/administracion`
- `GET /api/sucursales/por-cliente`
- `GET /api/contactos/sucursal`
- `GET /api/incidencias/camaras-por-sucursal`
- `POST /api/contacto-cliente/enviar-info`
- `GET /api/clientes-soporte`
- `GET /api/tareas/tipos`
- `GET /api/tecnicos/pendientes`
- `GET /api/tecnicos/ruta-optima`

### Protocolos

- `GET /api/protocolos/listas`
- `POST /api/protocolos/registro`
- `GET /api/protocolos/registros`
- `POST /api/protocolos/reportes/semanal/ejecutar`
- `GET /api/protocolos/informes`
- `GET /api/protocolos/informes/{informe_id}/contactos`
- `POST /api/protocolos/informes/{informe_id}/enviar`
- `POST /api/protocolos/informes/{informe_id}/rechazar`
- `DELETE /api/protocolos/informes/{informe_id}`

### Coordinacion, rendiciones y finanzas

- `GET /api/derivaciones`
- `POST /api/coordinacion/finalizar`
- `POST /api/coordinacion/observacion-final`
- `POST /api/coordinacion/enviar-correo`
- `POST /api/rendiciones`
- `GET /api/rendiciones/url`
- `GET /api/rendiciones/duplicado`
- `POST /api/rendiciones/upload-boleta`
- `GET /api/rendiciones`
- `GET /api/rendiciones/exportar`
- `PATCH /api/rendiciones/{rendicion_id}/monto`
- `PATCH /api/rendiciones/{rendicion_id}`
- `GET /api/finanzas/consolidado`
- `GET /api/finanzas/viatico-especial`
- `PATCH /api/finanzas/viatico-cap/{codigo}`
- `GET /api/finanzas/pagos`
- `POST /api/finanzas/pagos`
- `PATCH /api/finanzas/pagos/{pago_id}`
- `DELETE /api/finanzas/pagos/{pago_id}`
- `GET /api/finanzas/suma-pagos`

### Indicadores y pruebas de sonido

- `GET /api/servicio/kpis-data`
- `GET /servicio/indicadores/informe`
- `GET /api/pruebas-sonido/sucursales`
- `POST /api/pruebas-sonido`
- `DELETE /api/pruebas-sonido/{prueba_id}`

## Comandos utiles

```bash
# Ver solo rutas registradas en este router
rg -n "^@router\\." ATC/app/routes/incidencias.py

# Ver solo funciones/clases y ubicacion
rg -n "^def |^class " ATC/app/routes/incidencias.py

# Buscar una ruta o campo en router, servicio y templates
rg -n "derivar-tecnico|tecnicos_pendientes|Pendientes Prioritarios" \
  ATC/app/routes/incidencias.py \
  ATC/app/services/incidencias_service.py \
  ATC/app/templates

# Validacion Python si se toca codigo
PYTHONPYCACHEPREFIX=/private/tmp/atc_compile_cache python3 -m compileall ATC/app
```
