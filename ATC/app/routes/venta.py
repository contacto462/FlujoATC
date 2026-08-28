from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ATC.app.core.incidencias_db import get_db
from ATC.app.models.incidencias import SucursalBBDD
from ATC.app.schemas.venta import (
    VentaAdminEstadoRequest,
    VentaAnularODSRequest,
    VentaClienteCreateRequest,
    VentaClienteCreateResponse,
    VentaClienteTableUpdateRequest,
    VentaClienteTelefonoUpdateRequest,
    VentaContratoUploadRequest,
    VentaFinanzasEstadoRequest,
    VentaODSCreateRequest,
    VentaODSCreateResponse,
    VentaODSUpdateRequest,
    VentaOperacionesEstadoRequest,
    VentaOperacionesFechaRequest,
    VentaPersonaCampoUpdateRequest,
    VentaPersonaRegistroRequest,
    VentaServicioTecnicoEstadoRequest,
    VentaServicioTecnicoLayoutFinalRequest,
    VentaServicioTecnicoValorRequest,
    VentaSucursalCreateRequest,
    VentaSucursalCreateResponse,
    VentaSucursalTableUpdateRequest,
)
from ATC.app.services.incidencias_service import IncidenciasService
from ATC.app.services.venta_service import (
    _normalize_text,
    add_persona_registro,
    anular_ods_venta,
    avisar_sucursal_lista_bitacora,
    create_cliente,
    create_ods,
    create_sucursal,
    fetch_comunas,
    fetch_regiones,
    get_admin_ods_detail,
    get_admin_ods_rows,
    get_cliente_nombre_by_rut,
    get_clientes_informacion_list,
    get_cliente_resumen_by_rut,
    get_cliente_sucursal_resumen,
    get_clientes_table,
    get_comercial_todo,
    get_coordinates_for_address,
    get_finanzas_ods_detail,
    get_finanzas_ods_facturacion,
    get_finanzas_ods_pendiente_count,
    get_finanzas_ods_rows,
    get_ods_codes,
    get_ods_data_by_rut,
    get_ods_detail,
    get_operaciones_ods_rows,
    get_proveedores_electricidad,
    get_proveedores_internet,
    get_servicio_tecnico_ventas_contacto,
    get_servicio_tecnico_ventas_detail,
    get_servicio_tecnico_ventas_rows,
    get_sucursal_revision_bitacora,
    get_sucursales_pendientes_bitacora_comercial,
    get_sucursales_table,
    resolve_ods_archivo_path,
    rut_exists,
    subir_contrato_venta,
    update_admin_ods_estado,
    update_cliente_row,
    update_cliente_telefono,
    update_finanzas_ods_estado,
    update_ods,
    update_operaciones_ods_estado,
    update_operaciones_ods_fecha,
    update_persona_campo,
    update_servicio_tecnico_layout_final,
    update_servicio_tecnico_ventas_estado,
    update_servicio_tecnico_ventas_valor,
    update_sucursal_row,
    delete_sucursal_row,
    upsert_sucursal_info_extra,
)
from ATC.app.services.suscripciones_service import (
    asignar_sucursal_suscripcion,
    obtener_cache_piriod,
    obtener_camaras_monitoreadas_por_sucursal,
    obtener_suscripciones,
    suscripciones_piriod_crudo,
)


router = APIRouter(tags=["venta"])
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def get_service(db: Annotated[Session, Depends(get_db)]) -> IncidenciasService:
    return IncidenciasService(db)


def _login_redirect(next_form: str) -> RedirectResponse:
    return RedirectResponse(url=f"/?form=login&next={next_form}", status_code=303)


def _require_token(service: IncidenciasService, token: str, next_form: str = "panelSelectorVenta") -> None:
    if not service.usuario_logueado_por_token(str(token or "").strip()):
        raise HTTPException(status_code=401, detail="No autenticado.")


def _guard_page(
    request: Request,
    db: Session,
    service: IncidenciasService,
    token: str,
    next_form: str = "panelSelectorVenta",
) -> RedirectResponse | None:
    """Para paginas HTML (no API): en vez de un 401, redirige a login si no
    hay sesion activa. Revisa el token primero (navegacion con ?token=...): si
    es valido, lo consume — crea la cookie de sesion web y redirige a la misma
    URL sin el token en la barra de direcciones (mismo patron que
    compras.py._consume_session_token), para no dejar el token de sesion
    expuesto. Si no hay token valido cae a la cookie de sesion (navegacion
    interna sin token, ej. un boton "Volver" a una pagina que no propaga el
    query param)."""
    from ATC.app.core.config import settings as _settings
    from ATC.app.core.security import create_access_token as _create_access_token
    from ATC.app.core.session_policy import max_age_cookie_segundos as _max_age_cookie_segundos
    from ATC.app.models.incidencias import LoginSession as _LoginSession
    from ATC.app.models.user import User as _User
    from ATC.app.routes.web import COOKIE_NAME as _COOKIE_NAME, _decode_cookie_token as _decode_cookie_token_web
    from ATC.app.services.user_service import UserService as _UserService
    from datetime import datetime as _dt, timezone as _timezone

    token_limpio = str(token or "").strip()
    if token_limpio:
        row = (
            db.query(_User.username, _User.id)
            .join(_LoginSession, _User.id == _LoginSession.user_id)
            .filter(
                _LoginSession.token == token_limpio,
                _LoginSession.expires_at > _dt.now(_timezone.utc),
                _User.is_active == 1,
            )
            .first()
        )
        if row:
            web_token = _create_access_token({"sub": row[0]})
            clean_url = str(request.url.remove_query_params(["token"]))
            response = RedirectResponse(url=clean_url, status_code=303)
            response.set_cookie(
                key=_COOKIE_NAME,
                value=web_token,
                httponly=True,
                samesite="lax",
                secure=False,
                max_age=_max_age_cookie_segundos(row[1], _settings.JWT_EXPIRES_MIN * 60),
            )
            return response
    try:
        cookie = request.cookies.get(_COOKIE_NAME, "")
        if cookie:
            login = _decode_cookie_token_web(cookie)
            user = _UserService.find_by_login(db, login)
            if user and user.is_active:
                return None
    except Exception:
        pass
    return _login_redirect(next_form)


def _require_login(request: Request, db: Session = Depends(get_db)):
    """Dependencia para endpoints /api/venta/* y /api/prevencion/* que leen o
    modifican datos reales (clientes, sucursales, ODS, finanzas, prevencion):
    exige la misma cookie de sesion que ya validan las paginas HTML de este
    router via _guard_page, pero devolviendo 401 en vez de redirigir. Antes
    estos endpoints no verificaban ninguna sesion — cualquiera con la URL
    podia leer/escribir estos datos sin login (hallazgo de auditoria de
    seguridad, ago 2026)."""
    from ATC.app.routes.web import COOKIE_NAME as _COOKIE_NAME, _decode_cookie_token as _decode_cookie_token_web
    from ATC.app.services.user_service import UserService as _UserService

    cookie = request.cookies.get(_COOKIE_NAME, "")
    if cookie:
        try:
            login = _decode_cookie_token_web(cookie)
            user = _UserService.find_by_login(db, login)
            if user and user.is_active:
                return user
        except Exception:
            pass
    raise HTTPException(status_code=401, detail="No autenticado.")


