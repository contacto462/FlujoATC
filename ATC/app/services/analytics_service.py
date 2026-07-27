from datetime import datetime, timedelta, timezone

from sqlalchemy import Date, and_, case, cast, func, literal_column, or_

from ATC.app.models.ticket import Ticket
from ATC.app.models.ticket_sla_feedback import TicketSlaFeedback
from ATC.app.models.user import User


PENDING_STATUS_CODES = (
    "pending",
    "pending_service",
    "pending_client",
)


# =========================================================
# HELPERS DE COMPATIBILIDAD SQL
# =========================================================
def _get_dialect_name(db) -> str:
    """
    Obtiene el nombre del motor usado por SQLAlchemy.

    Ejemplos:
    - mssql
    - postgresql
    - sqlite
    - mysql
    """
    try:
        bind = db.get_bind()
        if bind is not None and bind.dialect is not None:
            return str(bind.dialect.name or "").lower()
    except Exception:
        pass

    return ""


def _seconds_between(db, start_column, end_column):
    """
    Retorna una expresión SQL que calcula la diferencia en segundos
    entre dos columnas de fecha.

    SQL Server:
        DATEDIFF(SECOND, inicio, fin)

    PostgreSQL:
        EXTRACT(EPOCH FROM (fin - inicio))

    SQLite:
        (julianday(fin) - julianday(inicio)) * 86400
    """
    dialect_name = _get_dialect_name(db)

    if dialect_name == "mssql":
        return func.datediff(
            literal_column("SECOND"),
            start_column,
            end_column,
        )

    if dialect_name == "sqlite":
        return (
            func.julianday(end_column)
            - func.julianday(start_column)
        ) * 86400.0

    if dialect_name == "mysql":
        return func.timestampdiff(
            literal_column("SECOND"),
            start_column,
            end_column,
        )

    # PostgreSQL y motores compatibles con EXTRACT(EPOCH).
    return func.extract(
        "epoch",
        end_column - start_column,
    )


def _column_or_now(db, column):
    """
    Devuelve la columna si tiene valor, o "ahora" si es NULL — para medir
    tiempo transcurrido en tickets que aun no tienen respuesta/resolucion,
    igual que el criterio de 'Cumplimiento SLA' (en vez de excluirlos del
    promedio, cuentan con el tiempo que llevan esperando).
    """
    dialect_name = _get_dialect_name(db)

    if dialect_name == "mssql":
        return func.coalesce(column, func.getutcdate())

    if dialect_name == "sqlite":
        return func.coalesce(column, func.datetime("now"))

    return func.coalesce(column, func.now())


def _date_only(column):
    """
    Convierte una columna datetime a DATE.

    Se utiliza CAST(... AS DATE), compatible con SQL Server,
    PostgreSQL, SQLite y otros motores.
    """
    return cast(column, Date)


# =========================================================
# KPI RESUMEN GENERAL
# =========================================================
def _pct(part: int | float, total: int | float) -> float:
    """
    Evita división por cero y normaliza porcentajes.
    """
    if not total:
        return 0.0

    return round(
        (float(part) / float(total)) * 100,
        2,
    )


