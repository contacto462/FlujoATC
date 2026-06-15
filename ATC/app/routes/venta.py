from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path

from ATC.app.core.incidencias_db import get_db
from ATC.app.schemas.incidencias import LoginRequest
from ATC.app.services.incidencias_service import IncidenciasService
from ATC.app.schemas.venta import (
    VentaClienteCreateRequest,
    VentaClienteCreateResponse,
    VentaODSCreateRequest,
    VentaODSCreateResponse,
    VentaODSUpdateRequest,
    VentaPersonaCampoUpdateRequest,
    VentaPersonaRegistroRequest,
    VentaClienteTableUpdateRequest,
    VentaAdminEstadoRequest,
    VentaAnularODSRequest,
    VentaContratoUploadRequest,
    VentaFinanzasEstadoRequest,
    VentaOperacionesEstadoRequest,
    VentaOperacionesFechaRequest,
    VentaServicioTecnicoEstadoRequest,
    VentaServicioTecnicoValorRequest,
    VentaSucursalCreateRequest,
    VentaSucursalCreateResponse,
    VentaSucursalTableUpdateRequest,
)
from ATC.app.services.venta_service import (
    VENTA_UPLOADS_DIR,
    create_cliente,
    create_ods,
    create_sucursal,
    add_persona_registro,
    anular_ods_venta,
    fetch_comunas,
    fetch_regiones,
    get_cliente_resumen_by_rut,
    get_cliente_nombre_by_rut,
    get_cliente_sucursal_resumen,
    get_clientes_table,
    get_comercial_todo,
    get_ods_codes,
    get_ods_detail,
    get_admin_ods_detail,
    get_admin_ods_rows,
    get_finanzas_ods_detail,
    get_finanzas_ods_rows,
    get_operaciones_ods_rows,
    get_servicio_tecnico_ventas_contacto,
    get_servicio_tecnico_ventas_detail,
    get_servicio_tecnico_ventas_rows,
    get_ods_data_by_rut,
    get_sucursales_table,
    get_coordinates_for_address,
    get_proveedores_electricidad,
    get_proveedores_internet,
    rut_exists,
    subir_contrato_venta,
    update_ods,
    update_admin_ods_estado,
    update_finanzas_ods_estado,
    update_operaciones_ods_estado,
    update_operaciones_ods_fecha,
    update_servicio_tecnico_ventas_estado,
    update_servicio_tecnico_ventas_valor,
    update_cliente_row,
    update_persona_campo,
    update_sucursal_row,
)

router = APIRouter(tags=["venta"])
INCIDENCIAS_APP_DIR = Path(__file__).resolve().parents[2] / "incidencias" / "app"
templates = Jinja2Templates(directory=str(INCIDENCIAS_APP_DIR / "templates"))


def get_service(db: Session = Depends(get_db)) -> IncidenciasService:
    return IncidenciasService(db)


def require_venta_token(
    token: str = Query(default="", min_length=1),
    service: IncidenciasService = Depends(get_service),
) -> str:
    if not service.usuario_logueado_por_token(token):
        raise HTTPException(status_code=401, detail="No autenticado")
    return token


import re as _re_dept
from sqlalchemy import text as _sql_text
from ATC.app.models.incidencias import LoginSession as _LoginSession, User as _User


_DEPARTMENT_AREA_MAP = {
    "soporte": ["soporte"],
    "servicio tecnico": ["servicio_tecnico"],
    "servicio técnico": ["servicio_tecnico"],
    "tecnicos": ["tecnicos"],
    "técnicos": ["tecnicos"],
    "operador": ["incidencias", "coordinacion"],
    "coordinacion": ["coordinacion"],
    "coordinación": ["coordinacion"],
    "comercial": ["venta"],
    "finanzas": ["finanzas"],
    "administracion": ["administracion"],
    "administración": ["administracion"],
    "operaciones": ["operaciones"],
}


def _back_context_for_token(db: Session, token: str) -> tuple[bool, str]:
    """Devuelve (show_back_button, back_url) para los panel_selectors de Incidencias.

    Muestra el botón si el user tiene >1 área y vuelve al selector correcto
    preservando el token actual.
    """
    from ATC.app.core.incidencias_config import settings as _settings
    count = _count_areas_for_token(db, token)
    if count <= 1:
        return False, f"/seleccionar-area?token={token}" if token else "/seleccionar-area"
    helpdesk = (_settings.helpdesk_base_url or "").rstrip("/")
    if not helpdesk:
        return True, f"/seleccionar-area?token={token}" if token else "/seleccionar-area"
    return True, f"{helpdesk}/seleccionar-area?token={token}"


