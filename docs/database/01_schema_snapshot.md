# Snapshot del esquema PostgreSQL

Fecha de inspeccion: 2026-05-19 18:38:03

## Resumen
Se inspeccionaron `DATABASE_URL` y `INCIDENCIAS_DATABASE_URL` desde `ATC/.env`. No se ejecutaron cambios sobre PostgreSQL; solo lectura de catalogo y metadatos.

### Base `ATC/helpdesk`
- Conexion: `postgresql://***:***@localhost:5432/helpdesk`
- Estado: OK
- Base real: `helpdesk`
- Version: `PostgreSQL 18.2 on x86_64-windows`
- Tablas detectadas: 18

### Base `Incidencias`
- Conexion: `postgresql://***:***@localhost:5432/incidencias`
- Estado: OK
- Base real: `incidencias`
- Version: `PostgreSQL 18.2 on x86_64-windows`
- Tablas detectadas: 28

## Lista de tablas
### `ATC/helpdesk`
- Schema `public`: `automation_logs`, `email_sync_states`, `incidencias_cierres`, `incidencias_imagenes`, `incidencias_imagenes_odt`, `incidencias_tecnicos`, `internal_chat_messages`, `internal_chat_read_states`, `messages`, `requesters`, `ticket_alert_read_states`, `ticket_assignment_history`, `ticket_internal_note_read_states`, `ticket_message_read_states`, `ticket_sla_feedback`, `ticket_sla_feedback_events`, `tickets`, `users`

### `Incidencias`
- Schema `public`: `administracion_odt`, `bbdd_clientes`, `bbdd_sucursales`, `catalogo_clientes`, `contactos_emergencia`, `finanzas_odt`, `incidencias_cierres`, `incidencias_data`, `incidencias_imagenes_odt`, `login_sessions`, `mantenciones_imagenes_sucursal`, `operaciones_venta_odt`, `protocolos_informes`, `protocolos_registro`, `registro`, `registros_correos_cliente`, `rendiciones`, `servicio_tecnico_ventas_odt`, `sesiones_tecnico`, `sucursal_contactos_emergencia`, `sucursal_guardias`, `sucursal_personas_autorizadas`, `sync_outbox`, `tareas`, `users`, `venta_clientes`, `venta_ods`, `venta_ods_archivos`

## Detalle por tabla
# Base `ATC/helpdesk`
## Schema `public`
### `automation_logs`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".automation_logs_id_seq'::regclass)` |
| `ticket_id` | `INTEGER` | NULL |  |
| `rule_key` | `VARCHAR(100)` | NOT NULL |  |
| `event_name` | `VARCHAR(100)` | NOT NULL |  |
| `status` | `VARCHAR(20)` | NOT NULL |  |
| `details` | `JSON` | NOT NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `automation_logs_pkey` (`id`)
- FKs:
  - `automation_logs_ticket_id_fkey`: `ticket_id` -> `tickets.id`
- Unique constraints: no declaradas.
- Indices:
  - `ix_automation_logs_event_name` (`event_name`)
  - `ix_automation_logs_rule_key` (`rule_key`)
  - `ix_automation_logs_ticket_id` (`ticket_id`)
- Check constraints: no declaradas.

