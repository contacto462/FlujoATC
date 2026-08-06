from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from ATC.app.core.config import settings
from ATC.app.core.db import get_db
from ATC.app.core.incidencias_db import (
    SessionLocal as IncidenciasSessionLocal,
)
from ATC.app.services.incidencias_service import IncidenciasService
from ATC.app.services.user_service import UserService


router = APIRouter(tags=["unified-access"])
COOKIE_NAME = "access_token"


def _web_user_name_from_cookie(
    request: Request,
    db: Session,
) -> str | None:
    token = request.cookies.get(COOKIE_NAME)

    if not token:
        return None

    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALG],
        )
    except JWTError:
        return None

    username = payload.get("sub")

    if not username:
        return None

    user = UserService.find_by_login(
        db,
        username,
    )

    if not user or not user.is_active:
        return None

    return user.name or user.username


def _incidencias_user_name_from_token(
    token: str,
) -> str:
    db = IncidenciasSessionLocal()

    try:
        return IncidenciasService(
            db
        ).get_usuario_actual(token)

    finally:
        db.close()


@router.get(
    "/api/usuario-actual",
    include_in_schema=False,
)
def usuario_actual_unificado(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
):
    token = (token or "").strip()

    if not token:
        # Sin token en la URL (ya limpiada por el guard de sesion): recupera
        # el token crudo desde la cookie "atc_token" (incidencias.py) antes
        # de caer a la cookie JWT del panel unificado.
        token = (request.cookies.get("atc_token") or "").strip()

    if token:
        usuario = _incidencias_user_name_from_token(token)
        if usuario and usuario != "Desconocido":
            return {"usuario": usuario}

    web_user_name = _web_user_name_from_cookie(
        request,
        db,
    )

    if web_user_name:
        return {
            "usuario": web_user_name
        }

    return {
        "usuario": "Desconocido"
    }