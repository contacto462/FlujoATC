from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ATC.app.core.config import settings


def build_engine(database_url: str, **kwargs):
    kwargs.setdefault("pool_pre_ping", True)
    kwargs.setdefault("pool_size", 15)
    kwargs.setdefault("max_overflow", 25)
    try:
        parsed = make_url(database_url)
    except Exception:
        parsed = None
    if parsed is not None and parsed.drivername.startswith("mssql+pyodbc"):
        kwargs.setdefault("fast_executemany", True)
    return create_engine(database_url, future=True, **kwargs)


engine = build_engine(settings.DATABASE_URL, echo=False)

if str(settings.DATABASE_URL).startswith("mssql+"):
    @event.listens_for(engine, "connect")
    def _set_sqlserver_session_options(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SET DATEFORMAT dmy")
        finally:
            cursor.close()

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
