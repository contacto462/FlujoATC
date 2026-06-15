from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ATC.app.core.config import settings


def _build_connect_args(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    if database_url.startswith("postgresql"):
        options: list[str] = []
        lock_ms = max(0, int(settings.postgres_lock_timeout_ms or 0))
        stmt_ms = max(0, int(settings.postgres_statement_timeout_ms or 0))
        if lock_ms:
            options.append(f"-c lock_timeout={lock_ms}")
        if stmt_ms:
            options.append(f"-c statement_timeout={stmt_ms}")
        if options:
            return {"options": " ".join(options)}
    return {}


def build_engine(database_url: str, **kwargs):
    connect_args = _build_connect_args(database_url)
    return create_engine(database_url, future=True, connect_args=connect_args, **kwargs)


engine = build_engine(settings.DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# Aliases — backwards-compat durante la migración (Fase 2)
incidencias_engine = engine
IncidenciasSessionLocal = SessionLocal


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Alias — web.py usa get_incidencias_db; misma BBDD, se unifica en Fase 4
get_incidencias_db = get_db
