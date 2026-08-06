from fastapi import APIRouter, Depends, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import asc

from ATC.app.core.db import get_db
from ATC.app.core.templates import templates

from ATC.app.models.ticket import Ticket
from ATC.app.models.requester import Requester

from ATC.app.schemas.ticket import TicketCreate, TicketOut
from ATC.app.models.ticket_history import TicketAssignmentHistory

router = APIRouter(prefix="/tickets", tags=["tickets"])


def _require_login(request: Request, db: Session = Depends(get_db)):
    """Exige la cookie de sesion (access_token) que ya usan las paginas
    HTML de web.py. Antes ningun endpoint de este router verificaba sesion
    (hallazgo de auditoria de seguridad, ago 2026)."""
    from ATC.app.routes.web import COOKIE_NAME as _COOKIE_NAME, _decode_cookie_token as _decode_cookie_token_web
    from ATC.app.services.user_service import UserService as _UserService

    cookie = request.cookies.get(_COOKIE_NAME, "")
    if cookie:
        try:
            login = _decode_cookie_token_web(cookie)
            user = _UserService.find_by_login(db, login)
            if user and user.is_active:
                return user
        except Exception:
            pass
    raise HTTPException(status_code=401, detail="No autenticado.")


# ==============================
# API - CREAR TICKET
# ==============================
@router.post("/", response_model=TicketOut)
def create_ticket(data: TicketCreate, db: Session = Depends(get_db), current_user=Depends(_require_login)):

    source = (data.source or "").strip().lower()
    priority = (data.priority or "").strip().lower()
    if source in {"email", "whatsapp"} and priority not in {"low", "medium", "high", "urgent"}:
        priority = ""

    ticket = Ticket(
        subject=data.subject,
        priority=priority,
        source=data.source,
        requester_id=data.requester_id,
        status="open",
    )

    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    return ticket


# ==============================
# API - LISTAR TICKETS
# ==============================
@router.get("/", response_model=list[TicketOut])
def list_tickets(db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return db.query(Ticket).all()


# ==============================
# TICKETERA - DETALLE TICKET
# ==============================
@router.get("/ticketera/{ticket_id}", response_class=HTMLResponse)
@router.get("/dashboard/{ticket_id}", response_class=HTMLResponse)
def ticket_detail(
    ticket_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(_require_login),
):

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    # ðŸ”¹ Obtener siguiente ticket
    next_ticket = (
        db.query(Ticket)
        .filter(Ticket.id > ticket_id)
        .order_by(asc(Ticket.id))
        .first()
    )

    return templates.TemplateResponse(
        "detalle_ticket.html",
        {
            "request": request,
            "ticket": ticket,
            "next_ticket_id": next_ticket.id if next_ticket else None,
        },
    )


# ==============================
# GUARDAR NOTAS DEL CLIENTE
# ==============================
@router.post("/requesters/{requester_id}/notes")
def update_requester_notes(
    requester_id: int,
    notes: str = Form(...),
    ticket_id: int = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(_require_login),
):

    requester = db.query(Requester).filter(Requester.id == requester_id).first()

    if not requester:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    requester.notes = notes

    db.commit()

    return RedirectResponse(
        url=f"/ticketera/tickets/{ticket_id}",
        status_code=303
    )

@router.post("/{ticket_id}/assign")
def assign_ticket(
    ticket_id: int,
    user_id: int = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(_require_login),
):

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    # usuario anterior
    old_user = ticket.assigned_to_id

    # guardar historial
    history = TicketAssignmentHistory(
        ticket_id=ticket.id,
        from_user_id=old_user,
        to_user_id=user_id,
        changed_by_id=user_id
    )

    db.add(history)

    # actualizar ticket
    ticket.assigned_to_id = user_id

    db.commit()

    return RedirectResponse(
        url=f"/tickets/ticketera/{ticket_id}",
        status_code=303
    )
