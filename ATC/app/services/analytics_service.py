from sqlalchemy import and_, case, func, or_
from datetime import datetime, timezone, timedelta

from ATC.app.models.ticket import Ticket
from ATC.app.models.user import User
from ATC.app.models.ticket_sla_feedback import TicketSlaFeedback


# =========================
# SLA RULES (horas)
# =========================
SLA_RULES_HOURS = {
    "low": {"first_reply": 8, "resolution": 72},
    "medium": {"first_reply": 4, "resolution": 48},
    "high": {"first_reply": 2, "resolution": 24},
    "urgent": {"first_reply": 1, "resolution": 8},
}

PENDING_STATUS_CODES = ("pending", "pending_service", "pending_client")


# =========================================================
# KPI RESUMEN GENERAL
# =========================================================
def _pct(part: int | float, total: int | float) -> float:
    # Evita division por cero y normaliza porcentaje para todos los KPI.
    if not total:
        return 0.0
    return round((float(part) / float(total)) * 100, 2)


def _apply_ticket_created_range(query, date_from: datetime | None = None, date_to: datetime | None = None):
    # Aplica filtro opcional por fecha de creacion para reutilizar la misma logica.
    if date_from is not None:
        query = query.filter(Ticket.created_at >= date_from)
    if date_to is not None:
        query = query.filter(Ticket.created_at <= date_to)
    return query


def get_overview_kpis(db, date_from: datetime | None = None, date_to: datetime | None = None):

    base_query = db.query(Ticket).filter(
        Ticket.is_deleted == False,
        Ticket.is_spam == False
    )
    base_query = _apply_ticket_created_range(base_query, date_from, date_to)

    total = base_query.count()
    open_count = base_query.filter(Ticket.status == "open").count()
    pending_count = base_query.filter(Ticket.status.in_(PENDING_STATUS_CODES)).count()
    resolved_count = base_query.filter(Ticket.status == "resolved").count()
    backlog_count = open_count + pending_count
    assigned_count = base_query.filter(Ticket.assigned_to_id.isnot(None)).count()
    reopened_count = base_query.filter(Ticket.reopen_count > 0).count()

    now_utc = datetime.now(timezone.utc)
    # Si hay filtro hasta una fecha, usamos ese cierre como referencia operativa.
    range_end_reference = date_to or now_utc
    since_7d = range_end_reference - timedelta(days=7)
    created_last_7d = base_query.filter(Ticket.created_at >= since_7d).count()
    resolved_last_7d = base_query.filter(
        Ticket.resolved_at.isnot(None),
        Ticket.resolved_at >= since_7d
    ).count()

    avg_frt_query = db.query(
        func.avg(
            func.extract("epoch", Ticket.first_agent_reply_at - Ticket.created_at)
        )
    ).filter(
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
        Ticket.first_agent_reply_at.isnot(None)
    )
    avg_frt_query = _apply_ticket_created_range(avg_frt_query, date_from, date_to)
    avg_frt_seconds = avg_frt_query.scalar()

    avg_resolution_query = db.query(
        func.avg(
            func.extract("epoch", Ticket.resolved_at - Ticket.created_at)
        )
    ).filter(
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
        Ticket.resolved_at.isnot(None)
    )
    avg_resolution_query = _apply_ticket_created_range(avg_resolution_query, date_from, date_to)
    avg_resolution_seconds = avg_resolution_query.scalar()

    # CSAT / calidad (solo feedback asociado a tickets activos)
    csat_base_query = (
        db.query(TicketSlaFeedback)
        .join(Ticket, Ticket.id == TicketSlaFeedback.ticket_id)
        .filter(
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
        )
    )
    if date_from is not None:
        csat_base_query = csat_base_query.filter(Ticket.created_at >= date_from)
    if date_to is not None:
        csat_base_query = csat_base_query.filter(Ticket.created_at <= date_to)

    csat_avg_raw = csat_base_query.filter(
        TicketSlaFeedback.technician_rating.isnot(None)
    ).with_entities(func.avg(TicketSlaFeedback.technician_rating)).scalar()

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
        "resolution_rate_pct": _pct(resolved_count, total),
        "assignment_rate_pct": _pct(assigned_count, total),
        "reopened_tickets": reopened_count,
        "reopen_rate_pct": _pct(reopened_count, resolved_count),
        "created_last_7d": created_last_7d,
        "resolved_last_7d": resolved_last_7d,
        "throughput_7d_pct": _pct(resolved_last_7d, created_last_7d),
        "avg_frt_hours": round((avg_frt_seconds or 0) / 3600, 2),
        "avg_resolution_hours": round((avg_resolution_seconds or 0) / 3600, 2),
        "csat_avg_rating": round(csat_avg_raw, 2) if csat_avg_raw is not None else None,
        "csat_rating_count": csat_rating_count,
        "csat_response_rate_pct": _pct(csat_response_count, resolved_count),
        "resolution_satisfaction_pct": _pct(resolution_yes_count, resolution_answered_count),
    }


