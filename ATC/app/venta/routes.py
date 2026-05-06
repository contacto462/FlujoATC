from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.security import create_access_token, verify_password
from app.models.user import User
from app.routes.web import COOKIE_NAME, get_current_user_web
from app.venta.schemas import VentaClienteCreateRequest, VentaClienteCreateResponse, VentaClienteTableUpdateRequest
from app.venta.service import (
    create_cliente,
    fetch_comunas,
    fetch_regiones,
    get_clientes_table,
    rut_exists,
    update_cliente_row,
)

router = APIRouter(tags=["venta"])
templates = Jinja2Templates(directory="app/templates")


def require_venta_user(current_user: User = Depends(get_current_user_web)) -> User:
    if not (current_user.is_admin or current_user.is_agent):
        raise HTTPException(status_code=403, detail="No tienes permisos para acceder a Venta.")
    return current_user


def _decode_cookie_username(token: str) -> str:
    payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
    username = payload.get("sub")
    if not username:
        raise JWTError("Token sin sub")
    return username


@router.get("/venta/login", response_class=HTMLResponse)
def venta_login_page(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            username = _decode_cookie_username(token)
            user = db.query(User).filter(User.username == username).first()
            if user and user.is_active and (user.is_admin or user.is_agent):
                return RedirectResponse(url="/venta/clientes", status_code=303)
        except Exception:
            pass

    return templates.TemplateResponse("venta_login.html", {"request": request, "error": None})


@router.post("/venta/web/login")
def venta_web_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.is_active or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            "venta_login.html",
            {"request": request, "error": "Usuario o contrasena incorrectos"},
            status_code=401,
        )
    if not (user.is_admin or user.is_agent):
        return templates.TemplateResponse(
            "venta_login.html",
            {"request": request, "error": "No tienes permisos para RegistroClientes"},
            status_code=403,
        )

    token = create_access_token({"sub": user.username})
    resp = RedirectResponse(url="/venta/clientes", status_code=303)
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=settings.JWT_EXPIRES_MIN * 60,
    )
    return resp


@router.get("/venta/clientes", response_class=HTMLResponse)
def venta_clientes_page(
    request: Request,
    current_user: User = Depends(require_venta_user),
):
    return templates.TemplateResponse(
        "RegistroCliente.html",
        {"request": request, "user": current_user},
    )


@router.get("/venta/bbdd-clientes", response_class=HTMLResponse)
def venta_bbdd_clientes_page(
    request: Request,
    current_user: User = Depends(require_venta_user),
):
    return templates.TemplateResponse(
        "BBDDClientes.html",
        {"request": request, "user": current_user},
    )


@router.get("/api/venta/usuario-actual")
def venta_usuario_actual(current_user: User = Depends(require_venta_user)):
    return {"name": current_user.name, "username": current_user.username}


@router.get("/api/venta/clientes/verificar-rut")
def venta_verificar_rut(
    rut: str = Query(..., min_length=3),
    db: Session = Depends(get_db),
    _: User = Depends(require_venta_user),
):
    return {"exists": rut_exists(db, rut)}


@router.post("/api/venta/clientes", response_model=VentaClienteCreateResponse)
def venta_crear_cliente(
    payload: VentaClienteCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_venta_user),
):
    record = create_cliente(db, payload, ejecutivo_email=current_user.username)
    return VentaClienteCreateResponse(
        ok=True,
        cliente_id=record.id,
        message="Cliente registrado correctamente.",
    )


@router.get("/api/venta/catalogo/regiones")
def venta_catalogo_regiones(_: User = Depends(require_venta_user)):
    return {"regiones": fetch_regiones()}


@router.get("/api/venta/catalogo/comunas")
def venta_catalogo_comunas(
    region: str = Query(..., min_length=2),
    _: User = Depends(require_venta_user),
):
    return {"comunas": fetch_comunas(region)}


@router.get("/api/venta/clientes/tabla")
def venta_clientes_tabla(
    db: Session = Depends(get_db),
    _: User = Depends(require_venta_user),
):
    return get_clientes_table(db)


@router.post("/api/venta/clientes/tabla/guardar-fila")
def venta_clientes_guardar_fila(
    payload: VentaClienteTableUpdateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_venta_user),
):
    update_cliente_row(db, payload.row_id, payload.values)
    return {"ok": True}
