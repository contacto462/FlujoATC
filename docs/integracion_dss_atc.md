# Integracion Dahua DSS con ATC

Esta integracion queda preparada para Dahua DSS Platform API V8.x. No requiere
credenciales reales para arrancar el servidor; si DSS no esta configurado, los
endpoints devuelven un error claro.

## Variables .env

Completar cuando Dahua, TI o el integrador entreguen los datos:

```env
DSS_BASE_URL=https://IP-O-DOMINIO:PUERTO
DSS_USERNAME=usuario_api
DSS_PASSWORD=clave_api
DSS_STATIC_TOKEN=
DSS_VERIFY_SSL=false
DSS_TIMEOUT_SEC=15
DSS_DEFAULT_PROTOCOL=https
DSS_DEFAULT_STREAM_TYPE=1
DSS_DEFAULT_RECORD_SOURCE=3
```

`DSS_STATIC_TOKEN` es opcional. Sirve para una prueba rapida si el DSS entrega
un token temporal. En uso normal se dejan `DSS_USERNAME` y `DSS_PASSWORD`.

## Datos por camara

La tabla `sucursal_camaras_monitoreo` ahora soporta:

- `dss_device_code`
- `dss_channel_id`
- `dss_channel_name`
- `dss_last_status`
- `dss_last_checked_at`

El dato principal es `dss_channel_id`, por ejemplo `1000001$1$0$0`. Si
`dss_device_code` viene vacio, ATC intenta derivarlo desde el texto antes del
primer `$`.

## Endpoints internos ATC

Todos requieren usuario de Bitacora. Configurar DSS por camara:

```http
PUT /api/bitacora/camaras/{camara_id}/dss/config
```

```json
{
  "deviceCode": "1000001",
  "channelId": "1000001$1$0$0",
  "channelName": "Caja 1"
}
```

Consultar estado online/offline:

```http
GET /api/bitacora/camaras/{camara_id}/dss/status
```

Capturar imagen actual:

```http
POST /api/bitacora/camaras/{camara_id}/dss/capture
```

Obtener URL de vivo:

```http
GET /api/bitacora/camaras/{camara_id}/dss/live-url?fmt=hls&protocol=https&streamType=1
```

Buscar grabaciones:

```http
GET /api/bitacora/camaras/{camara_id}/dss/records?startTime=1724700000&endTime=1724703600
```

Obtener playback HLS desde un resultado de busqueda:

```http
POST /api/bitacora/camaras/{camara_id}/dss/playback-hls
```

```json
{
  "startTime": "1724700000",
  "endTime": "1724703600",
  "streamId": "39977",
  "recordSource": 3,
  "recordType": 0,
  "streamType": 1,
  "protocol": "https"
}
```

Obtener RTSP de playback:

```http
POST /api/bitacora/camaras/{camara_id}/dss/playback-rtsp
```

```json
{
  "startTime": "1724700000",
  "endTime": "1724703600",
  "streamId": "112",
  "ssId": "100101@video#local",
  "recordSource": 2,
  "recordType": 1,
  "streamType": 1,
  "refer": 1
}
```
