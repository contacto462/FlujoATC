from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import LoginRequest
from app.services import IncidenciasService
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


def get_service(db: Session = Depends(get_db)) -> IncidenciasService:
    return IncidenciasService(db)


def require_venta_token(
    token: str = Query(default="", min_length=1),
    service: IncidenciasService = Depends(get_service),
) -> str:
    if not service.usuario_logueado_por_token(token):
        raise HTTPException(status_code=401, detail="No autenticado")
    return token


@router.get("/venta/clientes", response_class=HTMLResponse)
def venta_clientes_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse("RegistroCliente.html", {"request": request, "token": token})


@router.get("/venta/panel-selector", response_class=HTMLResponse)
def venta_panel_selector_page(
    request: Request,
    token: str = Depends(require_venta_token),
    service: IncidenciasService = Depends(get_service),
):
    tecnico = service.get_usuario_actual(token)
    return templates.TemplateResponse(
        "panel_selector_venta.html",
        {"request": request, "token": token, "tecnico": tecnico},
    )


@router.get("/venta/bbdd-clientes", response_class=HTMLResponse)
def venta_bbdd_clientes_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse("BBDDClientes.html", {"request": request, "token": token})


@router.get("/api/venta/usuario-actual")
def venta_usuario_actual(
    token: str = Depends(require_venta_token),
    service: IncidenciasService = Depends(get_service),
):
    usuario = service.get_usuario_actual(token)
    return {"name": usuario, "username": usuario}


@router.get("/api/venta/clientes/verificar-rut")
def venta_verificar_rut(
    rut: str = Query(..., min_length=3),
    db: Session = Depends(get_db),
    _: str = Depends(require_venta_token),
):
    return {"exists": rut_exists(db, rut)}


@router.post("/api/venta/clientes", response_model=VentaClienteCreateResponse)
def venta_crear_cliente(
    payload: VentaClienteCreateRequest,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
    service: IncidenciasService = Depends(get_service),
):
    usuario = service.get_usuario_actual(token)
    record = create_cliente(db, payload, ejecutivo_email=usuario)
    return VentaClienteCreateResponse(ok=True, cliente_id=record.id, message="Cliente registrado correctamente.")


@router.get("/api/venta/catalogo/regiones")
def venta_catalogo_regiones(_: str = Depends(require_venta_token)):
    return {"regiones": fetch_regiones()}


@router.get("/api/venta/catalogo/comunas")
def venta_catalogo_comunas(
    region: str = Query(..., min_length=2),
    _: str = Depends(require_venta_token),
):
    return {"comunas": fetch_comunas(region)}


@router.get("/api/venta/clientes/tabla")
def venta_clientes_tabla(
    db: Session = Depends(get_db),
    _: str = Depends(require_venta_token),
):
    return get_clientes_table(db)


@router.post("/api/venta/clientes/tabla/guardar-fila")
def venta_clientes_guardar_fila(
    payload: VentaClienteTableUpdateRequest,
    db: Session = Depends(get_db),
    _: str = Depends(require_venta_token),
):
    update_cliente_row(db, payload.row_id, payload.values)
    return {"ok": True}