def _apply_ticket_created_range(
    query,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    """
    Aplica filtros opcionales sobre Ticket.created_at.
    """
    if date_from is not None:
        query = query.filter(
            Ticket.created_at >= date_from
        )

    if date_to is not None:
        query = query.filter(
            Ticket.created_at <= date_to
        )

    return query


def get_overview_kpis(
    db,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    base_query = db.query(Ticket).filter(
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
    )

    base_query = _apply_ticket_created_range(
        base_query,
        date_from,
        date_to,
    )

    total = base_query.count()

    open_count = base_query.filter(
        Ticket.status == "open"
    ).count()

    pending_count = base_query.filter(
        Ticket.status.in_(PENDING_STATUS_CODES)
    ).count()

    resolved_count = base_query.filter(
        Ticket.status == "resolved"
    ).count()

    backlog_count = open_count + pending_count

    assigned_count = base_query.filter(
        Ticket.assigned_to_id.isnot(None)
    ).count()

    reopened_count = base_query.filter(
        Ticket.reopen_count > 0
    ).count()

    now_utc = datetime.now(timezone.utc)

    # Si existe un filtro "hasta", se utiliza como cierre del rango.
    range_end_reference = date_to or now_utc
    since_7d = range_end_reference - timedelta(days=7)

    created_last_7d = base_query.filter(
        Ticket.created_at >= since_7d
    ).count()

    resolved_last_7d = base_query.filter(
        Ticket.resolved_at.isnot(None),
        Ticket.resolved_at >= since_7d,
    ).count()

    # =====================================================
    # TIEMPO PROMEDIO DE PRIMERA RESPUESTA
    # =====================================================
    first_reply_seconds_expression = _seconds_between(
        db,
        Ticket.created_at,
        Ticket.first_agent_reply_at,
    )

    avg_frt_query = db.query(
        func.avg(first_reply_seconds_expression)
    ).filter(
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
        Ticket.first_agent_reply_at.isnot(None),
        Ticket.created_at.isnot(None),
    )

    avg_frt_query = _apply_ticket_created_range(
        avg_frt_query,
        date_from,
        date_to,
    )

    avg_frt_seconds = avg_frt_query.scalar()

    # =====================================================
    # TIEMPO PROMEDIO DE RESOLUCIÓN
    # =====================================================
    resolution_seconds_expression = _seconds_between(
        db,
        Ticket.created_at,
        Ticket.resolved_at,
    )

    avg_resolution_query = db.query(
        func.avg(resolution_seconds_expression)
    ).filter(
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
        Ticket.resolved_at.isnot(None),
        Ticket.created_at.isnot(None),
    )

    avg_resolution_query = _apply_ticket_created_range(
        avg_resolution_query,
        date_from,
        date_to,
    )

    avg_resolution_seconds = avg_resolution_query.scalar()

    # =====================================================
    # CSAT / CALIDAD
    # =====================================================
    csat_base_query = (
        db.query(TicketSlaFeedback)
        .join(
            Ticket,
            Ticket.id == TicketSlaFeedback.ticket_id,
        )
        .filter(
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
        )
    )

    if date_from is not None:
        csat_base_query = csat_base_query.filter(
            Ticket.created_at >= date_from
        )

    if date_to is not None:
        csat_base_query = csat_base_query.filter(
            Ticket.created_at <= date_to
        )

    csat_avg_raw = (
        csat_base_query
        .filter(
            TicketSlaFeedback.technician_rating.isnot(None)
        )
        .with_entities(
            func.avg(
                TicketSlaFeedback.technician_rating
            )
        )
        .scalar()
    )

    csat_rating_count = csat_base_query.filter(
        TicketSlaFeedback.technician_rating.isnot(None)
    ).count()

    csat_response_count = csat_base_query.filter(
        TicketSlaFeedback.submitted_at.isnot(None)
    ).count()

    resolution_answered_count = csat_base_query.filter(
        TicketSlaFeedback.resolution_satisfied.isnot(None)
    ).count()

    resolution_yes_count = csat_base_query.filter(
        TicketSlaFeedback.resolution_satisfied == True
    ).count()

    return {
        "total": total,
        "open": open_count,
        "pending": pending_count,
        "resolved": resolved_count,
        "backlog": backlog_count,
        "backlog_pct": _pct(backlog_count, total),
        "resolution_rate_pct": _pct(
            resolved_count,
            total,
        ),
        "assignment_rate_pct": _pct(
            assigned_count,
            total,
        ),
        "reopened_tickets": reopened_count,
        "reopen_rate_pct": _pct(
            reopened_count,
            resolved_count,
        ),
        "created_last_7d": created_last_7d,
        "resolved_last_7d": resolved_last_7d,
        "throughput_7d_pct": _pct(
            resolved_last_7d,
            created_last_7d,
        ),
        "avg_frt_hours": round(
            float(avg_frt_seconds or 0) / 3600,
            2,
        ),
        "avg_resolution_hours": round(
            float(avg_resolution_seconds or 0) / 3600,
            2,
        ),
        "csat_avg_rating": (
            round(float(csat_avg_raw), 2)
            if csat_avg_raw is not None
            else None
        ),
        "csat_rating_count": csat_rating_count,
        "csat_response_rate_pct": _pct(
            csat_response_count,
            resolved_count,
        ),
        "resolution_satisfaction_pct": _pct(
            resolution_yes_count,
            resolution_answered_count,
        ),
    }


# =========================================================
# SLA SUMMARY
# =========================================================
# Umbrales unicos (ya no dependen de la prioridad del ticket):
#   verde  ("ok")      -> 0 a 12 horas sin respuesta/resolucion
#   amarillo ("at_risk") -> entre 12 y 24 horas
#   rojo   ("overdue") -> mas de 24 horas (mas de 1 dia)
# Se evalua sobre TODOS los tickets del rango filtrado (no solo los activos):
# si ya hubo respuesta/resolucion se usa el tiempo real que tomo; si todavia
# esta pendiente, se usa el tiempo transcurrido hasta ahora.
SLA_GREEN_HOURS = 12
SLA_RED_HOURS = 24


def get_sla_summary(
    db,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    now = datetime.now(timezone.utc)

    tickets_query = db.query(Ticket).filter(
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
        Ticket.created_at.isnot(None),
    )
    tickets_query = _apply_ticket_created_range(tickets_query, date_from, date_to)
    tickets = tickets_query.all()

    def _clasificar(hours: float) -> str:
        if hours > SLA_RED_HOURS:
            return "overdue"
        if hours > SLA_GREEN_HOURS:
            return "at_risk"
        return "ok"

    first_reply = {"overdue": 0, "at_risk": 0, "ok": 0}
    resolution = {"overdue": 0, "at_risk": 0, "ok": 0}

    for ticket in tickets:
        reply_reference = ticket.first_agent_reply_at or now
        elapsed_reply_hours = (reply_reference - ticket.created_at).total_seconds() / 3600
        first_reply[_clasificar(elapsed_reply_hours)] += 1

        resolution_reference = ticket.resolved_at or now
        elapsed_resolution_hours = (resolution_reference - ticket.created_at).total_seconds() / 3600
        resolution[_clasificar(elapsed_resolution_hours)] += 1

    total_first = sum(first_reply.values())
    total_resolution = sum(resolution.values())

    # "Cumplimiento" = respondido/resuelto dentro de 24h (verde + amarillo);
    # solo lo rojo (mas de 1 dia) se considera fuera de SLA.
    first_reply_compliance = round(
        (first_reply["ok"] + first_reply["at_risk"]) / total_first * 100, 2
    ) if total_first > 0 else 0.0

    resolution_compliance = round(
        (resolution["ok"] + resolution["at_risk"]) / total_resolution * 100, 2
    ) if total_resolution > 0 else 0.0

    return {
        "first_reply": first_reply,
        "resolution": resolution,
        "first_reply_compliance": first_reply_compliance,
        "resolution_compliance": resolution_compliance,
    }


# =========================================================
# VOLUMEN DE TICKETS
# =========================================================
def get_ticket_volume_30d(
    db,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    since = (
        datetime.now(timezone.utc)
        - timedelta(days=30)
    )

    created_date_expression = _date_only(
        Ticket.created_at
    )

    volume_query = db.query(
        created_date_expression.label("day"),
        func.count(Ticket.id).label("count"),
    ).filter(
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
    )

    # Sin filtros se utilizan los últimos 30 días.
    # Con filtros se utiliza el rango seleccionado.
    if date_from is None and date_to is None:
        volume_query = volume_query.filter(
            Ticket.created_at >= since
        )
    else:
        volume_query = _apply_ticket_created_range(
            volume_query,
            date_from,
            date_to,
        )

    rows = (
        volume_query
        .group_by(created_date_expression)
        .order_by(created_date_expression)
        .all()
    )

    return [
        {
            "day": (
                row.day.isoformat()
                if hasattr(row.day, "isoformat")
                else str(row.day)
            ),
            "count": int(row.count or 0),
        }
        for row in rows
    ]


# =========================================================
# CANTIDAD DE TICKETS POR MES / DIA (historico, con zoom dinamico)
# =========================================================
def get_ticket_volume_monthly(
    db,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    """Cantidad de tickets, agrupados por dia si el rango filtrado es corto
    (<=62 dias, mismo criterio que 'Incidencias por mes' en coordinacion, para
    que se vea el 'zoom' dentro del rango) o por mes si es largo o no hay
    filtro. Sin filtro, ultimos 12 meses agrupados por mes."""
    if date_from is None and date_to is None:
        today = datetime.now(timezone.utc)
        month = today.month - 11
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        date_from = datetime(year, month, 1, tzinfo=timezone.utc)

    span_days = None
    if date_from is not None and date_to is not None:
        span_days = (date_to - date_from).days
    daily = span_days is not None and span_days <= 62

    if daily:
        bucket_expression = _date_only(Ticket.created_at)
        extra_group_cols = ()
        key_field = "day"
    else:
        year_expression = func.year(Ticket.created_at)
        month_number_expression = func.month(Ticket.created_at)
        bucket_expression = func.datefromparts(year_expression, month_number_expression, 1)
        extra_group_cols = (year_expression, month_number_expression)
        key_field = "month"

    volume_query = db.query(
        bucket_expression.label("bucket"),
        Ticket.status.label("status"),
        func.count(Ticket.id).label("count"),
    ).filter(
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
    )
    volume_query = _apply_ticket_created_range(volume_query, date_from, date_to)

    rows = (
        volume_query
        .group_by(bucket_expression, Ticket.status, *extra_group_cols)
        .order_by(bucket_expression)
        .all()
    )

    resolved_status_codes = {"resolved", "resolved_service", "resolved_client", "closed"}

    buckets: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        key = row.bucket.isoformat() if hasattr(row.bucket, "isoformat") else str(row.bucket)
        if key not in buckets:
            buckets[key] = {key_field: key, "pendientes": 0, "terminadas": 0}
            order.append(key)
        status_code = (row.status or "").strip().lower()
        if status_code in resolved_status_codes:
            buckets[key]["terminadas"] += int(row.count or 0)
        else:
            buckets[key]["pendientes"] += int(row.count or 0)

    return {"granularity": "day" if daily else "month", "buckets": [buckets[k] for k in order]}


_PRIORITY_RANK = {"urgent": 0, "high": 1, "medium": 2, "low": 3}


def get_tickets_priority_detail(
    db,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    """Lista de tickets del rango (para el popup '+ Info'), ordenada de
    mas urgente a menos urgente, con el titulo (subject) de cada uno."""
    tickets_query = db.query(Ticket).filter(
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
    )
    tickets_query = _apply_ticket_created_range(tickets_query, date_from, date_to)

    tickets = tickets_query.all()
    tickets.sort(
        key=lambda t: (
            _PRIORITY_RANK.get((t.priority or "").strip().lower(), 4),
            -(t.created_at.timestamp() if t.created_at else 0),
        )
    )

    return [
        {
            "id": t.id,
            "subject": t.subject or "(sin título)",
            "priority": (t.priority or "").strip().lower() or "unassigned",
            "status": t.status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tickets
    ]


# =========================================================
# TICKETS POR ESTADO DETALLADO
# =========================================================
def get_ticket_status_breakdown(
    db,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    def _count_status(status_code: str) -> int:
        query = db.query(Ticket).filter(
            Ticket.status == status_code,
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
        )

        query = _apply_ticket_created_range(
            query,
            date_from,
            date_to,
        )

        return query.count()

    closed_query = db.query(Ticket).filter(
        or_(
            Ticket.status == "resolved",
            Ticket.is_deleted == True,
            Ticket.is_spam == True,
        )
    )

    closed_query = _apply_ticket_created_range(
        closed_query,
        date_from,
        date_to,
    )

    return {
        "open": _count_status("open"),
        "pending": _count_status("pending"),
        "pending_service": _count_status(
            "pending_service"
        ),
        "pending_client": _count_status(
            "pending_client"
        ),
        "closed": closed_query.count(),
    }


# =========================================================
# TICKETS POR PRIORIDAD
# =========================================================
def get_tickets_by_priority(
    db,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    tickets_query = db.query(Ticket).filter(
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
    )

    tickets_query = _apply_ticket_created_range(
        tickets_query,
        date_from,
        date_to,
    )

    result = {
        "unassigned": 0,
        "low": 0,
        "medium": 0,
        "high": 0,
        "urgent": 0,
    }

    for ticket in tickets_query.all():
        priority = (
            ticket.priority or ""
        ).strip().lower()

        if priority in result:
            result[priority] += 1
        else:
            result["unassigned"] += 1

    return result


# =========================================================
# PRIMERA RESPUESTA V/S TIEMPO DE RESOLUCION (historico)
# =========================================================
def get_response_resolution_history(
    db,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    """Promedio de horas de 1a respuesta y de resolucion, historico. Mismo
    criterio de zoom que 'Cantidad de tickets': por dia si el rango filtrado
    es <=62 dias, por mes si es largo o no hay filtro (ultimos 12 meses)."""
    if date_from is None and date_to is None:
        today = datetime.now(timezone.utc)
        month = today.month - 11
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        date_from = datetime(year, month, 1, tzinfo=timezone.utc)

    span_days = None
    if date_from is not None and date_to is not None:
        span_days = (date_to - date_from).days
    daily = span_days is not None and span_days <= 62

    if daily:
        bucket_expression = _date_only(Ticket.created_at)
        extra_group_cols = ()
        key_field = "day"
    else:
        year_expression = func.year(Ticket.created_at)
        month_number_expression = func.month(Ticket.created_at)
        bucket_expression = func.datefromparts(year_expression, month_number_expression, 1)
        extra_group_cols = (year_expression, month_number_expression)
        key_field = "month"

    frt_seconds_expression = _seconds_between(
        db, Ticket.created_at, _column_or_now(db, Ticket.first_agent_reply_at)
    )
    resolution_seconds_expression = _seconds_between(
        db, Ticket.created_at, _column_or_now(db, Ticket.resolved_at)
    )

    query = db.query(
        bucket_expression.label("bucket"),
        func.avg(frt_seconds_expression).label("avg_frt"),
        func.avg(resolution_seconds_expression).label("avg_resolution"),
    ).filter(
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
    )
    query = _apply_ticket_created_range(query, date_from, date_to)

    rows = (
        query
        .group_by(bucket_expression, *extra_group_cols)
        .order_by(bucket_expression)
        .all()
    )

    buckets = []
    for row in rows:
        key = row.bucket.isoformat() if hasattr(row.bucket, "isoformat") else str(row.bucket)
        buckets.append({
            key_field: key,
            "frt_hours": round(float(row.avg_frt) / 3600, 2) if row.avg_frt is not None else None,
            "resolution_hours": round(float(row.avg_resolution) / 3600, 2) if row.avg_resolution is not None else None,
        })

    return {"granularity": "day" if daily else "month", "buckets": buckets}


def get_response_resolution_by_agent(
    db,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    allowed_user_ids: set[int] | None = None,
):
    """Promedio de horas de 1a respuesta y de resolucion, por agente asignado
    (para el popup '+ Info' del grafico historico)."""
    frt_seconds_expression = _seconds_between(
        db, Ticket.created_at, _column_or_now(db, Ticket.first_agent_reply_at)
    )
    resolution_seconds_expression = _seconds_between(
        db, Ticket.created_at, _column_or_now(db, Ticket.resolved_at)
    )

    join_conditions = [
        Ticket.assigned_to_id == User.id,
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
    ]
    if date_from is not None:
        join_conditions.append(Ticket.created_at >= date_from)
    if date_to is not None:
        join_conditions.append(Ticket.created_at <= date_to)

    query = (
        db.query(
            User.name.label("agent"),
            func.count(Ticket.id).label("tickets"),
            func.avg(frt_seconds_expression).label("avg_frt"),
            func.avg(resolution_seconds_expression).label("avg_resolution"),
        )
        .outerjoin(Ticket, and_(*join_conditions))
        .filter(User.is_active == True)
        .group_by(User.name)
    )

    if allowed_user_ids is not None:
        if len(allowed_user_ids) == 0:
            return []
        query = query.filter(User.id.in_(allowed_user_ids))

    result = []
    for row in query.all():
        if not row.tickets:
            continue
        result.append({
            "agent": row.agent,
            "tickets": int(row.tickets or 0),
            "frt_hours": round(float(row.avg_frt) / 3600, 2) if row.avg_frt is not None else None,
            "resolution_hours": round(float(row.avg_resolution) / 3600, 2) if row.avg_resolution is not None else None,
        })

    result.sort(key=lambda a: a["tickets"], reverse=True)
    return result


# =========================================================
# TICKETS POR AGENTE
# =========================================================
def get_tickets_by_agent(
    db,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    allowed_user_ids: set[int] | None = None,
):
    # Referencia móvil para el KPI de últimos 7 días.
    since_7d = (
        date_to or datetime.now(timezone.utc)
    ) - timedelta(days=7)

    join_conditions = [
        Ticket.assigned_to_id == User.id,
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
    ]

    if date_from is not None:
        join_conditions.append(
            Ticket.created_at >= date_from
        )

    if date_to is not None:
        join_conditions.append(
            Ticket.created_at <= date_to
        )

    query = (
        db.query(
            User.name.label("agent"),
            func.count(
                Ticket.id
            ).label("tickets"),
            func.sum(
                case(
                    (
                        Ticket.status == "resolved",
                        1,
                    ),
                    else_=0,
                )
            ).label("resolved"),
            func.sum(
                case(
                    (
                        and_(
                            Ticket.resolved_at.isnot(None),
                            Ticket.resolved_at >= since_7d,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("resolved_7d"),
        )
        .outerjoin(
            Ticket,
            and_(*join_conditions),
        )
        .filter(
            User.is_active == True
        )
    )

    if allowed_user_ids is not None:
        if len(allowed_user_ids) == 0:
            return []

        query = query.filter(
            User.id.in_(allowed_user_ids)
        )

    query = (
        query
        .group_by(
            User.id,
            User.name,
        )
        .order_by(
            User.name.asc()
        )
    )

    rows = query.all()

    return [
        {
            "agent": row.agent,
            "tickets": int(
                row.tickets or 0
            ),
            "resolved": int(
                row.resolved or 0
            ),
            "resolved_7d": int(
                row.resolved_7d or 0
            ),
        }
        for row in rows
    ]


def get_ticket_detail_by_agent(
    db,
    allowed_user_ids: set[int] | None = None,
):
    """Detalle ticket a ticket (canal, prioridad, fecha, agente asignado),
    para el popup '+ Info' de 'Desempeño por tecnico' — se filtra y agrupa
    por usuario en el cliente (tipo de entrada / prioridad / fecha)."""
    query = (
        db.query(
            Ticket.id,
            Ticket.source,
            Ticket.priority,
            Ticket.status,
            Ticket.created_at,
            User.name.label("agent"),
        )
        .join(User, Ticket.assigned_to_id == User.id)
        .filter(
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
            User.is_active == True,
        )
    )

    if allowed_user_ids is not None:
        if len(allowed_user_ids) == 0:
            return []
        query = query.filter(User.id.in_(allowed_user_ids))

    rows = query.order_by(Ticket.created_at.desc()).all()

    return [
        {
            "id": row.id,
            "agent": row.agent,
            "source": (row.source or "").strip().lower() or "internal",
            "priority": (row.priority or "").strip().lower() or "unassigned",
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


# =========================================================
# AGING DE TICKETS ABIERTOS
# =========================================================
def get_ticket_aging(
    db,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    now = datetime.now(timezone.utc)

    aging_query = db.query(Ticket).filter(
        Ticket.status.in_(
            ["open", *PENDING_STATUS_CODES]
        ),
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
    )

    aging_query = _apply_ticket_created_range(
        aging_query,
        date_from,
        date_to,
    )

    tickets = aging_query.all()

    buckets = {
        "0-24h": 0,
        "24-48h": 0,
        "48-72h": 0,
        "72h+": 0,
    }

    for ticket in tickets:
        if ticket.created_at is None:
            continue

        age_hours = (
            now - ticket.created_at
        ).total_seconds() / 3600

        if age_hours <= 24:
            buckets["0-24h"] += 1
        elif age_hours <= 48:
            buckets["24-48h"] += 1
        elif age_hours <= 72:
            buckets["48-72h"] += 1
        else:
            buckets["72h+"] += 1

    return buckets


# =========================================================
# INFORME PDF — SOPORTE / HELPDESK
# =========================================================
def generar_informe_soporte_pdf(
    db,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    support_user_ids: set[int] | None = None,
) -> bytes:
    """Genera el PDF "Informe de Gestion - Soporte / Helpdesk" reflejando los
    mismos datos y criterios que dashboard_soporte.html (panel-indicadores)
    para el rango desde/hasta seleccionado: KPIs, cantidad de tickets por
    mes/dia, primera respuesta V/S resolucion, antiguedad del backlog,
    cumplimiento SLA y desempeño por tecnico (historico completo, igual que
    el dashboard, no depende del filtro de fecha)."""
    import io
    from pathlib import Path

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.colors import HexColor, white
    from reportlab.platypus import (
        BaseDocTemplate, Frame, PageTemplate, PageBreak,
        Table, TableStyle, Paragraph, Spacer, HRFlowable,
    )
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics.charts.barcharts import VerticalBarChart, HorizontalBarChart

    _C_DARK, _C_ORANGE, _C_ORDK = "#0b1424", "#5a8cff", "#1d4ed8"
    _C_BG, _C_BORDER, _C_TEXT, _C_SOFT, _C_GREY = "#f7f8fa", "#e5e7eb", "#111827", "#4b5563", "#9ca3af"
    _C_OK, _C_WARN, _C_BAD = "#1e9c83", "#d97706", "#c0392b"
    C_DARK, C_ORANGE, C_ORDK = HexColor(_C_DARK), HexColor(_C_ORANGE), HexColor(_C_ORDK)
    C_BG, C_BORDER, C_TEXT, C_SOFT, C_GREY = (
        HexColor(_C_BG), HexColor(_C_BORDER), HexColor(_C_TEXT), HexColor(_C_SOFT), HexColor(_C_GREY)
    )
    C_OK, C_WARN, C_BAD = HexColor(_C_OK), HexColor(_C_WARN), HexColor(_C_BAD)

    # ── Datos: mismas funciones y criterios que panel_indicadores ──
    kpis = get_overview_kpis(db, date_from=date_from, date_to=date_to)
    volume = get_ticket_volume_monthly(db, date_from=date_from, date_to=date_to)
    frt_res = get_response_resolution_history(db, date_from=date_from, date_to=date_to)
    aging = get_ticket_aging(db, date_from=date_from, date_to=date_to)
    sla = get_sla_summary(db, date_from=date_from, date_to=date_to)

    # "Desempeño por tecnico" siempre es historico completo, igual que en
    # el dashboard (no depende del filtro Desde/Hasta de la pagina).
    agentes = get_tickets_by_agent(db, date_from=None, date_to=None, allowed_user_ids=support_user_ids)
    agentes.sort(key=lambda a: -(a.get("tickets") or 0))

    detalle_tickets = get_ticket_detail_by_agent(db, allowed_user_ids=support_user_ids)
    resolved_status_set = {"resolved", "resolved_service", "resolved_client", "closed"}
    por_agente_canal: dict[str, dict] = {}
    for t in detalle_tickets:
        entry = por_agente_canal.setdefault(
            t["agent"],
            {"agent": t["agent"], "tickets": 0, "resueltos": 0, "whatsapp": 0, "email": 0, "internal": 0},
        )
        entry["tickets"] += 1
        if t["status"] in resolved_status_set:
            entry["resueltos"] += 1
        if t["source"] in entry:
            entry[t["source"]] += 1
    canal_rows = sorted(por_agente_canal.values(), key=lambda e: -e["tickets"])

    # ── Encabezado / metadatos del documento ──
    def _fmt(d: datetime | None) -> str:
        return d.strftime("%d/%m/%Y") if d else ""

    if date_from and date_to:
        rango_txt = f"Período: {_fmt(date_from)} al {_fmt(date_to)}"
    elif date_from:
        rango_txt = f"Período: desde el {_fmt(date_from)}"
    elif date_to:
        rango_txt = f"Período: hasta el {_fmt(date_to)}"
    else:
        rango_txt = "Período: histórico completo (sin filtro de fecha)"

    ahora = datetime.now()
    fecha_emision = ahora.strftime("%d/%m/%Y %H:%M")
    titulo_hdr = "INFORME DE GESTIÓN — SOPORTE / HELPDESK"
    subtitulo_hdr = rango_txt

    W, H = A4
    pad = 1.4 * cm
    HEADER_H = 2.7 * cm
    ORANGE_H = 5
    FOOTER_H = 1.0 * cm
    BODY_TOP = HEADER_H + ORANGE_H + 12
    BODY_BOT = FOOTER_H + 8
    fw = W - 2 * pad

    _atc_root = Path(__file__).resolve().parents[2]
    logo_path = _atc_root / "ATC" / "static" / "img" / "logo-atc.png"
    if not logo_path.exists():
        logo_path = _atc_root / "static" / "img" / "logo-atc.png"
    logo_w, logo_h = 2.8 * cm, 1.4 * cm

    def draw_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(C_DARK)
        canvas.rect(0, H - HEADER_H, W, HEADER_H, fill=1, stroke=0)
        if logo_path.exists():
            try:
                canvas.drawImage(
                    str(logo_path),
                    pad, H - HEADER_H + (HEADER_H - logo_h) / 2,
                    width=logo_w, height=logo_h,
                    preserveAspectRatio=True, mask="auto",
                )
            except Exception:
                pass
        tx = pad + logo_w + 0.5 * cm
        canvas.setFillColor(white)
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(tx, H - HEADER_H + 1.35 * cm, titulo_hdr)
        canvas.setFillColor(HexColor("#bfdbfe"))
        canvas.setFont("Helvetica", 8.5)
        canvas.drawString(tx, H - HEADER_H + 0.75 * cm, subtitulo_hdr)
        canvas.setFillColor(C_ORANGE)
        canvas.rect(0, H - HEADER_H - ORANGE_H, W, ORANGE_H, fill=1, stroke=0)
        canvas.setFillColor(C_DARK)
        canvas.rect(0, 0, W, FOOTER_H, fill=1, stroke=0)
        canvas.setFillColor(C_GREY)
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(
            W / 2, FOOTER_H / 2 - 3,
            f"Documento generado automáticamente  ·  Alguien Te Cuida  ·  {fecha_emision}",
        )
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(W - pad, FOOTER_H / 2 - 3, f"Página {doc.page}")
        canvas.restoreState()

    frame = Frame(
        pad, BODY_BOT, fw, H - BODY_TOP - BODY_BOT,
        leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0,
    )
    page_tmpl = PageTemplate(id="main", frames=[frame], onPage=draw_page)
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4, pageTemplates=[page_tmpl],
        leftMargin=0, rightMargin=0, topMargin=0, bottomMargin=0,
        title=titulo_hdr, author="Alguien Te Cuida",
    )

    st_kpi_num = ParagraphStyle("kpiNumH", fontName="Helvetica-Bold", fontSize=20, textColor=C_TEXT, leading=22, alignment=1)
    st_kpi_lbl = ParagraphStyle("kpiLblH", fontName="Helvetica-Bold", fontSize=7, textColor=C_SOFT, leading=9, alignment=1)
    st_sec = ParagraphStyle("secH", fontName="Helvetica-Bold", fontSize=11, textColor=C_ORDK, leading=14, spaceBefore=14, spaceAfter=6)
    st_body = ParagraphStyle("bodyH", fontName="Helvetica", fontSize=9.5, textColor=C_SOFT, leading=14.5, alignment=TA_JUSTIFY, spaceAfter=6)
    st_th = ParagraphStyle("thH", fontName="Helvetica-Bold", fontSize=8, textColor=white, leading=10)
    st_td = ParagraphStyle("tdH", fontName="Helvetica", fontSize=8, textColor=C_TEXT, leading=11)
    st_td_soft = ParagraphStyle("tdSoftH", fontName="Helvetica", fontSize=7.5, textColor=C_SOFT, leading=10)

    story: list = []

    # ── KPIs ──────────────────────────────────────────────────────────
    def kpi_card(numero: str, etiqueta: str, color) -> Table:
        t = Table([[Paragraph(numero, st_kpi_num)], [Paragraph(etiqueta, st_kpi_lbl)]], colWidths=[fw / 4 - 8])
        t.setStyle(TableStyle([
            ("TOPPADDING", (0, 0), (-1, 0), 12), ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
            ("TOPPADDING", (0, 1), (-1, 1), 2), ("BOTTOMPADDING", (0, 1), (-1, 1), 12),
            ("BOX", (0, 0), (-1, -1), 1, C_BORDER),
            ("LINEABOVE", (0, 0), (-1, 0), 3, color),
            ("BACKGROUND", (0, 0), (-1, -1), white),
        ]))
        return t

    resolucion_pct = kpis.get("resolution_rate_pct") or 0
    color_resolucion = C_OK if resolucion_pct >= 80 else (C_WARN if resolucion_pct >= 50 else C_BAD)
    kpisTable = Table(
        [[
            kpi_card(str(kpis.get("backlog", 0)), "TICKETS PENDIENTES\n(BACKLOG)", C_BAD),
            kpi_card(f"{resolucion_pct}%", "TASA DE\nRESOLUCIÓN", color_resolucion),
            kpi_card(f"{kpis.get('avg_frt_hours', 0)}", "1ª RESPUESTA\nPROMEDIO (H)", C_WARN),
            kpi_card(f"{kpis.get('avg_resolution_hours', 0)}", "RESOLUCIÓN\nPROMEDIO (H)", C_ORDK),
        ]],
        colWidths=[fw / 4] * 4,
    )
    kpisTable.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]))
    story.append(kpisTable)
    story.append(Spacer(1, 14))

    # ── Resumen general ──────────────────────────────────────────────
    story.append(Paragraph("RESUMEN GENERAL", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=8))
    total_tickets = kpis.get("total", 0)
    if total_tickets:
        story.append(Paragraph(
            f"El área de Soporte / Helpdesk registró <b>{total_tickets} tickets</b> en el período seleccionado. "
            f"De estos, <b>{kpis.get('resolved', 0)} fueron resueltos</b> (tasa de resolución {resolucion_pct}%), "
            f"y quedan <b>{kpis.get('backlog', 0)} pendientes</b> ({kpis.get('open', 0)} abiertos + "
            f"{kpis.get('pending', 0)} pendientes de seguimiento). El tiempo promedio de primera respuesta es de "
            f"<b>{kpis.get('avg_frt_hours', 0)} horas</b> y el de resolución de <b>{kpis.get('avg_resolution_hours', 0)} horas</b>.",
            st_body,
        ))
    else:
        story.append(Paragraph(
            "No se registraron tickets de Soporte / Helpdesk en el período seleccionado.",
            st_body,
        ))
    story.append(Spacer(1, 6))

    # ── Cantidad de tickets por mes/dia ──────────────────────────────────
    vol_buckets = volume.get("buckets", [])
    vol_diario = volume.get("granularity") == "day"
    story.append(Paragraph("CANTIDAD DE TICKETS " + ("POR DÍA" if vol_diario else "POR MES"), st_sec))
    story.append(HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=8))
    story.append(Paragraph(
        "Comparación entre tickets terminados y pendientes, agrupados por " + ("día" if vol_diario else "mes") +
        " de creación (sin filtro se muestran los últimos 12 meses).",
        st_td_soft,
    ))
    story.append(Spacer(1, 6))

    if vol_buckets:
        etiquetas_vol = [b.get("day") or b.get("month") or "" for b in vol_buckets]
        bar_h = 6.0 * cm
        bdwg = Drawing(fw, bar_h)
        chart = VerticalBarChart()
        chart.x, chart.y = 1.4 * cm, 1.2 * cm
        chart.width, chart.height = fw - 2.0 * cm, bar_h - 2.0 * cm
        chart.data = [[b.get("terminadas", 0) for b in vol_buckets], [b.get("pendientes", 0) for b in vol_buckets]]
        chart.categoryAxis.categoryNames = etiquetas_vol
        chart.categoryAxis.labels.fontName = "Helvetica"
        chart.categoryAxis.labels.fontSize = 6
        chart.categoryAxis.labels.angle = 90 if len(vol_buckets) > 12 else 0
        chart.categoryAxis.labels.dy = -18 if len(vol_buckets) > 12 else -2
        chart.valueAxis.valueMin = 0
        maximo_vol = max([b.get("terminadas", 0) for b in vol_buckets] + [b.get("pendientes", 0) for b in vol_buckets] + [1])
        chart.valueAxis.valueMax = maximo_vol * 1.15
        chart.valueAxis.labels.fontName = "Helvetica"
        chart.valueAxis.labels.fontSize = 6.5
        chart.bars[0].fillColor = C_OK
        chart.bars[1].fillColor = C_BAD
        chart.groupSpacing = 8
        chart.barSpacing = 1
        chart.categoryAxis.strokeColor = C_BORDER
        chart.valueAxis.strokeColor = C_BORDER
        bdwg.add(chart)
        story.append(bdwg)

        leyenda_cells = []
        for lbl, col in [("Terminados", C_OK), ("Pendientes", C_BAD)]:
            leyenda_cells.append(Table([[""]], colWidths=[9], rowHeights=[9], style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), col)])))
            leyenda_cells.append(Paragraph(lbl, st_td_soft))
        leyenda = Table([leyenda_cells], colWidths=None)
        leyenda.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
        story.append(Table([[leyenda]], colWidths=[fw], style=TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")])))
        story.append(Spacer(1, 8))

        filas_vol = [[
            Paragraph(("DÍA" if vol_diario else "MES"), st_th), Paragraph("TERMINADOS", st_th),
            Paragraph("PENDIENTES", st_th), Paragraph("TOTAL", st_th),
        ]]
        for b, etiqueta in zip(vol_buckets, etiquetas_vol):
            filas_vol.append([
                Paragraph(etiqueta, st_td),
                Paragraph(str(b.get("terminadas", 0)), st_td),
                Paragraph(str(b.get("pendientes", 0)), st_td),
                Paragraph(str(b.get("terminadas", 0) + b.get("pendientes", 0)), st_td),
            ])
        tabla_vol = Table(filas_vol, colWidths=[fw * 0.25, fw * 0.25, fw * 0.30, fw * 0.20], repeatRows=1)
        tabla_vol.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_DARK),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, C_BG]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("BOX", (0, 0), (-1, -1), 1, C_BORDER),
        ]))
        story.append(tabla_vol)
    else:
        story.append(Paragraph("No hay datos suficientes para construir este gráfico.", st_td_soft))

    # ── Página 2: Primera respuesta V/S Resolución + Antigüedad del backlog ──
    story.append(PageBreak())
    frt_buckets = frt_res.get("buckets", [])
    frt_diario = frt_res.get("granularity") == "day"
    story.append(Paragraph("PRIMERA RESPUESTA V/S TIEMPO DE RESOLUCIÓN", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=8))
    story.append(Paragraph(
        "Promedio en horas de 1ª respuesta y de resolución, agrupado por " + ("día" if frt_diario else "mes") +
        ". Incluye tickets aún sin responder/resolver, usando el tiempo transcurrido hasta ahora — el mismo "
        "criterio que \"Cumplimiento SLA\".",
        st_td_soft,
    ))
    story.append(Spacer(1, 6))

    if frt_buckets:
        etiquetas_frt = [b.get("day") or b.get("month") or "" for b in frt_buckets]
        bar_h2 = 6.0 * cm
        bdwg2 = Drawing(fw, bar_h2)
        chart2 = VerticalBarChart()
        chart2.x, chart2.y = 1.4 * cm, 1.2 * cm
        chart2.width, chart2.height = fw - 2.0 * cm, bar_h2 - 2.0 * cm
        chart2.data = [
            [b.get("frt_hours") or 0 for b in frt_buckets],
            [b.get("resolution_hours") or 0 for b in frt_buckets],
        ]
        chart2.categoryAxis.categoryNames = etiquetas_frt
        chart2.categoryAxis.labels.fontName = "Helvetica"
        chart2.categoryAxis.labels.fontSize = 6
        chart2.categoryAxis.labels.angle = 90 if len(frt_buckets) > 12 else 0
        chart2.categoryAxis.labels.dy = -18 if len(frt_buckets) > 12 else -2
        chart2.valueAxis.valueMin = 0
        maximo_frt = max([b.get("frt_hours") or 0 for b in frt_buckets] + [b.get("resolution_hours") or 0 for b in frt_buckets] + [1])
        chart2.valueAxis.valueMax = maximo_frt * 1.15
        chart2.valueAxis.labels.fontName = "Helvetica"
        chart2.valueAxis.labels.fontSize = 6.5
        chart2.bars[0].fillColor = HexColor("#5a8cff")
        chart2.bars[1].fillColor = HexColor("#8b72ff")
        chart2.groupSpacing = 8
        chart2.barSpacing = 1
        chart2.categoryAxis.strokeColor = C_BORDER
        chart2.valueAxis.strokeColor = C_BORDER
        bdwg2.add(chart2)
        story.append(bdwg2)

        leyenda2_cells = []
        for lbl, col in [("1ª respuesta (h)", HexColor("#5a8cff")), ("Resolución (h)", HexColor("#8b72ff"))]:
            leyenda2_cells.append(Table([[""]], colWidths=[9], rowHeights=[9], style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), col)])))
            leyenda2_cells.append(Paragraph(lbl, st_td_soft))
        leyenda2 = Table([leyenda2_cells], colWidths=None)
        leyenda2.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
        story.append(Table([[leyenda2]], colWidths=[fw], style=TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")])))
    else:
        story.append(Paragraph("No hay datos suficientes para construir este gráfico.", st_td_soft))

    story.append(Spacer(1, 14))
    story.append(Paragraph("ANTIGÜEDAD DEL BACKLOG", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=8))
    story.append(Paragraph(
        "Tickets abiertos según cuántas horas llevan desde su creación.",
        st_td_soft,
    ))
    story.append(Spacer(1, 6))

    aging_labels = list(aging.keys())
    aging_values = list(aging.values())
    if any(aging_values):
        bar_h3 = max(2.6 * cm, len(aging_labels) * 0.55 * cm)
        bdwg3 = Drawing(fw, 4.2 * cm)
        chart3 = HorizontalBarChart()
        chart3.x, chart3.y = 3.6 * cm, 6
        chart3.width, chart3.height = fw - 4.4 * cm, 3.6 * cm
        chart3.data = [aging_values]
        chart3.categoryAxis.categoryNames = aging_labels
        chart3.categoryAxis.labels.fontName = "Helvetica"
        chart3.categoryAxis.labels.fontSize = 8
        chart3.valueAxis.valueMin = 0
        chart3.valueAxis.valueMax = max(aging_values + [1]) * 1.15
        chart3.valueAxis.labels.fontName = "Helvetica"
        chart3.valueAxis.labels.fontSize = 6.5
        chart3.bars[0].fillColor = C_ORDK
        chart3.barLabels.fontName = "Helvetica-Bold"
        chart3.barLabels.fontSize = 7.5
        chart3.barLabelFormat = "%d"
        chart3.barLabels.dx = 14
        chart3.categoryAxis.strokeColor = C_BORDER
        chart3.valueAxis.strokeColor = C_BORDER
        bdwg3.add(chart3)
        story.append(bdwg3)
    else:
        story.append(Paragraph("No hay tickets abiertos en este período.", st_td_soft))

    # ── Página 3: Cumplimiento SLA ────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("CUMPLIMIENTO SLA", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=8))
    story.append(Paragraph(
        "Umbrales únicos (no dependen de la prioridad del ticket): verde ≤12 horas, amarillo entre 12 y 24 horas, "
        "rojo más de 24 horas (más de 1 día) sin respuesta/resolución. Se evalúa sobre todos los tickets del rango: "
        "si ya hubo respuesta/resolución se usa el tiempo real que tomó; si sigue pendiente, se usa el tiempo "
        "transcurrido hasta ahora.",
        st_td_soft,
    ))
    story.append(Spacer(1, 6))

    fr = sla.get("first_reply", {})
    rs = sla.get("resolution", {})
    bar_h4 = 5.4 * cm
    bdwg4 = Drawing(fw, bar_h4)
    chart4 = VerticalBarChart()
    chart4.x, chart4.y = 1.4 * cm, 1.2 * cm
    chart4.width, chart4.height = fw - 2.0 * cm, bar_h4 - 2.0 * cm
    chart4.data = [
        [fr.get("ok", 0), rs.get("ok", 0)],
        [fr.get("at_risk", 0), rs.get("at_risk", 0)],
        [fr.get("overdue", 0), rs.get("overdue", 0)],
    ]
    chart4.categoryAxis.categoryNames = ["1ª respuesta", "Resolución"]
    chart4.categoryAxis.labels.fontName = "Helvetica-Bold"
    chart4.categoryAxis.labels.fontSize = 9
    chart4.valueAxis.valueMin = 0
    maximo_sla = max(
        fr.get("ok", 0) + fr.get("at_risk", 0) + fr.get("overdue", 0),
        rs.get("ok", 0) + rs.get("at_risk", 0) + rs.get("overdue", 0),
        1,
    )
    chart4.valueAxis.valueMax = maximo_sla * 1.15
    chart4.valueAxis.labels.fontName = "Helvetica"
    chart4.valueAxis.labels.fontSize = 7
    chart4.bars[0].fillColor = C_OK
    chart4.bars[1].fillColor = C_WARN
    chart4.bars[2].fillColor = C_BAD
    chart4.groupSpacing = 20
    chart4.barSpacing = 2
    chart4.categoryAxis.strokeColor = C_BORDER
    chart4.valueAxis.strokeColor = C_BORDER
    bdwg4.add(chart4)
    story.append(bdwg4)

    leyenda4_cells = []
    for lbl, col in [("≤12h", C_OK), ("12–24h", C_WARN), (">24h", C_BAD)]:
        leyenda4_cells.append(Table([[""]], colWidths=[9], rowHeights=[9], style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), col)])))
        leyenda4_cells.append(Paragraph(lbl, st_td_soft))
    leyenda4 = Table([leyenda4_cells], colWidths=None)
    leyenda4.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story.append(Table([[leyenda4]], colWidths=[fw], style=TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")])))
    story.append(Spacer(1, 10))

    filas_sla = [[
        Paragraph("MÉTRICA", st_th), Paragraph("≤12H", st_th), Paragraph("12–24H", st_th),
        Paragraph(">24H", st_th), Paragraph("CUMPLIMIENTO", st_th),
    ]]
    filas_sla.append([
        Paragraph("1ª respuesta", st_td),
        Paragraph(str(fr.get("ok", 0)), st_td),
        Paragraph(str(fr.get("at_risk", 0)), st_td),
        Paragraph(str(fr.get("overdue", 0)), st_td),
        Paragraph(f"{sla.get('first_reply_compliance', 0)}%", st_td),
    ])
    filas_sla.append([
        Paragraph("Resolución", st_td),
        Paragraph(str(rs.get("ok", 0)), st_td),
        Paragraph(str(rs.get("at_risk", 0)), st_td),
        Paragraph(str(rs.get("overdue", 0)), st_td),
        Paragraph(f"{sla.get('resolution_compliance', 0)}%", st_td),
    ])
    tabla_sla = Table(filas_sla, colWidths=[fw * 0.28, fw * 0.16, fw * 0.16, fw * 0.16, fw * 0.24], repeatRows=1)
    tabla_sla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_DARK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, C_BG]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, C_BORDER),
        ("BOX", (0, 0), (-1, -1), 1, C_BORDER),
    ]))
    story.append(tabla_sla)

    # ── Página 4: Desempeño por técnico ───────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("DESEMPEÑO POR TÉCNICO (HISTÓRICO COMPLETO)", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=8))
    story.append(Paragraph(
        "Tickets asignados, resueltos y resueltos en los últimos 7 días por técnico. Esta sección es siempre "
        "histórica (no depende del filtro de fecha de la página, igual que en el dashboard).",
        st_td_soft,
    ))
    story.append(Spacer(1, 6))

    if agentes:
        filas_ag = [[
            Paragraph("TÉCNICO", st_th), Paragraph("ASIGNADOS", st_th),
            Paragraph("RESUELTOS", st_th), Paragraph("ÚLTIMOS 7D", st_th),
        ]]
        for a in agentes:
            filas_ag.append([
                Paragraph(a.get("agent", "") or "—", st_td),
                Paragraph(str(a.get("tickets", 0)), st_td),
                Paragraph(str(a.get("resolved", 0)), st_td),
                Paragraph(str(a.get("resolved_7d", 0)), st_td),
            ])
        tabla_ag = Table(filas_ag, colWidths=[fw * 0.46, fw * 0.18, fw * 0.18, fw * 0.18], repeatRows=1)
        tabla_ag.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_DARK),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, C_BG]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("BOX", (0, 0), (-1, -1), 1, C_BORDER),
        ]))
        story.append(tabla_ag)
    else:
        story.append(Paragraph("No hay datos de técnicos.", st_td_soft))

    story.append(Spacer(1, 14))
    story.append(Paragraph("DESGLOSE POR CANAL DE ENTRADA (POR TÉCNICO)", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=8))
    story.append(Paragraph(
        "Cantidad de tickets por técnico según su canal de entrada (WhatsApp, Gmail o Interno). También histórico "
        "completo.",
        st_td_soft,
    ))
    story.append(Spacer(1, 6))

    if canal_rows:
        filas_canal = [[
            Paragraph("TÉCNICO", st_th), Paragraph("TICKETS", st_th), Paragraph("RESUELTOS", st_th),
            Paragraph("WHATSAPP", st_th), Paragraph("GMAIL", st_th), Paragraph("INTERNO", st_th),
        ]]
        for a in canal_rows:
            filas_canal.append([
                Paragraph(a.get("agent", "") or "—", st_td),
                Paragraph(str(a.get("tickets", 0)), st_td),
                Paragraph(str(a.get("resueltos", 0)), st_td),
                Paragraph(str(a.get("whatsapp", 0)), st_td),
                Paragraph(str(a.get("email", 0)), st_td),
                Paragraph(str(a.get("internal", 0)), st_td),
            ])
        tabla_canal = Table(filas_canal, colWidths=[fw * 0.30, fw * 0.14, fw * 0.14, fw * 0.14, fw * 0.14, fw * 0.14], repeatRows=1)
        tabla_canal.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_DARK),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, C_BG]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("BOX", (0, 0), (-1, -1), 1, C_BORDER),
        ]))
        story.append(tabla_canal)
    else:
        story.append(Paragraph("No hay datos de técnicos.", st_td_soft))

    # ── Conclusión ───────────────────────────────────────────────────────
    story.append(Spacer(1, 16))
    story.append(Paragraph("CONCLUSIÓN", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=8))

    partes: list[str] = []
    if not total_tickets:
        partes.append("No se registró actividad de Soporte / Helpdesk en el período seleccionado.")
    else:
        nivel_resolucion = "crítico" if resolucion_pct < 50 else ("de atención" if resolucion_pct < 80 else "saludable")
        partes.append(
            f"El estado general del área se considera <b>{nivel_resolucion}</b>: tasa de resolución de "
            f"{resolucion_pct}% con {kpis.get('backlog', 0)} tickets pendientes."
        )
        fr_pct = sla.get("first_reply_compliance")
        rs_pct = sla.get("resolution_compliance")
        if fr_pct is not None:
            partes.append(
                f"El cumplimiento de SLA de 1ª respuesta es {fr_pct}% y el de resolución {rs_pct}% "
                "(dentro de 24 horas)."
            )
        if agentes:
            top_ag = agentes[0]
            partes.append(
                f"El técnico con más tickets históricamente es <b>{top_ag.get('agent')}</b>, con "
                f"{top_ag.get('tickets', 0)} tickets asignados ({top_ag.get('resolved', 0)} resueltos)."
            )
        backlog_72 = aging.get("72h+", 0)
        if backlog_72:
            partes.append(
                f"Hay <b>{backlog_72} tickets</b> abiertos hace más de 72 horas — se recomienda priorizarlos."
            )
        partes.append(
            "Se recomienda dar seguimiento a los tickets con más antigüedad y a los técnicos con menor "
            "cumplimiento de SLA para mejorar los tiempos de respuesta del área."
        )
    story.append(Paragraph(" ".join(partes), st_body))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()

    return buckets