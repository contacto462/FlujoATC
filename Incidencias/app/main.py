from __future__ import annotations

from datetime import datetime
import json
import threading
import time
import logging
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine, get_db
from app.config import settings
from app.schemas import (
    CerrarIncidenciaRequest,
    DerivarTecnicoRequest,
    EditarIncidenciaTablaRequest,
    EnProcesoRequest,
    EnviarInformacionContactoRequest,
    FinalizarIncidenciaRequest,
    FormularioRegistro,
    IncidenciaNueva,
    LoginRequest,
    LoginResponse,
    ProtocoloRegistroCreateRequest,
    RendicionRequest,
    TareaManualRequest,
)
from app.services import IncidenciasService
from app.protocolos_service import ProtocolosService
from app.venta.routes import router as venta_router


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
STATIC_DIR = BASE_DIR / "static"
ATC_STATIC_DIR = BASE_DIR.parent.parent / "ATC" / "static"

app = FastAPI(title="Incidencias API", version="1.0.0")
LOGGER = logging.getLogger(__name__)
_protocolos_weekly_worker_started = False

if ATC_STATIC_DIR.exists():
    app.mount("/shared-static", StaticFiles(directory=str(ATC_STATIC_DIR)), name="shared-static")
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(venta_router)

TIPOS_Y_ESPECIFICACIONES = {
    "GestiÃ³n de Grabaciones y Evidencia": [
        "Solicitud / envÃ­o de grabaciones",
        "Solicitud de imÃ¡genes",
        "Grabaciones faltantes",
    ],
    "Monitoreo y Estado de CÃ¡maras": [
        "CÃ¡maras caÃ­das / intermitentes",
        "CÃ¡maras fuera de horario",
        "CÃ¡maras obstruidas / tapadas / movidas",
        "CÃ¡mara nueva / reemplazo",
        "VerificaciÃ³n de cÃ¡maras en lÃ­nea",
        "InicializaciÃ³n / renombrado de cÃ¡maras",
    ],
    "ConfiguraciÃ³n y Ajustes de CÃ¡maras / NVR / DVR": [
        "ConfiguraciÃ³n de cÃ¡maras nuevas",
        "ConfiguraciÃ³n de NVR / DVR / XVR",
        "Planes de grabaciÃ³n",
        "RetenciÃ³n de dÃ­as",
        "ConfiguraciÃ³n FTP / IVS / PTZ",
        "CorrecciÃ³n IVS",
        "Cambio de nombre / orden de cÃ¡maras",
        "NormalizaciÃ³n de hora",
    ],
    "Sistema de Audio y Alertas": [
        "Problemas de audio",
        "Pruebas de audio",
        "Sonidos molestos",
        "Audio por activaciÃ³n de IVS",
        "Sistema de audio no funciona",
    ],
    "IVS, POP UPS y Automatizaciones": [
        "CreaciÃ³n / ajuste de IVS",
        "Alertas automÃ¡ticas",
        "POP UPS",
        "ActivaciÃ³n / revisiÃ³n de POP UPS",
    ],
    "Soporte a Equipos de ComputaciÃ³n": [
        "Notebook lento / no enciende",
        "Formateo de PC / notebook",
        "Cambio de RAM",
        "Cambio de computador / torre",
        "Problemas de disco duro",
        "Limpieza de equipos",
        "ActivaciÃ³n de Windows",
    ],
    "Soporte a Pantallas y PerifÃ©ricos": [
        "Pantallas sin seÃ±al",
        "HDMI / VGA defectuoso",
        "Monitores apagados / intermitentes",
        "Mouse / teclado",
        "Impresoras (tÃ©rmica / normal)",
    ],
    "Redes y Conectividad": [
        "CaÃ­das de red",
        "Cambio IP / DHCP",
        "Router / antenas",
        "Intermitencia de enlace",
        "Internet caÃ­do",
    ],
    "Sistema de Alarmas y Sensores": [
        "Problemas de alarma",
        "Sensores (humo / gas / pÃ¡nico)",
        "Sirenas",
        "ZonificaciÃ³n",
        "Panel de alarma",
        "Notificaciones que no llegan",
    ],
    "Soporte a Software y Plataformas": [
        "DSS / HikCentral lento o fallando",
        "SoftGuard",
        "App de alarma / cÃ¡mara",
        "ConfiguraciÃ³n de usuarios",
        "Credenciales",
        "Cambio de contraseÃ±as",
    ],
    "GestiÃ³n Operativa y Administrativa": [
        "Orden y creaciÃ³n de planillas",
        "Registro de incidencias",
        "Correos informativos",
        "Solicitud de folios / QR",
        "CoordinaciÃ³n con tÃ©cnicos externos",
        "ComunicaciÃ³n con clientes / prioridades",
    ],
    "Cierres, Validaciones y RevisiÃ³n de Novedades": [
        "Cierre de caseta / instalaciÃ³n (Emergencia)",
        "RevisiÃ³n de novedades",
        "Validaciones finales",
        "Pruebas posteriores a intervenciÃ³n",
    ],
    "Mantenimiento Preventivo": [
        "MantenciÃ³n de equipos",
        "RevisiÃ³n periÃ³dica",
        "NormalizaciÃ³n preventiva",
        "RevisiÃ³n programada de IVS / audio / cÃ¡maras",
    ],
}