# =========================================================
# SLA SUMMARY
# =========================================================
def get_sla_summary(db, date_from: datetime | None = None, date_to: datetime | None = None):

    now = datetime.now(timezone.utc)

    active_query = db.query(Ticket).filter(
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
        Ticket.status.in_(["open", *PENDING_STATUS_CODES])
    )
    active_query = _apply_ticket_created_range(active_query, date_from, date_to)
    active = active_query.all()

    first_reply = {"overdue": 0, "at_risk": 0, "ok": 0}
    resolution = {"overdue": 0, "at_risk": 0, "ok": 0}

    for t in active:

        rules = SLA_RULES_HOURS.get(
            (t.priority or "medium").lower(),
            SLA_RULES_HOURS["medium"]
        )

        # ==============================
        # FIRST REPLY SLA
        # ==============================
        if t.first_agent_reply_at is None:

            deadline = t.created_at + timedelta(hours=rules["first_reply"])
            remaining = (deadline - now).total_seconds()

            if remaining < 0:
                first_reply["overdue"] += 1

            elif remaining <= 3600:
                first_reply["at_risk"] += 1

            else:
                first_reply["ok"] += 1

        # ==============================
        # RESOLUTION SLA
        # ==============================
        if t.resolved_at is None:

            deadline = t.created_at + timedelta(hours=rules["resolution"])
            remaining = (deadline - now).total_seconds()

            if remaining < 0:
                resolution["overdue"] += 1

            elif remaining <= 4 * 3600:
                resolution["at_risk"] += 1

            else:
                resolution["ok"] += 1

    total_first = sum(first_reply.values())
    total_resolution = sum(resolution.values())

    first_reply_compliance = 0
    resolution_compliance = 0

    if total_first > 0:
        first_reply_compliance = round(
            (first_reply["ok"] / total_first) * 100, 2
        )

    if total_resolution > 0:
        resolution_compliance = round(
            (resolution["ok"] / total_resolution) * 100, 2
        )

    return {
        "first_reply": first_reply,
        "resolution": resolution,
        "first_reply_compliance": first_reply_compliance,
        "resolution_compliance": resolution_compliance
    }


# =========================================================
# VOLUMEN DE TICKETS (30 DIAS)
# =========================================================
def get_ticket_volume_30d(db, date_from: datetime | None = None, date_to: datetime | None = None):

    since = datetime.now(timezone.utc) - timedelta(days=30)

    volume_query = db.query(
        func.date(Ticket.created_at).label("day"),
        func.count(Ticket.id)
    ).filter(
        Ticket.is_deleted == False,
        Ticket.is_spam == False
    )

    # Sin filtros usamos ultimos 30 dias; con filtros usamos el rango elegido.
    if date_from is None and date_to is None:
        volume_query = volume_query.filter(Ticket.created_at >= since)
    else:
        volume_query = _apply_ticket_created_range(volume_query, date_from, date_to)

    rows = (
        volume_query
        .group_by(func.date(Ticket.created_at))
        .order_by(func.date(Ticket.created_at))
        .all()
    )

    return [
        {
            "day": str(r[0]),
            "count": r[1]
        }
        for r in rows
    ]


