from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from sqlalchemy import text
from sqlalchemy.orm import Session

from ATC.app.core.config import settings
from ATC.app.core.db import get_db
from ATC.app.core.security import create_access_token
from ATC.app.core.session_policy import max_age_cookie_segundos
from ATC.app.models.compras import SolicitudCompra
from ATC.app.models.incidencias import LoginSession
from ATC.app.models.user import User
from ATC.app.services.compras_service import (
    _area_codes_for_user,
    cambiar_estado_panel,
    crear_solicitud,
    procesar_decision,
    GESTORS_COMPRA,
)
from ATC.app.services.user_service import UserService

router = APIRouter(prefix="/compras", tags=["compras"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))

COOKIE_NAME = "access_token"


# ──────────────────────────────────────────────
# Auth helpers (mismo patrón que bitacora.py)
# ──────────────────────────────────────────────

def _decode_cookie_token(token: str) -> str:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
        username = payload.get("sub")
        if not username:
            raise ValueError("Token sin sub")
        return str(username)
    except (JWTError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Token inválido") from exc


def _get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="No autenticado")
    username = _decode_cookie_token(token)
    user = UserService.find_by_login(db, username)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="No autenticado")
    return user


def _redirect_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


def _set_web_cookie(resp: RedirectResponse, token: str, user_id: int | None = None) -> RedirectResponse:
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=max_age_cookie_segundos(user_id, settings.JWT_EXPIRES_MIN * 60),
    )
    return resp


def _consume_session_token(
    request: Request,
    db: Session,
    token: str,
) -> RedirectResponse | None:
    token_limpio = str(token or "").strip()
    if not token_limpio:
        return None
    row = (
        db.query(User.username, User.id)
        .join(LoginSession, User.id == LoginSession.user_id)
        .filter(
            LoginSession.token == token_limpio,
            LoginSession.expires_at > datetime.now(timezone.utc),
            User.is_active == 1,
        )
        .first()
    )
    if not row:
        return _redirect_login()
    web_token = create_access_token({"sub": row[0]})
    clean_url = str(request.url.remove_query_params(["token"]))
    return _set_web_cookie(RedirectResponse(url=clean_url, status_code=303), web_token, row[1])


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


# ──────────────────────────────────────────────
# Acceso por panel
# ──────────────────────────────────────────────

def _can_access_contabilidad(user: User) -> bool:
    codes = set(_area_codes_for_user(user))
    return user.is_admin or "finanzas" in codes


def _can_access_operaciones(user: User) -> bool:
    codes = set(_area_codes_for_user(user))
    return user.is_admin or bool(codes & {"operaciones", "coordinacion", "finanzas"})


def _can_access_gerencia(user: User) -> bool:
    codes = set(_area_codes_for_user(user))
    return user.is_admin or "finanzas" in codes


def _can_access_control(user: User) -> bool:
    codes = set(_area_codes_for_user(user))
    email = (user.email or "").lower().strip()
    return user.is_admin or "finanzas" in codes or email in {e.lower() for e in GESTORS_COMPRA}


# ──────────────────────────────────────────────
# Formulario de solicitud
# ──────────────────────────────────────────────

@router.get("/solicitud", response_class=HTMLResponse)
def get_solicitud(request: Request, db: Session = Depends(get_db), token: str = Query(default="")):
    token_redirect = _consume_session_token(request, db, token)
    if token_redirect:
        return token_redirect
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return _redirect_login()
    try:
        username = _decode_cookie_token(token)
    except HTTPException:
        return _redirect_login()
    user = UserService.find_by_login(db, username)
    if not user or not user.is_active:
        return _redirect_login()
    return templates.TemplateResponse(
        request, "compras_solicitud.html", {"request": request, "user": user}
    )


@router.post("/solicitud")
async def post_solicitud(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    empresa: str = Form(...),
    centro: str = Form(...),
    motivo: str = Form(...),
    items_json: str = Form(...),
    presupuestos: list[UploadFile] = File(default=[]),
):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="No autenticado")
    try:
        username = _decode_cookie_token(token)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Token inválido")

    user = UserService.find_by_login(db, username)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Usuario no válido")

    try:
        items: list[dict] = json.loads(items_json)
    except Exception:
        raise HTTPException(status_code=422, detail="items_json inválido")

    if not items:
        raise HTTPException(status_code=422, detail="Debes ingresar al menos un ítem.")

    if not empresa or not centro:
        raise HTTPException(status_code=422, detail="Empresa y centro son obligatorios.")

    if not motivo.strip():
        raise HTTPException(status_code=422, detail="El motivo es obligatorio.")

    archivos: list[tuple[str, bytes]] = []
    for upload in (presupuestos or [])[:3]:
        if upload and upload.filename:
            data = await upload.read()
            if data:
                archivos.append((upload.filename, data))

    base_url = _base_url(request)

    solicitud = crear_solicitud(
        db=db,
        user=user,
        empresa=empresa,
        centro=centro,
        items=items,
        motivo=motivo,
        archivos=archivos,
        base_url=base_url,
    )

    return JSONResponse({"ok": True, "codigo": solicitud.codigo})