@app.on_event("startup")
def startup() -> None:
    global _protocolos_weekly_worker_started
    Base.metadata.create_all(bind=engine)
    _ensure_registro_optional_columns()
    _ensure_protocolos_optional_columns()
    _ensure_bbdd_clientes_optional_columns()
    if not _protocolos_weekly_worker_started:
        _protocolos_weekly_worker_started = True
        threading.Thread(
            target=_protocolos_weekly_worker_loop,
            name="protocolos-weekly-worker",
            daemon=True,
        ).start()


def _ensure_registro_optional_columns() -> None:
    optional_columns: dict[str, str] = {
        "detalle_problema": "TEXT",
        "observacion_soporte": "TEXT",
        "observacion_servicio": "TEXT",
        "materiales": "TEXT",
        "responsable_cierre": "VARCHAR(40)",
        "causa_cierre": "VARCHAR(120)",
        "accion_cierre": "VARCHAR(120)",
        "resultado_cierre": "VARCHAR(120)",
        "pruebas_cierre": "TEXT",
        "requiere_seguimiento": "BOOLEAN",
        "drive_cierre_folder_id": "VARCHAR(255)",
        "drive_cierre_folder_url": "TEXT",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("registro"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("registro")}
            for col_name, col_type in optional_columns.items():
                if col_name in existing_columns:
                    continue
                conn.execute(text(f'ALTER TABLE registro ADD COLUMN "{col_name}" {col_type}'))
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'registro': %s", exc)


def _ensure_protocolos_optional_columns() -> None:
    optional_columns: dict[str, str] = {
        "protocolo_exitoso": "VARCHAR(20)",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("protocolos_registro"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("protocolos_registro")}
            for col_name, col_type in optional_columns.items():
                if col_name in existing_columns:
                    continue
                conn.execute(text(f'ALTER TABLE protocolos_registro ADD COLUMN "{col_name}" {col_type}'))
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'protocolos_registro': %s", exc)


def _ensure_bbdd_clientes_optional_columns() -> None:
    optional_columns: dict[str, str] = {
        "giro": "VARCHAR(255)",
        "region": "VARCHAR(120)",
        "comuna": "VARCHAR(120)",
        "email_facturas": "VARCHAR(255)",
        "nombre_representante": "VARCHAR(255)",
        "rut_representante": "VARCHAR(40)",
        "telefono": "VARCHAR(32)",
        "email_representante": "VARCHAR(255)",
        "ejecutivo_email": "VARCHAR(255)",
        "fecha_creacion": "TIMESTAMP WITH TIME ZONE DEFAULT NOW()",
    }
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if not inspector.has_table("bbdd_clientes"):
                return

            existing_columns = {str(c.get("name", "")).strip() for c in inspector.get_columns("bbdd_clientes")}
            for col_name, col_type in optional_columns.items():
                if col_name in existing_columns:
                    continue
                conn.execute(text(f'ALTER TABLE bbdd_clientes ADD COLUMN "{col_name}" {col_type}'))
    except Exception as exc:
        LOGGER.warning("No fue posible asegurar columnas opcionales en 'bbdd_clientes': %s", exc)


def get_service(db: Annotated[Session, Depends(get_db)]) -> IncidenciasService:
    return IncidenciasService(db)


def get_protocolos_service(db: Annotated[Session, Depends(get_db)]) -> ProtocolosService:
    return ProtocolosService(db)


def _protocolos_weekly_worker_loop() -> None:
    tz = ZoneInfo(settings.timezone or "America/Santiago")
    ultimo_dia_protocolo = ""
    ultimo_dia_mantenciones = ""
    ultimo_dia_mantenciones_mensuales = ""
    ultimo_dia_mantenciones_trimestrales = ""
    while True:
        try:
            now = datetime.now(tz)
            if now.weekday() == 0 and now.hour >= 6:
                dia_key = now.strftime("%Y-%m-%d")
                if dia_key != ultimo_dia_mantenciones:
                    db = SessionLocal()
                    try:
                        result = IncidenciasService(db).programar_mantenciones_quilpue(
                            fecha_referencia=now,
                            forzar=True,
                        )
                        LOGGER.info("Mantenciones programadas Quilpue: %s", result)
                    finally:
                        db.close()
                    ultimo_dia_mantenciones = dia_key

            if now.day == 1 and now.hour >= 6:
                dia_key = now.strftime("%Y-%m-%d")
                if dia_key != ultimo_dia_mantenciones_mensuales:
                    db = SessionLocal()
                    try:
                        result = IncidenciasService(db).programar_mantenciones_mensuales_llay_llay(
                            fecha_referencia=now,
                            forzar=True,
                        )
                        LOGGER.info("Mantenciones mensuales Llay Llay: %s", result)
                    finally:
                        db.close()
                    ultimo_dia_mantenciones_mensuales = dia_key

            if now.day == 1 and now.month in {3, 6, 9, 12} and now.hour >= 6:
                dia_key = now.strftime("%Y-%m-%d")
                if dia_key != ultimo_dia_mantenciones_trimestrales:
                    db = SessionLocal()
                    try:
                        result = IncidenciasService(db).programar_mantenciones_trimestrales_quintero_y_concon(
                            fecha_referencia=now,
                            forzar=True,
                        )
                        LOGGER.info("Mantenciones trimestrales Quintero/Concon: %s", result)
                    finally:
                        db.close()
                    ultimo_dia_mantenciones_trimestrales = dia_key

            if now.weekday() == 0 and now.hour >= 8:
                dia_key = now.strftime("%Y-%m-%d")
                if dia_key != ultimo_dia_protocolo:
                    db = SessionLocal()
                    try:
                        result = ProtocolosService(db).generar_resumenes_semanales_pendientes(forzar=True)
                        LOGGER.info("Resumen semanal protocolos: %s", result)
                    finally:
                        db.close()
                    ultimo_dia_protocolo = dia_key
        except Exception:
            LOGGER.exception("Fallo el worker semanal automatico (mantenciones/protocolos).")
        time.sleep(900)


@app.get("/", response_class=HTMLResponse)
def do_get(
    request: Request,
    form: str = Query(default="servicioTecnico"),
    tecnico: str = Query(default=""),
    cliente: str = Query(default=""),
    odt: str = Query(default=""),
    token: str = Query(default=""),
    next_form: str = Query(default="tecnicos", alias="next"),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    form_aliases = {
        "tabla": "servicioTecnico",
        "STVentas": "stVentas",
        "servicioTécnico": "servicioTecnico",
        "servicioTecnico": "servicioTecnico",
        "coordinación": "coordinacion",
        "coordinacion": "coordinacion",
    }
    form = form_aliases.get(form, form)
    next_form = form_aliases.get(next_form, next_form)
    formularios_validos = {
        "login",
        "panelSelector",
        "panelSelectorServicio",
        "panelSelectorCoordinacion",
        "panelSelectorVenta",
        "incidencias",
        "cierreAperturaClientes",
        "controlProtocolos",
        "tablaProtocolos",
        "envioProtocolosSemanales",
        "pendientes",
        "tecnicos",
        "coordinacion",
        "resumen",
        "formularioViatico",
        "servicioTecnico",
        "stVentas",
        "rendiciones",
        "rendicionesTecnico",
        "dashboardOperacional",
        "dashboardAnalitico",
    }

    if form not in formularios_validos:
        html = (
            f"<h2 style='font-family:sans-serif;color:darkred'>&#9888; Formulario desconocido: <code>{form}</code></h2>"
            "<p style='font-family:sans-serif'>Verifica que la URL este escrita correctamente.</p>"
        )
        return HTMLResponse(content=html, status_code=400)

    if form == "panelSelectorVenta" and not service.usuario_logueado_por_token(token):
        return HTMLResponse(
            content="<script>window.location.href='/venta/login';</script>",
            status_code=200,
        )

    if form in {
        "panelSelector",
        "panelSelectorServicio",
        "panelSelectorCoordinacion",
        "panelSelectorVenta",
        "incidencias",
        "cierreAperturaClientes",
        "controlProtocolos",
        "tablaProtocolos",
        "envioProtocolosSemanales",
        "pendientes",
        "tecnicos",
        "coordinacion",
        "formularioViatico",
        "rendiciones",
        "rendicionesTecnico",
        "servicioTecnico",
        "stVentas",
    } and not service.usuario_logueado_por_token(token):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "title": "Iniciar Sesion", "next_form": form},
        )

    if form in {"servicioTecnico", "panelSelectorServicio", "stVentas"} and not service.usuario_autorizado_para_tabla(token):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "title": "Iniciar Sesion", "next_form": "servicioTecnico"},
        )

    if form in {
        "tecnicos",
        "rendiciones",
        "rendicionesTecnico",
        "panelSelector",
        "panelSelectorServicio",
        "panelSelectorCoordinacion",
        "panelSelectorVenta",
        "coordinacion",
        "tablaProtocolos",
        "envioProtocolosSemanales",
    } and token:
        tecnico = service.get_usuario_actual(token)
    view_map = {
        "login": "login.html",
        "panelSelector": "panel_selector.html",
        "panelSelectorServicio": "panel_selector_servicio.html",
        "panelSelectorCoordinacion": "panel_selector_coordinacion.html",
        "panelSelectorVenta": "panel_selector_venta.html",
        "servicioTecnico": "tabla.html",
        "stVentas": "STVentas.html",
        "incidencias": "incidencias.html",
        "cierreAperturaClientes": "cierre_apertura_clientes.html",
        "controlProtocolos": "control_protocolos.html",
        "tablaProtocolos": "tabla_protocolos.html",
        "envioProtocolosSemanales": "envio_protocolos_semanales.html",
        "pendientes": "pendientes.html",
        "tecnicos": "tecnicos.html",
        "coordinacion": "coordinacion.html",
        "resumen": "resumen.html",
        "formularioViatico": "formularioViatico.html",
        "rendiciones": "rendiciones.html",
        "rendicionesTecnico": "rendiciones_tecnico.html",
        "dashboardOperacional": "dashboardOperacional.html",
        "dashboardAnalitico": "dashboardAnalitico.html",
    }
    tpl = view_map.get(form, "tabla.html")
    context = {
        "request": request,
        "title": "servicioTecnico" if form == "servicioTecnico" else form,
        "token": token,
        "tecnico": tecnico,
        "cliente": cliente,
        "odt": odt,
        "next_form": next_form
        if next_form
        in {
            "panelSelector",
            "panelSelectorServicio",
            "panelSelectorCoordinacion",
            "panelSelectorVenta",
            "incidencias",
            "cierreAperturaClientes",
            "controlProtocolos",
            "tablaProtocolos",
            "envioProtocolosSemanales",
            "pendientes",
            "tecnicos",
            "coordinacion",
            "formularioViatico",
            "rendiciones",
            "rendicionesTecnico",
            "servicioTecnico",
            "stVentas",
        }
        else "tecnicos",
    }
    resp = templates.TemplateResponse(tpl, context)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.post("/api/login", response_model=LoginResponse)
