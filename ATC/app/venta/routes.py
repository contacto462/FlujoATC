from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.user import User
from app.routes.web import get_current_user_web
from app.venta.schemas import VentaClienteCreateRequest, VentaClienteCreateResponse
from app.venta.service import create_cliente, fetch_comunas, fetch_regiones, rut_exists

router = APIRouter(tags=["venta"])
templates = Jinja2Templates(directory="app/templates")


def require_venta_user(current_user: User = Depends(get_current_user_web)) -> User:
    if not (current_user.is_admin or current_user.is_agent):
        raise HTTPException(status_code=403, detail="No tienes permisos para acceder a Venta.")
    return current_user


@router.get("/venta/clientes", response_class=HTMLResponse)
def venta_clientes_page(
    request: Request,
    current_user: User = Depends(require_venta_user),
):
    return templates.TemplateResponse(
        "RegistroCliente.html",
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

