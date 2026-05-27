"""Drop tablas de chat interno (irreversible).

Ejecutar UNA SOLA VEZ después de quitar el código de InternalChat:

    python drop_internal_chat_tables.py

Borra: internal_chat_messages, internal_chat_read_states.
"""
from __future__ import annotations

import sys

from sqlalchemy import inspect, text

from ATC.app.core.db import engine


TABLES_TO_DROP = ["internal_chat_messages", "internal_chat_read_states"]


def main() -> int:
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())

    targets = [t for t in TABLES_TO_DROP if t in existing]
    if not targets:
        print("No hay tablas de chat interno que borrar (ya estaban ausentes).")
        return 0

    print("Voy a DROP las siguientes tablas:")
    for t in targets:
        print(f"  - {t}")
    print()

    with engine.begin() as conn:
        for t in targets:
            print(f"DROP TABLE IF EXISTS {t} ...")
            conn.execute(text(f'DROP TABLE IF EXISTS "{t}" CASCADE'))

    print()
    print("Listo. Tablas eliminadas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