def _usuario_actual(service: IncidenciasService, token: str, request: Request | None = None) -> str:
    usuario = service.get_usuario_actual(str(token or "").strip())
    if (not usuario or usuario == "Desconocido") and request is not None:
        # Sin token valido (o vacio, ej. tras _guard_page limpiar la URL): cae
        # a la cookie de sesion web antes de dar por "Desconocido" al usuario.
        try:
            from ATC.app.routes.web import COOKIE_NAME as _COOKIE_NAME, _decode_cookie_token as _decode_cookie_token_web
            from ATC.app.services.user_service import UserService as _UserService

            cookie = request.cookies.get(_COOKIE_NAME, "")
            if cookie:
                login = _decode_cookie_token_web(cookie)
                user = _UserService.find_by_login(service.db, login)
                if user and user.is_active:
                    return user.name or user.username
        except Exception:
            pass
    return "" if not usuario or usuario == "Desconocido" else usuario


def _back_button_ctx(token: str) -> dict[str, object]:
    """Volver en los seleccion_panel_*: siempre para superadmin (navega entre
    todas las areas) y para cualquier usuario con mas de un area. La excepcion
    es gerencia, que no se renderiza por aqui (su boton es cerrar sesion)."""
    if not token:
        return {}
    try:
        from datetime import datetime as _dt

        from ATC.app.core.db import SessionLocal as _SessionLocal
        from ATC.app.models.incidencias import LoginSession as _LoginSession
        from ATC.app.models.user import User as _UserModel
        from ATC.app.services.incidencias_service import IncidenciasService as _Svc

        db = _SessionLocal()
        try:
            sesion = db.query(_LoginSession).filter(_LoginSession.token == token).first()
            if not sesion or not sesion.user_id or sesion.expires_at <= _dt.utcnow():
                return {}
            user = db.get(_UserModel, int(sesion.user_id))
            if not user or not user.is_active:
                return {}
            es_superadmin = str(user.role or "").strip().lower() == "superadmin"
            if not es_superadmin and _Svc(db).contar_areas_para_token(token) <= 1:
                return {}
        finally:
            db.close()
    except Exception:
        return {}
    return {
        "show_back_button": True,
        "back_url": f"/seleccionar-area?token={token}",
    }


def _template(request: Request, template_name: str, token: str = "", **extra) -> HTMLResponse:
    context = {"request": request, "token": token or "", **extra}
    if template_name.startswith("seleccion_panel_") and "show_back_button" not in context:
        context.update(_back_button_ctx(token or ""))
    return templates.TemplateResponse(request, template_name, context)


@router.get("/venta/login", response_class=HTMLResponse)
def venta_login_page(next_form: str = Query(default="panelSelectorVenta", alias="next")):
    return _login_redirect(next_form)


@router.get("/venta/administracion/login", response_class=HTMLResponse)
def venta_administracion_login_page():
    return _login_redirect("panelSelectorAdministracion")


@router.get("/venta/finanzas/login", response_class=HTMLResponse)
def venta_finanzas_login_page():
    return _login_redirect("panelSelectorFinanzas")


@router.get("/venta/operaciones/login", response_class=HTMLResponse)
def venta_operaciones_login_page():
    return _login_redirect("panelSelectorOperaciones")


@router.get("/venta/panel-selector", response_class=HTMLResponse)
def venta_panel_selector_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form="panelSelectorVenta")
    if guard:
        return guard
    return _template(request, "seleccion_panel_venta.html", token)


@router.get("/venta/clientes", response_class=HTMLResponse)
def venta_clientes_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorVenta')
    if guard:
        return guard
    return _template(request, "registro_cliente.html", token)


@router.get("/venta/sucursales", response_class=HTMLResponse)
def venta_sucursales_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorVenta')
    if guard:
        return guard
    return _template(request, "registro_sucursal.html", token)


@router.get("/venta/ods", response_class=HTMLResponse)
def venta_ods_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorVenta')
    if guard:
        return guard
    return _template(request, "registro_ods.html", token)


@router.get("/venta/bbdd-clientes", response_class=HTMLResponse)
def venta_bbdd_clientes_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorVenta')
    if guard:
        return guard
    return _template(request, "bbdd_clientes.html", token)


@router.get("/venta/bbdd-sucursales", response_class=HTMLResponse)
def venta_bbdd_sucursales_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorVenta')
    if guard:
        return guard
    return _template(request, "bbdd_sucursal.html", token)


@router.get("/venta/bbdd-orden-servicio", response_class=HTMLResponse)
def venta_bbdd_ods_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorVenta')
    if guard:
        return guard
    return _template(request, "bbdd_orden_servicio.html", token)


@router.get("/venta/informacion-cliente", response_class=HTMLResponse)
def venta_informacion_cliente_page(
    request: Request,
    token: str = Query(default=""),
    next_form: str = Query(default="panelSelectorVenta", alias="next"),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    if next_form not in {"panelSelectorVenta", "panelSelectorAdministracion"}:
        next_form = "panelSelectorVenta"
    guard = _guard_page(request, db, service, token, next_form=next_form)
    if guard:
        return guard
    back_url = (
        f"/venta/administracion?token={token or ''}"
        if next_form == "panelSelectorAdministracion"
        else f"/venta/panel-selector?token={token or ''}"
    )
    return _template(request, "informacion_cliente.html", token, back_url=back_url)


@router.get("/venta/tabla-comercial", response_class=HTMLResponse)
def venta_tabla_comercial_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorVenta')
    if guard:
        return guard
    return _template(request, "tabla_comercial_venta.html", token)


@router.get("/venta/administracion", response_class=HTMLResponse)
def venta_administracion_panel_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form="panelSelectorAdministracion")
    if guard:
        return guard
    filas_admin = get_admin_ods_rows(db)
    pendiente_administracion = sum(
        1
        for fila in filas_admin
        if not fila.get("anulada") and not fila.get("estados", {}).get("finalizado")
    )
    return _template(
        request,
        "seleccion_panel_administracion.html",
        token,
        pendiente_administracion=pendiente_administracion,
    )


@router.get("/venta/administracion/tabla", response_class=HTMLResponse)
def venta_tabla_administracion_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorAdministracion')
    if guard:
        return guard
    return _template(request, "tabla_administracion_venta.html", token)


