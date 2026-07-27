# Shim — re-exporta desde core/db.py (unificado en Fase 2).
# Fase 3 eliminará este archivo.
from ATC.app.core.db import (  # noqa: F401
    Base,
    SessionLocal,
    engine,
    get_db,
    build_engine,
)
