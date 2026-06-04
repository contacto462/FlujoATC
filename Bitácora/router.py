from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from sqlalchemy import text
from sqlalchemy.orm import Session

from Bitácora.access import can_access_bitacora
from ATC.app.core.config import settings
from ATC.app.core.db import get_db, get_incidencias_db
from ATC.app.models.user import User
from ATC.app.services.user_service import UserService


router = APIRouter(tags=["bitacora"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
COOKIE_NAME = "access_token"


def _decode_cookie_token(token: str) -> str:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
        username = payload.get("sub")
        if not username:
            raise ValueError("Token sin sub")
        return str(username)
    except (JWTError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Token invalido") from exc


def _require_bitacora_access(user: User) -> None:
    if not can_access_bitacora(user):
        raise HTTPException(
            status_code=403,
            detail="La bitacora no esta disponible para usuarios con acceso solo Tecnico.",
        )


def get_current_user_bitacora(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="No autenticado")

    username = _decode_cookie_token(token)
    user = UserService.find_by_login(db, username)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="No autenticado")
    return user


@router.get("/bitacora", response_class=HTMLResponse)
def bitacora_page(
    request: Request,
    db: Session = Depends(get_db),
    incidencias_db: Session = Depends(get_incidencias_db),
    token: str = Query(default=""),
):
    current_user: User | None = None
    token_limpio = (token or "").strip()
    if token_limpio:
        return RedirectResponse(url=f"/sso/login?token={token_limpio}&next=/bitacora", status_code=303)

    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token:
        try:
            username = _decode_cookie_token(cookie_token)
            current_user = UserService.find_by_login(db, username)
        except Exception:
            current_user = None

    if not current_user or not current_user.is_active:
        return RedirectResponse(url="/login", status_code=303)

    _require_bitacora_access(current_user)

    empresas_rows = incidencias_db.execute(
        text(
            """
            SELECT DISTINCT TRIM(nombre_empresa) AS empresa
            FROM bbdd_sucursales
            WHERE COALESCE(TRIM(nombre_empresa), '') <> ''
            ORDER BY TRIM(nombre_empresa) ASC
            """
        )
    ).mappings().all()
    empresas = [str(row.get("empresa") or "").strip() for row in empresas_rows if str(row.get("empresa") or "").strip()]

    return templates.TemplateResponse(
        request,
        "bitacora.html",
        {
            "request": request,
            "user": current_user,
            "empresas": empresas,
        },
    )


@router.get("/api/bitacora/sucursales")
def bitacora_sucursales_api(
    empresa: str = Query(...),
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: User = Depends(get_current_user_bitacora),
):
    _require_bitacora_access(current_user)
    empresa_limpia = str(empresa or "").strip()
    if not empresa_limpia:
        raise HTTPException(status_code=400, detail="Debes indicar una empresa.")

    rows = incidencias_db.execute(
        text(
            """
            SELECT
                id,
                COALESCE(TRIM(rut), '') AS rut,
                COALESCE(TRIM(nombre_empresa), '') AS nombre_empresa,
                COALESCE(TRIM(nombre_sucursal), '') AS nombre_sucursal,
                COALESCE(TRIM(direccion_sucursal), '') AS direccion_sucursal,
                COALESCE(TRIM(comuna), '') AS comuna,
                COALESCE(TRIM(region), '') AS region
            FROM bbdd_sucursales
            WHERE LOWER(TRIM(nombre_empresa)) = LOWER(TRIM(:empresa))
            ORDER BY nombre_sucursal ASC, direccion_sucursal ASC, id ASC
            """
        ),
        {"empresa": empresa_limpia},
    ).mappings().all()

    sucursales = [
        {
            "id": row.get("id"),
            "rut": str(row.get("rut") or "").strip(),
            "nombre_empresa": str(row.get("nombre_empresa") or "").strip(),
            "nombre_sucursal": str(row.get("nombre_sucursal") or "").strip(),
            "direccion_sucursal": str(row.get("direccion_sucursal") or "").strip(),
            "comuna": str(row.get("comuna") or "").strip(),
            "region": str(row.get("region") or "").strip(),
        }
        for row in rows
    ]
    return {"empresa": empresa_limpia, "total": len(sucursales), "sucursales": sucursales}