# ──────────────────────────────────────────────
# Decisión desde correo (aprobar/rechazar)
# ──────────────────────────────────────────────

@router.get("/decision", response_class=HTMLResponse)
def get_decision(
    request: Request,
    token: str = "",
    accion: str = "",
    obs: str = "",
    db: Session = Depends(get_db),
):
    # Si es rechazo sin obs → mostrar formulario
    if accion == "rechazar" and not obs:
        return templates.TemplateResponse(
            request, "compras_decision_rechazo.html",
            {"request": request, "token": token, "accion": accion},
        )

    if not token or accion not in ("aprobar", "rechazar"):
        return HTMLResponse("<h2>Parámetros inválidos.</h2>", status_code=400)

    try:
        resultado = procesar_decision(
            db=db,
            token=token,
            accion=accion,
            obs=obs,
            base_url=_base_url(request),
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "compras_decision_resultado.html",
            {"request": request, "titulo": "Error", "mensaje": str(exc), "color": "#7f8c8d"},
        )

    return templates.TemplateResponse(
        request, "compras_decision_resultado.html",
        {
            "request": request,
            "titulo": resultado["titulo"],
            "mensaje": resultado["mensaje"],
            "color": resultado["color"],
        },
    )


@router.post("/decision/rechazar", response_class=HTMLResponse)
async def post_decision_rechazar(
    request: Request,
    db: Session = Depends(get_db),
    token: str = Form(...),
    obs: str = Form(...),
):
    try:
        resultado = procesar_decision(
            db=db,
            token=token,
            accion="rechazar",
            obs=obs,
            base_url=_base_url(request),
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "compras_decision_resultado.html",
            {"request": request, "titulo": "Error", "mensaje": str(exc), "color": "#7f8c8d"},
        )

    return templates.TemplateResponse(
        request, "compras_decision_resultado.html",
        {
            "request": request,
            "titulo": resultado["titulo"],
            "mensaje": resultado["mensaje"],
            "color": resultado["color"],
        },
    )


# ──────────────────────────────────────────────
# Panel Operaciones
# ──────────────────────────────────────────────

@router.get("/panel-operaciones", response_class=HTMLResponse)
def panel_operaciones(request: Request, db: Session = Depends(get_db), token: str = Query(default="")):
    token_redirect = _consume_session_token(request, db, token)
    if token_redirect:
        return token_redirect
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return _redirect_login()
    try:
        username = _decode_cookie_token(token)
    except HTTPException:
        return _redirect_login()
    user = UserService.find_by_login(db, username)
    if not user or not user.is_active:
        return _redirect_login()
    if not _can_access_operaciones(user):
        raise HTTPException(status_code=403, detail="Sin acceso al panel de Operaciones")

    solicitudes = (
        db.query(SolicitudCompra)
        .filter(SolicitudCompra.estado == "Pendiente Operaciones")
        .order_by(SolicitudCompra.fecha_solicitud.desc())
        .all()
    )
    return templates.TemplateResponse(
        request, "compras_panel_operaciones.html",
        {"request": request, "user": user, "solicitudes": solicitudes},
    )


# ──────────────────────────────────────────────
# Panel Contabilidad
# ──────────────────────────────────────────────

@router.get("/panel-contabilidad", response_class=HTMLResponse)
def panel_contabilidad(request: Request, db: Session = Depends(get_db), token: str = Query(default="")):
    token_redirect = _consume_session_token(request, db, token)
    if token_redirect:
        return token_redirect
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return _redirect_login()
    try:
        username = _decode_cookie_token(token)
    except HTTPException:
        return _redirect_login()
    user = UserService.find_by_login(db, username)
    if not user or not user.is_active:
        return _redirect_login()
    if not _can_access_contabilidad(user):
        raise HTTPException(status_code=403, detail="Sin acceso al panel de Contabilidad")

    solicitudes = (
        db.query(SolicitudCompra)
        .filter(SolicitudCompra.estado == "Pendiente Contabilidad")
        .order_by(SolicitudCompra.fecha_solicitud.desc())
        .all()
    )
    return templates.TemplateResponse(
        request, "compras_panel_contabilidad.html",
        {"request": request, "user": user, "solicitudes": solicitudes},
    )


# ──────────────────────────────────────────────
# Panel Gerencia
# ──────────────────────────────────────────────

