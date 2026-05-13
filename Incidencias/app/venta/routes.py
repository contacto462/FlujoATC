from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import LoginRequest
from app.services import IncidenciasService
from app.venta.schemas import (
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
from app.venta.service import (
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


@router.get("/venta/sucursales", response_class=HTMLResponse)
def venta_sucursales_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse("RegistroSucursal.html", {"request": request, "token": token})


@router.get("/venta/ods", response_class=HTMLResponse)
def venta_ods_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse("RegistroODS.html", {"request": request, "token": token})


@router.get("/venta/bbdd-orden-servicio", response_class=HTMLResponse)
def venta_bbdd_orden_servicio_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse("BBDDOrdenServicio.html", {"request": request, "token": token})


@router.get("/venta/administracion", response_class=HTMLResponse)
def venta_administracion_page(
    request: Request,
    token: str = Query(default=""),
    service: IncidenciasService = Depends(get_service),
):
    if not service.usuario_logueado_por_token(token):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "title": "Administracion", "next_form": "panelSelectorAdministracion"},
        )
    return templates.TemplateResponse("panel_selector_administracion.html", {"request": request, "token": token})


@router.get("/venta/administracion/tabla", response_class=HTMLResponse)
def venta_tabla_administracion_page(
    request: Request,
    token: str = Query(default=""),
    service: IncidenciasService = Depends(get_service),
):
    if not service.usuario_logueado_por_token(token):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "title": "Administracion", "next_form": "panelSelectorAdministracion"},
        )
    return templates.TemplateResponse("TablaAdministracion.html", {"request": request, "token": token})


@router.get("/venta/administracion/login", response_class=HTMLResponse)
def venta_administracion_login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "title": "Administracion", "next_form": "panelSelectorAdministracion"},
    )


@router.get("/venta/finanzas", response_class=HTMLResponse)
def venta_finanzas_page(
    request: Request,
    token: str = Query(default=""),
    service: IncidenciasService = Depends(get_service),
):
    if not service.usuario_logueado_por_token(token):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "title": "Finanzas", "next_form": "panelSelectorFinanzas"},
        )
    tecnico = service.get_usuario_actual(token)
    return templates.TemplateResponse("panel_selector_finanzas.html", {"request": request, "token": token, "tecnico": tecnico})


@router.get("/venta/finanzas/tabla", response_class=HTMLResponse)
def venta_tabla_finanzas_page(
    request: Request,
    token: str = Query(default=""),
    service: IncidenciasService = Depends(get_service),
):
    if not service.usuario_logueado_por_token(token):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "title": "Finanzas", "next_form": "panelSelectorFinanzas"},
        )
    return templates.TemplateResponse("TablaFinanzas.html", {"request": request, "token": token})


@router.get("/venta/finanzas/login", response_class=HTMLResponse)
def venta_finanzas_login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "title": "Finanzas", "next_form": "panelSelectorFinanzas"},
    )


@router.get("/venta/login", response_class=HTMLResponse)
def venta_login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "title": "Venta", "next_form": "panelSelectorVenta"},
    )


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


@router.get("/venta/bbdd-sucursales", response_class=HTMLResponse)
def venta_bbdd_sucursales_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse("BBCCSucursal.html", {"request": request, "token": token})


@router.get("/venta/informacion-cliente", response_class=HTMLResponse)
def venta_informacion_cliente_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse("InformacionCliente.html", {"request": request, "token": token})


@router.get("/venta/tabla-comercial", response_class=HTMLResponse)
def venta_tabla_comercial_page(
    request: Request,
    token: str = Depends(require_venta_token),
):
    return templates.TemplateResponse("TablaComercial.html", {"request": request, "token": token})


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


# ─── Operaciones ─────────────────────────────────────────────────────────────

@router.get("/venta/operaciones", response_class=HTMLResponse)
def venta_operaciones_page(
    request: Request,
    token: str = Query(default=""),
    service: IncidenciasService = Depends(get_service),
):
    if not service.usuario_logueado_por_token(token):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "title": "Operaciones", "next_form": "panelSelectorOperaciones"},
        )
    tecnico = service.get_usuario_actual(token)
    return templates.TemplateResponse(
        "panel_selector_operaciones.html",
        {"request": request, "token": token, "tecnico": tecnico},
    )


@router.get("/venta/operaciones/login", response_class=HTMLResponse)
def venta_operaciones_login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "title": "Operaciones", "next_form": "panelSelectorOperaciones"},
    )


@router.get("/venta/operaciones/tabla", response_class=HTMLResponse)
def venta_tabla_operaciones_page(
    request: Request,
    token: str = Query(default=""),
    service: IncidenciasService = Depends(get_service),
):
    if not service.usuario_logueado_por_token(token):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "title": "Operaciones", "next_form": "panelSelectorOperaciones"},
        )
    return templates.TemplateResponse("TablaOperaciones.html", {"request": request, "token": token})


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


# ─── Comercial (vista general) ───────────────────────────────────────────────

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
