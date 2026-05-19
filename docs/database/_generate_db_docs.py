import importlib
import importlib.util
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import create_engine, inspect, text


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "database"


def parse_env(path):
    env = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def norm_url(value):
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://") :]
    if value.startswith("postgresql://") and "+psycopg" not in value and "+psycopg2" not in value:
        value = value.replace("postgresql://", "postgresql+psycopg2://", 1)
    return value


def mask_url(value):
    try:
        display = value.replace("postgresql+psycopg2://", "postgresql://", 1).replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
        parsed = urlsplit(display)
        return f"{parsed.scheme}://***:***@{parsed.hostname}:{parsed.port}{parsed.path}"
    except Exception:
        return "<url no parseable>"


def clear_app_modules():
    for key in list(sys.modules):
        if key == "app" or key.startswith("app.") or key == "venta_models_direct":
            sys.modules.pop(key, None)
    sys.path[:] = [p for p in sys.path if p not in {str(ROOT / "ATC"), str(ROOT / "Incidencias")}]
    os.chdir(ROOT)


def load_model_metadata(project):
    clear_app_modules()
    base = ROOT / project
    sys.path.insert(0, str(base))
    os.chdir(base)
    for key, value in parse_env(base / ".env").items():
        os.environ[key] = value
    errors = []
    try:
        if project == "ATC":
            from app.core.db import Base

            modules = [
                "app.models.user",
                "app.models.requester",
                "app.models.ticket",
                "app.models.message",
                "app.models.ticket_history",
                "app.models.ticket_sla_feedback",
                "app.models.ticket_sla_feedback_event",
                "app.models.ticket_message_read_state",
                "app.models.ticket_internal_note_read_state",
                "app.models.ticket_alert_read_state",
                "app.models.internal_chat_read_state",
                "app.models.internal_chat_message",
                "app.models.incidencia",
                "app.models.incidencia_imagen",
                "app.models.email_sync_state",
                "app.models.automation_log",
            ]
            for module in modules:
                try:
                    importlib.import_module(module)
                except Exception as exc:
                    errors.append(f"{module}: {exc}")
        else:
            from app.database import Base

            try:
                importlib.import_module("app.models")
            except Exception as exc:
                errors.append(f"app.models: {exc}")
            try:
                spec = importlib.util.spec_from_file_location(
                    "venta_models_direct", ROOT / "Incidencias" / "app" / "venta" / "models.py"
                )
                module = importlib.util.module_from_spec(spec)
                sys.modules["venta_models_direct"] = module
                spec.loader.exec_module(module)
            except Exception as exc:
                errors.append(f"app.venta.models: {exc}")
        tables = {}
        for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name):
            tables[table.name] = {
                "columns": {
                    column.name: {
                        "type": str(column.type),
                        "nullable": bool(column.nullable),
                        "pk": bool(column.primary_key),
                    }
                    for column in table.columns
                },
                "fks": sorted(
                    (fk.parent.name, ".".join(fk.target_fullname.split(".")[-2:]), fk.ondelete or "")
                    for fk in table.foreign_keys
                ),
            }
        return {"ok": True, "errors": errors, "tables": tables}
    except Exception as exc:
        return {"ok": False, "errors": errors + [repr(exc)], "tables": {}}
    finally:
        clear_app_modules()


def introspect_db(label, url):
    data = {
        "label": label,
        "url_masked": mask_url(url),
        "ok": False,
        "error": None,
        "database": None,
        "version": None,
        "schemas": {},
    }
    try:
        engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            data["database"] = conn.execute(text("select current_database()")).scalar()
            data["version"] = conn.execute(text("select version()")).scalar()
        inspector = inspect(engine)
        data["ok"] = True
        for schema in [s for s in inspector.get_schema_names() if s not in {"pg_catalog", "information_schema"}]:
            tables = sorted(inspector.get_table_names(schema=schema))
            if not tables:
                continue
            data["schemas"][schema] = {}
            for table in tables:
                data["schemas"][schema][table] = {
                    "columns": inspector.get_columns(table, schema=schema),
                    "pk": inspector.get_pk_constraint(table, schema=schema) or {},
                    "fks": inspector.get_foreign_keys(table, schema=schema) or [],
                    "indexes": inspector.get_indexes(table, schema=schema) or [],
                    "uniques": inspector.get_unique_constraints(table, schema=schema) or [],
                    "checks": inspector.get_check_constraints(table, schema=schema) or [],
                }
        engine.dispose()
    except Exception as exc:
        data["error"] = repr(exc)
    return data