def _count_areas_for_token(db: Session, token: str) -> int:
    sesion = db.query(_LoginSession).filter(_LoginSession.token == token).first()
    if not sesion or not sesion.user_id:
        return 0
    user = db.get(_User, int(sesion.user_id))
    if not user:
        return 0
    raw = str(user.department or "").strip()
    if not raw:
        return 0
    parts = _re_dept.split(r"[;,|]+", raw)
    seen: set[str] = set()
    for dept in parts:
        key = dept.strip().casefold()
        for code in _DEPARTMENT_AREA_MAP.get(key, []):
            seen.add(code)
    return len(seen)


@router.get("/venta/clientes", response_class=HTMLResponse)
def venta_clientes_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse(request, "registro_cliente.html", {"request": request, "token": token})


@router.get("/venta/sucursales", response_class=HTMLResponse)
def venta_sucursales_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse(request, "registro_sucursal.html", {"request": request, "token": token})


@router.get("/venta/ods", response_class=HTMLResponse)
def venta_ods_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse(request, "registro_ods.html", {"request": request, "token": token})


@router.get("/venta/bbdd-orden-servicio", response_class=HTMLResponse)
def venta_bbdd_orden_servicio_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse(request, "bbdd_orden_servicio.html", {"request": request, "token": token})


@router.get("/venta/administracion", response_class=HTMLResponse)
def venta_administracion_page(
    request: Request,
    token: str = Query(default=""),
    service: IncidenciasService = Depends(get_service),
    db: Session = Depends(get_db),
):
    if not service.usuario_logueado_por_token(token):
        return RedirectResponse(url="/?form=login&next=auto", status_code=303)
    show_back_button, back_url = _back_context_for_token(db, token)
    return templates.TemplateResponse(
        request,
        "seleccion_panel_administracion.html",
        {"request": request, "token": token, "show_back_button": show_back_button, "back_url": back_url},
    )


@router.get("/venta/administracion/tabla", response_class=HTMLResponse)
def venta_tabla_administracion_page(
    request: Request,
    token: str = Query(default=""),
    service: IncidenciasService = Depends(get_service),
):
    if not service.usuario_logueado_por_token(token):
        return RedirectResponse(url="/?form=login&next=auto", status_code=303)
    return templates.TemplateResponse(request, "tabla_administracion_venta.html", {"request": request, "token": token})


@router.get("/venta/administracion/login", response_class=HTMLResponse)
def venta_administracion_login_page(request: Request):
    return RedirectResponse(url="/?form=login&next=auto", status_code=303)


@router.get("/venta/finanzas", response_class=HTMLResponse)
def venta_finanzas_page(
    request: Request,
    token: str = Query(default=""),
    service: IncidenciasService = Depends(get_service),
    db: Session = Depends(get_db),
):
    if not service.usuario_logueado_por_token(token):
        return RedirectResponse(url="/?form=login&next=auto", status_code=303)
    tecnico = service.get_usuario_actual(token)
    show_back_button, back_url = _back_context_for_token(db, token)
    return templates.TemplateResponse(
        request,
        "seleccion_panel_finanzas.html",
        {"request": request, "token": token, "tecnico": tecnico, "show_back_button": show_back_button, "back_url": back_url},
    )


@router.get("/venta/finanzas/tabla", response_class=HTMLResponse)
def venta_tabla_finanzas_page(
    request: Request,
    token: str = Query(default=""),
    service: IncidenciasService = Depends(get_service),
):
    if not service.usuario_logueado_por_token(token):
        return RedirectResponse(url="/?form=login&next=auto", status_code=303)
    return templates.TemplateResponse(request, "tabla_finanzas_venta.html", {"request": request, "token": token})


@router.get("/venta/finanzas/rendiciones", response_class=HTMLResponse)
def venta_finanzas_rendiciones_page(
    request: Request,
    token: str = Query(default=""),
    service: IncidenciasService = Depends(get_service),
):
    if not service.usuario_logueado_por_token(token):
        return RedirectResponse(url="/?form=login&next=auto", status_code=303)
    return templates.TemplateResponse(
        request,
        "finanzas_libro.html",
        {"request": request, "token": token},
    )


