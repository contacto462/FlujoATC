from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ATC.app.core.incidencias_config import settings


class Base(DeclarativeBase):
    pass


def _build_connect_args(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    if database_url.startswith("postgresql"):
        options: list[str] = []
        lock_timeout = max(0, int(settings.postgres_lock_timeout_ms or 0))
        statement_timeout = max(0, int(settings.postgres_statement_timeout_ms or 0))
        if lock_timeout:
            options.append(f"-c lock_timeout={lock_timeout}")
        if statement_timeout:
            options.append(f"-c statement_timeout={statement_timeout}")
        if options:
            return {"options": " ".join(options)}
    return {}


def build_engine(database_url: str, **kwargs):
    connect_args = _build_connect_args(database_url)
    return create_engine(database_url, future=True, connect_args=connect_args, **kwargs)


engine = build_engine(settings.database_url, echo=False)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