def table_names(db):
    names = set()
    for tables in db.get("schemas", {}).values():
        names.update(tables.keys())
    return names


def db_table(db, table):
    for tables in db.get("schemas", {}).values():
        if table in tables:
            return tables[table]
    return None


def db_fks(db):
    rows = []
    for tables in db.get("schemas", {}).values():
        for table, meta in tables.items():
            for fk in meta["fks"]:
                cols = ", ".join(fk.get("constrained_columns") or [])
                ref = f"{fk.get('referred_table')}.{', '.join(fk.get('referred_columns') or [])}"
                ondelete = (fk.get("options") or {}).get("ondelete") or ""
                rows.append((table, cols, ref, ondelete, fk.get("name") or ""))
    return sorted(rows)


def col_line(column):
    default = column.get("default")
    default_text = f"`{default}`" if default is not None else ""
    nullable = "NULL" if column.get("nullable") else "NOT NULL"
    return f"| `{column['name']}` | `{column['type']}` | {nullable} | {default_text} |"


def constraints_text(meta):
    lines = []
    pk_cols = meta["pk"].get("constrained_columns") or []
    pk = ", ".join(f"`{col}`" for col in pk_cols) if pk_cols else "sin PK detectada"
    lines.append(f"- PK: `{meta['pk'].get('name') or 'sin nombre'}` ({pk})")
    if meta["fks"]:
        lines.append("- FKs:")
        for fk in meta["fks"]:
            opts = fk.get("options") or {}
            ondelete = f" ON DELETE {opts.get('ondelete')}" if opts.get("ondelete") else ""
            lines.append(
                f"  - `{fk.get('name')}`: `{', '.join(fk.get('constrained_columns') or [])}` -> "
                f"`{fk.get('referred_table')}.{', '.join(fk.get('referred_columns') or [])}`{ondelete}"
            )
    else:
        lines.append("- FKs: no declaradas.")
    if meta["uniques"]:
        lines.append("- Unique constraints:")
        for item in meta["uniques"]:
            lines.append(f"  - `{item.get('name')}` ({', '.join('`' + c + '`' for c in (item.get('column_names') or []))})")
    else:
        lines.append("- Unique constraints: no declaradas.")
    if meta["indexes"]:
        lines.append("- Indices:")
        for item in meta["indexes"]:
            unique = " UNIQUE" if item.get("unique") else ""
            cols = item.get("column_names") or []
            lines.append(f"  - `{item.get('name')}`{unique} ({', '.join('`' + c + '`' for c in cols)})")
    else:
        lines.append("- Indices: no secundarios detectados.")
    if meta["checks"]:
        lines.append("- Check constraints:")
        for item in meta["checks"]:
            lines.append(f"  - `{item.get('name')}`: `{item.get('sqltext')}`")
    else:
        lines.append("- Check constraints: no declaradas.")
    return "\n".join(lines)