@router.get("/venta/finanzas/rendiciones/hoja", response_class=HTMLResponse)
def venta_finanzas_rendiciones_hoja_page(
    request: Request,
    token: str = Query(default=""),
    service: IncidenciasService = Depends(get_service),
):
    if not service.usuario_logueado_por_token(token):
        return RedirectResponse(url="/?form=login&next=auto", status_code=303)
    return templates.TemplateResponse(
        request,
        "finanzas_rendiciones.html",
        {"request": request, "token": token},
    )


def _finanzas_page(template_name: str):
    def _route(
        request: Request,
        token: str = Query(default=""),
        service: IncidenciasService = Depends(get_service),
    ):
        if not service.usuario_logueado_por_token(token):
            return RedirectResponse(url="/?form=login&next=auto", status_code=303)
        return templates.TemplateResponse(request, template_name, {"request": request, "token": token})
    return _route


router.add_api_route("/venta/finanzas/consolidado", _finanzas_page("finanzas_consolidado.html"), methods=["GET"], response_class=HTMLResponse)
router.add_api_route("/venta/finanzas/viatico-especial", _finanzas_page("finanzas_viatico_especial.html"), methods=["GET"], response_class=HTMLResponse)
router.add_api_route("/venta/finanzas/pagos", _finanzas_page("finanzas_pagos.html"), methods=["GET"], response_class=HTMLResponse)
router.add_api_route("/venta/finanzas/suma-pagos", _finanzas_page("finanzas_suma_pagos.html"), methods=["GET"], response_class=HTMLResponse)


@router.get("/venta/finanzas/login", response_class=HTMLResponse)
def venta_finanzas_login_page(request: Request):
    return RedirectResponse(url="/?form=login&next=auto", status_code=303)


@router.get("/venta/login", response_class=HTMLResponse)
def venta_login_page(request: Request):
    return RedirectResponse(url="/?form=login&next=auto", status_code=303)


@router.get("/venta/panel-selector", response_class=HTMLResponse)
def venta_panel_selector_page(
    request: Request,
    token: str = Depends(require_venta_token),
    service: IncidenciasService = Depends(get_service),
    db: Session = Depends(get_db),
):
    tecnico = service.get_usuario_actual(token)
    show_back_button, back_url = _back_context_for_token(db, token)
    return templates.TemplateResponse(
        request,
        "seleccion_panel_venta.html",
        {
            "request": request,
            "token": token,
            "tecnico": tecnico,
            "show_back_button": show_back_button,
            "back_url": back_url,
        },
    )


@router.get("/venta/bbdd-clientes", response_class=HTMLResponse)
def venta_bbdd_clientes_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse(request, "bbdd_clientes.html", {"request": request, "token": token})


@router.get("/venta/bbdd-sucursales", response_class=HTMLResponse)
def venta_bbdd_sucursales_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse(request, "bbdd_sucursal.html", {"request": request, "token": token})


@router.get("/venta/informacion-cliente", response_class=HTMLResponse)
def venta_informacion_cliente_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse(request, "informacion_cliente.html", {"request": request, "token": token})


@router.get("/venta/tabla-comercial", response_class=HTMLResponse)
def venta_tabla_comercial_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse(request, "tabla_comercial_venta.html", {"request": request, "token": token})


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


@router.get("/api/venta/clientes/buscar-por-rut")
def venta_buscar_cliente_por_rut(
    rut: str = Query(..., min_length=3),
    db: Session = Depends(get_db),
    _: str = Depends(require_venta_token),
):
    return {"nombre": get_cliente_nombre_by_rut(db, rut)}


@router.get("/api/venta/clientes/resumen")
def venta_cliente_resumen(
    rut: str = Query(..., min_length=3),
    db: Session = Depends(get_db),
    _: str = Depends(require_venta_token),
):
    return get_cliente_resumen_by_rut(db, rut)


@router.get("/api/venta/clientes/resumen-sucursal")
def venta_cliente_resumen_sucursal(
    rut: str = Query(..., min_length=3),
    sucursal_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    _: str = Depends(require_venta_token),
):
    return get_cliente_sucursal_resumen(db, rut, sucursal_id)


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


@router.get("/api/venta/proveedores/internet")
def venta_proveedores_internet(_: str = Depends(require_venta_token)):
    return {"proveedores": get_proveedores_internet()}


@router.get("/api/venta/proveedores/electricidad")
def venta_proveedores_electricidad(_: str = Depends(require_venta_token)):
    return {"proveedores": get_proveedores_electricidad()}


@router.get("/api/venta/ods/datos-por-rut")
def venta_ods_datos_por_rut(
    rut: str = Query(..., min_length=3),
    db: Session = Depends(get_db),
    _: str = Depends(require_venta_token),
):
    return get_ods_data_by_rut(db, rut)