@router.get("/venta/finanzas", response_class=HTMLResponse)
def venta_finanzas_panel_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    from ATC.app.models.compras import SolicitudCompra
    from ATC.app.models.incidencias import Rendicion

    guard = _guard_page(request, db, service, token, next_form="panelSelectorFinanzas")
    if guard:
        return guard
    pendiente_finanzas_tabla = get_finanzas_ods_pendiente_count(db)
    # A diferencia de "Aprobar Rendiciones" (Servicio Tecnico, que cuenta las que
    # esperan revision), Finanzas paga rendiciones ya aceptadas: el badge cuenta
    # "Por pagar" (estado_revision = Aceptada), no las pendientes de aprobacion.
    pendiente_rendiciones_finanzas = sum(
        1
        for (estado_rev,) in db.query(Rendicion.estado_revision).all()
        if "acept" in _normalize_text(estado_rev)
    )
    pendiente_compras_control = (
        db.query(SolicitudCompra.id)
        .filter(SolicitudCompra.estado.in_(["Pendiente", "En proceso"]))
        .count()
    )
    return _template(
        request,
        "seleccion_panel_finanzas.html",
        token,
        pendiente_finanzas_tabla=pendiente_finanzas_tabla,
        pendiente_rendiciones_finanzas=pendiente_rendiciones_finanzas,
        pendiente_compras_control=pendiente_compras_control,
    )


@router.get("/venta/finanzas/tabla", response_class=HTMLResponse)
def venta_tabla_finanzas_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorFinanzas')
    if guard:
        return guard
    return _template(request, "tabla_finanzas_venta.html", token)


@router.get("/venta/finanzas/rendiciones", response_class=HTMLResponse)
def venta_finanzas_rendiciones_page(
    request: Request,
    token: str = Query(default=""),
    from_: str = Query(default="", alias="from"),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form="panelSelectorFinanzas")
    if guard:
        return guard
    return _template(request, "finanzas_rendiciones.html", token, from_area=from_)