MERMAID = """erDiagram
    USERS ||--o{ TICKETS : asigna
    USERS ||--o{ MESSAGES : envia
    USERS ||--o{ TICKET_ASSIGNMENT_HISTORY : cambia
    USERS ||--o{ INTERNAL_CHAT_MESSAGES : envia
    USERS ||--o| INTERNAL_CHAT_READ_STATES : lee
    USERS ||--o{ TICKET_ALERT_READ_STATES : lee
    REQUESTERS ||--o{ TICKETS : solicita
    TICKETS ||--o{ MESSAGES : contiene
    TICKETS ||--o{ TICKET_ASSIGNMENT_HISTORY : registra
    TICKETS ||--o| TICKET_SLA_FEEDBACK : califica
    TICKETS ||--o{ TICKET_SLA_FEEDBACK_EVENTS : audita
    TICKETS ||--o{ TICKET_MESSAGE_READ_STATES : lectura_mensajes
    TICKETS ||--o{ TICKET_INTERNAL_NOTE_READ_STATES : lectura_notas
    TICKETS ||--o{ AUTOMATION_LOGS : automatiza

    BBDD_CLIENTES ||--o{ BBDD_SUCURSALES : posee
    BBDD_SUCURSALES ||--o{ SUCURSAL_CONTACTOS_EMERGENCIA : tiene
    BBDD_SUCURSALES ||--o{ SUCURSAL_PERSONAS_AUTORIZADAS : autoriza
    BBDD_SUCURSALES ||--o{ SUCURSAL_GUARDIAS : asigna
    BBDD_CLIENTES ||--o{ VENTA_ODS : solicita
    VENTA_ODS ||--o{ VENTA_ODS_ARCHIVOS : adjunta
    VENTA_ODS ||--o| ADMINISTRACION_ODT : gestiona
    VENTA_ODS ||--o| FINANZAS_ODT : factura
    VENTA_ODS ||--o| SERVICIO_TECNICO_VENTAS_ODT : instala
    VENTA_ODS ||--o| OPERACIONES_VENTA_ODT : coordina

    PROTOCOLOS_REGISTRO ||--o{ PROTOCOLOS_INFORMES : genera
    INCIDENCIAS_DATA ||--o{ INCIDENCIAS_CIERRES : cierra_logico
    INCIDENCIAS_DATA ||--o| INCIDENCIAS_IMAGENES_ODT : evidencia_logica
    REGISTRO ||--o| INCIDENCIAS_IMAGENES_ODT : evidencia_por_odt
    REGISTRO ||--o{ REGISTROS_CORREOS_CLIENTE : notifica
    REGISTRO ||--o{ RENDICIONES : consume_gastos

    USERS {
        int id PK
        string username UK
        string role
        boolean is_active
    }
    REQUESTERS {
        int id PK
        string email
        string name
    }
    TICKETS {
        int id PK
        int requester_id FK
        int assigned_to_id FK
        string status
        string priority
        timestamp created_at
    }
    MESSAGES {
        int id PK
        int ticket_id FK
        int sender_id FK
        string channel
        timestamp created_at
    }
    TICKET_ASSIGNMENT_HISTORY {
        int id PK
        int ticket_id FK
        int from_user_id FK
        int to_user_id FK
        int changed_by_id FK
    }
    TICKET_SLA_FEEDBACK {
        int ticket_id PK_FK
        int rating
    }
    TICKET_SLA_FEEDBACK_EVENTS {
        int id PK
        int ticket_id FK
        string event_type
    }
    AUTOMATION_LOGS {
        int id PK
        int ticket_id FK
        string rule_key
        string status
    }
    INTERNAL_CHAT_MESSAGES {
        int id PK
        int sender_id FK
        text content
    }
    INTERNAL_CHAT_READ_STATES {
        int user_id PK_FK
        int last_seen_message_id
    }
    TICKET_ALERT_READ_STATES {
        int user_id PK_FK
        int last_seen_alert_id
    }
    TICKET_MESSAGE_READ_STATES {
        int user_id PK_FK
        int ticket_id FK
    }
    TICKET_INTERNAL_NOTE_READ_STATES {
        int user_id PK_FK
        int ticket_id FK
    }
    BBDD_CLIENTES {
        int id PK
        string rut UK
        string cliente UK
    }
    BBDD_SUCURSALES {
        int id PK
        string rut FK
        string nombre_sucursal
        string direccion_sucursal
    }
    SUCURSAL_CONTACTOS_EMERGENCIA {
        int id PK
        int sucursal_id FK
        string nombre
    }
    SUCURSAL_PERSONAS_AUTORIZADAS {
        int id PK
        int sucursal_id FK
        string nombre
    }
    SUCURSAL_GUARDIAS {
        int id PK
        int sucursal_id FK
        string nombre
    }
    VENTA_ODS {
        int id PK
        string codigo UK
        string rut_cliente FK
        string estado
    }
    VENTA_ODS_ARCHIVOS {
        int id PK
        int ods_id FK
        string codigo_ods
    }
    ADMINISTRACION_ODT {
        int id PK
        string odt FK_UK
        boolean finalizado
    }
    FINANZAS_ODT {
        int id PK
        string odt FK_UK
        boolean finalizado
    }
    SERVICIO_TECNICO_VENTAS_ODT {
        int id PK
        string odt FK_UK
        boolean finalizado
    }
    OPERACIONES_VENTA_ODT {
        int id PK
        string odt FK_UK
        boolean terminado
    }
    PROTOCOLOS_REGISTRO {
        int id PK
        string cliente
        string sucursal
        string tipo_protocolo
    }
    PROTOCOLOS_INFORMES {
        int id PK
        int registro_id FK
        string tipo_informe
        string estado
    }
    REGISTRO {
        int id PK
        string odt UK
        string cliente
        string estado
    }
    INCIDENCIAS_DATA {
        int id PK
        string odt
        string cliente
        string estado
    }
    INCIDENCIAS_CIERRES {
        int id PK
        int incidencia_id FK_PENDIENTE
        string odt
    }
    INCIDENCIAS_IMAGENES_ODT {
        int id PK
        string odt UK
        json imagenes
    }
    REGISTROS_CORREOS_CLIENTE {
        int id PK
        string odt
        string estado
    }
    RENDICIONES {
        int id PK
        int folio UK
        string odt
        numeric monto_total
    }
"""


