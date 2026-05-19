# Cambios SQL aplicados al MER

## Alcance

Fecha de aplicacion: 2026-05-19.

Base: `incidencias`.

Schema: `public`.

Tabla canonica de incidencias: `registro`.

Objetivo aplicado: formalizar fisicamente la relacion segura `incidencias_cierres.incidencia_id -> registro.id`.

No se modificaron datos existentes. No se ejecuto `DROP`, `DELETE`, `TRUNCATE`, `UPDATE` ni limpieza automatica de huerfanos.

## Validaciones ejecutadas antes del cambio

### 1. Huerfanos en `incidencias_cierres`

Consulta logica:

```sql
SELECT COUNT(*)
FROM public.incidencias_cierres c
LEFT JOIN public.registro r ON r.id = c.incidencia_id
WHERE r.id IS NULL;
```

Resultado: `0`.

Dictamen: no habia cierres huerfanos por `incidencia_id`.

### 2. Primary key de `registro`

Resultado:

| Constraint | Definicion |
|---|---|
| `registro_pkey` | `PRIMARY KEY (id)` |

Dictamen: `registro.id` existe como primary key real.

### 3. Foreign key equivalente existente

Resultado: no existia FK equivalente entre `incidencias_cierres.incidencia_id` y `registro.id`.

Dictamen: era seguro crear la constraint sin duplicarla.

### 4. Indice previo sobre `incidencias_cierres.incidencia_id`

Resultado: no existia indice previo sobre `incidencia_id`.

Dictamen: correspondia crear indice antes o junto con la FK para mantener buen rendimiento en joins y validaciones.

## SQL ejecutado

### 1. Crear indice

```sql
CREATE INDEX IF NOT EXISTS ix_incidencias_cierres_incidencia_id
ON public.incidencias_cierres (incidencia_id);
```

Resultado: ejecutado correctamente.

### 2. Crear foreign key con `NOT VALID`

```sql
ALTER TABLE public.incidencias_cierres
ADD CONSTRAINT fk_incidencias_cierres_registro
FOREIGN KEY (incidencia_id)
REFERENCES public.registro(id)
NOT VALID;
```

Resultado: ejecutado correctamente.

### 3. Validar foreign key

```sql
ALTER TABLE public.incidencias_cierres
VALIDATE CONSTRAINT fk_incidencias_cierres_registro;
```

Resultado: ejecutado correctamente.

## Validacion posterior

### Foreign key creada

| Constraint | Validada | Definicion |
|---|---:|---|
| `fk_incidencias_cierres_registro` | Si | `FOREIGN KEY (incidencia_id) REFERENCES registro(id)` |

### Indice creado

| Indice | Definicion |
|---|---|
| `ix_incidencias_cierres_incidencia_id` | `CREATE INDEX ix_incidencias_cierres_incidencia_id ON public.incidencias_cierres USING btree (incidencia_id)` |

### Huerfanos posteriores

Resultado: `0`.

## Cambios que NO se aplicaron

No se aplico:

```sql
incidencias_imagenes_odt.odt -> registro.odt
```

Motivo: la validacion previa encontro `47` registros huerfanos de `66`. Aplicarla ahora romperia la validacion de la constraint.

No se aplico:

```sql
registros_correos_cliente.odt -> registro.odt
```

Motivo: la validacion previa encontro `23` registros huerfanos de `24`. Aplicarla ahora romperia la validacion de la constraint.

## Estado final del MER despues del cambio

El MER queda parcialmente formalizado:

- `registro -> incidencias_cierres` ya es una relacion fisica real en PostgreSQL.
- `registro -> incidencias_imagenes_odt` sigue siendo relacion logica o pendiente.
- `registro -> registros_correos_cliente` sigue siendo relacion logica o pendiente.

La siguiente fase debe decidir si las ODT huerfanas en imagenes y correos deben:

1. crear registros padre en `registro`;
2. migrarse a otra entidad canonica, por ejemplo mantenciones;
3. permanecer como relaciones logicas sin FK global.