@router.get("/venta/finanzas/rendiciones/hoja", response_class=HTMLResponse)
def venta_finanzas_rendiciones_hoja_page(
    request: Request,
    token: str = Query(default=""),
    from_: str = Query(default="", alias="from"),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorFinanzas')
    if guard:
        return guard
    back_url = (
        f"/?form=panelSelectorServicio&token={token}"
        if from_ == "servicio"
        else f"/venta/finanzas?token={token}"
    )
    return _template(request, "finanzas_libro.html", token, back_url=back_url, origen=from_)


@router.get("/venta/finanzas/consolidado", response_class=HTMLResponse)
def venta_finanzas_consolidado_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorFinanzas')
    if guard:
        return guard
    return _template(request, "finanzas_consolidado.html", token)


@router.get("/venta/finanzas/suscripciones", response_class=HTMLResponse)
def venta_finanzas_suscripciones_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorFinanzas')
    if guard:
        return guard
    return _template(request, "finanzas_suscripciones.html", token)


@router.get("/venta/finanzas/viatico-especial", response_class=HTMLResponse)
def venta_finanzas_viatico_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorFinanzas')
    if guard:
        return guard
    return _template(request, "finanzas_viatico_especial.html", token)


@router.get("/venta/finanzas/pagos", response_class=HTMLResponse)
def venta_finanzas_pagos_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorFinanzas')
    if guard:
        return guard
    return _template(request, "finanzas_pagos.html", token)


@router.get("/venta/finanzas/suma-pagos", response_class=HTMLResponse)
def venta_finanzas_suma_pagos_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorFinanzas')
    if guard:
        return guard
    return _template(request, "finanzas_suma_pagos.html", token)


@router.get("/venta/finanzas/pagos-atc", response_class=HTMLResponse)
def venta_finanzas_pagos_atc_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorFinanzas')
    if guard:
        return guard
    return _template(
        request, "finanzas_pagos_grupo.html", token,
        tipo="atc", titulo="Pagos ATC",
        subtitulo="Pagos pendientes que NO son Materiales/Combustible, agrupados por RUT.",
    )


@router.get("/venta/finanzas/pagos-vl", response_class=HTMLResponse)
def venta_finanzas_pagos_vl_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorFinanzas')
    if guard:
        return guard
    return _template(
        request, "finanzas_pagos_grupo.html", token,
        tipo="vl", titulo="Pagos VL",
        subtitulo="Pagos pendientes de Materiales/Combustible, agrupados por RUT.",
    )


@router.get("/venta/operaciones", response_class=HTMLResponse)
def venta_operaciones_panel_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    from ATC.app.models.compras import SolicitudCompra

    guard = _guard_page(request, db, service, token, next_form="panelSelectorOperaciones")
    if guard:
        return guard
    filas_operaciones = get_operaciones_ods_rows(db).get("rows", [])
    # Operaciones recien tiene trabajo accionable cuando Soporte ya termino su
    # parte (terminadoSoporte) — antes de eso no hay nada pendiente real para
    # ellos, aunque su propio flag "terminado" siga en False.
    pendiente_operaciones_tabla = sum(
        1
        for fila in filas_operaciones
        if not fila.get("anulada")
        and fila.get("terminadoSoporte")
        and not fila.get("estados", {}).get("terminado")
    )
    pendiente_revision_operaciones = (
        db.query(SolicitudCompra.id)
        .filter(SolicitudCompra.estado == "Pendiente Operaciones")
        .count()
    )
    return _template(
        request,
        "seleccion_panel_operaciones.html",
        token,
        pendiente_operaciones_tabla=pendiente_operaciones_tabla,
        pendiente_revision_operaciones=pendiente_revision_operaciones,
    )


@router.get("/venta/operaciones/dashboard-coordinacion", response_class=HTMLResponse)
def venta_dashboard_coordinacion_page(
    request: Request,
    token: str = Query(default=""),
    desde: str = Query(default=""),
    hasta: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form="panelSelectorOperaciones")
    if guard:
        return guard
    from datetime import date as _date
    fecha_desde = None
    fecha_hasta = None
    try:
        if desde:
            fecha_desde = _date.fromisoformat(desde)
        if hasta:
            fecha_hasta = _date.fromisoformat(hasta)
    except ValueError:
        fecha_desde = fecha_hasta = None
    datos = service.obtener_dashboard_coordinacion(desde=fecha_desde, hasta=fecha_hasta)
    return _template(
        request,
        "dashboard_coordinacion.html",
        token,
        filtro_desde=desde,
        filtro_hasta=hasta,
        filtro_desde_fmt=fecha_desde.strftime("%d/%m/%Y") if fecha_desde else "",
        filtro_hasta_fmt=fecha_hasta.strftime("%d/%m/%Y") if fecha_hasta else "",
        **datos,
    )


@router.get("/venta/operaciones/dashboard-coordinacion/informe")
def venta_dashboard_coordinacion_informe(
    request: Request,
    token: str = Query(default=""),
    desde: str = Query(default=""),
    hasta: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    import io
    from datetime import date as _date
    from datetime import datetime as _dt

    from fastapi.responses import StreamingResponse

    guard = _guard_page(request, db, service, token, next_form="panelSelectorOperaciones")
    if guard:
        return guard

    fecha_desde = fecha_hasta = None
    try:
        if desde:
            fecha_desde = _date.fromisoformat(desde)
        if hasta:
            fecha_hasta = _date.fromisoformat(hasta)
    except ValueError:
        fecha_desde = fecha_hasta = None

    pdf_bytes = service.generar_informe_coordinacion_pdf(desde=fecha_desde, hasta=fecha_hasta)
    sufijo = f"_{desde}_a_{hasta}" if (desde and hasta) else ""
    nombre = f"Informe_Coordinacion_Cliente{sufijo}_{_dt.now().strftime('%Y%m%d_%H%M')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{nombre}"'},
    )


@router.get("/api/venta/operaciones/detalle-informes-semanales")
def venta_detalle_informes_semanales(
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    if token and not service.usuario_logueado_por_token(token):
        raise HTTPException(status_code=401, detail="Sesión inválida.")
    return service.obtener_detalle_informes_semanales()


@router.get("/venta/operaciones/tabla", response_class=HTMLResponse)
def venta_tabla_operaciones_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorOperaciones')
    if guard:
        return guard
    return _template(request, "tabla_operaciones_venta.html", token)


@router.get("/api/venta/usuario-actual")
def venta_usuario_actual(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
current_user=Depends(_require_login)):
    usuario = _usuario_actual(service, token, request)
    return {"name": usuario, "username": usuario, "usuario": usuario}


@router.get("/api/venta/catalogo/regiones")
def venta_catalogo_regiones(current_user=Depends(_require_login)):
    return {"regiones": fetch_regiones()}


@router.get("/api/venta/catalogo/comunas")
def venta_catalogo_comunas(region: str = Query(default=""), current_user=Depends(_require_login)):
    return {"comunas": fetch_comunas(region)}


@router.get("/api/venta/proveedores/internet")
def venta_proveedores_internet(current_user=Depends(_require_login)):
    return {"proveedores": get_proveedores_internet()}


@router.get("/api/venta/proveedores/electricidad")
def venta_proveedores_electricidad(current_user=Depends(_require_login)):
    return {"proveedores": get_proveedores_electricidad()}


@router.get("/api/venta/coordenadas")
def venta_coordenadas(direccion: str = Query(default=""), comuna: str = Query(default=""), db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return get_coordinates_for_address(db, direccion, comuna)


@router.get("/api/venta/clientes/verificar-rut")
def venta_clientes_verificar_rut(rut: str = Query(default=""), db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return {"exists": rut_exists(db, rut), "existe": rut_exists(db, rut)}


@router.get("/api/venta/clientes/buscar-por-rut")
def venta_clientes_buscar_por_rut(rut: str = Query(default=""), db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return {"nombre": get_cliente_nombre_by_rut(db, rut)}


@router.post("/api/venta/clientes", response_model=VentaClienteCreateResponse)
def venta_clientes_crear(
    request: Request,
    payload: VentaClienteCreateRequest,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
current_user=Depends(_require_login)):
    usuario = _usuario_actual(IncidenciasService(db), token, request)
    cliente = create_cliente(db, payload, usuario)
    return {"ok": True, "cliente_id": cliente.id, "message": "Cliente creado correctamente."}


@router.post("/api/venta/sucursales", response_model=VentaSucursalCreateResponse)
def venta_sucursales_crear(
    request: Request,
    payload: VentaSucursalCreateRequest,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
current_user=Depends(_require_login)):
    usuario = _usuario_actual(IncidenciasService(db), token, request)
    sucursal = create_sucursal(db, payload, usuario)
    return {"ok": True, "sucursal_id": sucursal.id, "message": "Sucursal creada correctamente."}


@router.get("/api/venta/ods/datos-por-rut")
def venta_ods_datos_por_rut(rut: str = Query(default=""), db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return get_ods_data_by_rut(db, rut)


@router.post("/api/venta/ods", response_model=VentaODSCreateResponse)
def venta_ods_crear(
    request: Request,
    payload: VentaODSCreateRequest,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
current_user=Depends(_require_login)):
    usuario = _usuario_actual(IncidenciasService(db), token, request)
    ods = create_ods(db, payload, usuario)
    return {"ok": True, "ods_id": ods.id, "codigo": ods.codigo, "message": "ODS creada correctamente."}


@router.get("/api/venta/ods/lista")
def venta_ods_lista(db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return {"ods": get_ods_codes(db), "items": get_ods_codes(db)}


@router.get("/api/venta/ods/detalle")
def venta_ods_detalle(codigo: str = Query(default=""), db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return get_ods_detail(db, codigo)


@router.post("/api/venta/ods/guardar")
def venta_ods_guardar(
    request: Request,
    payload: VentaODSUpdateRequest,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
current_user=Depends(_require_login)):
    usuario = _usuario_actual(IncidenciasService(db), token, request)
    ods = update_ods(db, payload, usuario)
    return {"ok": True, "codigo": ods.codigo}


@router.get("/api/venta/clientes/tabla")
def venta_clientes_tabla(db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return get_clientes_table(db)


@router.post("/api/venta/clientes/tabla/guardar-fila")
def venta_clientes_tabla_guardar(payload: VentaClienteTableUpdateRequest, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    update_cliente_row(db, payload.row_id, payload.values)
    return {"ok": True}


@router.post("/api/venta/clientes/telefono")
def venta_cliente_telefono_guardar(payload: VentaClienteTelefonoUpdateRequest, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    """Edición inline del teléfono del cliente desde Información Clientes (campo
    "Contacto" que marca Bitácora — vive en bbdd_clientes, no en la sucursal)."""
    update_cliente_telefono(db, payload.rut, payload.telefono)
    return {"ok": True}


@router.get("/api/venta/sucursales/tabla")
def venta_sucursales_tabla(db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return get_sucursales_table(db)


@router.get("/api/venta/finanzas/suscripciones")
def venta_finanzas_suscripciones_api(db: Session = Depends(get_db), current_user=Depends(_require_login)):
    """Datos para la hoja 'Registro de Suscripción': las filas son las de
    la tabla `suscripcion` (importada del CSV una vez — ver
    ATC/scripts/_importar_suscripciones_csv.py), no todas las sucursales de
    ATC. "Cantidad Cámaras" se reemplaza por el dato de bbdd_sucursales
    (Bitácora > Información Puestos) solo cuando Servicio es
    "Televigilancia" (ver suscripciones_service.obtener_suscripciones)."""
    camaras_por_sucursal = obtener_camaras_monitoreadas_por_sucursal(db)
    piriod_actualizado_en = obtener_cache_piriod()["actualizado_en"]
    return {
        "rows": obtener_suscripciones(db, camaras_por_sucursal),
        "piriod_actualizado_en": piriod_actualizado_en.isoformat() if piriod_actualizado_en else None,
    }


@router.get("/api/venta/finanzas/suscripciones/piriod-crudo")
def venta_finanzas_suscripciones_piriod_crudo_api(
    db: Session = Depends(get_db), current_user=Depends(_require_login)
):
    """Todas las suscripciones que hay en Piriod, sin curar — ver
    suscripciones_service.suscripciones_piriod_crudo."""
    piriod_actualizado_en = obtener_cache_piriod()["actualizado_en"]
    return {
        "rows": suscripciones_piriod_crudo(db),
        "piriod_actualizado_en": piriod_actualizado_en.isoformat() if piriod_actualizado_en else None,
    }


@router.get("/api/venta/sucursales/opciones")
def venta_sucursales_opciones_api(db: Session = Depends(get_db), current_user=Depends(_require_login)):
    """Lista liviana de sucursales (id, nombre, empresa, rut) para el
    selector de 'Registro de Suscripción' — a diferencia de /tabla, no trae
    las ~17 columnas completas de cada sucursal."""
    tabla = get_sucursales_table(db)
    opciones = [
        {
            "id": row[0] if len(row) > 0 else None,
            "rut": row[1] if len(row) > 1 else "",
            "nombre_empresa": row[2] if len(row) > 2 else "",
            "nombre_sucursal": row[3] if len(row) > 3 else "",
            "direccion": row[4] if len(row) > 4 else "",
        }
        for row in tabla.get("rows", [])
    ]
    return {"opciones": opciones}


class AsignarSucursalSuscripcionRequest(BaseModel):
    codigo: str
    sucursal_id: int


@router.post("/api/venta/finanzas/suscripciones/asignar-sucursal")
def venta_finanzas_suscripciones_asignar_sucursal(
    payload: AsignarSucursalSuscripcionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(_require_login),
):
    """Asigna a mano la sucursal de ATC que corresponde a una fila de
    `suscripcion` (identificada por su Código), para cuando el cruce
    automático de la importación no la encontró o la encontró equivocada."""
    sucursal = db.query(SucursalBBDD).filter(SucursalBBDD.id == payload.sucursal_id).first()
    if not sucursal:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")
    try:
        asignar_sucursal_suscripcion(
            db,
            codigo=payload.codigo,
            sucursal_id=payload.sucursal_id,
            nombre_sucursal=sucursal.nombre_sucursal or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    camaras_por_sucursal = obtener_camaras_monitoreadas_por_sucursal(db)
    cantidad_camaras = camaras_por_sucursal.get(payload.sucursal_id)
    return {
        "ok": True,
        "nombre_sucursal": sucursal.nombre_sucursal or "",
        "cantidad_camaras": cantidad_camaras if cantidad_camaras is not None else "",
        "camaras_sin_bbdd": cantidad_camaras is None,
    }


@router.post("/api/venta/sucursales/tabla/guardar-fila")
def venta_sucursales_tabla_guardar(payload: VentaSucursalTableUpdateRequest, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    update_sucursal_row(db, payload.row_id, payload.values)
    return {"ok": True}


@router.delete("/api/venta/sucursales/{sucursal_id}")
def venta_sucursal_eliminar(sucursal_id: int, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    delete_sucursal_row(db, sucursal_id)
    return {"ok": True}


@router.get("/api/venta/sucursales/{sucursal_id}/revision-bitacora")
def venta_sucursal_revision_bitacora(sucursal_id: int, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    """Qué campos marcó Bitácora como falta/mal la última vez que notificó sobre
    esta sucursal, y si sigue pendiente de aceptación — para que BBDD Sucursales /
    Información Clientes resalten esos campos y muestren el botón "Avisar que está
    listo" solo cuando corresponde."""
    return get_sucursal_revision_bitacora(db, sucursal_id)


@router.get("/api/venta/sucursales/mis-pendientes-bitacora")
def venta_sucursales_mis_pendientes_bitacora(
    todos: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user=Depends(_require_login),
):
    """Banner de Tabla Sucursal: sucursales que Bitácora marcó como "falta o está
    mal". Por defecto devuelve solo las del comercial logueado; con todos=1
    devuelve el total pendiente para alimentar badges de panel."""
    nombre = str(getattr(current_user, "name", "") or getattr(current_user, "username", "") or "").strip()
    return get_sucursales_pendientes_bitacora_comercial(db, nombre, todos=todos)


class SucursalAvisarListoRequest(BaseModel):
    mensaje: str = ""
    campos: list[str] = []


@router.post("/api/venta/sucursales/{sucursal_id}/avisar-listo")
def venta_sucursal_avisar_listo(
    sucursal_id: int,
    payload: SucursalAvisarListoRequest,
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
current_user=Depends(_require_login)):
    """Comercial avisa a Bitácora que ya corrigió lo que le habían marcado como
    pendiente/incompleto en una sucursal (botón en BBDD Sucursales e Información
    Clientes). payload.campos es lo que Comercial tildó como corregido en el
    popup — puede ser solo una parte de lo marcado; el resto queda pendiente."""
    usuario = _usuario_actual(IncidenciasService(db), token, request)
    resultado = avisar_sucursal_lista_bitacora(db, sucursal_id, usuario, payload.mensaje, payload.campos)
    if not resultado.get("email_sent"):
        raise HTTPException(status_code=400, detail=resultado.get("email_error") or "No se pudo enviar el aviso.")
    return {"ok": True, "enviado_a": resultado.get("email_to") or []}


@router.get("/api/venta/clientes/resumen")
def venta_cliente_resumen(rut: str = Query(default=""), db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return get_cliente_resumen_by_rut(db, rut)


@router.get("/api/venta/clientes/buscar")
def venta_clientes_buscar(q: str = Query(default=""), db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return get_clientes_informacion_list(db, q)


@router.get("/api/venta/clientes/resumen-sucursal")
def venta_cliente_sucursal_resumen(
    rut: str = Query(default=""),
    sucursal_id: int = Query(default=0),
    db: Session = Depends(get_db),
current_user=Depends(_require_login)):
    return get_cliente_sucursal_resumen(db, rut, sucursal_id)


@router.post("/api/venta/clientes/persona")
def venta_cliente_persona(payload: VentaPersonaRegistroRequest, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    add_persona_registro(db, payload)
    return {"ok": True}


@router.post("/api/venta/clientes/persona/editar")
def venta_cliente_persona_editar(payload: VentaPersonaCampoUpdateRequest, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    update_persona_campo(db, payload)
    return {"ok": True}


@router.post("/api/venta/clientes/sucursal-info-extra")
def venta_sucursal_info_extra(payload: dict, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    sucursal_id = int(payload.get("sucursalId") or payload.get("sucursal_id") or 0)
    campo = str(payload.get("campo") or "").strip()
    valor = str(payload.get("valor") or payload.get("nuevoValor") or "")
    upsert_sucursal_info_extra(db, sucursal_id, campo, valor)
    return {"ok": True}


@router.get("/api/venta/comercial-todo")
def venta_comercial_todo(db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return get_comercial_todo(db)


@router.post("/api/venta/ods/anular")
def venta_ods_anular(payload: VentaAnularODSRequest, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return anular_ods_venta(db, payload.codigo)


@router.post("/api/venta/ods/subir-contrato")
def venta_ods_subir_contrato(payload: VentaContratoUploadRequest, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return subir_contrato_venta(db, payload.codigo, payload.nombre, payload.data)


@router.get("/api/venta/ods/archivo/{archivo_id}")
def venta_ods_archivo(archivo_id: int, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    path, filename = resolve_ods_archivo_path(db, archivo_id)
    return FileResponse(path, filename=filename)


@router.get("/api/venta/admin-ods")
def venta_admin_ods(db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return {"rows": get_admin_ods_rows(db)}


@router.get("/api/venta/admin-ods/{codigo}/detalle")
def venta_admin_ods_detalle(codigo: str, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return get_admin_ods_detail(db, codigo)


@router.post("/api/venta/admin-ods/estado")
def venta_admin_ods_estado(payload: VentaAdminEstadoRequest, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return update_admin_ods_estado(db, payload.codigo, payload.campo, payload.valor)


@router.get("/api/venta/finanzas-ods")
def venta_finanzas_ods(db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return get_finanzas_ods_rows(db)


@router.get("/api/venta/finanzas-ods/{codigo}/detalle")
def venta_finanzas_ods_detalle(codigo: str, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return get_finanzas_ods_detail(db, codigo)


@router.get("/api/venta/finanzas-ods/{codigo}/facturacion")
def venta_finanzas_ods_facturacion(codigo: str, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return get_finanzas_ods_facturacion(db, codigo)


@router.post("/api/venta/finanzas-ods/estado")
def venta_finanzas_ods_estado(payload: VentaFinanzasEstadoRequest, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return update_finanzas_ods_estado(db, payload.codigo, payload.campo, payload.valor)


@router.get("/api/venta/operaciones-ods")
def venta_operaciones_ods(db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return get_operaciones_ods_rows(db)


@router.post("/api/venta/operaciones-ods/actualizar-estado")
def venta_operaciones_ods_estado(payload: VentaOperacionesEstadoRequest, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return update_operaciones_ods_estado(db, payload.codigo, payload.campo, payload.valor)


@router.post("/api/venta/operaciones-ods/actualizar-fecha")
def venta_operaciones_ods_fecha(payload: VentaOperacionesFechaRequest, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return update_operaciones_ods_fecha(db, payload.codigo, payload.fecha)


@router.post("/api/venta/operaciones-ods/notificar-inicio")
def venta_operaciones_ods_notificar(payload: VentaOperacionesFechaRequest, current_user=Depends(_require_login)):
    return {"ok": True, "notificacion_gestionada_al_actualizar_fecha": True}


@router.get("/api/venta/servicio-tecnico-ods")
def venta_servicio_tecnico_ods(db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return {"rows": get_servicio_tecnico_ventas_rows(db)}


@router.get("/api/venta/servicio-tecnico-ods/{codigo}/detalle")
def venta_servicio_tecnico_ods_detalle(codigo: str, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return get_servicio_tecnico_ventas_detail(db, codigo)


@router.get("/api/venta/servicio-tecnico-ods/contacto")
def venta_servicio_tecnico_ods_contacto(direccion: str = Query(default=""), db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return get_servicio_tecnico_ventas_contacto(db, direccion)


@router.post("/api/venta/servicio-tecnico-ods/estado")
def venta_servicio_tecnico_ods_estado(payload: VentaServicioTecnicoEstadoRequest, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return update_servicio_tecnico_ventas_estado(db, payload.codigo, payload.campo, payload.valor)


@router.post("/api/venta/servicio-tecnico-ods/valor")
def venta_servicio_tecnico_ods_valor(payload: VentaServicioTecnicoValorRequest, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return update_servicio_tecnico_ventas_valor(db, payload.codigo, payload.campo, payload.valor)


@router.post("/api/venta/servicio-tecnico-ods/layout-final")
def venta_servicio_tecnico_ods_layout(payload: VentaServicioTecnicoLayoutFinalRequest, db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return update_servicio_tecnico_layout_final(db, payload.codigo, payload.nombre, payload.data)


# ─── RRHH ────────────────────────────────────────────────────────────────

@router.get("/rrhh", response_class=HTMLResponse)
def rrhh_panel_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorRRHH')
    if guard:
        return guard
    return _template(request, "seleccion_panel_rrhh.html", token)


# ─── Prevención ──────────────────────────────────────────────────────────

@router.get("/prevencion", response_class=HTMLResponse)
def prevencion_panel_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form='panelSelectorPrevencion')
    if guard:
        return guard
    return _template(request, "seleccion_panel_prevencion.html", token)


def _ensure_estatus_gestion_seed(db: Session) -> None:
    from ATC.app.data.estatus_gestion_prevencion import ESTATUS_GESTION_SECCIONES
    from ATC.app.models.prevencion import EstatusGestionItem

    existe = db.query(EstatusGestionItem.id).first()
    if existe:
        return
    orden = 0
    for seccion in ESTATUS_GESTION_SECCIONES:
        for it in seccion["items"]:
            db.add(
                EstatusGestionItem(
                    seccion=seccion["seccion"],
                    orden=orden,
                    documento=it["documento"],
                    responsable=it["responsable"] or None,
                    revisor=it["revisor"] or None,
                    avance=it["avance"],
                    observaciones=it["observaciones"] or None,
                )
            )
            orden += 1
    db.commit()


def _agrupar_estatus_gestion(db: Session) -> list[dict]:
    from ATC.app.models.prevencion import EstatusGestionItem

    filas = db.query(EstatusGestionItem).order_by(EstatusGestionItem.orden.asc(), EstatusGestionItem.id.asc()).all()
    secciones: list[dict] = []
    por_nombre: dict[str, dict] = {}
    for f in filas:
        grupo = por_nombre.get(f.seccion)
        if grupo is None:
            grupo = {"seccion": f.seccion, "items": []}
            por_nombre[f.seccion] = grupo
            secciones.append(grupo)
        grupo["items"].append(
            {
                "id": f.id,
                "documento": f.documento,
                "responsable": f.responsable or "",
                "revisor": f.revisor or "",
                "avance": f.avance,
                "observaciones": f.observaciones or "",
            }
        )
    return secciones


@router.get("/prevencion/estatus-gestion", response_class=HTMLResponse)
def prevencion_estatus_gestion_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form="panelSelectorPrevencion")
    if guard:
        return guard
    _ensure_estatus_gestion_seed(db)
    secciones = _agrupar_estatus_gestion(db)

    total_items = sum(len(s["items"]) for s in secciones)
    con_avance = [it["avance"] for s in secciones for it in s["items"] if it["avance"] is not None]
    promedio_general = round(sum(con_avance) / len(con_avance), 1) if con_avance else 0

    return _template(
        request,
        "estatus_gestion.html",
        token,
        back_url=f"/prevencion?token={token}",
        secciones=secciones,
        total_items=total_items,
        promedio_general=promedio_general,
    )


class EstatusGestionAvanceRequest(BaseModel):
    avance: int | None = None
    responsable: str | None = None
    revisor: str | None = None


@router.patch("/api/prevencion/estatus-gestion/{item_id}")
def actualizar_estatus_gestion_avance(
    item_id: int,
    payload: EstatusGestionAvanceRequest,
    db: Session = Depends(get_db),
current_user=Depends(_require_login)):
    from ATC.app.models.prevencion import EstatusGestionItem

    item = db.get(EstatusGestionItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item no encontrado")

    campos_enviados = payload.model_fields_set
    if "avance" in campos_enviados:
        if payload.avance is not None and not (0 <= payload.avance <= 100):
            raise HTTPException(status_code=400, detail="El avance debe estar entre 0 y 100")
        item.avance = payload.avance
    if "responsable" in campos_enviados:
        item.responsable = (payload.responsable or "").strip() or None
    if "revisor" in campos_enviados:
        item.revisor = (payload.revisor or "").strip() or None

    db.commit()
    return {
        "ok": True,
        "id": item.id,
        "avance": item.avance,
        "responsable": item.responsable or "",
        "revisor": item.revisor or "",
    }


@router.get("/prevencion/estatus-gestion/informe")
def descargar_informe_estatus_gestion(db: Session = Depends(get_db)):
    import io as _io
    from datetime import datetime as _dt
    from fastapi.responses import StreamingResponse
    from ATC.app.services.prevencion_informe_service import generar_informe_estatus_gestion_pdf

    secciones = _agrupar_estatus_gestion(db)
    pdf_bytes = generar_informe_estatus_gestion_pdf(secciones)
    nombre = f"Estatus_Gestion_Prevencion_{_dt.now().strftime('%Y%m%d_%H%M')}.pdf"
    return StreamingResponse(
        _io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


# ─── Estatus Documentación Técnicos ──────────────────────────────────────

# Personas que SI aparecen en la lista general de tecnicos (usada por
# incidencias_servicio_tecnico y tabla_servicio_tecnico_venta para asignar
# servicios) pero que no corresponden a esta matriz de documentacion/
# capacitaciones de tecnicos de terreno — sacados a pedido explicito del
# usuario, ago 2026, y excluidos aca para que la sincronizacion no los
# vuelva a agregar en la proxima carga de la pagina.
_ESTATUS_DOCUMENTACION_EXCLUIDOS = {
    "christopher esteban villegas ruz",
    "gianpiero alessandro lubiano forno",
    "kevin ignacio valenzuela valenzuela",
}


def _sincronizar_estatus_documentacion_tecnicos(db: Session, service: IncidenciasService) -> None:
    """Alinea prevencion_estatus_documentacion_tecnicos con la misma lista de
    tecnicos que usan incidencias_servicio_tecnico.html y
    tabla_servicio_tecnico_venta.html (service.obtener_listas_bbdd()["tecnicos"]),
    en vez de la lista propia (por department de Users) que quedaba
    desincronizada, salvo _ESTATUS_DOCUMENTACION_EXCLUIDOS. Agrega los
    tecnicos nuevos y borra los que ya no figuran en esa lista — decision
    explicita del usuario, ago 2026 (se pierde el checklist de los que se
    borran, pero deja la lista igual en las 3 paginas)."""
    from sqlalchemy import func
    from ATC.app.models.incidencias import User as _UserModel
    from ATC.app.models.prevencion import EstatusDocumentacionTecnico

    nombres_canonicos = service.obtener_listas_bbdd().get("tecnicos") or []
    canonicos_por_key = {
        service._normalizar_texto(n): n
        for n in nombres_canonicos
        if service._normalizar_texto(n) not in _ESTATUS_DOCUMENTACION_EXCLUIDOS
    }
    ruts_por_key = {
        service._normalizar_texto(u.name): u.username
        for u in db.query(_UserModel).all()
    }

    filas = db.query(EstatusDocumentacionTecnico).all()
    keys_existentes = set()
    for fila in filas:
        key = service._normalizar_texto(fila.nombre)
        if key not in canonicos_por_key:
            db.delete(fila)
        else:
            keys_existentes.add(key)

    siguiente_orden = (db.query(func.max(EstatusDocumentacionTecnico.orden)).scalar() or 0) + 1
    for key, nombre in canonicos_por_key.items():
        if key in keys_existentes:
            continue
        db.add(EstatusDocumentacionTecnico(orden=siguiente_orden, nombre=nombre, rut=ruts_por_key.get(key, "")))
        siguiente_orden += 1

    db.commit()


def _serializar_estatus_documentacion_tecnicos(db: Session) -> list[dict]:
    from ATC.app.models.prevencion import DOCUMENTACION_TECNICO_CHECK_FIELDS, EstatusDocumentacionTecnico

    filas = (
        db.query(EstatusDocumentacionTecnico)
        .order_by(EstatusDocumentacionTecnico.orden.asc(), EstatusDocumentacionTecnico.id.asc())
        .all()
    )
    total_campos = len(DOCUMENTACION_TECNICO_CHECK_FIELDS)
    resultado = []
    for f in filas:
        checks = {campo: bool(getattr(f, campo)) for campo, _ in DOCUMENTACION_TECNICO_CHECK_FIELDS}
        completados = sum(1 for v in checks.values() if v)
        avance = round((completados / total_campos) * 100) if total_campos else 0
        resultado.append(
            {
                "id": f.id,
                "nombre": f.nombre,
                "rut": f.rut or "",
                "checks": checks,
                "avance": avance,
            }
        )
    return resultado


@router.get("/prevencion/estatus-documentacion", response_class=HTMLResponse)
def prevencion_estatus_documentacion_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    from ATC.app.models.prevencion import DOCUMENTACION_TECNICO_CHECK_FIELDS

    guard = _guard_page(request, db, service, token, next_form="panelSelectorPrevencion")
    if guard:
        return guard

    _sincronizar_estatus_documentacion_tecnicos(db, service)
    tecnicos = _serializar_estatus_documentacion_tecnicos(db)
    promedio_general = round(sum(t["avance"] for t in tecnicos) / len(tecnicos), 1) if tecnicos else 0

    return _template(
        request,
        "estatus_documentacion_tecnicos.html",
        token,
        back_url=f"/prevencion?token={token}",
        columnas=DOCUMENTACION_TECNICO_CHECK_FIELDS,
        tecnicos=tecnicos,
        promedio_general=promedio_general,
    )


class EstatusDocumentacionTecnicoUpdateRequest(BaseModel):
    campo: str
    valor: bool


@router.patch("/api/prevencion/estatus-documentacion/{item_id}")
def actualizar_estatus_documentacion_tecnico(
    item_id: int,
    payload: EstatusDocumentacionTecnicoUpdateRequest,
    db: Session = Depends(get_db),
current_user=Depends(_require_login)):
    from ATC.app.models.prevencion import DOCUMENTACION_TECNICO_CHECK_FIELDS, EstatusDocumentacionTecnico

    item = db.get(EstatusDocumentacionTecnico, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")

    campos_check = {campo for campo, _ in DOCUMENTACION_TECNICO_CHECK_FIELDS}
    campo = payload.campo.strip()
    if campo not in campos_check:
        raise HTTPException(status_code=400, detail="Campo inválido")
    setattr(item, campo, bool(payload.valor))
    db.commit()

    total_campos = len(DOCUMENTACION_TECNICO_CHECK_FIELDS)
    completados = sum(1 for c, _ in DOCUMENTACION_TECNICO_CHECK_FIELDS if getattr(item, c))
    avance = round((completados / total_campos) * 100) if total_campos else 0
    return {"ok": True, "id": item.id, "avance": avance}


def _campo_documentacion_folder_map() -> dict[str, str]:
    """campo_key -> nombre de carpeta Drive (mismo criterio de limpieza que
    upload_documentacion_tecnico_archivos, que arma el nombre de carpeta a
    partir del label con _clean_filename)."""
    from ATC.app.models.prevencion import DOCUMENTACION_TECNICO_CHECK_FIELDS
    from ATC.app.services.drive_base_service import _clean_filename

    return {campo: _clean_filename(label, fallback=campo) for campo, label in DOCUMENTACION_TECNICO_CHECK_FIELDS}


@router.get("/api/prevencion/estatus-documentacion/{item_id}/archivos")
def obtener_archivos_documentacion_tecnico(
    item_id: int,
    db: Session = Depends(get_db),
current_user=Depends(_require_login)):
    from ATC.app.models.prevencion import DOCUMENTACION_TECNICO_CHECK_FIELDS, EstatusDocumentacionTecnico
    from ATC.app.services.incidencias_drive_report_service import listar_documentacion_tecnico_archivos

    item = db.get(EstatusDocumentacionTecnico, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")

    por_carpeta = listar_documentacion_tecnico_archivos(tecnico=item.nombre)
    folder_por_campo = _campo_documentacion_folder_map()

    campos = [
        {"campo": campo, "label": label, "archivos": por_carpeta.get(folder_por_campo[campo], [])}
        for campo, label in DOCUMENTACION_TECNICO_CHECK_FIELDS
    ]
    return {"tecnico": item.nombre, "campos": campos}


@router.post("/api/prevencion/estatus-documentacion/{item_id}/archivos")
async def subir_archivos_documentacion_tecnico(
    item_id: int,
    campo: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
current_user=Depends(_require_login)):
    from ATC.app.models.prevencion import DOCUMENTACION_TECNICO_CHECK_FIELDS, EstatusDocumentacionTecnico
    from ATC.app.services.incidencias_drive_report_service import (
        DriveReportError,
        upload_documentacion_tecnico_archivos,
    )

    item = db.get(EstatusDocumentacionTecnico, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")

    campos_por_key = dict(DOCUMENTACION_TECNICO_CHECK_FIELDS)
    campo_limpio = (campo or "").strip()
    if campo_limpio not in campos_por_key:
        raise HTTPException(status_code=400, detail="Campo inválido")

    payloads: list[dict[str, object]] = []
    for upload in files or []:
        if not upload:
            continue
        content = await upload.read()
        if not content:
            continue
        payloads.append(
            {
                "filename": upload.filename or "archivo",
                "mime_type": upload.content_type or "application/octet-stream",
                "bytes": content,
            }
        )
    if not payloads:
        raise HTTPException(status_code=400, detail="Debes adjuntar al menos un archivo.")

    try:
        resultado = upload_documentacion_tecnico_archivos(
            tecnico=item.nombre,
            campo_label=campos_por_key[campo_limpio],
            file_payloads=payloads,
        )
    except DriveReportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"ok": True, "archivos": resultado["archivos"]}


# ─── Guardia / Supervisores ──────────────────────────────────────────────

@router.get("/guardia", response_class=HTMLResponse)
def guardia_panel_page(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    guard = _guard_page(request, db, service, token, next_form="panelSelectorGuardia")
    if guard:
        return guard
    return _template(request, "seleccion_panel_guardia.html", token)


@router.get("/supervisores", response_class=HTMLResponse)
def supervisores_panel_page(
    request: Request,
    token: str = Query(default=""),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
    db: Session = Depends(get_db),
):
    from ATC.app.models.incidencias import LoginSession as _LoginSession, User as _UserModel
    from ATC.app.routes.web import COOKIE_NAME as _COOKIE_NAME, _decode_cookie_token as _decode_cookie_token_web
    from ATC.app.services.user_service import UserService as _UserService

    tecnico = None
    rut = ""
    es_admin = False
    departamento = ""
    token_limpio = (token or "").strip()
    if token_limpio and service.usuario_logueado_por_token(token_limpio):
        tecnico = service.get_usuario_actual(token_limpio)
        sesion = db.query(_LoginSession).filter(_LoginSession.token == token_limpio).first()
        if sesion and sesion.user_id:
            user = db.get(_UserModel, int(sesion.user_id))
            if user:
                rut = str(user.username or "").strip()
                es_admin = bool(user.is_admin)
                departamento = str(user.department or "")
    else:
        try:
            cookie = request.cookies.get(_COOKIE_NAME, "")
            if cookie:
                login = _decode_cookie_token_web(cookie)
                user = _UserService.find_by_login(db, login)
                if user and user.is_active:
                    tecnico = user.name
                    rut = str(user.username or "").strip()
                    es_admin = bool(user.is_admin)
                    departamento = str(user.department or "")
        except Exception:
            pass
    if not tecnico:
        return RedirectResponse(url="/?form=login&next=auto", status_code=303)

    rut_norm = rut.replace(".", "").strip().casefold()
    departamentos = {p.strip().casefold() for p in departamento.split(";") if p.strip()}
    solo_quintero = "supervisorquintero" in departamentos and not es_admin
    solo_concon = "supervisorconcon" in departamentos and not es_admin
    # "Privados" solo lo veia el rut hardcodeado del supervisor de esa zona;
    # se agrega tambien para admin/superadmin para poder verla desde el panel
    # sin perder la de Quintero (que sigue mostrandose salvo a ese supervisor
    # especifico de privados).
    mostrar_privados = (rut_norm in ("11825227-6", "11111111-1") or es_admin) and not solo_quintero and not solo_concon

    return _template(
        request,
        "seleccion_panel_supervisores.html",
        token_limpio,
        tecnico=tecnico,
        rut=rut,
        mostrar_privados=mostrar_privados,
        mostrar_concon=not solo_quintero,
        mostrar_quintero=(es_admin or rut_norm != "11825227-6") and not solo_concon,
    )