def main():
    env = parse_env(ROOT / "ATC" / ".env")
    dbs = {
        "ATC/helpdesk": introspect_db("ATC/helpdesk", norm_url(env["DATABASE_URL"])),
        "Incidencias": introspect_db("Incidencias", norm_url(env["INCIDENCIAS_DATABASE_URL"])),
    }
    models = {"ATC": load_model_metadata("ATC"), "Incidencias": load_model_metadata("Incidencias")}
    comparisons = {}
    for label, model_key in [("ATC/helpdesk", "ATC"), ("Incidencias", "Incidencias")]:
        db_names = table_names(dbs[label])
        model_names = set(models[model_key]["tables"])
        col_diffs = []
        for table in sorted(db_names & model_names):
            real_cols = {c["name"] for c in db_table(dbs[label], table)["columns"]}
            model_cols = set(models[model_key]["tables"][table]["columns"])
            if real_cols - model_cols or model_cols - real_cols:
                col_diffs.append((table, sorted(model_cols - real_cols), sorted(real_cols - model_cols)))
        fk_real = {(t, c, r) for t, c, r, _, _ in db_fks(dbs[label])}
        fk_model = {(t, c, r) for t, meta in models[model_key]["tables"].items() for c, r, _ in meta["fks"]}
        comparisons[label] = {
            "extra_db": sorted(db_names - model_names),
            "missing_db": sorted(model_names - db_names),
            "column_diffs": col_diffs,
            "fk_missing_db": sorted(fk_model - fk_real),
            "fk_extra_db": sorted(fk_real - fk_model),
        }

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_snapshot(dbs, now)
    write_review(dbs, comparisons)
    write_mer_propuesto()
    write_sql()
    write_checklist()
    write_final()
    for name in [
        "01_schema_snapshot.md",
        "02_mer_review_profesional.md",
        "03_mer_propuesto.md",
        "04_sql_sugerido_no_ejecutar.sql",
        "05_checklist_mer_profesional.md",
        "MER_FINAL.md",
        "MER_FINAL.mmd",
    ]:
        path = OUT / name
        print(name, path.exists(), path.stat().st_size if path.exists() else 0)


