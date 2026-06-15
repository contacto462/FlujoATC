from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ATC.app.core.db import get_db
from ATC.app.routes import web as helpdesk_web
from ATC.app.routes import incidencias as incidencias_main


router = APIRouter(tags=["client-notes"])


def _use_incidencias_notes(request: Request) -> bool:
    return bool(request.query_params.get("token")) or "access_token" not in request.cookies


@router.get("/api/client-notes")
def get_client_internal_notes(
    request: Request,
    client: str = Query(""),
    db: Session = Depends(get_db),
) -> JSONResponse:
    if _use_incidencias_notes(request):
        return incidencias_main.get_client_internal_notes(client=client)

    current_user = helpdesk_web.get_current_user_web(request=request, db=db)
    return helpdesk_web.api_get_client_internal_notes(
        client=client,
        db=db,
        current_user=current_user,
    )


@router.post("/api/client-notes")
def add_client_internal_note(
    request: Request,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
) -> JSONResponse:
    if _use_incidencias_notes(request):
        return incidencias_main.add_client_internal_note(payload=payload)

    current_user = helpdesk_web.get_current_user_web(request=request, db=db)
    return helpdesk_web.api_add_client_internal_note(
        payload=payload,
        db=db,
        current_user=current_user,
    )
