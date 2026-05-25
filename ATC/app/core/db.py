from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from ATC.app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 2},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# AJUSTE SOPORTE REGISTRO SQL #
_INCIDENCIAS_DATABASE_URL = settings.INCIDENCIAS_DATABASE_URL or settings.DATABASE_URL
incidencias_engine = create_engine(
    _INCIDENCIAS_DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 2},
)
IncidenciasSessionLocal = sessionmaker(bind=incidencias_engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# AJUSTE SOPORTE REGISTRO SQL #
def get_incidencias_db():
    db = IncidenciasSessionLocal()
    try:
        yield db
    finally:
        db.close()