@router.get("/panel-gerencia", response_class=HTMLResponse)
def panel_gerencia(request: Request, db: Session = Depends(get_db), token: str = Query(default="")):
    token_redirect = _consume_session_token(request, db, token)
    if token_redirect:
        return token_redirect
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return _redirect_login()
    try:
        username = _decode_cookie_token(token)
    except HTTPException:
        return _redirect_login()
    user = UserService.find_by_login(db, username)
    if not user or not user.is_active:
        return _redirect_login()
    if not _can_access_gerencia(user):
        raise HTTPException(status_code=403, detail="Sin acceso al panel de Gerencia")

    solicitudes = (
        db.query(SolicitudCompra)
        .filter(SolicitudCompra.estado == "Pendiente Gerencia")
        .order_by(SolicitudCompra.fecha_solicitud.desc())
        .all()
    )
    return templates.TemplateResponse(
        request, "compras_panel_gerencia.html",
        {"request": request, "user": user, "solicitudes": solicitudes},
    )


# ──────────────────────────────────────────────
# Panel Control (gestors de compra)
# ──────────────────────────────────────────────

@router.get("/panel-control", response_class=HTMLResponse)
def panel_control(request: Request, db: Session = Depends(get_db), token: str = Query(default="")):
    token_redirect = _consume_session_token(request, db, token)
    if token_redirect:
        return token_redirect
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return _redirect_login()
    try:
        username = _decode_cookie_token(token)
    except HTTPException:
        return _redirect_login()
    user = UserService.find_by_login(db, username)
    if not user or not user.is_active:
        return _redirect_login()
    if not _can_access_control(user):
        raise HTTPException(status_code=403, detail="Sin acceso al panel de Control de Compras")

    solicitudes = (
        db.query(SolicitudCompra)
        .filter(
            SolicitudCompra.estado.in_(["Pendiente", "En proceso", "Terminado", "Rechazado"])
        )
        .order_by(SolicitudCompra.fecha_solicitud.desc())
        .all()
    )
    return templates.TemplateResponse(
        request, "compras_panel_control.html",
        {"request": request, "user": user, "solicitudes": solicitudes},
    )


# ──────────────────────────────────────────────
# API: cambiar estado desde panel
# ──────────────────────────────────────────────

@router.post("/api/cambiar-estado")
async def api_cambiar_estado(
    request: Request,
    db: Session = Depends(get_db),
):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="No autenticado")
    try:
        username = _decode_cookie_token(token)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Token inválido")
    user = UserService.find_by_login(db, username)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="No autenticado")

    body = await request.json()
    solicitud_id = int(body.get("id", 0))
    nuevo_estado = str(body.get("estado", ""))
    obs = str(body.get("obs", ""))

    # Permisos según el estado destino
    if nuevo_estado in ("Pendiente Contabilidad",) and not _can_access_operaciones(user):
        raise HTTPException(status_code=403, detail="Sin permisos")
    if nuevo_estado in ("Pendiente", "Pendiente Gerencia") and not _can_access_contabilidad(user):
        raise HTTPException(status_code=403, detail="Sin permisos")
    if nuevo_estado in ("En proceso", "Terminado", "Rechazado") and not _can_access_control(user):
        raise HTTPException(status_code=403, detail="Sin permisos")

    try:
        solicitud = cambiar_estado_panel(
            db=db,
            solicitud_id=solicitud_id,
            nuevo_estado=nuevo_estado,
            obs=obs,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return JSONResponse({"ok": True, "estado": solicitud.estado, "codigo": solicitud.codigo})


# ──────────────────────────────────────────────
# API: decision desde panel (operaciones/conta/gerencia)
# ──────────────────────────────────────────────

@router.post("/api/decision-panel")
async def api_decision_panel(
    request: Request,
    db: Session = Depends(get_db),
):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="No autenticado")
    try:
        username = _decode_cookie_token(token)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Token inválido")
    user = UserService.find_by_login(db, username)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="No autenticado")

    body = await request.json()
    id_raw = body.get("id")
    if not id_raw:
        raise HTTPException(status_code=422, detail="id requerido")
    solicitud_id = int(id_raw)
    accion = str(body.get("accion", ""))  # "aprobar" | "rechazar"
    obs = str(body.get("obs", ""))

    solicitud = db.get(SolicitudCompra, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")

    # Validar permiso según estado actual
    estado = solicitud.estado
    if estado == "Pendiente Operaciones" and not _can_access_operaciones(user):
        raise HTTPException(status_code=403, detail="Sin permisos")
    if estado == "Pendiente Contabilidad" and not _can_access_contabilidad(user):
        raise HTTPException(status_code=403, detail="Sin permisos")
    if estado == "Pendiente Gerencia" and not _can_access_gerencia(user):
        raise HTTPException(status_code=403, detail="Sin permisos")

    try:
        resultado = procesar_decision(
            db=db,
            token=solicitud.decision_token or "",
            accion=accion,
            obs=obs,
            base_url=_base_url(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return JSONResponse(resultado)
