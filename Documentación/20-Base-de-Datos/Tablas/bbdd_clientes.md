---
tipo: tabla
modulo: venta
db: postgres
tags:
  - db/tabla
  - modulo/venta
actualizado: 2026-06-14
---

# Tabla `bbdd_clientes`

> [!abstract] Responsabilidad
> Catálogo de clientes (antes hoja `BBDD`). Raíz de sucursales y venta ODS.

## Columnas (según MER)
| Columna | Tipo | Notas |
|---|---|---|
| `id` | int PK |  |
| `rut` | varchar UK |  |
| `cliente` | varchar UK |  |

> [!todo] Pendiente
> Completar columnas reales contra `ATC/incidencias/app/models.py`.

## Relaciones
- **← referenciada por** `bbdd_sucursales` (`rut`), `venta_ods` (`rut_cliente`)

## Módulos que la usan
- [[Venta]], [[Incidencias]]