@router.get("/api/venta/ods/lista")
def venta_ods_lista(
    db: Session = Depends(get_db),
    _: str = Depends(require_venta_token),
):
    return {"ods": get_ods_codes(db)}


@router.get("/api/venta/ods/detalle")
def venta_ods_detalle(
    codigo: str = Query(..., min_length=2),
    db: Session = Depends(get_db),
    _: str = Depends(require_venta_token),
):
    return get_ods_detail(db, codigo)


@router.get("/api/venta/coordenadas")
def venta_coordenadas(
    direccion: str = Query(..., min_length=2),
    comuna: str = Query(..., min_length=2),
    db: Session = Depends(get_db),
    _: str = Depends(require_venta_token),
):
    return get_coordinates_for_address(db, direccion, comuna)


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


@router.get("/api/venta/sucursales/tabla")
def venta_sucursales_tabla(
    db: Session = Depends(get_db),
    _: str = Depends(require_venta_token),
):
    return get_sucursales_table(db)


@router.post("/api/venta/sucursales/tabla/guardar-fila")
def venta_sucursales_guardar_fila(
    payload: VentaSucursalTableUpdateRequest,
    db: Session = Depends(get_db),
    _: str = Depends(require_venta_token),
):
    update_sucursal_row(db, payload.row_id, payload.values)
    return {"ok": True}


@router.post("/api/venta/clientes/persona")
def venta_cliente_agregar_persona(
    payload: VentaPersonaRegistroRequest,
    db: Session = Depends(get_db),
    _: str = Depends(require_venta_token),
):
    add_persona_registro(db, payload)
    return {"ok": True}


@router.post("/api/venta/clientes/persona/editar")
def venta_cliente_editar_persona(
    payload: VentaPersonaCampoUpdateRequest,
    db: Session = Depends(get_db),
    _: str = Depends(require_venta_token),
):
    update_persona_campo(db, payload)
    return {"ok": True}


@router.post("/api/venta/sucursales", response_model=VentaSucursalCreateResponse)
def venta_crear_sucursal(
    payload: VentaSucursalCreateRequest,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
    service: IncidenciasService = Depends(get_service),
):
    usuario = service.get_usuario_actual(token)
    record = create_sucursal(db, payload, usuario_email=usuario)
    return VentaSucursalCreateResponse(
        ok=True,
        sucursal_id=record.id,
        message="Sucursal registrada correctamente.",
    )


@router.post("/api/venta/ods", response_model=VentaODSCreateResponse)
def venta_crear_ods(
    payload: VentaODSCreateRequest,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
    service: IncidenciasService = Depends(get_service),
):
    usuario = service.get_usuario_actual(token)
    record = create_ods(db, payload, usuario_email=usuario)
    return VentaODSCreateResponse(
        ok=True,
        ods_id=record.id,
        codigo=record.codigo,
        message="Orden de servicio registrada correctamente.",
    )


@router.post("/api/venta/ods/guardar")
def venta_guardar_ods(
    payload: VentaODSUpdateRequest,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
    service: IncidenciasService = Depends(get_service),
):
    usuario = service.get_usuario_actual(token)
    record = update_ods(db, payload, usuario_email=usuario)
    return {"ok": True, "codigo": record.codigo}


