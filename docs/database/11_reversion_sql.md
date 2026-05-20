# Reversion de cambios SQL

## Alcance

Fecha de reversion: 2026-05-19.

Solicitud: deshacer lo modificado en SQL.

Objetos objetivo:

- Constraint: `fk_incidencias_cierres_registro`
- Indice: `ix_incidencias_cierres_incidencia_id`

## Bases revisadas

Se revisaron las conexiones configuradas en el proyecto:

- `helpdesk`
- `incidencias`

## Situacion encontrada

Antes de ejecutar la reversion deterministica, se verifico que:

- No existia `fk_incidencias_cierres_registro` en `helpdesk`.
- No existia `ix_incidencias_cierres_incidencia_id` en `helpdesk`.
- No existia `fk_incidencias_cierres_registro` en `incidencias`.
- No existia `ix_incidencias_cierres_incidencia_id` en `incidencias`.
- En la base `incidencias`, la tabla `public.incidencias_cierres` no estaba presente al momento de la reversion.

## SQL ejecutado

Se ejecuto en `helpdesk` y `incidencias`:

```sql
ALTER TABLE IF EXISTS public.incidencias_cierres
DROP CONSTRAINT IF EXISTS fk_incidencias_cierres_registro;
```

Resultado: ejecutado correctamente.

```sql
DROP INDEX IF EXISTS public.ix_incidencias_cierres_incidencia_id;
```

Resultado: ejecutado correctamente.

## Validacion posterior

Resultado posterior:

- Constraint `fk_incidencias_cierres_registro`: no existe.
- Indice `ix_incidencias_cierres_incidencia_id`: no existe.

## Estado del MER despues de revertir

El MER vuelve a tratar la relacion `registro -> incidencias_cierres` como relacion logica/pendiente, no como foreign key fisica vigente.

Tambien siguen pendientes:

- `incidencias_imagenes_odt.odt -> registro.odt`
- `registros_correos_cliente.odt -> registro.odt`