# =========================================================
# TICKETS POR ESTADO DETALLADO
# =========================================================
def get_ticket_status_breakdown(db, date_from: datetime | None = None, date_to: datetime | None = None):

    def _count_status(status_code: str) -> int:
        query = db.query(Ticket).filter(
            Ticket.status == status_code,
            Ticket.is_deleted == False,
            Ticket.is_spam == False,
        )
        query = _apply_ticket_created_range(query, date_from, date_to)
        return query.count()

    closed_query = db.query(Ticket).filter(
        or_(
            Ticket.status == "resolved",
            Ticket.is_deleted == True,
            Ticket.is_spam == True,
        )
    )
    closed_query = _apply_ticket_created_range(closed_query, date_from, date_to)

    return {
        "open": _count_status("open"),
        "pending": _count_status("pending"),
        "pending_service": _count_status("pending_service"),
        "pending_client": _count_status("pending_client"),
        "closed": closed_query.count(),
    }


# =========================================================
# TICKETS POR PRIORIDAD
# =========================================================
def get_tickets_by_priority(db, date_from: datetime | None = None, date_to: datetime | None = None):

    tickets_query = db.query(Ticket).filter(
        Ticket.is_deleted == False,
        Ticket.is_spam == False
    )
    tickets_query = _apply_ticket_created_range(tickets_query, date_from, date_to)

    result = {
        "unassigned": 0,
        "low": 0,
        "medium": 0,
        "high": 0,
        "urgent": 0
    }

    for ticket in tickets_query.all():
        priority = (ticket.priority or "").strip().lower()
        if priority in result:
            result[priority] += 1
        else:
            result["unassigned"] += 1

    return result


# =========================================================
# TICKETS POR AGENTE
# =========================================================
def get_tickets_by_agent(db, date_from: datetime | None = None, date_to: datetime | None = None):

    # Referencia movil para mantener el KPI de "ultimos 7 dias" consistente.
    since_7d = (date_to or datetime.now(timezone.utc)) - timedelta(days=7)

    join_conditions = [
        Ticket.assigned_to_id == User.id,
        Ticket.is_deleted == False,
        Ticket.is_spam == False,
    ]
    if date_from is not None:
        join_conditions.append(Ticket.created_at >= date_from)
    if date_to is not None:
        join_conditions.append(Ticket.created_at <= date_to)

    rows = (
        db.query(
            User.name.label("agent"),
            func.count(Ticket.id).label("tickets"),
            func.sum(
                case((Ticket.status == "resolved", 1), else_=0)
            ).label("resolved"),
            func.sum(
                case((and_(Ticket.resolved_at.isnot(None), Ticket.resolved_at >= since_7d), 1), else_=0)
            ).label("resolved_7d"),
        )
        .outerjoin(Ticket, and_(*join_conditions))
        .filter(User.is_active == True)
        .group_by(User.id, User.name)
        .order_by(User.name.asc())
        .all()
    )

    return [
        {
            "agent": r.agent,
            "tickets": int(r.tickets or 0),
            "resolved": int(r.resolved or 0),
            "resolved_7d": int(r.resolved_7d or 0),
        }
        for r in rows
    ]


# =========================================================
# AGING DE TICKETS ABIERTOS
# =========================================================
def get_ticket_aging(db, date_from: datetime | None = None, date_to: datetime | None = None):

    now = datetime.now(timezone.utc)

    aging_query = db.query(Ticket).filter(
        Ticket.status.in_(["open", *PENDING_STATUS_CODES]),
        Ticket.is_deleted == False,
        Ticket.is_spam == False
    )
    aging_query = _apply_ticket_created_range(aging_query, date_from, date_to)
    tickets = aging_query.all()

    buckets = {
        "0-24h": 0,
        "24-48h": 0,
        "48-72h": 0,
        "72h+": 0
    }

    for t in tickets:

        age_hours = (now - t.created_at).total_seconds() / 3600

        if age_hours <= 24:
            buckets["0-24h"] += 1

        elif age_hours <= 48:
            buckets["24-48h"] += 1

        elif age_hours <= 72:
            buckets["48-72h"] += 1

        else:
            buckets["72h+"] += 1

    return buckets

