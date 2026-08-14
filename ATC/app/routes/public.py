from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ATC.app.core.db import get_db
from ATC.app.core.config import settings
from ATC.app.models.ticket import Ticket
from ATC.app.services.sla_feedback_service import (
    apply_ticket_sla_feedback,
    build_sla_feedback_link,
    build_sla_feedback_token,
    extract_feedback_from_payload,
    get_or_create_ticket_sla_feedback,
    parse_rating_value,
    parse_resolution_value,
    parse_ticket_id_value,
    store_sla_feedback_event,
    verify_sla_feedback_token,
)
from ATC.app.services.ticket_service import create_ticket_from_public


router = APIRouter(prefix="/public", tags=["public"])
lavados_router = APIRouter(tags=["lavados"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


class PublicTicketCreate(BaseModel):
    name: str
    email: str
    subject: str
    message: str


class LavadosRegistroCreate(BaseModel):
    patente: str
    fecha: str
    servicio: str
    kilometraje: str
    observaciones: str = ""
    imgAntes1: str = ""
    imgAntes2: str = ""
    imgDespues1: str = ""
    imgDespues2: str = ""


class ComiteParitarioPostulacion(BaseModel):
    nombre: str
    rut: str
    cargo: str
    correo: str


@router.post("/tickets")
def create_public_ticket(data: PublicTicketCreate, db: Session = Depends(get_db)):
    ticket = create_ticket_from_public(
        db=db,
        name=data.name,
        email=data.email,
        subject=data.subject,
        message_text=data.message,
    )

    return {
        "ticket_id": ticket.id,
        "status": "created",
    }


@lavados_router.get("/lavados.html", response_class=HTMLResponse)
def lavados_page(request: Request):
    return templates.TemplateResponse(request, "lavados.html", {})


@lavados_router.get("/api/lavados/opciones")
def lavados_opciones():
    from ATC.app.services.lavados_service import LavadosServiceError, obtener_opciones_lavados

    try:
        return obtener_opciones_lavados()
    except LavadosServiceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"No se pudieron cargar las opciones de lavados: {exc}") from exc


@lavados_router.post("/api/lavados/registros")
def lavados_guardar_registro(payload: LavadosRegistroCreate, background_tasks: BackgroundTasks):
    from ATC.app.services.lavados_service import (
        LavadosServiceError,
        guardar_registro_lavado,
        procesar_registro_lavado_background,
    )

    try:
        registro = guardar_registro_lavado(payload.model_dump())
    except LavadosServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo guardar el registro de lavados: {exc}") from exc

    background_tasks.add_task(procesar_registro_lavado_background, registro)
    return {
        "ok": True,
        "id": registro["id"],
        "mensaje": registro["mensaje"],
    }


@router.get("/comite-paritario", response_class=HTMLResponse)
def comite_paritario_form(request: Request):
    return templates.TemplateResponse(request, "public_comite_paritario.html", {})


@router.post("/comite-paritario/postular")
def comite_paritario_postular(payload: ComiteParitarioPostulacion, background_tasks: BackgroundTasks):
    from ATC.app.services.comite_paritario_service import enviar_postulacion_comite_paritario_email

    data = payload.model_dump()
    data = {key: str(value or "").strip() for key, value in data.items()}
    if not data["nombre"] or not data["rut"] or not data["cargo"] or not data["correo"]:
        raise HTTPException(status_code=422, detail="Nombre, RUT, cargo y correo son obligatorios.")
    if "@" not in data["correo"] or "." not in data["correo"].split("@")[-1]:
        raise HTTPException(status_code=422, detail="Ingresa un correo valido.")

    background_tasks.add_task(enviar_postulacion_comite_paritario_email, data)
    return {"ok": True, "mensaje": "Postulacion enviada correctamente."}


@router.get("/tickets/{ticket_id}/sla-feedback", response_class=HTMLResponse)
def ticket_sla_feedback(
    request: Request,
    ticket_id: int,
    token: str = Query(...),
    rating: int | None = Query(None, ge=1, le=5),
    resolved: str | None = Query(None),
    db: Session = Depends(get_db),
):
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    if not verify_sla_feedback_token(token, ticket_id):
        raise HTTPException(status_code=403, detail="Token invalido")

    resolved_value: bool | None = None
    if resolved is not None:
        lowered = resolved.strip().lower()
        if lowered in {"si", "sí", "yes", "true", "1"}:
            resolved_value = True
        elif lowered in {"no", "false", "0"}:
            resolved_value = False
        else:
            raise HTTPException(status_code=400, detail="Respuesta invalida")

    if rating is not None or resolved_value is not None:
        feedback = apply_ticket_sla_feedback(
            db,
            ticket_id=ticket_id,
            rating=rating,
            resolved=resolved_value,
        )
    else:
        feedback = get_or_create_ticket_sla_feedback(db, ticket_id)

    token_value = build_sla_feedback_token(ticket_id)

    return templates.TemplateResponse(
        "public_sla_feedback.html",
        {
            "request": request,
            "ticket": ticket,
            "feedback": feedback,
            "rating_links": {
                value: build_sla_feedback_link(ticket_id=ticket_id, token=token_value, rating=value)
                for value in range(1, 6)
            },
            "resolved_yes_link": build_sla_feedback_link(ticket_id=ticket_id, token=token_value, resolved="si"),
            "resolved_no_link": build_sla_feedback_link(ticket_id=ticket_id, token=token_value, resolved="no"),
            "is_complete": (
                feedback.technician_rating is not None
                and feedback.resolution_satisfied is not None
            ),
        },
    )


@router.get("/encuesta/{ticket_id}", response_class=HTMLResponse)
def ticket_sla_feedback_corporate(
    request: Request,
    ticket_id: int,
    token: str = Query(...),
    rating: int | None = Query(None, ge=1, le=5),
    resolved: str | None = Query(None),
    db: Session = Depends(get_db),
):
    return ticket_sla_feedback(
        request=request,
        ticket_id=ticket_id,
        token=token,
        rating=rating,
        resolved=resolved,
        db=db,
    )


@router.post("/fillout/webhook")
def fillout_sla_webhook(
    payload: dict = Body(...),
    token: str | None = Query(None),
    db: Session = Depends(get_db),
):
    configured_token = (settings.SLA_WEBHOOK_TOKEN or "").strip()
    provided_token = (token or "").strip()

    if configured_token and provided_token != configured_token:
        raise HTTPException(status_code=403, detail="Webhook token invalido")

    ticket_id, rating, resolved = extract_feedback_from_payload(payload)

    query_params = payload.get("queryParameters") if isinstance(payload, dict) else None
    if isinstance(query_params, dict):
        if ticket_id is None:
            ticket_id = parse_ticket_id_value(
                query_params.get("ticket_id") or query_params.get("ticketId")
            )

    if ticket_id is None:
        top_ticket_id = payload.get("ticket_id") or payload.get("ticketId")
        ticket_id = parse_ticket_id_value(top_ticket_id)

    if rating is None:
        rating = parse_rating_value(
            payload.get("atencion_tecnico")
            or payload.get("technician_rating")
            or payload.get("rating")
        )

    if resolved is None:
        resolved = parse_resolution_value(
            payload.get("tiempo_resolucion")
            or payload.get("resolution_satisfied")
            or payload.get("resolved")
        )

    store_sla_feedback_event(
        db,
        payload=payload,
        source="fillout",
        ticket_id=ticket_id,
        rating=rating,
        resolved=resolved,
    )

    if ticket_id is None:
        return JSONResponse(
            {
                "ok": False,
                "stored": True,
                "message": "Webhook recibido, pero no se pudo identificar ticket_id.",
            },
            status_code=202,
        )

    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        return JSONResponse(
            {
                "ok": False,
                "stored": True,
                "message": f"Webhook recibido, pero el ticket #{ticket_id} no existe.",
            },
            status_code=202,
        )

    feedback = apply_ticket_sla_feedback(
        db,
        ticket_id=ticket_id,
        rating=rating,
        resolved=resolved,
    )

    return {
        "ok": True,
        "ticket_id": ticket_id,
        "technician_rating": feedback.technician_rating,
        "resolution_satisfied": feedback.resolution_satisfied,
    }


# ──────────────────────────────────────────────
# Comprobante Ley Karin — formulario público, sin login
# ──────────────────────────────────────────────

class LeyKarinComprobante(BaseModel):
    nombre_completo: str
    rut: str
    cargo: str = ""
    correo: str = ""
    fecha: str = ""
    documentos: list[int] = []
    declaraciones: list[int] = []


@router.get("/ley-karin", response_class=HTMLResponse)
def ley_karin_form(request: Request):
    return templates.TemplateResponse(request, "public_ley_karin.html", {})


@router.post("/ley-karin/informe")
def ley_karin_informe(payload: LeyKarinComprobante, background_tasks: BackgroundTasks):
    from io import BytesIO

    from fastapi.responses import StreamingResponse

    from ATC.app.services.ley_karin_service import (
        enviar_comprobante_ley_karin_email,
        generar_comprobante_ley_karin_pdf,
    )

    if not payload.nombre_completo.strip() or not payload.rut.strip():
        raise HTTPException(status_code=422, detail="Nombre completo y RUT son obligatorios.")

    pdf_bytes = generar_comprobante_ley_karin_pdf(payload.model_dump())

    background_tasks.add_task(
        enviar_comprobante_ley_karin_email, payload.correo.strip(), pdf_bytes, payload.nombre_completo
    )

    import re as _re
    nombre_ascii = _re.sub(r"[^A-Za-z0-9_-]+", "_", f"Comprobante_LeyKarin_{payload.nombre_completo}")[:80]
    filename = f"{nombre_ascii}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=background_tasks,
    )


