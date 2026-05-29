import os
import re
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


TABLENAME_RE = re.compile(r'__tablename__\s*=\s*"([^"]+)"')
FK_RE = re.compile(r'ForeignKey\(\s*"([^"]+)"')
CREATE_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z0-9_]+)", re.IGNORECASE)


@dataclass
class TableInfo:
    name: str
    defined_in: set[Path] = field(default_factory=set)
    outgoing_fks: set[str] = field(default_factory=set)  # referenced table names
    incoming_fks: set[str] = field(default_factory=set)  # source table names
    usage_files: set[Path] = field(default_factory=set)  # non-definition files mentioning table


def iter_files(base: Path, exts: set[str]) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        # Skip venv/git for speed
        dirnames[:] = [d for d in dirnames if d not in {".git", ".venv", "__pycache__"}]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in exts:
                out.append(p)
    return out


def load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def main() -> None:
    py_files = iter_files(ROOT / "ATC", {".py"})
    sql_files = iter_files(ROOT, {".sql"})
    doc_files = iter_files(ROOT / "docs", {".md", ".mmd"})

    tables: dict[str, TableInfo] = {}

    def get_table(name: str) -> TableInfo:
        info = tables.get(name)
        if info is None:
            info = TableInfo(name=name)
            tables[name] = info
        return info

    # 1) Collect __tablename__ from python models
    for p in py_files:
        text = load_text(p)
        for m in TABLENAME_RE.finditer(text):
            get_table(m.group(1)).defined_in.add(p)

    # 2) Collect CREATE TABLE names (for tables created outside ORM)
    for p in sql_files + py_files:
        text = load_text(p)
        for m in CREATE_TABLE_RE.finditer(text):
            get_table(m.group(1)).defined_in.add(p)

    # 3) FK graph: scan python files keeping current __tablename__ context
    for p in py_files:
        lines = load_text(p).splitlines()
        current_table: str | None = None
        for line in lines:
            m_tab = TABLENAME_RE.search(line)
            if m_tab:
                current_table = m_tab.group(1)
                continue
            if not current_table:
                continue
            m_fk = FK_RE.search(line)
            if not m_fk:
                continue
            target = m_fk.group(1).split(".", 1)[0].strip()
            if not target:
                continue
            get_table(current_table).outgoing_fks.add(target)
            get_table(target).incoming_fks.add(current_table)

    # 4) "Uso": any mention outside definition files (py+sql+docs+templates)
    searchable_files = (
        iter_files(ROOT / "ATC", {".py", ".html", ".md", ".mmd", ".sql"})
        + doc_files
        + sql_files
    )
    searchable_files = list({p for p in searchable_files})
    definition_files = set()
    for info in tables.values():
        definition_files |= info.defined_in

    for p in searchable_files:
        text = load_text(p)
        for table_name, info in tables.items():
            if p in definition_files:
                continue
            # whole-word-ish match (underscores ok): avoid matching inside longer identifiers
            if re.search(rf"(?<![a-zA-Z0-9_]){re.escape(table_name)}(?![a-zA-Z0-9_])", text):
                info.usage_files.add(p)

    # Normalize list
    all_table_names = sorted(tables.keys())

    # Classifications
    disconnected = [t for t in all_table_names if not tables[t].incoming_fks and not tables[t].outgoing_fks]
    no_incoming = [t for t in all_table_names if not tables[t].incoming_fks]
    no_usage = [t for t in all_table_names if len(tables[t].usage_files) == 0]

    # Output
    print(f"Total tablas detectadas: {len(all_table_names)}")
    print()
    print("Listado (A-Z):")
    for t in all_table_names:
        print(f"- {t}")
    print()
    print("Tablas sin conexiones FK (no entrantes ni salientes):")
    for t in disconnected:
        print(f"- {t}")
    print()
    print("Tablas sin FK entrantes (nadie las referencia por FK):")
    for t in no_incoming:
        print(f"- {t}")
    print()
    print("Tablas con 0 referencias (menciones) fuera de definiciones (candidato 'sin uso' en el repo):")
    for t in no_usage:
        print(f"- {t}")
    print()
    print("Sugerencia SQL para detectar tablas vacias (PostgreSQL):")
    print("SELECT relname AS tabla, n_live_tup AS filas_aprox FROM pg_stat_user_tables ORDER BY n_live_tup ASC, relname ASC;")
    print()
    print("Sugerencia SQL (exacto, pero mas caro) - reemplaza <TABLA>:")
    print("SELECT COUNT(*) FROM <TABLA>;")


if __name__ == "__main__":
    main()
