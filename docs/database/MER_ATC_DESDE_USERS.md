# MER BBDD ATC desde users

Este mapa esta ordenado con `users` como entidad raiz.

- Linea solida: relacion fisica con FK real en PostgreSQL.
- Linea punteada: relacion funcional/inferida por campo de negocio. Ejemplos:
  `created_by`, `usuario_soporte`, `tecnico`, `odt`, `cliente`.

## Vista Mermaid

```mermaid
%% El diagrama fuente editable esta en:
%% docs/database/MER_ATC_DESDE_USERS.mmd

erDiagram
  users {
    int id PK
    string username UK
    string name
    string role
    string department
    bool is_active
  }
  areas {
    int id PK
    string code UK
    string name
    string department
  }
  user_areas {
    int id PK
    int user_id FK
    int area_id FK
    bool is_primary
  }
  requesters {
    int id PK
    string email
    string name
  }
  tickets {
    int id PK
    int requester_id FK
    int assigned_to_id FK
    string subject
    string status
  }
  messages {
    int id PK
    int ticket_id FK
    int sender_id FK
    string sender_type
  }
  bbdd_clientes {
    int id PK
    string rut UK
    string cliente UK
  }
  bbdd_sucursales {
    int id PK
    string rut FK
    string nombre_sucursal
    string created_by
  }
  venta_ods {
    int id PK
    string codigo UK
    string rut_cliente FK
    string creado_por
    string estado
  }
  registro {
    int id PK
    string odt UK
    string cliente
    string tecnicos
    string estado
  }
  protocolos_registro {
    int id PK
    string encargado
    string operador
    string cliente
    string sucursal
  }

  users ||--o{ user_areas : "FK"
  areas ||--o{ user_areas : "FK"
  users ||--o{ tickets : "assigned_to_id"
  requesters ||--o{ tickets : "requester_id"
  users ||--o{ messages : "sender_id"
  tickets ||--o{ messages : "ticket_id"
  bbdd_clientes ||--o{ bbdd_sucursales : "rut"
  bbdd_clientes ||--o{ venta_ods : "rut_cliente"
  users ||..o{ venta_ods : "creado_por"
  users ||..o{ bbdd_sucursales : "created_by"
  users ||..o{ registro : "tecnicos"
  users ||..o{ protocolos_registro : "encargado/operador"
```

## Archivos

- MER completo: `docs/database/MER_ATC_DESDE_USERS.mmd`
- Esta vista resumida: `docs/database/MER_ATC_DESDE_USERS.md`

## Orden logico

1. `users`
2. `areas` / `user_areas`
3. Helpdesk: `requesters`, `tickets`, `messages`, historiales, lecturas y SLA
4. Clientes y sucursales
5. Venta ODS y flujo por areas
6. Incidencias operativas
7. Protocolos
8. Integraciones: correo, outbox y sincronizacion

