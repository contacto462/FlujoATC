-- PostgreSQL schema sugerido para reemplazar Google Sheets.

CREATE TABLE IF NOT EXISTS bbdd_clientes (
  id BIGSERIAL PRIMARY KEY,
  cliente VARCHAR(255) NOT NULL UNIQUE,
  giro VARCHAR(255),
  direccion VARCHAR(255),
  region VARCHAR(120),
  comuna VARCHAR(120),
  contacto VARCHAR(255),
  correo VARCHAR(255),
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
  porcentaje_avance VARCHAR(20),
  foto_1 TEXT,
  foto_2 TEXT,
  foto_3 TEXT,
  pdf_url TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_registro_cliente ON registro(cliente);
CREATE INDEX IF NOT EXISTS idx_registro_estado ON registro(estado);
CREATE INDEX IF NOT EXISTS idx_registro_odt ON registro(odt);

CREATE TABLE IF NOT EXISTS odt_ventas (
  id BIGSERIAL PRIMARY KEY,
  odt VARCHAR(30) NOT NULL UNIQUE,
  observacion TEXT,
  servicio VARCHAR(255),
  tecnico VARCHAR(255),
  fecha TIMESTAMP,
  cliente VARCHAR(255),
  direccion VARCHAR(255),
  estado VARCHAR(100) NOT NULL DEFAULT 'En Proceso',
  observacion_final TEXT,
  avance VARCHAR(20),
  fecha_cierre TIMESTAMP,
  foto_url TEXT,
  acompanante VARCHAR(255),
  prioridad INTEGER
);

CREATE TABLE IF NOT EXISTS administracion_odt (
  id BIGSERIAL PRIMARY KEY,
  odt VARCHAR(30) NOT NULL UNIQUE,
  tecnico VARCHAR(255),
  acompanante VARCHAR(255),
  fecha_derivacion TIMESTAMP,
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
  folio BIGINT NOT NULL UNIQUE,
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

CREATE INDEX IF NOT EXISTS idx_rendiciones_folio ON rendiciones(folio);
CREATE INDEX IF NOT EXISTS idx_rendiciones_nrodoc ON rendiciones(nro_documento);