# ──────────────────────────────────────────────
# Toma de Conocimiento — Capacitación Ley Karin (jefaturas) — formulario
# público, sin login. Documento distinto y más acotado que el comprobante
# general de arriba: acredita la participación en UNA capacitación puntual.
# ──────────────────────────────────────────────

class LeyKarinCapacitacionComprobante(BaseModel):
    nombre_completo: str
    rut: str
    cargo: str = ""
    correo: str = ""
    fecha_capacitacion: str = ""
    modalidad: str = ""


@router.get("/ley-karin-capacitacion", response_class=HTMLResponse)
def ley_karin_capacitacion_form(request: Request):
    return templates.TemplateResponse(request, "public_ley_karin_capacitacion.html", {})


@router.post("/ley-karin-capacitacion/informe")
def ley_karin_capacitacion_informe(payload: LeyKarinCapacitacionComprobante, background_tasks: BackgroundTasks):
    from io import BytesIO

    from fastapi.responses import StreamingResponse

    from ATC.app.services.ley_karin_service import (
        enviar_toma_conocimiento_capacitacion_email,
        generar_toma_conocimiento_capacitacion_pdf,
    )

    if not payload.nombre_completo.strip() or not payload.rut.strip():
        raise HTTPException(status_code=422, detail="Nombre completo y RUT son obligatorios.")

    pdf_bytes = generar_toma_conocimiento_capacitacion_pdf(payload.model_dump())

    background_tasks.add_task(
        enviar_toma_conocimiento_capacitacion_email, payload.correo.strip(), pdf_bytes, payload.nombre_completo
    )

    import re as _re
    nombre_ascii = _re.sub(r"[^A-Za-z0-9_-]+", "_", f"TomaConocimiento_Capacitacion_LeyKarin_{payload.nombre_completo}")[:80]
    filename = f"{nombre_ascii}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=background_tasks,
    )
