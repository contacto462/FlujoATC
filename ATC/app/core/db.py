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
incidencias_engine = (
    create_engine(
        settings.INCIDENCIAS_DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 2},
    )
    if settings.INCIDENCIAS_DATABASE_URL
    else None
)
IncidenciasSessionLocal = (
    sessionmaker(bind=incidencias_engine, autoflush=False, autocommit=False)
    if incidencias_engine is not None
    else None
)

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
    if IncidenciasSessionLocal is None:
        raise RuntimeError('# AJUSTE SOPORTE REGISTRO SQL # INCIDENCIAS_DATABASE_URL no configurada.')
    db = IncidenciasSessionLocal()
    try:
        yield db
    finally:
        db.close()