@router.get("/api/venta/admin-ods")
def venta_admin_ods_listar(
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return {"rows": get_admin_ods_rows(db)}


@router.get("/api/venta/admin-ods/{codigo}/detalle")
def venta_admin_ods_detalle(
    codigo: str,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return get_admin_ods_detail(db, codigo)


@router.post("/api/venta/admin-ods/estado")
def venta_admin_ods_estado(
    payload: VentaAdminEstadoRequest,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return update_admin_ods_estado(db, payload.codigo, payload.campo, payload.valor)


@router.get("/api/venta/finanzas-ods")
def venta_finanzas_ods_listar(
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return get_finanzas_ods_rows(db)


@router.get("/api/venta/finanzas-ods/{codigo}/detalle")
def venta_finanzas_ods_detalle(
    codigo: str,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return get_finanzas_ods_detail(db, codigo)


@router.post("/api/venta/finanzas-ods/estado")
def venta_finanzas_ods_estado(
    payload: VentaFinanzasEstadoRequest,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return update_finanzas_ods_estado(db, payload.codigo, payload.campo, payload.valor)


@router.get("/api/venta/servicio-tecnico-ods")
def venta_servicio_tecnico_ods_listar(
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return {"rows": get_servicio_tecnico_ventas_rows(db)}


@router.get("/api/venta/servicio-tecnico-ods/{codigo}/detalle")
def venta_servicio_tecnico_ods_detalle(
    codigo: str,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return get_servicio_tecnico_ventas_detail(db, codigo)


@router.get("/api/venta/servicio-tecnico-ods/contacto")
def venta_servicio_tecnico_ods_contacto(
    direccion: str = Query(default=""),
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return get_servicio_tecnico_ventas_contacto(db, direccion)


@router.post("/api/venta/servicio-tecnico-ods/estado")
def venta_servicio_tecnico_ods_estado(
    payload: VentaServicioTecnicoEstadoRequest,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return update_servicio_tecnico_ventas_estado(db, payload.codigo, payload.campo, payload.valor)


@router.post("/api/venta/servicio-tecnico-ods/valor")
def venta_servicio_tecnico_ods_valor(
    payload: VentaServicioTecnicoValorRequest,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return update_servicio_tecnico_ventas_valor(db, payload.codigo, payload.campo, payload.valor)


# â”€â”€â”€ Operaciones â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/venta/operaciones", response_class=HTMLResponse)
def venta_operaciones_page(
    request: Request,
    token: str = Query(default=""),
    service: IncidenciasService = Depends(get_service),
    db: Session = Depends(get_db),
):
    if not service.usuario_logueado_por_token(token):
        return RedirectResponse(url="/?form=login&next=auto", status_code=303)
    tecnico = service.get_usuario_actual(token)
    show_back_button, back_url = _back_context_for_token(db, token)
    return templates.TemplateResponse(
        request,
        "seleccion_panel_operaciones.html",
        {
            "request": request,
            "token": token,
            "tecnico": tecnico,
            "show_back_button": show_back_button,
            "back_url": back_url,
        },
    )


@router.get("/venta/operaciones/login", response_class=HTMLResponse)
def venta_operaciones_login_page(request: Request):
    return RedirectResponse(url="/?form=login&next=auto", status_code=303)


@router.get("/venta/operaciones/tabla", response_class=HTMLResponse)
def venta_tabla_operaciones_page(
    request: Request,
    token: str = Query(default=""),
    service: IncidenciasService = Depends(get_service),
):
    if not service.usuario_logueado_por_token(token):
        return RedirectResponse(url="/?form=login&next=auto", status_code=303)
    return templates.TemplateResponse(request, "tabla_operaciones_venta.html", {"request": request, "token": token})


@router.get("/api/venta/operaciones-ods")
def venta_operaciones_ods_listar(
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return get_operaciones_ods_rows(db)


@router.post("/api/venta/operaciones-ods/actualizar-estado")
def venta_operaciones_ods_estado(
    payload: VentaOperacionesEstadoRequest,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return update_operaciones_ods_estado(db, payload.codigo, payload.campo, payload.valor)


@router.post("/api/venta/operaciones-ods/actualizar-fecha")
def venta_operaciones_ods_fecha(
    payload: VentaOperacionesFechaRequest,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return update_operaciones_ods_fecha(db, payload.codigo, payload.fecha)


@router.post("/api/venta/operaciones-ods/notificar-inicio")
def venta_operaciones_ods_notificar(
    payload: VentaOperacionesFechaRequest,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return {"ok": True, "mensaje": "pendiente_configuracion"}


# â”€â”€â”€ Comercial (vista general) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/api/venta/comercial-todo")
def venta_comercial_todo(
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return get_comercial_todo(db)


@router.post("/api/venta/ods/anular")
def venta_ods_anular(
    payload: VentaAnularODSRequest,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return anular_ods_venta(db, payload.codigo)


@router.post("/api/venta/ods/subir-contrato")
def venta_ods_subir_contrato(
    payload: VentaContratoUploadRequest,
    db: Session = Depends(get_db),
    token: str = Depends(require_venta_token),
):
    return subir_contrato_venta(db, payload.codigo, payload.nombre, payload.data)


@router.get("/api/venta/ods/{codigo}/contrato/{nombre}")
def venta_ods_contrato_file(
    codigo: str,
    nombre: str,
    _: str = Depends(require_venta_token),
):
    ruta = VENTA_UPLOADS_DIR / codigo / "contrato" / nombre
    if not ruta.exists() or not ruta.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    return FileResponse(str(ruta), filename=nombre)