### `email_sync_states`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `mailbox_key` | `VARCHAR(255)` | NOT NULL |  |
| `last_uid` | `BIGINT` | NOT NULL |  |
| `uid_validity` | `VARCHAR(255)` | NULL |  |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `email_sync_states_pkey` (`mailbox_key`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices: no secundarios detectados.
- Check constraints: no declaradas.

### `incidencias_cierres`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `BIGINT` | NOT NULL | `nextval('"public".incidencias_cierres_id_seq'::regclass)` |
| `incidencia_id` | `BIGINT` | NOT NULL |  |
| `odt` | `VARCHAR(80)` | NULL |  |
| `observacion` | `TEXT` | NULL |  |
| `cerrado_por` | `VARCHAR(180)` | NULL |  |
| `cerrado_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `incidencias_cierres_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices: no secundarios detectados.
- Check constraints: no declaradas.

### `incidencias_imagenes`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `BIGINT` | NOT NULL | `nextval('"public".incidencias_imagenes_id_seq'::regclass)` |
| `odt` | `VARCHAR(80)` | NOT NULL |  |
| `sucursal` | `VARCHAR(255)` | NULL |  |
| `imagenes` | `JSONB` | NOT NULL | `'[]'::jsonb` |
| `created_by` | `VARCHAR(180)` | NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `incidencias_imagenes_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints:
  - `incidencias_imagenes_odt_key` (`odt`)
- Indices:
  - `idx_incidencias_imagenes_odt` (`odt`)
  - `incidencias_imagenes_odt_key` UNIQUE (`odt`)
  - `uq_incidencias_imagenes_odt` UNIQUE (`odt`)
- Check constraints: no declaradas.

### `incidencias_imagenes_odt`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `BIGINT` | NOT NULL | `nextval('"public".incidencias_imagenes_odt_id_seq'::regclass)` |
| `odt` | `VARCHAR(80)` | NOT NULL |  |
| `sucursal` | `VARCHAR(255)` | NULL |  |
| `imagenes` | `JSONB` | NOT NULL | `'[]'::jsonb` |
| `created_by` | `VARCHAR(180)` | NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `incidencias_imagenes_odt_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints:
  - `incidencias_imagenes_odt_odt_key` (`odt`)
- Indices:
  - `incidencias_imagenes_odt_odt_key` UNIQUE (`odt`)
  - `uq_incidencias_imagenes_odt_odt` UNIQUE (`odt`)
- Check constraints: no declaradas.

### `incidencias_tecnicos`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `BIGINT` | NOT NULL | `nextval('"public".incidencias_tecnicos_id_seq'::regclass)` |
| `nombre` | `VARCHAR(180)` | NOT NULL |  |
| `activo` | `BOOLEAN` | NOT NULL | `true` |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `incidencias_tecnicos_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints:
  - `incidencias_tecnicos_nombre_key` (`nombre`)
- Indices:
  - `incidencias_tecnicos_nombre_key` UNIQUE (`nombre`)
- Check constraints: no declaradas.

### `internal_chat_messages`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".internal_chat_messages_id_seq'::regclass)` |
| `sender_id` | `INTEGER` | NULL |  |
| `content` | `TEXT` | NOT NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `internal_chat_messages_pkey` (`id`)
- FKs:
  - `internal_chat_messages_sender_id_fkey`: `sender_id` -> `users.id`
- Unique constraints: no declaradas.
- Indices:
  - `ix_internal_chat_messages_sender_id` (`sender_id`)
- Check constraints: no declaradas.

### `internal_chat_read_states`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `user_id` | `INTEGER` | NOT NULL |  |
| `last_seen_message_id` | `INTEGER` | NOT NULL |  |
| `updated_at` | `TIMESTAMP` | NULL | `now()` |

- PK: `internal_chat_read_states_pkey` (`user_id`)
- FKs:
  - `internal_chat_read_states_user_id_fkey`: `user_id` -> `users.id`
- Unique constraints: no declaradas.
- Indices: no secundarios detectados.
- Check constraints: no declaradas.

### `messages`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".messages_id_seq'::regclass)` |
| `ticket_id` | `INTEGER` | NOT NULL |  |
| `sender_type` | `VARCHAR(20)` | NOT NULL |  |
| `sender_id` | `INTEGER` | NULL |  |
| `channel` | `VARCHAR(20)` | NOT NULL |  |
| `content` | `TEXT` | NOT NULL |  |
| `external_id` | `VARCHAR(255)` | NULL |  |
| `is_internal_note` | `BOOLEAN` | NOT NULL | `false` |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `sender_name` | `VARCHAR(255)` | NULL |  |
| `sender_email` | `VARCHAR(320)` | NULL |  |

- PK: `messages_pkey` (`id`)
- FKs:
  - `messages_sender_id_fkey`: `sender_id` -> `users.id`
  - `messages_ticket_id_fkey`: `ticket_id` -> `tickets.id`
- Unique constraints: no declaradas.
- Indices:
  - `ix_messages_external_id` (`external_id`)
  - `ix_messages_sender_id` (`sender_id`)
  - `ix_messages_ticket_id` (`ticket_id`)
- Check constraints: no declaradas.

### `requesters`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".requesters_id_seq'::regclass)` |
| `email` | `VARCHAR(255)` | NULL |  |
| `name` | `VARCHAR(100)` | NOT NULL |  |
| `notes` | `TEXT` | NULL |  |
| `internal_name` | `VARCHAR(120)` | NULL |  |

- PK: `requesters_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices: no secundarios detectados.
- Check constraints: no declaradas.

### `ticket_alert_read_states`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `user_id` | `INTEGER` | NOT NULL |  |
| `last_seen_ticket_id` | `INTEGER` | NOT NULL |  |
| `updated_at` | `TIMESTAMP` | NULL | `now()` |

- PK: `ticket_alert_read_states_pkey` (`user_id`)
- FKs:
  - `ticket_alert_read_states_user_id_fkey`: `user_id` -> `users.id`
- Unique constraints: no declaradas.
- Indices: no secundarios detectados.
- Check constraints: no declaradas.

### `ticket_assignment_history`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".ticket_assignment_history_id_seq'::regclass)` |
| `ticket_id` | `INTEGER` | NOT NULL |  |
| `from_user_id` | `INTEGER` | NULL |  |
| `to_user_id` | `INTEGER` | NULL |  |
| `changed_by_id` | `INTEGER` | NOT NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `ticket_assignment_history_pkey` (`id`)
- FKs:
  - `ticket_assignment_history_changed_by_id_fkey`: `changed_by_id` -> `users.id`
  - `ticket_assignment_history_from_user_id_fkey`: `from_user_id` -> `users.id`
  - `ticket_assignment_history_ticket_id_fkey`: `ticket_id` -> `tickets.id`
  - `ticket_assignment_history_to_user_id_fkey`: `to_user_id` -> `users.id`
- Unique constraints: no declaradas.
- Indices: no secundarios detectados.
- Check constraints: no declaradas.

### `ticket_internal_note_read_states`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `user_id` | `INTEGER` | NOT NULL |  |
| `ticket_id` | `INTEGER` | NOT NULL |  |
| `last_seen_note_count` | `INTEGER` | NOT NULL | `0` |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `ticket_internal_note_read_states_pkey` (`user_id`, `ticket_id`)
- FKs:
  - `ticket_internal_note_read_states_ticket_id_fkey`: `ticket_id` -> `tickets.id`
  - `ticket_internal_note_read_states_user_id_fkey`: `user_id` -> `users.id`
- Unique constraints: no declaradas.
- Indices:
  - `ix_ticket_internal_note_read_states_ticket_id` (`ticket_id`)
  - `ix_ticket_internal_note_read_states_user_id` (`user_id`)
- Check constraints: no declaradas.

### `ticket_message_read_states`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `user_id` | `INTEGER` | NOT NULL |  |
| `ticket_id` | `INTEGER` | NOT NULL |  |
| `last_seen_message_id` | `INTEGER` | NOT NULL | `0` |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `ticket_message_read_states_pkey` (`user_id`, `ticket_id`)
- FKs:
  - `ticket_message_read_states_ticket_id_fkey`: `ticket_id` -> `tickets.id`
  - `ticket_message_read_states_user_id_fkey`: `user_id` -> `users.id`
- Unique constraints: no declaradas.
- Indices:
  - `ix_ticket_message_read_states_ticket_id` (`ticket_id`)
  - `ix_ticket_message_read_states_user_id` (`user_id`)
- Check constraints: no declaradas.

### `ticket_sla_feedback`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `ticket_id` | `INTEGER` | NOT NULL |  |
| `technician_rating` | `INTEGER` | NULL |  |
| `resolution_satisfied` | `BOOLEAN` | NULL |  |
| `submitted_at` | `TIMESTAMP` | NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `ticket_sla_feedback_pkey` (`ticket_id`)
- FKs:
  - `ticket_sla_feedback_ticket_id_fkey`: `ticket_id` -> `tickets.id`
- Unique constraints: no declaradas.
- Indices: no secundarios detectados.
- Check constraints: no declaradas.

### `ticket_sla_feedback_events`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".ticket_sla_feedback_events_id_seq'::regclass)` |
| `ticket_id` | `INTEGER` | NULL |  |
| `source` | `VARCHAR(50)` | NOT NULL |  |
| `technician_rating` | `INTEGER` | NULL |  |
| `resolution_satisfied` | `BOOLEAN` | NULL |  |
| `payload` | `JSON` | NOT NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `ticket_sla_feedback_events_pkey` (`id`)
- FKs:
  - `ticket_sla_feedback_events_ticket_id_fkey`: `ticket_id` -> `tickets.id`
- Unique constraints: no declaradas.
- Indices:
  - `ix_ticket_sla_feedback_events_ticket_id` (`ticket_id`)
- Check constraints: no declaradas.

### `tickets`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".tickets_id_seq'::regclass)` |
| `subject` | `VARCHAR(300)` | NOT NULL |  |
| `status` | `VARCHAR(20)` | NOT NULL |  |
| `priority` | `VARCHAR(20)` | NOT NULL |  |
| `source` | `VARCHAR(20)` | NOT NULL |  |
| `is_deleted` | `BOOLEAN` | NOT NULL |  |
| `is_spam` | `BOOLEAN` | NOT NULL |  |
| `requester_id` | `INTEGER` | NOT NULL |  |
| `assigned_to_id` | `INTEGER` | NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `first_agent_reply_at` | `TIMESTAMP` | NULL |  |
| `resolved_at` | `TIMESTAMP` | NULL |  |
| `closed_at` | `TIMESTAMP` | NULL |  |
| `reopen_count` | `INTEGER` | NOT NULL |  |

- PK: `tickets_pkey` (`id`)
- FKs:
  - `tickets_assigned_to_id_fkey`: `assigned_to_id` -> `users.id`
  - `tickets_requester_id_fkey`: `requester_id` -> `requesters.id`
- Unique constraints: no declaradas.
- Indices: no secundarios detectados.
- Check constraints: no declaradas.

### `users`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".users_id_seq'::regclass)` |
| `name` | `VARCHAR(100)` | NOT NULL |  |
| `username` | `VARCHAR(50)` | NOT NULL |  |
| `hashed_password` | `VARCHAR(255)` | NOT NULL |  |
| `role` | `VARCHAR(20)` | NOT NULL |  |
| `is_active` | `BOOLEAN` | NOT NULL |  |

- PK: `users_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices:
  - `ix_users_role` (`role`)
  - `ix_users_username` UNIQUE (`username`)
- Check constraints: no declaradas.

# Base `Incidencias`
## Schema `public`
### `administracion_odt`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".administracion_odt_id_seq'::regclass)` |
| `odt` | `VARCHAR(30)` | NOT NULL |  |
| `tecnico` | `VARCHAR(255)` | NULL |  |
| `acompanante` | `VARCHAR(255)` | NULL |  |
| `fecha_derivacion` | `TIMESTAMP` | NULL |  |
| `finalizado` | `BOOLEAN` | NOT NULL |  |
| `fecha_cierre` | `TIMESTAMP` | NULL |  |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `recepcion_info` | `BOOLEAN` | NOT NULL | `false` |
| `fecha_recepcion_info` | `TIMESTAMP` | NULL |  |
| `registro_alpha3` | `BOOLEAN` | NOT NULL | `false` |
| `fecha_registro_alpha3` | `TIMESTAMP` | NULL |  |
| `registro_intranet` | `BOOLEAN` | NOT NULL | `false` |
| `fecha_registro_intranet` | `TIMESTAMP` | NULL |  |
| `envio_solicitud_instalacion` | `BOOLEAN` | NOT NULL | `false` |
| `fecha_envio_solicitud_instalacion` | `TIMESTAMP` | NULL |  |
| `envio_datos_facturacion` | `BOOLEAN` | NOT NULL | `false` |
| `fecha_envio_datos_facturacion` | `TIMESTAMP` | NULL |  |
| `envio_carta_bienvenida` | `BOOLEAN` | NOT NULL | `false` |
| `fecha_envio_carta_bienvenida` | `TIMESTAMP` | NULL |  |

- PK: `administracion_odt_pkey` (`id`)
- FKs:
  - `fk_administracion_odt_venta_ods`: `odt` -> `venta_ods.codigo` ON DELETE CASCADE
- Unique constraints: no declaradas.
- Indices:
  - `ix_administracion_odt_odt` UNIQUE (`odt`)
- Check constraints: no declaradas.

### `bbdd_clientes`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".bbdd_clientes_id_seq'::regclass)` |
| `cliente` | `VARCHAR(255)` | NOT NULL |  |
| `direccion` | `VARCHAR(255)` | NULL |  |
| `contacto` | `VARCHAR(255)` | NULL |  |
| `correo` | `VARCHAR(255)` | NULL |  |
| `rut` | `VARCHAR(40)` | NULL |  |
| `giro` | `VARCHAR(255)` | NULL |  |
| `region` | `VARCHAR(120)` | NULL |  |
| `comuna` | `VARCHAR(120)` | NULL |  |
| `email_facturas` | `VARCHAR(255)` | NULL |  |
| `nombre_representante` | `VARCHAR(255)` | NULL |  |
| `rut_representante` | `VARCHAR(40)` | NULL |  |
| `telefono` | `VARCHAR(32)` | NULL |  |
| `email_representante` | `VARCHAR(255)` | NULL |  |
| `ejecutivo_email` | `VARCHAR(255)` | NULL |  |
| `fecha_creacion` | `TIMESTAMP` | NULL | `now()` |

- PK: `bbdd_clientes_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints:
  - `uq_bbdd_clientes_rut` (`rut`)
- Indices:
  - `ix_bbdd_clientes_cliente` UNIQUE (`cliente`)
  - `uq_bbdd_clientes_rut` UNIQUE (`rut`)
- Check constraints: no declaradas.

### `bbdd_sucursales`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".bbdd_sucursales_id_seq'::regclass)` |
| `rut` | `VARCHAR(40)` | NOT NULL |  |
| `nombre_empresa` | `VARCHAR(255)` | NULL |  |
| `nombre_sucursal` | `VARCHAR(255)` | NOT NULL |  |
| `direccion_sucursal` | `VARCHAR(255)` | NOT NULL |  |
| `region` | `VARCHAR(120)` | NULL |  |
| `comuna` | `VARCHAR(120)` | NULL |  |
| `referencia_ubicacion` | `TEXT` | NULL |  |
| `latitud` | `VARCHAR(40)` | NULL |  |
| `longitud` | `VARCHAR(40)` | NULL |  |
| `latitud_longitud` | `VARCHAR(120)` | NULL |  |
| `email_facturas` | `VARCHAR(255)` | NULL |  |
| `proveedor_internet` | `VARCHAR(120)` | NULL |  |
| `proveedor_electricidad` | `VARCHAR(120)` | NULL |  |
| `nro_proveedor_electricidad` | `VARCHAR(120)` | NULL |  |
| `horario_apertura` | `VARCHAR(20)` | NULL |  |
| `horario_cierre` | `VARCHAR(20)` | NULL |  |
| `dias_funcionamiento` | `VARCHAR(255)` | NULL |  |
| `created_by` | `VARCHAR(255)` | NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `bbdd_sucursales_pkey` (`id`)
- FKs:
  - `fk_bbdd_sucursales_cliente_rut`: `rut` -> `bbdd_clientes.rut`
- Unique constraints: no declaradas.
- Indices:
  - `ix_bbdd_sucursales_direccion_sucursal` (`direccion_sucursal`)
  - `ix_bbdd_sucursales_nombre_sucursal` (`nombre_sucursal`)
  - `ix_bbdd_sucursales_rut` (`rut`)
- Check constraints: no declaradas.

### `catalogo_clientes`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `BIGINT` | NOT NULL | `nextval('"public".catalogo_clientes_id_seq'::regclass)` |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `rut_cliente` | `VARCHAR(30)` | NULL |  |
| `nombre_cliente` | `VARCHAR(255)` | NULL |  |
| `nombre_sucursal` | `VARCHAR(255)` | NOT NULL |  |
| `direccion_sucursal` | `VARCHAR(255)` | NULL |  |
| `rut_empleado` | `VARCHAR(30)` | NULL |  |
| `nombre_empleado` | `VARCHAR(255)` | NULL |  |
| `celular` | `VARCHAR(80)` | NULL |  |
| `email` | `VARCHAR(255)` | NULL |  |
| `nro_emergencia` | `VARCHAR(120)` | NULL |  |

- PK: `catalogo_clientes_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices: no secundarios detectados.
- Check constraints: no declaradas.

### `contactos_emergencia`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".contactos_emergencia_id_seq'::regclass)` |
| `sucursal` | `VARCHAR(255)` | NOT NULL |  |
| `nombre` | `VARCHAR(255)` | NULL |  |
| `celular` | `VARCHAR(80)` | NULL |  |
| `email` | `VARCHAR(255)` | NULL |  |
| `prioridad` | `VARCHAR(80)` | NULL |  |

- PK: `contactos_emergencia_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices:
  - `ix_contactos_emergencia_sucursal` (`sucursal`)
- Check constraints: no declaradas.

### `finanzas_odt`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".finanzas_odt_id_seq'::regclass)` |
| `odt` | `VARCHAR(30)` | NOT NULL |  |
| `fecha_inicio_servicio` | `VARCHAR(40)` | NULL |  |
| `recepcion_datos_facturacion` | `BOOLEAN` | NOT NULL |  |
| `fecha_recepcion_datos_facturacion` | `TIMESTAMP` | NULL |  |
| `creacion_clientes_piriod` | `BOOLEAN` | NOT NULL |  |
| `fecha_creacion_clientes_piriod` | `TIMESTAMP` | NULL |  |
| `creacion_clientes_bd` | `BOOLEAN` | NOT NULL |  |
| `fecha_creacion_clientes_bd` | `TIMESTAMP` | NULL |  |
| `facturacion_instalacion` | `BOOLEAN` | NOT NULL |  |
| `fecha_facturacion_instalacion` | `TIMESTAMP` | NULL |  |
| `facturacion_servicio` | `BOOLEAN` | NOT NULL |  |
| `fecha_facturacion_servicio` | `TIMESTAMP` | NULL |  |
| `finalizado` | `BOOLEAN` | NOT NULL |  |
| `fecha_cierre` | `TIMESTAMP` | NULL |  |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `finanzas_odt_pkey` (`id`)
- FKs:
  - `fk_finanzas_odt_venta_ods`: `odt` -> `venta_ods.codigo` ON DELETE CASCADE
- Unique constraints: no declaradas.
- Indices:
  - `ix_finanzas_odt_odt` UNIQUE (`odt`)
- Check constraints: no declaradas.

### `incidencias_cierres`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `BIGINT` | NOT NULL | `nextval('"public".incidencias_cierres_id_seq'::regclass)` |
| `incidencia_id` | `BIGINT` | NOT NULL |  |
| `odt` | `VARCHAR(80)` | NULL |  |
| `observacion` | `TEXT` | NULL |  |
| `cerrado_por` | `VARCHAR(180)` | NULL |  |
| `cerrado_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `incidencias_cierres_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices: no secundarios detectados.
- Check constraints: no declaradas.

### `incidencias_data`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `BIGINT` | NOT NULL | `nextval('"public".incidencias_id_seq'::regclass)` |
| `odt` | `VARCHAR(30)` | NOT NULL |  |
| `fecha_registro` | `TIMESTAMP` | NOT NULL |  |
| `puesto` | `VARCHAR(30)` | NOT NULL |  |
| `cliente` | `VARCHAR(255)` | NOT NULL |  |
| `tipo_incidencia` | `VARCHAR(255)` | NOT NULL |  |
| `derivacion` | `VARCHAR(255)` | NOT NULL | `'Servicio Técnico'::character varying` |
| `descripcion` | `TEXT` | NOT NULL |  |
| `tecnico` | `VARCHAR(255)` | NOT NULL | `''::character varying` |
| `estado` | `VARCHAR(60)` | NOT NULL | `'Pendiente'::character varying` |
| `dias_ejecucion` | `INTEGER` | NULL |  |
| `fecha_cierre` | `TIMESTAMP` | NULL |  |
| `fecha_derivacion` | `TIMESTAMP` | NOT NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `observacion_final` | `TEXT` | NULL |  |
| `imagen_1` | `TEXT` | NULL |  |
| `imagen_2` | `TEXT` | NULL |  |
| `imagen_3` | `TEXT` | NULL |  |
| `acompanante` | `VARCHAR(255)` | NULL |  |
| `observacion_pendiente` | `TEXT` | NULL |  |

- PK: `incidencias_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints:
  - `incidencias_odt_key` (`odt`)
- Indices:
  - `idx_incidencias_cliente` (`cliente`)
  - `idx_incidencias_estado` (`estado`)
  - `idx_incidencias_odt` (`odt`)
  - `incidencias_odt_key` UNIQUE (`odt`)
- Check constraints: no declaradas.

### `incidencias_imagenes_odt`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".incidencias_imagenes_odt_id_seq'::regclass)` |
| `odt` | `VARCHAR(80)` | NOT NULL |  |
| `sucursal` | `VARCHAR(255)` | NULL |  |
| `imagenes` | `TEXT` | NOT NULL |  |
| `created_by` | `VARCHAR(180)` | NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `incidencias_imagenes_odt_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices:
  - `idx_incidencias_imagenes_odt_odt` (`odt`)
  - `ix_incidencias_imagenes_odt_odt` UNIQUE (`odt`)
  - `uq_incidencias_imagenes_odt_odt` UNIQUE (`odt`)
- Check constraints: no declaradas.

### `login_sessions`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `token` | `VARCHAR(120)` | NOT NULL |  |
| `usuario` | `VARCHAR(255)` | NOT NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL |  |
| `expires_at` | `TIMESTAMP` | NOT NULL |  |

- PK: `login_sessions_pkey` (`token`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices:
  - `ix_login_sessions_expires_at` (`expires_at`)
  - `ix_login_sessions_usuario` (`usuario`)
- Check constraints: no declaradas.

### `mantenciones_imagenes_sucursal`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".mantenciones_imagenes_sucursal_id_seq'::regclass)` |
| `sucursal_key` | `VARCHAR(255)` | NOT NULL |  |
| `sucursal` | `VARCHAR(255)` | NOT NULL |  |
| `imagenes` | `TEXT` | NOT NULL |  |
| `created_by` | `VARCHAR(180)` | NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL |  |
| `updated_at` | `TIMESTAMP` | NOT NULL |  |

- PK: `mantenciones_imagenes_sucursal_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices:
  - `ix_mantenciones_imagenes_sucursal_sucursal` (`sucursal`)
  - `ix_mantenciones_imagenes_sucursal_sucursal_key` UNIQUE (`sucursal_key`)
- Check constraints: no declaradas.

### `operaciones_venta_odt`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".operaciones_venta_odt_id_seq'::regclass)` |
| `odt` | `VARCHAR(30)` | NOT NULL |  |
| `fecha_inicio_servicio` | `VARCHAR(40)` | NULL |  |
| `fecha_coordinacion` | `BOOLEAN` | NOT NULL |  |
| `ts_fecha_coordinacion` | `TIMESTAMP` | NULL |  |
| `reunion_coordinacion` | `BOOLEAN` | NOT NULL |  |
| `ts_reunion_coordinacion` | `TIMESTAMP` | NULL |  |
| `coord_apertura_puesto` | `BOOLEAN` | NOT NULL |  |
| `ts_coord_apertura_puesto` | `TIMESTAMP` | NULL |  |
| `coord_equipo` | `BOOLEAN` | NOT NULL |  |
| `ts_coord_equipo` | `TIMESTAMP` | NULL |  |
| `terminado` | `BOOLEAN` | NOT NULL |  |
| `ts_terminado` | `TIMESTAMP` | NULL |  |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `operaciones_venta_odt_pkey` (`id`)
- FKs:
  - `fk_operaciones_venta_odt_venta_ods`: `odt` -> `venta_ods.codigo` ON DELETE CASCADE
- Unique constraints: no declaradas.
- Indices:
  - `ix_operaciones_venta_odt_odt` UNIQUE (`odt`)
- Check constraints: no declaradas.

### `protocolos_informes`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".protocolos_informes_id_seq'::regclass)` |
| `tipo_informe` | `VARCHAR(20)` | NOT NULL |  |
| `estado` | `VARCHAR(30)` | NOT NULL |  |
| `registro_id` | `INTEGER` | NULL |  |
| `cliente` | `VARCHAR(255)` | NOT NULL |  |
| `sucursal` | `VARCHAR(255)` | NOT NULL |  |
| `periodo_inicio` | `TIMESTAMP` | NULL |  |
| `periodo_fin` | `TIMESTAMP` | NULL |  |
| `titulo` | `VARCHAR(255)` | NULL |  |
| `pdf_url` | `TEXT` | NULL |  |
| `drive_file_id` | `VARCHAR(120)` | NULL |  |
| `drive_folder_id` | `VARCHAR(120)` | NULL |  |
| `drive_folder_name` | `VARCHAR(255)` | NULL |  |
| `error_detalle` | `TEXT` | NULL |  |
| `metadata_json` | `TEXT` | NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `protocolos_informes_pkey` (`id`)
- FKs:
  - `fk_protocolos_informes_registro`: `registro_id` -> `protocolos_registro.id` ON DELETE CASCADE
- Unique constraints:
  - `uq_protocolo_informe_individual` (`tipo_informe`, `registro_id`)
  - `uq_protocolo_informe_semanal` (`tipo_informe`, `cliente`, `sucursal`, `periodo_inicio`, `periodo_fin`)
- Indices:
  - `ix_protocolos_informes_cliente` (`cliente`)
  - `ix_protocolos_informes_created_at` (`created_at`)
  - `ix_protocolos_informes_estado` (`estado`)
  - `ix_protocolos_informes_periodo_fin` (`periodo_fin`)
  - `ix_protocolos_informes_periodo_inicio` (`periodo_inicio`)
  - `ix_protocolos_informes_registro_id` (`registro_id`)
  - `ix_protocolos_informes_sucursal` (`sucursal`)
  - `ix_protocolos_informes_tipo_informe` (`tipo_informe`)
  - `uq_protocolo_informe_individual` UNIQUE (`tipo_informe`, `registro_id`)
  - `uq_protocolo_informe_semanal` UNIQUE (`tipo_informe`, `cliente`, `sucursal`, `periodo_inicio`, `periodo_fin`)
- Check constraints: no declaradas.

### `protocolos_registro`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".protocolos_registro_id_seq'::regclass)` |
| `fecha_registro` | `TIMESTAMP` | NOT NULL |  |
| `encargado` | `VARCHAR(255)` | NULL |  |
| `grupo` | `VARCHAR(120)` | NULL |  |
| `cliente` | `VARCHAR(255)` | NOT NULL |  |
| `sucursal` | `VARCHAR(255)` | NOT NULL |  |
| `tipo_protocolo` | `VARCHAR(120)` | NULL |  |
| `detectado` | `VARCHAR(20)` | NULL |  |
| `efectivo` | `VARCHAR(20)` | NULL |  |
| `sirena` | `VARCHAR(20)` | NULL |  |
| `voz` | `VARCHAR(20)` | NULL |  |
| `carabineros` | `VARCHAR(20)` | NULL |  |
| `alpha3` | `VARCHAR(20)` | NULL |  |
| `informado` | `VARCHAR(20)` | NULL |  |
| `bitacora` | `VARCHAR(20)` | NULL |  |
| `puesto` | `VARCHAR(80)` | NULL |  |
| `operador` | `VARCHAR(255)` | NULL |  |
| `observaciones_raw` | `TEXT` | NULL |  |
| `observaciones_formal` | `TEXT` | NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `protocolo_exitoso` | `VARCHAR(20)` | NULL |  |

- PK: `protocolos_registro_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices:
  - `ix_protocolos_registro_cliente` (`cliente`)
  - `ix_protocolos_registro_encargado` (`encargado`)
  - `ix_protocolos_registro_fecha_registro` (`fecha_registro`)
  - `ix_protocolos_registro_sucursal` (`sucursal`)
  - `ix_protocolos_registro_tipo_protocolo` (`tipo_protocolo`)
- Check constraints: no declaradas.

### `registro`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".registro_id_seq'::regclass)` |
| `odt` | `VARCHAR(30)` | NOT NULL |  |
| `fecha_registro` | `TIMESTAMP` | NOT NULL |  |
| `puesto` | `VARCHAR(80)` | NULL |  |
| `cliente` | `VARCHAR(255)` | NOT NULL |  |
| `problema` | `VARCHAR(255)` | NOT NULL |  |
| `derivacion` | `VARCHAR(120)` | NOT NULL |  |
| `observacion` | `TEXT` | NULL |  |
| `tecnicos` | `VARCHAR(255)` | NULL |  |
| `acompanante` | `VARCHAR(255)` | NULL |  |
| `estado` | `VARCHAR(100)` | NOT NULL |  |
| `dias_ejecucion` | `INTEGER` | NULL |  |
| `fecha_cierre` | `TIMESTAMP` | NULL |  |
| `fecha_derivacion_area` | `TIMESTAMP` | NULL |  |
| `fecha_derivacion_tecnico` | `TIMESTAMP` | NULL |  |
| `direccion` | `VARCHAR(255)` | NULL |  |
| `observacion_final` | `TEXT` | NULL |  |
| `prioridad` | `INTEGER` | NULL |  |
| `materiales` | `TEXT` | NULL |  |
| `porcentaje_avance` | `VARCHAR(20)` | NULL |  |
| `foto_1` | `TEXT` | NULL |  |
| `foto_2` | `TEXT` | NULL |  |
| `foto_3` | `TEXT` | NULL |  |
| `pdf_url` | `TEXT` | NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `observacion_pendiente` | `TEXT` | NULL |  |
| `observacion_soporte` | `TEXT` | NULL |  |
| `detalle_problema` | `TEXT` | NULL |  |
| `observacion_servicio` | `TEXT` | NULL |  |
| `responsable_cierre` | `VARCHAR(40)` | NULL |  |
| `causa_cierre` | `VARCHAR(120)` | NULL |  |
| `accion_cierre` | `VARCHAR(120)` | NULL |  |
| `resultado_cierre` | `VARCHAR(120)` | NULL |  |
| `pruebas_cierre` | `TEXT` | NULL |  |
| `requiere_seguimiento` | `BOOLEAN` | NULL |  |
| `drive_cierre_folder_id` | `VARCHAR(255)` | NULL |  |
| `drive_cierre_folder_url` | `TEXT` | NULL |  |

- PK: `registro_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices:
  - `ix_registro_cliente` (`cliente`)
  - `ix_registro_estado` (`estado`)
  - `ix_registro_odt` UNIQUE (`odt`)
- Check constraints: no declaradas.

### `registros_correos_cliente`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".registros_correos_cliente_id_seq'::regclass)` |
| `odt` | `VARCHAR(30)` | NOT NULL |  |
| `sucursal` | `VARCHAR(255)` | NOT NULL |  |
| `fecha_envio` | `TIMESTAMP` | NOT NULL |  |
| `observacion` | `TEXT` | NULL |  |
| `estado` | `VARCHAR(100)` | NULL |  |

- PK: `registros_correos_cliente_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices:
  - `ix_registros_correos_cliente_odt` (`odt`)
- Check constraints: no declaradas.

### `rendiciones`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".rendiciones_id_seq'::regclass)` |
| `folio` | `INTEGER` | NOT NULL |  |
| `codigo_diario` | `VARCHAR(120)` | NOT NULL |  |
| `fecha_registro` | `TIMESTAMP` | NOT NULL |  |
| `tecnico` | `VARCHAR(255)` | NOT NULL |  |
| `mail` | `VARCHAR(255)` | NULL |  |
| `odt` | `VARCHAR(30)` | NOT NULL |  |
| `cliente` | `VARCHAR(255)` | NOT NULL |  |
| `comuna` | `VARCHAR(255)` | NULL |  |
| `tipo_gasto` | `VARCHAR(120)` | NOT NULL |  |
| `tipo_documento` | `VARCHAR(120)` | NOT NULL |  |
| `nro_documento` | `VARCHAR(120)` | NOT NULL |  |
| `fecha_documento` | `TIMESTAMP` | NOT NULL |  |
| `monto_total` | `NUMERIC(14, 2)` | NOT NULL |  |
| `descripcion` | `TEXT` | NULL |  |
| `url_boleta` | `TEXT` | NULL |  |
| `url_informe` | `TEXT` | NULL |  |
| `documento` | `VARCHAR(255)` | NULL |  |
| `estado_revision` | `VARCHAR(30)` | NOT NULL |  |

- PK: `rendiciones_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices:
  - `ix_rendiciones_cliente` (`cliente`)
  - `ix_rendiciones_codigo_diario` (`codigo_diario`)
  - `ix_rendiciones_folio` UNIQUE (`folio`)
  - `ix_rendiciones_nro_documento` (`nro_documento`)
  - `ix_rendiciones_odt` (`odt`)
  - `ix_rendiciones_tecnico` (`tecnico`)
- Check constraints: no declaradas.

### `servicio_tecnico_ventas_odt`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".servicio_tecnico_ventas_odt_id_seq'::regclass)` |
| `odt` | `VARCHAR(30)` | NOT NULL |  |
| `recepcion_solicitud_instalacion` | `BOOLEAN` | NOT NULL | `false` |
| `fecha_recepcion_solicitud_instalacion` | `TIMESTAMP` | NULL |  |
| `llamar_cliente` | `TEXT` | NULL |  |
| `solicitud_materiales` | `TEXT` | NULL |  |
| `fecha_inicio_instalacion` | `VARCHAR(40)` | NULL |  |
| `fecha_fin_instalacion` | `VARCHAR(40)` | NULL |  |
| `tecnico_a_cargo` | `VARCHAR(255)` | NULL |  |
| `acompanante` | `VARCHAR(255)` | NULL |  |
| `instalacion_finalizada` | `BOOLEAN` | NOT NULL | `false` |
| `fecha_instalacion_finalizada` | `TIMESTAMP` | NULL |  |
| `finalizado` | `BOOLEAN` | NOT NULL | `false` |
| `fecha_cierre` | `TIMESTAMP` | NULL |  |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `configuracion_camaras` | `BOOLEAN` | NULL | `false` |
| `fecha_configuracion_camaras` | `TIMESTAMP` | NULL |  |
| `posicionamiento_imagen` | `BOOLEAN` | NULL | `false` |
| `fecha_posicionamiento_imagen` | `TIMESTAMP` | NULL |  |
| `enlace_servidor` | `BOOLEAN` | NULL | `false` |
| `fecha_enlace_servidor` | `TIMESTAMP` | NULL |  |
| `configuracion_ivs` | `BOOLEAN` | NULL | `false` |
| `fecha_configuracion_ivs` | `TIMESTAMP` | NULL |  |
| `plan_grabacion` | `BOOLEAN` | NULL | `false` |
| `fecha_plan_grabacion` | `TIMESTAMP` | NULL |  |
| `requiere_puesto_nuevo` | `TEXT` | NULL |  |
| `numero_central_asignado` | `TEXT` | NULL |  |
| `configuracion_cliente` | `BOOLEAN` | NULL | `false` |
| `fecha_configuracion_cliente` | `TIMESTAMP` | NULL |  |
| `vb_final_servicio` | `BOOLEAN` | NULL | `false` |
| `fecha_vb_final_servicio` | `TIMESTAMP` | NULL |  |
| `camaras_registradas` | `TEXT` | NULL |  |

- PK: `servicio_tecnico_ventas_odt_pkey` (`id`)
- FKs:
  - `fk_servicio_tecnico_ventas_odt_venta_ods`: `odt` -> `venta_ods.codigo` ON DELETE CASCADE
- Unique constraints: no declaradas.
- Indices:
  - `ix_servicio_tecnico_ventas_odt_odt` UNIQUE (`odt`)
- Check constraints: no declaradas.

### `sesiones_tecnico`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `token` | `VARCHAR(255)` | NOT NULL |  |
| `nombre_tecnico` | `VARCHAR(255)` | NOT NULL |  |
| `expira_en` | `TIMESTAMP` | NULL |  |

- PK: `sesiones_tecnico_pkey` (`token`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices: no secundarios detectados.
- Check constraints: no declaradas.

### `sucursal_contactos_emergencia`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".sucursal_contactos_emergencia_id_seq'::regclass)` |
| `sucursal_id` | `INTEGER` | NOT NULL |  |
| `nombre` | `VARCHAR(255)` | NULL |  |
| `rut` | `VARCHAR(40)` | NULL |  |
| `telefono` | `VARCHAR(32)` | NULL |  |
| `email` | `VARCHAR(255)` | NULL |  |

- PK: `sucursal_contactos_emergencia_pkey` (`id`)
- FKs:
  - `fk_sucursal_contactos_sucursal`: `sucursal_id` -> `bbdd_sucursales.id` ON DELETE CASCADE
- Unique constraints: no declaradas.
- Indices:
  - `ix_sucursal_contactos_emergencia_sucursal_id` (`sucursal_id`)
- Check constraints: no declaradas.

### `sucursal_guardias`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".sucursal_guardias_id_seq'::regclass)` |
| `sucursal_id` | `INTEGER` | NOT NULL |  |
| `nombre` | `VARCHAR(255)` | NULL |  |
| `rut` | `VARCHAR(40)` | NULL |  |
| `telefono` | `VARCHAR(32)` | NULL |  |
| `horario_desde` | `VARCHAR(20)` | NULL |  |
| `horario_hasta` | `VARCHAR(20)` | NULL |  |

- PK: `sucursal_guardias_pkey` (`id`)
- FKs:
  - `fk_sucursal_guardias_sucursal`: `sucursal_id` -> `bbdd_sucursales.id` ON DELETE CASCADE
- Unique constraints: no declaradas.
- Indices:
  - `ix_sucursal_guardias_sucursal_id` (`sucursal_id`)
- Check constraints: no declaradas.

### `sucursal_personas_autorizadas`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".sucursal_personas_autorizadas_id_seq'::regclass)` |
| `sucursal_id` | `INTEGER` | NOT NULL |  |
| `nombre` | `VARCHAR(255)` | NULL |  |
| `rut` | `VARCHAR(40)` | NULL |  |
| `telefono` | `VARCHAR(32)` | NULL |  |
| `email` | `VARCHAR(255)` | NULL |  |
| `clave_verde` | `VARCHAR(255)` | NULL |  |
| `clave_roja` | `VARCHAR(255)` | NULL |  |

- PK: `sucursal_personas_autorizadas_pkey` (`id`)
- FKs:
  - `fk_sucursal_personas_sucursal`: `sucursal_id` -> `bbdd_sucursales.id` ON DELETE CASCADE
- Unique constraints: no declaradas.
- Indices:
  - `ix_sucursal_personas_autorizadas_sucursal_id` (`sucursal_id`)
- Check constraints: no declaradas.

### `sync_outbox`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".sync_outbox_id_seq'::regclass)` |
| `event_type` | `VARCHAR(80)` | NOT NULL |  |
| `entity_key` | `VARCHAR(80)` | NOT NULL |  |
| `payload_json` | `TEXT` | NOT NULL |  |
| `status` | `VARCHAR(20)` | NOT NULL |  |
| `attempts` | `INTEGER` | NOT NULL |  |
| `last_error` | `TEXT` | NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL |  |
| `updated_at` | `TIMESTAMP` | NOT NULL |  |
| `sent_at` | `TIMESTAMP` | NULL |  |

- PK: `sync_outbox_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices:
  - `ix_sync_outbox_entity_key` (`entity_key`)
  - `ix_sync_outbox_event_type` (`event_type`)
  - `ix_sync_outbox_status` (`status`)
- Check constraints: no declaradas.

### `tareas`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".tareas_id_seq'::regclass)` |
| `codigo` | `VARCHAR(30)` | NOT NULL |  |
| `usuario_soporte` | `VARCHAR(255)` | NOT NULL |  |
| `fecha_creacion` | `TIMESTAMP` | NOT NULL |  |
| `cliente` | `VARCHAR(255)` | NOT NULL |  |
| `tipo_tarea` | `VARCHAR(255)` | NOT NULL |  |
| `especificacion` | `VARCHAR(255)` | NOT NULL |  |
| `descripcion` | `TEXT` | NOT NULL |  |
| `solicitante` | `VARCHAR(255)` | NULL |  |
| `estado` | `VARCHAR(80)` | NOT NULL |  |
| `tecnico_cierre` | `VARCHAR(255)` | NULL |  |
| `fecha_cierre` | `TIMESTAMP` | NULL |  |
| `dias_ejecucion` | `INTEGER` | NULL |  |

- PK: `tareas_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints: no declaradas.
- Indices:
  - `ix_tareas_codigo` UNIQUE (`codigo`)
- Check constraints: no declaradas.

### `users`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".users_id_seq'::regclass)` |
| `name` | `VARCHAR(100)` | NOT NULL |  |
| `username` | `VARCHAR(50)` | NOT NULL |  |
| `hashed_password` | `VARCHAR(255)` | NOT NULL |  |
| `role` | `VARCHAR(20)` | NOT NULL | `'agent'::character varying` |
| `is_active` | `BOOLEAN` | NOT NULL | `true` |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `users_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints:
  - `users_username_key` (`username`)
- Indices:
  - `users_username_key` UNIQUE (`username`)
- Check constraints: no declaradas.

### `venta_clientes`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".venta_clientes_id_seq'::regclass)` |
| `rut` | `VARCHAR(32)` | NOT NULL |  |
| `razon_social` | `VARCHAR(255)` | NOT NULL |  |
| `giro` | `VARCHAR(255)` | NOT NULL |  |
| `direccion` | `VARCHAR(255)` | NOT NULL |  |
| `region` | `VARCHAR(120)` | NOT NULL |  |
| `comuna` | `VARCHAR(120)` | NOT NULL |  |
| `email_facturas` | `VARCHAR(255)` | NOT NULL |  |
| `nombre_representante` | `VARCHAR(255)` | NOT NULL |  |
| `rut_representante` | `VARCHAR(32)` | NOT NULL |  |
| `telefono` | `VARCHAR(32)` | NOT NULL |  |
| `email_representante` | `VARCHAR(255)` | NOT NULL |  |
| `ejecutivo_email` | `VARCHAR(255)` | NOT NULL |  |
| `fecha_creacion` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `venta_clientes_pkey` (`id`)
- FKs: no declaradas.
- Unique constraints:
  - `venta_clientes_rut_key` (`rut`)
- Indices:
  - `ix_venta_clientes_rut` UNIQUE (`rut`)
  - `venta_clientes_rut_key` UNIQUE (`rut`)
- Check constraints: no declaradas.

### `venta_ods`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".venta_ods_id_seq'::regclass)` |
| `codigo` | `VARCHAR(30)` | NOT NULL |  |
| `ejecutivo_venta` | `VARCHAR(255)` | NULL |  |
| `creado_por` | `VARCHAR(255)` | NULL |  |
| `rut_cliente` | `VARCHAR(40)` | NOT NULL |  |
| `razon_social` | `VARCHAR(255)` | NOT NULL |  |
| `direccion_sucursal` | `VARCHAR(255)` | NOT NULL |  |
| `nombre_sucursal` | `VARCHAR(255)` | NULL |  |
| `tipo_cliente` | `VARCHAR(120)` | NULL |  |
| `tipo_servicio` | `TEXT` | NOT NULL |  |
| `tipo_plan` | `VARCHAR(120)` | NULL |  |
| `observacion` | `TEXT` | NULL |  |
| `numero_camaras_instalar` | `INTEGER` | NULL |  |
| `numero_camaras_desinstalar` | `INTEGER` | NULL |  |
| `numero_camaras_vigilar` | `INTEGER` | NULL |  |
| `dias_grabacion` | `INTEGER` | NULL |  |
| `dias_monitoreo_desde` | `VARCHAR(20)` | NULL |  |
| `dias_monitoreo_hasta` | `VARCHAR(20)` | NULL |  |
| `dias_monitoreo_adicional` | `VARCHAR(120)` | NULL |  |
| `horario_monitoreo` | `VARCHAR(20)` | NULL |  |
| `materiales` | `TEXT` | NULL |  |
| `consideraciones` | `TEXT` | NULL |  |
| `agua_bano` | `VARCHAR(30)` | NULL |  |
| `requiere_oc` | `VARCHAR(10)` | NULL |  |
| `montos_a_cobrar` | `TEXT` | NULL |  |
| `cotizacion_path` | `TEXT` | NULL |  |
| `odc_path` | `TEXT` | NULL |  |
| `desglose_path` | `TEXT` | NULL |  |
| `estado` | `VARCHAR(80)` | NOT NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `updated_at` | `TIMESTAMP` | NOT NULL | `now()` |
| `contrato_path` | `TEXT` | NULL |  |

- PK: `venta_ods_pkey` (`id`)
- FKs:
  - `fk_venta_ods_cliente_rut`: `rut_cliente` -> `bbdd_clientes.rut`
- Unique constraints: no declaradas.
- Indices:
  - `ix_venta_ods_codigo` UNIQUE (`codigo`)
  - `ix_venta_ods_direccion_sucursal` (`direccion_sucursal`)
  - `ix_venta_ods_estado` (`estado`)
  - `ix_venta_ods_razon_social` (`razon_social`)
  - `ix_venta_ods_rut_cliente` (`rut_cliente`)
- Check constraints: no declaradas.

### `venta_ods_archivos`
| Columna | Tipo | Nullable | Default |
|---|---|---:|---|
| `id` | `INTEGER` | NOT NULL | `nextval('"public".venta_ods_archivos_id_seq'::regclass)` |
| `ods_id` | `INTEGER` | NOT NULL |  |
| `codigo_ods` | `VARCHAR(30)` | NOT NULL |  |
| `tipo_documento` | `VARCHAR(80)` | NULL |  |
| `servicio` | `VARCHAR(120)` | NULL |  |
| `nombre_archivo` | `VARCHAR(255)` | NULL |  |
| `mime_type` | `VARCHAR(120)` | NULL |  |
| `ruta_archivo` | `TEXT` | NULL |  |
| `created_at` | `TIMESTAMP` | NOT NULL | `now()` |

- PK: `venta_ods_archivos_pkey` (`id`)
- FKs:
  - `fk_venta_ods_archivos_ods`: `ods_id` -> `venta_ods.id` ON DELETE CASCADE
- Unique constraints: no declaradas.
- Indices:
  - `ix_venta_ods_archivos_codigo_ods` (`codigo_ods`)
  - `ix_venta_ods_archivos_ods_id` (`ods_id`)
- Check constraints: no declaradas.

## Relaciones detectadas por Foreign Key
### `ATC/helpdesk`
- `automation_logs.ticket_id` -> `tickets.id` (`automation_logs_ticket_id_fkey`)
- `internal_chat_messages.sender_id` -> `users.id` (`internal_chat_messages_sender_id_fkey`)
- `internal_chat_read_states.user_id` -> `users.id` (`internal_chat_read_states_user_id_fkey`)
- `messages.sender_id` -> `users.id` (`messages_sender_id_fkey`)
- `messages.ticket_id` -> `tickets.id` (`messages_ticket_id_fkey`)
- `ticket_alert_read_states.user_id` -> `users.id` (`ticket_alert_read_states_user_id_fkey`)
- `ticket_assignment_history.changed_by_id` -> `users.id` (`ticket_assignment_history_changed_by_id_fkey`)
- `ticket_assignment_history.from_user_id` -> `users.id` (`ticket_assignment_history_from_user_id_fkey`)
- `ticket_assignment_history.ticket_id` -> `tickets.id` (`ticket_assignment_history_ticket_id_fkey`)
- `ticket_assignment_history.to_user_id` -> `users.id` (`ticket_assignment_history_to_user_id_fkey`)
- `ticket_internal_note_read_states.ticket_id` -> `tickets.id` (`ticket_internal_note_read_states_ticket_id_fkey`)
- `ticket_internal_note_read_states.user_id` -> `users.id` (`ticket_internal_note_read_states_user_id_fkey`)
- `ticket_message_read_states.ticket_id` -> `tickets.id` (`ticket_message_read_states_ticket_id_fkey`)
- `ticket_message_read_states.user_id` -> `users.id` (`ticket_message_read_states_user_id_fkey`)
- `ticket_sla_feedback.ticket_id` -> `tickets.id` (`ticket_sla_feedback_ticket_id_fkey`)
- `ticket_sla_feedback_events.ticket_id` -> `tickets.id` (`ticket_sla_feedback_events_ticket_id_fkey`)
- `tickets.assigned_to_id` -> `users.id` (`tickets_assigned_to_id_fkey`)
- `tickets.requester_id` -> `requesters.id` (`tickets_requester_id_fkey`)

### `Incidencias`
- `administracion_odt.odt` -> `venta_ods.codigo` (`fk_administracion_odt_venta_ods`; ON DELETE CASCADE)
- `bbdd_sucursales.rut` -> `bbdd_clientes.rut` (`fk_bbdd_sucursales_cliente_rut`)
- `finanzas_odt.odt` -> `venta_ods.codigo` (`fk_finanzas_odt_venta_ods`; ON DELETE CASCADE)
- `operaciones_venta_odt.odt` -> `venta_ods.codigo` (`fk_operaciones_venta_odt_venta_ods`; ON DELETE CASCADE)
- `protocolos_informes.registro_id` -> `protocolos_registro.id` (`fk_protocolos_informes_registro`; ON DELETE CASCADE)
- `servicio_tecnico_ventas_odt.odt` -> `venta_ods.codigo` (`fk_servicio_tecnico_ventas_odt_venta_ods`; ON DELETE CASCADE)
- `sucursal_contactos_emergencia.sucursal_id` -> `bbdd_sucursales.id` (`fk_sucursal_contactos_sucursal`; ON DELETE CASCADE)
- `sucursal_guardias.sucursal_id` -> `bbdd_sucursales.id` (`fk_sucursal_guardias_sucursal`; ON DELETE CASCADE)
- `sucursal_personas_autorizadas.sucursal_id` -> `bbdd_sucursales.id` (`fk_sucursal_personas_sucursal`; ON DELETE CASCADE)
- `venta_ods.rut_cliente` -> `bbdd_clientes.rut` (`fk_venta_ods_cliente_rut`)
- `venta_ods_archivos.ods_id` -> `venta_ods.id` (`fk_venta_ods_archivos_ods`; ON DELETE CASCADE)