def check_login(
    payload: LoginRequest,
    request: Request,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    app_url = str(request.base_url).rstrip("/")
    data = service.check_login(
        payload.nombre_tecnico,
        payload.clave,
        payload.token,
        app_url,
        payload.destino or "pendientes",
    )
    return LoginResponse(**data)


@app.post("/api/logout")
def logout(token: str, service: Annotated[IncidenciasService, Depends(get_service)]):
    return {"ok": service.logout(token)}


@app.get("/api/usuario-actual")
def get_usuario_actual(token: str, service: Annotated[IncidenciasService, Depends(get_service)]):
    return {"usuario": service.get_usuario_actual(token)}


@app.get("/api/login/usuarios")
def get_usuarios_login(
    destino: str = "tecnicos",
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    return {"usuarios": service.obtener_usuarios_login_tecnicos(destino)}


@app.get("/api/listas/bbdd")
def obtener_listas_bbdd(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_listas_bbdd()


@app.get("/api/listas/incidencias")
def obtener_listas_incidencias(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_listas_incidencias()


@app.get("/api/catalogo-clientes")
def obtener_catalogo_clientes(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_catalogo_clientes()


@app.get("/api/registros")
def obtener_registros(
    tecnico: str = "",
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    try:
        return service.obtener_registros(tecnico)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/registros/administracion")
def obtener_registros_administracion(
    tecnico: str = "",
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    try:
        return service.obtener_registros_desde_administracion(tecnico)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/incidencias/puesto")
def obtener_incidencias_por_puesto(
    service: Annotated[IncidenciasService, Depends(get_service)],
    tecnico: str = "",
):
    try:
        return service.obtener_incidencias_por_puesto(tecnico)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/tecnicos/ruta-optima")
def obtener_ruta_optima_tecnico(
    service: Annotated[IncidenciasService, Depends(get_service)],
    tecnico: str = "",
):
    try:
        return service.obtener_ruta_optima_tecnico(tecnico=tecnico)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/incidencias/coordinacion")
def obtener_incidencias_coordinacion(
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        return service.obtener_incidencias_derivadas_cliente()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/sucursal/detalle")
def obtener_detalle_sucursal(
    odt: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    return service.obtener_datos_sucursal_con_coordenadas(odt)


@app.get("/api/sucursal/incidencias")
def obtener_historial_sucursal(
    cliente: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    return service.obtener_ultimas_incidencias_sucursal(cliente)


@app.get("/api/incidencias/imagenes")
def obtener_imagenes_incidencia(
    odt: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    return service.obtener_imagenes_finalizacion(odt)


@app.get("/api/incidencias/imagenes-tabla")
def obtener_imagenes_tabla(
    odt: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    return {"odt": odt, "imagenes": service.obtener_imagenes_tabla(odt)}


@app.post("/api/incidencias/upload-image-tabla")
async def subir_imagenes_tabla(
    odt: str = Form(...),
    token: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    service: IncidenciasService = Depends(get_service),
):
    payloads: list[dict[str, object]] = []
    for upload in files or []:
        if not upload:
            continue
        content = await upload.read()
        if not content:
            continue
        payloads.append(
            {
                "filename": upload.filename or "imagen.png",
                "mime_type": (upload.content_type or "image/png"),
                "bytes": content,
            }
        )

    try:
        return service.subir_imagenes_tabla(odt=odt, image_payloads=payloads, token=token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/formulario")
def enviar_formulario(
    payload: FormularioRegistro,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        message = service.enviar_formulario(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": message}


@app.post("/api/incidencias/nueva")
def guardar_incidencia_nueva(
    payload: IncidenciaNueva,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        odt = service.guardar_incidencia_nueva(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"odt": odt}


@app.post("/api/incidencias/multiples")
def enviar_multiples_incidencias(
    payload: list[IncidenciaNueva],
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        odts = service.enviar_multiples_incidencias(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"odts": odts}


@app.post("/api/incidencias/cerrar")
def cerrar_incidencia(
    payload: CerrarIncidenciaRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        ok = service.registrar_finalizacion_rapida(
            payload.odt,
            payload.observacion,
            responsable_cierre=payload.responsable_cierre,
            causa_cierre=payload.causa_cierre,
            accion_cierre=payload.accion_cierre,
            resultado_cierre=payload.resultado_cierre,
            pruebas_cierre=payload.pruebas_cierre,
            materiales=payload.materiales,
            materiales_sin_uso=payload.materiales_sin_uso,
            requiere_seguimiento=payload.requiere_seguimiento,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"result": ok}


@app.post("/api/incidencias/finalizar-completo")
def finalizar_incidencia_completo(
    payload: FinalizarIncidenciaRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        result = service.continuar_finalizacion_asincrona(
            payload.odt,
            payload.fotos_base64,
            payload.observacion,
            responsable_cierre=payload.responsable_cierre,
            causa_cierre=payload.causa_cierre,
            accion_cierre=payload.accion_cierre,
            resultado_cierre=payload.resultado_cierre,
            pruebas_cierre=payload.pruebas_cierre,
            materiales=payload.materiales,
            materiales_sin_uso=payload.materiales_sin_uso,
            requiere_seguimiento=payload.requiere_seguimiento,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if isinstance(result, dict):
        return result
    return {"result": result}


@app.post("/api/incidencias/cierre-mantencion")
async def cerrar_mantencion_con_imagenes(
    odt: str = Form(...),
    token: str = Form(""),
    diagnostico: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    service: IncidenciasService = Depends(get_service),
):
    odt_limpia = str(odt or "").strip()
    try:
        data = json.loads(diagnostico or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Diagnostico de cierre invalido.") from exc

    uploads = [upload for upload in (files or []) if upload and upload.filename]
    if not uploads:
        raise HTTPException(status_code=400, detail="Debes adjuntar al menos una imagen para cerrar una mantencion.")
    if len(uploads) > service.MANTENCION_CIERRE_MAX_IMAGENES:
        raise HTTPException(
            status_code=400,
            detail=f"Solo puedes adjuntar hasta {service.MANTENCION_CIERRE_MAX_IMAGENES} imagenes.",
        )

    try:
        service.validar_odt_mantencion_preventiva(odt_limpia)
        staging_dir = service.crear_staging_cierre_mantencion(odt_limpia)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    staged_files = []
    for idx, upload in enumerate(uploads, start=1):
        mime_type = str(upload.content_type or "").lower()
        if not mime_type.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"{upload.filename or 'archivo'} no es una imagen valida.")

        target = staging_dir / service.nombre_staging_cierre_mantencion(idx, upload.filename or "", mime_type)
        total = 0
        with target.open("wb") as fh:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > service.MANTENCION_CIERRE_MAX_BYTES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{upload.filename or 'archivo'} supera el limite de 10 MB.",
                    )
                fh.write(chunk)
        if total <= 0:
            raise HTTPException(status_code=400, detail=f"{upload.filename or 'archivo'} esta vacio.")
        staged_files.append(target)

    try:
        return service.cerrar_mantencion_con_imagenes_staging(
            odt=odt_limpia,
            staged_files=staged_files,
            observacion=str(data.get("observacion") or ""),
            responsable_cierre=str(data.get("responsableCierre") or ""),
            causa_cierre=str(data.get("causaCierre") or ""),
            accion_cierre=str(data.get("accionCierre") or ""),
            resultado_cierre=str(data.get("resultadoCierre") or ""),
            pruebas_cierre=data.get("pruebasCierre") or [],
            materiales=data.get("materiales") or [],
            materiales_sin_uso=bool(data.get("materialesSinUso")),
            requiere_seguimiento=bool(data.get("requiereSeguimiento")),
            token=token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/incidencias/en-proceso")
def guardar_incidencia_en_proceso(
    payload: EnProcesoRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        ok = service.guardar_datos_en_proceso(
            payload.odt,
            payload.avance,
            payload.observacion,
            payload.token or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"result": ok}


@app.post("/api/incidencias/derivar-tecnico")
def derivar_incidencia_tecnico(
    payload: DerivarTecnicoRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        ok = service.derivar_odt_a_tecnico(
            payload.odt,
            payload.tecnico,
            payload.acompanante or "",
            payload.derivacion or "Servicio Técnico",
            payload.estado or "Pendiente",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=f"ODT {payload.odt} no encontrada")
    return {"ok": True}


@app.patch("/api/incidencias/editar-tabla")
def editar_incidencia_tabla(
    payload: EditarIncidenciaTablaRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        result = service.editar_incidencia_tabla(
            token=payload.token,
            odt=payload.odt,
            derivacion=payload.derivacion,
            observacion=payload.observacion,
            observacion_servicio=payload.observacion_servicio,
            observacion_final=payload.observacion_final,
            repetida_odt_ref=payload.repetida_odt_ref,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=f"ODT {payload.odt} no encontrada")
    return result


@app.post("/api/incidencias/cerrar-encargado")
def cerrar_incidencia_encargado(
    odt: str,
    fecha_cierre: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        dt = datetime.fromisoformat(fecha_cierre)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="fecha_cierre debe ser ISO-8601") from exc
    ok = service.cerrar_incidencia(odt, dt)
    if not ok:
        raise HTTPException(status_code=404, detail=f"ODT {odt} no encontrada")
    return {"ok": True}


@app.get("/api/tecnicos/pendientes")
def obtener_tecnicos_pendientes(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_tecnicos_pendientes()


@app.post("/api/mantencion/correctiva")
def guardar_mantencion_correctiva(
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    return {"result": service.guardar_mantencion_correctiva(payload)}


@app.post("/api/mantencion/programada/quilpue/ejecutar")
def ejecutar_mantencion_programada_quilpue(
    fecha_referencia: str | None = None,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    ref = None
    if fecha_referencia:
        try:
            ref = datetime.fromisoformat(fecha_referencia)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="fecha_referencia debe venir en formato ISO-8601 (ej: 2026-04-20T06:00:00).",
            ) from exc
    return service.programar_mantenciones_quilpue(fecha_referencia=ref, forzar=True)


@app.post("/api/mantencion/programada/quintero/ejecutar")
def ejecutar_mantencion_programada_quintero(
    fecha_referencia: str | None = None,
    limite: int | None = None,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    ref = None
    if fecha_referencia:
        try:
            ref = datetime.fromisoformat(fecha_referencia)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="fecha_referencia debe venir en formato ISO-8601 (ej: 2026-06-01T06:00:00).",
            ) from exc
    return service.programar_mantenciones_trimestrales_quintero(fecha_referencia=ref, forzar=True, limite=limite)


@app.post("/api/mantencion/programada/concon/ejecutar")
def ejecutar_mantencion_programada_concon(
    fecha_referencia: str | None = None,
    limite: int | None = None,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    ref = None
    if fecha_referencia:
        try:
            ref = datetime.fromisoformat(fecha_referencia)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="fecha_referencia debe venir en formato ISO-8601 (ej: 2026-06-01T06:00:00).",
            ) from exc
    return service.programar_mantenciones_trimestrales_concon(fecha_referencia=ref, forzar=True, limite=limite)


@app.get("/api/mantencion/programada/plantilla")
def obtener_plantilla_mantencion_programada(
    sucursal: str,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    if not str(sucursal or "").strip():
        raise HTTPException(status_code=400, detail="sucursal es obligatoria.")
    imagenes = service.obtener_plantilla_imagenes_mantencion(sucursal)
    return {"sucursal": sucursal, "imagenes": imagenes, "total_imagenes": len(imagenes)}


@app.post("/api/mantencion/programada/plantilla")
def guardar_plantilla_mantencion_programada(
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    try:
        sucursal = str(payload.get("sucursal") or "").strip()
        imagenes = payload.get("imagenes") or []
        return service.guardar_plantilla_imagenes_mantencion(sucursal=sucursal, imagenes=imagenes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/mantencion/programada/plantilla-desde-odt")
def guardar_plantilla_mantencion_programada_desde_odt(
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    try:
        sucursal = str(payload.get("sucursal") or "").strip()
        odt_origen = str(payload.get("odt_origen") or "").strip()
        return service.guardar_plantilla_imagenes_mantencion_desde_odt(
            sucursal=sucursal,
            odt_origen=odt_origen,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/contactos/sucursal")
def obtener_contactos_por_sucursal(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_contactos_por_sucursal()


@app.post("/api/contacto-cliente/enviar-info")
def enviar_info_contacto_cliente(
    payload: EnviarInformacionContactoRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        return service.registrar_envio_informacion_contacto(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/clientes-soporte")
def obtener_clientes_soporte(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_clientes_soporte()


@app.post("/api/sync/soporte/retry")
def reintentar_sync_soporte(
    limit: int = 50,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    return service.sync_soporte_pendientes(limit)


@app.get("/api/sync/soporte/outbox")
def estado_sync_soporte(
    limit: int = 100,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    return service.obtener_estado_sync_outbox(limit)


@app.get("/api/tareas/tipos")
def obtener_tipos_especificaciones():
    return TIPOS_Y_ESPECIFICACIONES


@app.get("/api/protocolos/listas")
def obtener_listas_protocolos(
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)],
):
    return service.obtener_listas()


@app.post("/api/protocolos/registro")
def crear_registro_protocolo(
    payload: ProtocoloRegistroCreateRequest,
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)],
):
    try:
        return service.guardar_registro(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/protocolos/registros")
def listar_registros_protocolos(
    cliente: str = "",
    sucursal: str = "",
    tipo_protocolo: str = "",
    fecha_desde: str = "",
    fecha_hasta: str = "",
    limit: int = 300,
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)] = None,
):
    try:
        return service.listar_registros(
            cliente=cliente,
            sucursal=sucursal,
            tipo_protocolo=tipo_protocolo,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/protocolos/reportes/semanal/ejecutar")
def ejecutar_reportes_semanales_protocolos(
    forzar: bool = False,
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)] = None,
):
    try:
        return service.generar_resumenes_semanales_pendientes(forzar=forzar)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/protocolos/informes")
def listar_informes_protocolos(
    cliente: str = "",
    sucursal: str = "",
    tipo_informe: str = "",
    limit: int = 200,
    service: Annotated[ProtocolosService, Depends(get_protocolos_service)] = None,
):
    try:
        return service.listar_informes(
            cliente=cliente,
            sucursal=sucursal,
            tipo_informe=tipo_informe,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/derivaciones")
def obtener_registros_derivaciones(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_registros_derivaciones()


@app.post("/api/coordinacion/finalizar")
def finalizar_odt_coordinacion(
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        result = service.finalizar_odt_coordinacion(
            str(payload.get("odt") or ""),
            str(payload.get("observacion_final") or payload.get("observacionFinal") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail="ODT no encontrada")
    return result


@app.post("/api/coordinacion/observacion-final")
def guardar_observacion_final_coordinacion(
    payload: dict,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        result = service.actualizar_observacion_final_coordinacion(
            str(payload.get("odt") or ""),
            str(payload.get("observacion_final") or payload.get("observacionFinal") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail="ODT no encontrada")
    return result


@app.post("/api/coordinacion/enviar-correo")
def enviar_correo_coordinacion(
    payload: EnviarInformacionContactoRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        return service.registrar_envio_correo_coordinacion(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/tareas")
def registrar_tarea_manual(
    payload: TareaManualRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        codigo = service.registrar_tarea_manual(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": codigo}


@app.get("/api/tareas")
def obtener_tareas(service: Annotated[IncidenciasService, Depends(get_service)]):
    return service.obtener_registro_tareas()


@app.patch("/api/tareas/{tarea_id}")
def actualizar_tarea(
    tarea_id: int,
    columna: str,
    valor: str,
    token: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    ok = service.actualizar_celda_tarea(tarea_id, columna, valor, token)
    if not ok:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return {"ok": True}


@app.post("/api/rendiciones")
def registrar_rendicion(
    payload: RendicionRequest,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        return service.registrar_gasto(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/rendiciones/url")
def obtener_url_formulario_rendicion(request: Request):
    return {"url": str(request.base_url).rstrip("/")}


@app.get("/api/rendiciones/duplicado")
def verificar_nro_documento_duplicado(
    nro_documento: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    return {"duplicado": service.existe_nro_documento_duplicado(nro_documento)}


@app.post("/api/rendiciones/upload-boleta")
async def subir_boleta_rendicion(
    file: UploadFile = File(...),
    tecnico: str = Form(""),
    odt: str = Form(""),
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    if not file:
        raise HTTPException(status_code=400, detail="Debes adjuntar una imagen de boleta.")
    if not str(file.content_type or "").lower().startswith("image/"):
        raise HTTPException(status_code=400, detail="El archivo debe ser una imagen.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacio.")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="La imagen supera el limite de 10MB.")

    try:
        url = service.guardar_boleta_rendicion(
            content=content,
            filename=file.filename or "boleta.jpg",
            tecnico=tecnico,
            odt=odt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"url": url}


@app.get("/api/rendiciones")
def obtener_rendiciones(
    tecnico: str = "",
    pendientes: bool = False,
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    return service.obtener_rendiciones(tecnico=tecnico, pendientes_only=pendientes)


@app.patch("/api/rendiciones/{folio}")
def marcar_rendicion(
    folio: int,
    accion: str,
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    try:
        ok = service.marcar_rendicion(folio, accion)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="RendiciÃ³n no encontrada")
    return {"ok": True}


@app.get("/api/planificacion")
def obtener_planificacion_total(
    mes: int,
    anio: int,
    estado: str = "Todos",
    tecnico: str = "Todos",
    service: Annotated[IncidenciasService, Depends(get_service)] = None,
):
    return service.obtener_planificacion_total(mes, anio, estado, tecnico)


@app.get("/api/debug/db")
def debug_db(
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IncidenciasService, Depends(get_service)],
):
    out = {
        "database_url": settings.database_url,
        "db_schema_setting": settings.db_schema,
    }
    try:
        out["current_schema"] = db.execute(text("SELECT current_schema()")).scalar_one()
    except Exception as e:
        out["current_schema_error"] = str(e)
    try:
        out["catalogo_clientes_count"] = db.execute(text("SELECT COUNT(*) FROM catalogo_clientes")).scalar_one()
    except Exception as e:
        out["catalogo_clientes_count_error"] = str(e)
    try:
        out["catalogo_clientes_columns"] = [
            r[0]
            for r in db.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'catalogo_clientes'
                    ORDER BY ordinal_position
                    """
                )
            ).all()
        ]
    except Exception as e:
        out["catalogo_clientes_columns_error"] = str(e)
    try:
        out["catalogo_clientes_schemas"] = [
            r[0]
            for r in db.execute(
                text(
                    """
                    SELECT DISTINCT table_schema
                    FROM information_schema.columns
                    WHERE table_name = 'catalogo_clientes'
                      AND table_schema NOT IN ('pg_catalog', 'information_schema')
                    ORDER BY table_schema
                    """
                )
            ).all()
        ]
    except Exception as e:
        out["catalogo_clientes_schemas_error"] = str(e)
    try:
        out["catalogo_clientes_sample"] = service.obtener_catalogo_clientes()[:10]
    except Exception as e:
        out["catalogo_clientes_sample_error"] = str(e)
    return out
