# Checklist MER profesional

## Validacion final
- [ ] Nombres consistentes por dominio.
- [ ] Todas las tablas productivas tienen primary key clara.
- [ ] Las foreign keys reales cubren relaciones obligatorias.
- [ ] Las cardinalidades estan documentadas.
- [ ] Existen indices para joins, bandejas, filtros por estado, fechas, ODT, usuario y cliente.
- [ ] CHECK/UNIQUE/NOT NULL/DEFAULT reflejan reglas de negocio reales.
- [ ] Estados, roles, canales y prioridades tienen dominio controlado.
- [ ] Fechas, montos, booleanos y numeros usan tipos PostgreSQL adecuados.
- [ ] No hay duplicidad innecesaria de imagenes, cierres, clientes o catalogos.
- [ ] Los campos ambiguos tienen definicion funcional y propietario.
- [ ] El MER visual separa dominios: soporte, ventas, incidencias, protocolos y auditoria.
- [ ] Las tablas legacy/no modeladas estan documentadas o incorporadas.
- [ ] El esquema se administra mediante migraciones versionadas.
- [ ] El modelo esta probado en staging antes de aplicarse a produccion.

## Estado actual
- Primary keys: mayoritariamente correcto.
- Foreign keys: correctas en nucleos principales, incompletas en incidencias/cierres/relaciones por ODT.
- Normalizacion: aceptable en soporte y ventas; debil en incidencias/catalogos legacy.
- Presentacion profesional: posible con observaciones, no como MER fisico perfecto hasta cerrar inconsistencias.
