from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ATC.app.core.db import Base


class InicioTurnoRegistro(Base):
    __tablename__ = "inicio_turno_registros"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rut: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    nombre_guardia: Mapped[str] = mapped_column(String(255), nullable=False)
    tipo_turno: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    recinto: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    sucursal_id: Mapped[Optional[int]] = mapped_column(nullable=True, index=True)
    latitud: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitud: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    precision_metros: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ubicacion_estado: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    ip_origen: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    registrado_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    # Fusion de marcajes duplicados por traslado (ver
    # inicio_turno.py:_GRUPOS_FUSION_RECINTOS): un guardia que marca en dos
    # recintos del mismo grupo el mismo dia (ej. Consistorial 8am ->
    # Juzgado 10am) genera dos filas que antes inflaban el conteo mensual de
    # turnos. El marcaje sobrante se archiva (no se borra) al fusionarse.
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="activo", server_default="activo")
    fusionado_con_id: Mapped[Optional[int]] = mapped_column(nullable=True)
    archivado_motivo: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    archivado_en: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    archivado_por: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class InicioTurnoGuardia(Base):
    __tablename__ = "bbdd_guardias"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rut: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class GuardiaJustificacion(Base):
    """Registro independiente de licencias/faltas/permisos/vacaciones de un
    guardia — no depende de que exista antes un registro en la tabla de
    supervisor (dia+recinto). Sirve como bitacora consultable aparte."""

    __tablename__ = "guardia_justificaciones"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rut: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    nombre_guardia: Mapped[str] = mapped_column(String(255), nullable=False)
    motivo: Mapped[str] = mapped_column(String(40), nullable=False)
    fecha_desde: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    fecha_hasta: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    creado_por: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class RecintoQrGenerado(Base):
    __tablename__ = "inicio_turno_qr_generados"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    recinto_id: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    recinto_label: Mapped[str] = mapped_column(String(255), nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    numero: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    verificador: Mapped[Optional[int]] = mapped_column(Integer, unique=True, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class RondaRegistro(Base):
    __tablename__ = "inicio_turno_ronda_registros"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    qr_generado_id: Mapped[int] = mapped_column(index=True, nullable=False)
    recinto_id: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    recinto_label: Mapped[str] = mapped_column(String(255), nullable=False)
    numero: Mapped[int] = mapped_column(Integer, nullable=False)
    rut_guardia: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    nombre_guardia: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    nota: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    aprobado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    aprobado_por: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    aprobado_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    registrado_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class SupervisorRegistro(Base):
    __tablename__ = "supervisor_registros"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    recinto: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    nombre_guardia: Mapped[str] = mapped_column(String(255), nullable=False)
    tipo_turno: Mapped[str] = mapped_column(String(80), nullable=False)
    supervisor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TurnoEstipulado(Base):
    """Cantidad EXACTA de turnos mensuales contratados por dependencia (ver
    "Cantidad de Guardias Concón" / "GUARDIAS CONCON", planilla entregada
    por el usuario, ago 2026) — la cuota contra la que se compara el
    conteo real de inicio_turno_registros en el informe de cumplimiento
    (routes/inicio_turno.py: _datos_cumplimiento_turnos). Puede haber mas
    de una fila por dependencia si el turno se cubre en tramos horarios
    distintos (ej. Juzgado: Lunes / Miercoles / Jueves con horarios
    separados) — el total estipulado de la dependencia es la suma de sus
    filas."""

    __tablename__ = "turnos_estipulados"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    grupo: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    dependencia: Mapped[str] = mapped_column(String(255), nullable=False)
    sucursal_id: Mapped[Optional[int]] = mapped_column(nullable=True, index=True)
    horario: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    cobertura: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    dotacion: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Turnos estipulados POR DIA en que la fila aplica (columnas "1".."31"
    # de la planilla — no la columna "Dotacion", que es un total de
    # personas asignadas a lo largo del mes/rotacion, no la cantidad
    # simultanea de un dia puntual).
    turnos_dia: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Dias de la semana en que turnos_dia aplica, como lista separada por
    # comas de date.weekday() (0=Lunes .. 6=Domingo), ej. "0,1,2,3,4" para
    # Lun-Vier. NULL/vacio = aplica todos los dias (ej. Concon, siempre
    # constante). Se deriva de la planilla comparando el patron de los 31
    # dias contra el ciclo semanal real de esas columnas — no de la
    # columna "Dias" de texto libre, que en la planilla original venia
    # incompleta para varias filas (ver _importar_turnos_estipulados_*).
    dias_semana: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Referencia informativa (como venian en la planilla) — el calculo real
    # de cumplimiento usa turnos_dia + dias_semana, no estas columnas,
    # porque el total real de un mes concreto depende de cuantos dias de
    # cada tipo caen ese mes especifico.
    turnos_mes_30: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    turnos_mes_31: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