def write_snapshot(dbs, now):
    parts = [
        "# Snapshot del esquema PostgreSQL\n\n",
        f"Fecha de inspeccion: {now}\n\n",
        "## Resumen\n",
        "Se inspeccionaron `DATABASE_URL` y `INCIDENCIAS_DATABASE_URL` desde `ATC/.env`. No se ejecutaron cambios sobre PostgreSQL; solo lectura de catalogo y metadatos.\n\n",
    ]
    for label, db in dbs.items():
        parts += [f"### Base `{label}`\n", f"- Conexion: `{db['url_masked']}`\n", f"- Estado: {'OK' if db['ok'] else 'ERROR'}\n"]
        if db["ok"]:
            total = sum(len(tables) for tables in db["schemas"].values())
            parts += [f"- Base real: `{db['database']}`\n", f"- Version: `{db['version'].split(',')[0]}`\n", f"- Tablas detectadas: {total}\n\n"]
        else:
            parts += [f"- Error: `{db['error']}`\n\n"]
    parts.append("## Lista de tablas\n")
    for label, db in dbs.items():
        parts.append(f"### `{label}`\n")
        for schema, tables in db.get("schemas", {}).items():
            parts.append(f"- Schema `{schema}`: {', '.join('`' + t + '`' for t in sorted(tables))}\n")
        parts.append("\n")
    parts.append("## Detalle por tabla\n")
    for label, db in dbs.items():
        parts.append(f"# Base `{label}`\n")
        for schema, tables in db.get("schemas", {}).items():
            parts.append(f"## Schema `{schema}`\n")
            for table, meta in tables.items():
                parts += [f"### `{table}`\n", "| Columna | Tipo | Nullable | Default |\n|---|---|---:|---|\n"]
                for column in meta["columns"]:
                    parts.append(col_line(column) + "\n")
                parts.append("\n" + constraints_text(meta) + "\n\n")
    parts.append("## Relaciones detectadas por Foreign Key\n")
    for label, db in dbs.items():
        parts.append(f"### `{label}`\n")
        for table, cols, ref, ondelete, name in db_fks(db):
            od = f"; ON DELETE {ondelete}" if ondelete else ""
            parts.append(f"- `{table}.{cols}` -> `{ref}` (`{name}`{od})\n")
        parts.append("\n")
    (OUT / "01_schema_snapshot.md").write_text("".join(parts), encoding="utf-8")


