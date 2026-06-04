-- PostgreSQL schema sugerido para reemplazar Google Sheets.

CREATE TABLE IF NOT EXISTS bbdd_clientes (
  id BIGSERIAL PRIMARY KEY,
  cliente VARCHAR(255) NOT NULL UNIQUE,
  giro VARCHAR(255),
  direccion VARCHAR(255),
  region VARCHAR(120),
  comuna VARCHAR(120),
  rut VARCHAR(40),
  email_facturas VARCHAR(255),
  nombre_representante VARCHAR(255),
  rut_representante VARCHAR(40),
  telefono VARCHAR(32),
  email_representante VARCHAR(255),
  ejecutivo_email VARCHAR(255),
  fecha_creacion TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS catalogo_clientes (
  id BIGSERIAL PRIMARY KEY,
  cliente VARCHAR(255) NOT NULL UNIQUE,
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_catalogo_clientes_cliente ON catalogo_clientes(cliente);

CREATE TABLE IF NOT EXISTS registro (
  id BIGSERIAL PRIMARY KEY,
  odt VARCHAR(30) NOT NULL UNIQUE,
  fecha_registro TIMESTAMP NOT NULL DEFAULT NOW(),
  puesto VARCHAR(80),
  cliente VARCHAR(255) NOT NULL,
  problema VARCHAR(255) NOT NULL,
  detalle_problema TEXT,
  derivacion VARCHAR(120) NOT NULL DEFAULT 'Servicio Técnico',
  observacion TEXT,
  tecnicos VARCHAR(255),
  acompanante VARCHAR(255),
  estado VARCHAR(100) NOT NULL DEFAULT 'Pendiente',
  dias_ejecucion INTEGER,
  fecha_cierre TIMESTAMP,
  fecha_derivacion_area TIMESTAMP,
  fecha_derivacion_tecnico TIMESTAMP,
  direccion VARCHAR(255),
  observacion_final TEXT,
  prioridad INTEGER,
  materiales TEXT,
  responsable_cierre VARCHAR(40),
  causa_cierre VARCHAR(120),
  accion_cierre VARCHAR(120),
  resultado_cierre VARCHAR(120),
  pruebas_cierre TEXT,
  requiere_seguimiento BOOLEAN,
  porcentaje_avance VARCHAR(20),
  foto_1 TEXT,
  foto_2 TEXT,
  foto_3 TEXT,
  drive_cierre_folder_id VARCHAR(255),
  drive_cierre_folder_url TEXT,
  pdf_url TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_registro_cliente ON registro(cliente);
CREATE INDEX IF NOT EXISTS idx_registro_estado ON registro(estado);
CREATE INDEX IF NOT EXISTS idx_registro_odt ON registro(odt);

CREATE TABLE IF NOT EXISTS administracion_odt (
  id BIGSERIAL PRIMARY KEY,
  odt VARCHAR(30) NOT NULL UNIQUE,
  tecnico VARCHAR(255),
  acompanante VARCHAR(255),
  fecha_derivacion TIMESTAMP,
  recepcion_info BOOLEAN NOT NULL DEFAULT FALSE,
  fecha_recepcion_info TIMESTAMP,
  registro_alpha3 BOOLEAN NOT NULL DEFAULT FALSE,
  fecha_registro_alpha3 TIMESTAMP,
  registro_intranet BOOLEAN NOT NULL DEFAULT FALSE,
  fecha_registro_intranet TIMESTAMP,
  envio_solicitud_instalacion BOOLEAN NOT NULL DEFAULT FALSE,
  fecha_envio_solicitud_instalacion TIMESTAMP,
  envio_datos_facturacion BOOLEAN NOT NULL DEFAULT FALSE,
  fecha_envio_datos_facturacion TIMESTAMP,
  envio_carta_bienvenida BOOLEAN NOT NULL DEFAULT FALSE,
  fecha_envio_carta_bienvenida TIMESTAMP,
  finalizado BOOLEAN NOT NULL DEFAULT FALSE,
  fecha_cierre TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
  );

  CREATE TABLE IF NOT EXISTS finanzas_odt (
    id BIGSERIAL PRIMARY KEY,
    odt VARCHAR(30) NOT NULL UNIQUE,
    fecha_inicio_servicio VARCHAR(40),
    recepcion_datos_facturacion BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_recepcion_datos_facturacion TIMESTAMP,
    creacion_clientes_piriod BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_creacion_clientes_piriod TIMESTAMP,
    creacion_clientes_bd BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_creacion_clientes_bd TIMESTAMP,
    facturacion_instalacion BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_facturacion_instalacion TIMESTAMP,
    facturacion_servicio BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_facturacion_servicio TIMESTAMP,
    finalizado BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_cierre TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
  );

  CREATE TABLE IF NOT EXISTS servicio_tecnico_ventas_odt (
    id BIGSERIAL PRIMARY KEY,
    odt VARCHAR(30) NOT NULL UNIQUE,
    recepcion_solicitud_instalacion BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_recepcion_solicitud_instalacion TIMESTAMP,
    llamar_cliente TEXT,
    solicitud_materiales TEXT,
    fecha_inicio_instalacion VARCHAR(40),
    fecha_fin_instalacion VARCHAR(40),
    tecnico_a_cargo VARCHAR(255),
    acompanante VARCHAR(255),
    instalacion_finalizada BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_instalacion_finalizada TIMESTAMP,
    finalizado BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_cierre TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
  );
  
  CREATE TABLE IF NOT EXISTS contactos_emergencia (
    id BIGSERIAL PRIMARY KEY,
    sucursal VARCHAR(255) NOT NULL,
  nombre VARCHAR(255),
  celular VARCHAR(80),
  email VARCHAR(255),
  prioridad VARCHAR(80)
);

CREATE INDEX IF NOT EXISTS idx_contactos_sucursal ON contactos_emergencia(sucursal);

CREATE TABLE IF NOT EXISTS registros_correos_cliente (
  id BIGSERIAL PRIMARY KEY,
  odt VARCHAR(30) NOT NULL,
  sucursal VARCHAR(255) NOT NULL,
  fecha_envio TIMESTAMP NOT NULL DEFAULT NOW(),
  observacion TEXT,
  estado VARCHAR(100)
);

CREATE INDEX IF NOT EXISTS idx_reg_correo_odt ON registros_correos_cliente(odt);

CREATE TABLE IF NOT EXISTS login_sessions (
  token VARCHAR(120) PRIMARY KEY,
  usuario VARCHAR(255) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_login_usuario ON login_sessions(usuario);
CREATE INDEX IF NOT EXISTS idx_login_exp ON login_sessions(expires_at);

CREATE TABLE IF NOT EXISTS sync_outbox (
  id BIGSERIAL PRIMARY KEY,
  event_type VARCHAR(80) NOT NULL,
  entity_key VARCHAR(80) NOT NULL,
  payload_json TEXT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
  sent_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sync_outbox_status ON sync_outbox(status);
CREATE INDEX IF NOT EXISTS idx_sync_outbox_event_key ON sync_outbox(event_type, entity_key);

CREATE TABLE IF NOT EXISTS tareas (
  id BIGSERIAL PRIMARY KEY,
  codigo VARCHAR(30) NOT NULL UNIQUE,
  usuario_soporte VARCHAR(255) NOT NULL,
  fecha_creacion TIMESTAMP NOT NULL DEFAULT NOW(),
  cliente VARCHAR(255) NOT NULL,
  tipo_tarea VARCHAR(255) NOT NULL,
  especificacion VARCHAR(255) NOT NULL,
  descripcion TEXT NOT NULL,
  solicitante VARCHAR(255),
  estado VARCHAR(80) NOT NULL DEFAULT 'Pendiente',
  tecnico_cierre VARCHAR(255),
  fecha_cierre TIMESTAMP,
  dias_ejecucion INTEGER
);

CREATE TABLE IF NOT EXISTS rendiciones (
  id BIGSERIAL PRIMARY KEY,
  codigo_diario VARCHAR(120) NOT NULL,
  fecha_registro TIMESTAMP NOT NULL DEFAULT NOW(),
  tecnico VARCHAR(255) NOT NULL,
  mail VARCHAR(255),
  odt VARCHAR(30) NOT NULL,
  cliente VARCHAR(255) NOT NULL,
  comuna VARCHAR(255),
  tipo_gasto VARCHAR(120) NOT NULL,
  tipo_documento VARCHAR(120) NOT NULL,
  nro_documento VARCHAR(120) NOT NULL,
  fecha_documento TIMESTAMP NOT NULL,
  monto_total NUMERIC(14,2) NOT NULL,
  descripcion TEXT,
  url_boleta TEXT,
  url_informe TEXT,
  documento VARCHAR(255),
  estado_revision VARCHAR(30) NOT NULL DEFAULT 'Pendiente'
);

CREATE INDEX IF NOT EXISTS idx_rendiciones_nrodoc ON rendiciones(nro_documento);

CREATE TABLE IF NOT EXISTS rendiciones_pagos (
  id BIGSERIAL PRIMARY KEY,
  codigo_diario VARCHAR(120) NOT NULL,
  tecnico VARCHAR(255) NOT NULL,
  rut_tecnico VARCHAR(30),
  tipo_pago VARCHAR(60) NOT NULL DEFAULT 'Transferencia',
  fecha_pago TIMESTAMP NOT NULL,
  monto NUMERIC(14,2) NOT NULL,
  creado_por VARCHAR(255),
  fecha_registro TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rend_pagos_codigo ON rendiciones_pagos(codigo_diario);
CREATE INDEX IF NOT EXISTS idx_rend_pagos_tecnico ON rendiciones_pagos(tecnico);

CREATE TABLE IF NOT EXISTS rendiciones_viatico_cap (
  codigo_diario VARCHAR(120) PRIMARY KEY,
  viatico_max NUMERIC(14,2) NOT NULL DEFAULT 10000,
  updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_by VARCHAR(255)
);
