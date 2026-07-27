from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ATC.app.core.db import Base


class EstatusGestionItem(Base):
    """Item de seguimiento de la pagina 'Estatus Gestion' de Prevencion.

    Se siembra una vez desde ATC/app/data/estatus_gestion_prevencion.py
    (snapshot del Excel de Barbara Nunez) con avance en NULL; desde ahi el
    porcentaje se ingresa manualmente en la pagina.
    """

    __tablename__ = "prevencion_estatus_gestion"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    seccion: Mapped[str] = mapped_column(String(255), index=True)
    orden: Mapped[int] = mapped_column(Integer, default=0, index=True)
    documento: Mapped[str] = mapped_column(Text)
    responsable: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    revisor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    avance: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    observaciones: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# Columnas de check de la matriz "Estatus Documentación Técnicos" (snapshot
# de "estatus documentacion tecnicos.xlsx"). Orden = orden de columnas en la
# tabla. Se usa tanto en el modelo como en la ruta/template para no repetir
# la lista de campos en varios lugares.
DOCUMENTACION_TECNICO_CHECK_FIELDS: list[tuple[str, str]] = [
    ("riohs", "RIOHS"),
    ("irl", "IRL"),
    ("difusion_trabajo_altura", 'Difusión procedimiento "Trabajo en altura" y su evaluación'),
    ("difusion_instalacion_camaras", 'Difusión procedimiento "Instalación de cámaras" y su evaluación'),
    ("difusion_mantencion_camaras", 'Difusión procedimiento "Mantención de cámaras" y su evaluación'),
    ("examen_altura", "Examen de altura"),
    ("capacitacion_altura", "Capacitación de altura"),
    ("curso_altura_mutual", 'Curso de altura "mutual"'),
    ("entrega_epp", "Entrega de EPP"),
    ("entrega_matriz", "Entrega de matriz"),
    ("induccion_hombre_nuevo", "Inducción hombre nuevo y su evaluación"),
]


class EstatusDocumentacionTecnico(Base):
    """Matriz de documentacion/capacitaciones por tecnico de la pagina
    'Estatus Documentación Técnicos' de Prevencion.

    Se siembra una vez desde ATC/app/data/estatus_documentacion_tecnicos.py
    (snapshot del Excel "estatus documentacion tecnicos.xlsx") con todos los
    checks en False; el RUT queda vacio para completarlo en la pagina (el
    Excel de origen no lo traia).
    """

    __tablename__ = "prevencion_estatus_documentacion_tecnicos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    orden: Mapped[int] = mapped_column(Integer, default=0, index=True)
    nombre: Mapped[str] = mapped_column(String(255), index=True)
    rut: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    riohs: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    irl: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    difusion_trabajo_altura: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    difusion_instalacion_camaras: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    difusion_mantencion_camaras: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    examen_altura: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    capacitacion_altura: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    curso_altura_mutual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    entrega_epp: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    entrega_matriz: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    induccion_hombre_nuevo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