def write_review(dbs, comparisons):
    issues = [
        ("Alta", "Desalineacion ATC: SQLAlchemy declara `incidencias`, pero `helpdesk` no tiene esa tabla; existen `incidencias_cierres`, `incidencias_tecnicos` e `incidencias_imagenes_odt` sin modelo ATC."),
        ("Alta", "`incidencias_cierres.incidencia_id` no tiene FK real. En `incidencias` apunta logicamente a `incidencias_data.id`, pero no esta declarado."),
        ("Alta", "No se detecto Alembic/migraciones versionadas; esto aumenta la deriva entre codigo y PostgreSQL."),
        ("Media", "`catalogo_clientes` en `incidencias` esta desalineada: el modelo espera `cliente`/`activo`, la base contiene datos de cliente, sucursal y contacto."),
        ("Media", "Hay tablas productivas no modeladas en `incidencias`: `incidencias_data`, `incidencias_cierres`, `sesiones_tecnico`, `users`."),
        ("Media", "Estados, roles, canales y prioridades estan como texto sin CHECK/ENUM en varias tablas."),
        ("Media", "Varias fechas y banderas se almacenan como VARCHAR/TEXT, especialmente en incidencias y seguimiento de venta."),
        ("Media", "Existe duplicidad semantica entre imagenes por ODT y fotos embebidas, y entre `registro`/`incidencias_data`."),
        ("Baja", "Los nombres mezclan estilos (`bbdd_*`, `registro`, `venta_ods`, `odt_ventas`, `incidencias_data`)."),
    ]
    parts = [
        "# Revision profesional del modelo entidad-relacion\n\n",
        "## Resumen ejecutivo\n",
        "El modelo es funcional y tiene relaciones centrales correctamente declaradas, especialmente en tickets y en venta/ODT. No obstante, no esta al 100% alineado con produccion: hay tablas reales no modeladas, una tabla modelada que no existe en `helpdesk`, relaciones logicas sin FK y falta de migraciones versionadas.\n\n",
        "Veredicto: presentable como MER revisado con salvedades, pero no como modelo fisico final de produccion hasta resolver las diferencias criticas.\n\n",
        "## Diagnostico general\n",
        "- Normalizacion: parcial; soporte y venta estan mejor estructurados que incidencias/catalogos legacy.\n",
        "- Integridad referencial: buena en relaciones principales declaradas; incompleta en cierres, imagenes y relaciones por ODT.\n",
        "- Produccion: requiere migraciones, constraints y decision canonica sobre la entidad de incidencia.\n\n",
        "## Inconsistencias entre codigo y PostgreSQL\n",
    ]
    for label, comp in comparisons.items():
        parts.append(f"### `{label}`\n")
        parts.append(f"- Tablas en PostgreSQL sin modelo: {', '.join('`' + x + '`' for x in comp['extra_db']) or 'ninguna'}.\n")
        parts.append(f"- Tablas en modelo sin tabla real: {', '.join('`' + x + '`' for x in comp['missing_db']) or 'ninguna'}.\n")
        if comp["column_diffs"]:
            parts.append("- Diferencias de columnas:\n")
            for table, model_only, db_only in comp["column_diffs"]:
                parts.append(f"  - `{table}`: en modelo/no DB: {model_only or 'ninguna'}; en DB/no modelo: {db_only or 'ninguna'}.\n")
        else:
            parts.append("- Diferencias de columnas: ninguna en tablas coincidentes.\n")
        parts.append(f"- FKs del modelo no presentes en DB: {comp['fk_missing_db'] or 'ninguna'}.\n")
        parts.append(f"- FKs reales no presentes en modelo: {comp['fk_extra_db'] or 'ninguna'}.\n\n")
    parts.append("## Problemas encontrados y recomendaciones\n")
    for priority, text in issues:
        parts.append(f"- Prioridad {priority}: {text}\n")
    parts += [
        "\n## Indices faltantes o recomendados\n",
        "- Prioridad Alta: indice en `incidencias_cierres.incidencia_id` para joins con la incidencia base.\n",
        "- Prioridad Alta: indice en `incidencias_cierres.odt` si el cierre se consulta por ODT.\n",
        "- Prioridad Media: indices compuestos `tickets(status, assigned_to_id, created_at)`, `messages(ticket_id, created_at)`, `registro(estado, fecha_registro)` y `venta_ods(estado, created_at)`.\n",
        "- Prioridad Media: indices por `odt` en tablas de evidencia, correos y rendiciones.\n\n",
        "## Restricciones faltantes sugeridas\n",
        "- CHECK para estados, roles, canales y banderas textuales.\n",
        "- FK para `incidencias_cierres.incidencia_id` hacia la tabla canonica de incidencias.\n",
        "- UNIQUE canonico sobre ODT si el negocio confirma que identifica una incidencia u ODT de forma unica.\n",
        "- NOT NULL en campos obligatorios, despues de revisar datos historicos.\n\n",
        "## Veredicto profesional final\n",
        "El MER actual esta razonablemente correcto en sus dominios centrales, pero incompleto como representacion fisica de produccion. Primero debe corregirse la deriva modelo-base; luego normalizacion/tipos; finalmente nombres y claridad visual.\n",
    ]
    (OUT / "02_mer_review_profesional.md").write_text("".join(parts), encoding="utf-8")


def write_mer_propuesto():
    text = (
        "# MER propuesto profesional\n\n"
        "## Entidades principales\n"
        "- Soporte: `users`, `requesters`, `tickets`, `messages`, historiales, feedback, estados de lectura y automatizaciones.\n"
        "- Clientes y sucursales: `bbdd_clientes`, `bbdd_sucursales` y tablas dependientes.\n"
        "- Ventas/ODT: `venta_ods`, archivos y seguimiento por Administracion, Finanzas, Servicio Tecnico y Operaciones.\n"
        "- Incidencias: `registro`/`incidencias_data`, cierres, imagenes, correos y rendiciones.\n"
        "- Protocolos: `protocolos_registro` e `protocolos_informes`.\n\n"
        "## Cardinalidades\n"
        "- Uno a muchos: cliente a sucursales, cliente a ODS, requester a tickets, ticket a mensajes, venta_ods a archivos.\n"
        "- Uno a uno: ticket a feedback SLA, venta_ods a cada seguimiento departamental.\n"
        "- Muchos a muchos: no hay tablas puente clasicas; los estados de lectura funcionan como puente usuario-ticket.\n"
        "- Relaciones logicas pendientes: cierres, imagenes, correos y rendiciones por `odt` o `incidencia_id` requieren FK o documentacion explicita.\n\n"
        "## Explicacion simple\n"
        "`||--o{` significa uno a muchos. `||--o|` significa uno a cero/uno. `FK_PENDIENTE` marca una relacion recomendable que aun no esta garantizada fisicamente en PostgreSQL.\n\n"
        "## Mermaid ER Diagram\n\n```mermaid\n"
        + MERMAID
        + "```\n"
    )
    (OUT / "03_mer_propuesto.md").write_text(text, encoding="utf-8")


def write_sql():
    text = """-- ============================================================
-- SQL SUGERIDO - NO EJECUTAR SIN REVISION, BACKUP Y MIGRACION
-- ============================================================
-- Este archivo documenta mejoras recomendadas. No fue aplicado a PostgreSQL.
-- Antes de usarlo: validar datos existentes, crear migracion Alembic, probar en staging.

-- 1) Integridad de cierres de incidencias en base incidencias.
-- Supuesto: public.incidencias_data es la tabla canonica de incidencias.
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidencias_cierres_incidencia_id
--     ON public.incidencias_cierres (incidencia_id);
-- ALTER TABLE public.incidencias_cierres
--     ADD CONSTRAINT fk_incidencias_cierres_incidencias_data
--     FOREIGN KEY (incidencia_id) REFERENCES public.incidencias_data(id);

-- 2) Consultas por ODT en cierres e imagenes.
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidencias_cierres_odt
--     ON public.incidencias_cierres (odt);
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidencias_imagenes_odt_odt
--     ON public.incidencias_imagenes_odt (odt);

-- 3) Bandejas de soporte.
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tickets_status_assigned_created
--     ON public.tickets (status, assigned_to_id, created_at DESC);
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_messages_ticket_created
--     ON public.messages (ticket_id, created_at DESC);

-- 4) Dominios controlados en helpdesk.
-- ALTER TABLE public.users
--     ADD CONSTRAINT ck_users_role CHECK (role IN ('admin', 'agent'));
-- ALTER TABLE public.tickets
--     ADD CONSTRAINT ck_tickets_status CHECK (status IN ('open', 'pending', 'resolved', 'closed'));
-- ALTER TABLE public.tickets
--     ADD CONSTRAINT ck_tickets_source CHECK (source IN ('email', 'whatsapp', 'internal'));
-- ALTER TABLE public.messages
--     ADD CONSTRAINT ck_messages_sender_type CHECK (sender_type IN ('requester', 'agent', 'system'));
-- ALTER TABLE public.messages
--     ADD CONSTRAINT ck_messages_channel CHECK (channel IN ('email', 'whatsapp', 'internal'));

-- 5) Ventas/ODT.
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_venta_ods_estado_created
--     ON public.venta_ods (estado, created_at DESC);
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_registro_estado_fecha
--     ON public.registro (estado, fecha_registro DESC);

-- 6) Tipos de datos recomendados, requieren limpieza previa.
-- Ejemplo conceptual. No ejecutar sin revisar valores invalidos:
-- ALTER TABLE public.servicio_tecnico_ventas_odt
--     ALTER COLUMN fecha_inicio_instalacion TYPE timestamp USING fecha_inicio_instalacion::timestamp;
"""
    (OUT / "04_sql_sugerido_no_ejecutar.sql").write_text(text, encoding="utf-8")


def write_checklist():
    text = """# Checklist MER profesional

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
"""
    (OUT / "05_checklist_mer_profesional.md").write_text(text, encoding="utf-8")


def write_final():
    text = (
        "# Mapa Entidad-Relacion Final - Sistema ATC\n\n"
        "## Explicacion del sistema\n"
        "El sistema ATC combina gestion de soporte, clientes, sucursales, ordenes de trabajo/servicio, incidencias, protocolos, evidencias, rendiciones y automatizaciones. Este MER presenta las entidades principales y sus relaciones de negocio mas relevantes.\n\n"
        "## Entidades principales\n"
        "- `users`: usuarios internos del sistema.\n"
        "- `requesters`: solicitantes o clientes que originan tickets.\n"
        "- `tickets` y `messages`: nucleo de atencion y conversacion.\n"
        "- `bbdd_clientes` y `bbdd_sucursales`: maestro de clientes y ubicaciones.\n"
        "- `venta_ods`: orden de servicio/venta principal.\n"
        "- `administracion_odt`, `finanzas_odt`, `servicio_tecnico_ventas_odt`, `operaciones_venta_odt`: seguimiento por area.\n"
        "- `registro` / `incidencias_data`: registros operativos de incidencias.\n"
        "- `protocolos_registro` y `protocolos_informes`: control e informes de protocolos.\n"
        "- `rendiciones`, `incidencias_imagenes_odt`, `registros_correos_cliente`: evidencias y trazabilidad.\n\n"
        "## Relaciones principales y cardinalidades\n"
        "- Un usuario puede tener muchos tickets asignados y muchos mensajes enviados.\n"
        "- Un solicitante puede tener muchos tickets.\n"
        "- Un ticket puede contener muchos mensajes, eventos, historiales y logs de automatizacion.\n"
        "- Un cliente puede tener muchas sucursales y muchas ODT.\n"
        "- Una sucursal puede tener muchos contactos, personas autorizadas y guardias.\n"
        "- Una ODT puede tener muchos archivos y un seguimiento por cada area operativa.\n"
        "- Un protocolo puede generar muchos informes.\n"
        "- Una incidencia puede tener cierres, evidencias, correos y rendiciones asociadas de forma logica por `id` u `odt`.\n\n"
        "## Diagrama Mermaid ER\n\n```mermaid\n"
        + MERMAID
        + "```\n\n"
        "## Como leer el diagrama\n"
        "`||--o{` significa uno a muchos. `||--o|` significa uno a cero/uno. `PK` identifica la tabla; `FK` apunta a otra tabla; `UK` indica valor unico. `FK_PENDIENTE` marca una relacion recomendable que debe validarse y declararse en PostgreSQL antes de tratarla como integridad fisica real.\n\n"
        "## Observaciones finales\n"
        "Este MER es presentable como mapa profesional del sistema, pero conserva salvedades tecnicas importantes: existen tablas reales sin modelo, una tabla modelada sin tabla fisica en `helpdesk`, y relaciones de incidencias por ODT que todavia no estan formalizadas completamente con foreign keys. Para produccion, lo primero es definir la tabla canonica de incidencias y versionar los cambios con migraciones.\n"
    )
    (OUT / "MER_FINAL.md").write_text(text, encoding="utf-8")
    (OUT / "MER_FINAL.mmd").write_text(MERMAID, encoding="utf-8")


if __name__ == "__main__":
    main()
