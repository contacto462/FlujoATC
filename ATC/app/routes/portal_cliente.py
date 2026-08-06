from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from ATC.app.core.config import settings
from ATC.app.core.db import get_db, get_incidencias_db
from ATC.app.core.security import create_access_token, hash_password, verify_password
from ATC.app.models.incidencias import (
    ClienteBBDD,
    SucursalBBDD,
    SucursalContactoEmergencia,
    SucursalPersonaAutorizada,
)
from ATC.app.models.portal_cliente import PortalPersonaLogin
from ATC.app.services.user_service import UserService

router = APIRouter(tags=["portal_cliente"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
# "access_token" es intencionalmente la MISMA cookie que usa el login de
# staff (ATC/app/routes/web.py): una cuenta "empresa" (User.cliente_rut) es
# la misma identidad/sesion sin importar por cual puerta entro (/login o
# /portal-cliente), asi que comparten cookie a proposito.
#
# El login "persona" (RUT + clave, tabla PortalPersonaLogin) es una
# identidad totalmente aparte, sin relacion con la tabla de staff — antes
# usaba esta MISMA cookie, y eso hacia que loguearse como persona en
# /portal-cliente sobreescribiera (y cerrara) la sesion de un staff logueado
# en el mismo navegador. Se le da su propia cookie para que ambas sesiones
# convivan sin pisarse (pedido explicito, jul 2026).
COOKIE_NAME = "access_token"
PERSONA_COOKIE_NAME = "portal_persona_token"
# Token intermedio: la persona ya puso su clave correcta pero esta
# autorizada en 2+ empresas distintas, asi que falta el paso de elegir cual
# ver antes de considerarla autenticada de verdad (pedido explicito, jul 2026).
PERSONA_PENDIENTE_KIND = "persona_pendiente"

# Duracion propia de la sesion del portal cliente (distinta de JWT_EXPIRES_MIN,
# que es la del login de staff): 6 horas, luego vuelve al login del portal —
# pedido explicito, jul 2026.
PORTAL_SESSION_MINUTES = 6 * 60


def _normalizar_rut(rut: str) -> str:
    return re.sub(r"[^0-9K]", "", str(rut or "").upper())


def _clave_default_desde_rut(rut_norm: str) -> str:
    cuerpo = rut_norm[:-1] if len(rut_norm) > 1 else rut_norm
    return cuerpo[:5]


def _nombre_visible(nombre: str | None) -> str:
    """Nombre para mostrar en el portal: sin anotaciones entre parentesis
    (ej. "Luis Gonzalez Osorio (apertura/cierre)" -> "Luis Gonzalez")
    y limitado a las dos primeras palabras."""
    limpio = re.sub(r"\([^)]*\)", "", str(nombre or "")).strip()
    palabras = limpio.split()
    return " ".join(palabras[:2])


def _resolver_empresas_por_rut(db: Session, rut_norm: str) -> list[dict]:
    """Busca `rut_norm` entre TODAS las personas autorizadas y contactos de
    emergencia (de cualquier sucursal/empresa) comparando RUT normalizado.
    Devuelve una entrada por EMPRESA distinta en la que la persona figura
    (deduplicado por cliente_rut — estar en varias sucursales de la MISMA
    empresa no cuenta como "mas de una empresa")."""
    empresas: dict[str, dict] = {}

    filas = (
        db.query(
            SucursalPersonaAutorizada.rut,
            SucursalPersonaAutorizada.nombre,
            SucursalBBDD.rut,
            SucursalBBDD.nombre_empresa,
        )
        .join(SucursalBBDD, SucursalBBDD.id == SucursalPersonaAutorizada.sucursal_id)
        .filter(SucursalPersonaAutorizada.rut.isnot(None))
        .all()
    )
    for rut, nombre, cliente_rut, nombre_empresa in filas:
        if cliente_rut and _normalizar_rut(rut) == rut_norm and cliente_rut not in empresas:
            empresas[cliente_rut] = {
                "cliente_rut": cliente_rut,
                "nombre_persona": nombre,
                "nombre_empresa": nombre_empresa or cliente_rut,
            }

    filas = (
        db.query(
            SucursalContactoEmergencia.rut,
            SucursalContactoEmergencia.nombre,
            SucursalBBDD.rut,
            SucursalBBDD.nombre_empresa,
        )
        .join(SucursalBBDD, SucursalBBDD.id == SucursalContactoEmergencia.sucursal_id)
        .filter(SucursalContactoEmergencia.rut.isnot(None))
        .all()
    )
    for rut, nombre, cliente_rut, nombre_empresa in filas:
        if cliente_rut and _normalizar_rut(rut) == rut_norm and cliente_rut not in empresas:
            empresas[cliente_rut] = {
                "cliente_rut": cliente_rut,
                "nombre_persona": nombre,
                "nombre_empresa": nombre_empresa or cliente_rut,
            }

    return list(empresas.values())


@dataclass
class PortalIdentity:
    cliente_rut: str
    display_name: str
    kind: str  # "empresa" | "persona"
    must_change_password: bool = False
    persona_rut: str | None = None  # solo para kind == "persona"


def _decode_portal_token(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
    except JWTError:
        return None


def _current_cliente_identity(request: Request, db: Session) -> PortalIdentity | None:
    # Cookie propia del login "persona" primero — no depende de la cookie
    # compartida con staff/empresa.
    persona_payload = _decode_portal_token(request.cookies.get(PERSONA_COOKIE_NAME))
    if persona_payload and persona_payload.get("kind") == "persona":
        sub = str(persona_payload.get("sub") or "")
        if sub:
            login = db.query(PortalPersonaLogin).filter(PortalPersonaLogin.rut == sub).first()
            if login and login.is_active and login.cliente_rut:
                return PortalIdentity(
                    cliente_rut=login.cliente_rut,
                    display_name=_nombre_visible(login.nombre) or login.rut,
                    kind="persona",
                    must_change_password=not login.password_changed,
                    persona_rut=login.rut,
                )

    # Cookie compartida con el login de staff, para cuentas "empresa"
    # (User.cliente_rut) — single sign-on intencional.
    payload = _decode_portal_token(request.cookies.get(COOKIE_NAME))
    if not payload:
        return None
    sub = str(payload.get("sub") or "")
    if not sub:
        return None

    user = UserService.find_by_login(db, sub)
    if not user or not user.is_active or not user.cliente_rut:
        return None
    return PortalIdentity(cliente_rut=user.cliente_rut, display_name=user.name, kind="empresa")


def get_current_user_portal_cliente(
    request: Request,
    db: Session = Depends(get_db),
) -> PortalIdentity:
    identity = _current_cliente_identity(request, db)
    if not identity:
        raise HTTPException(status_code=401, detail="No autenticado")
    return identity


def get_portal_identity_activa(
    identity: PortalIdentity = Depends(get_current_user_portal_cliente),
) -> PortalIdentity:
    if identity.must_change_password:
        raise HTTPException(status_code=403, detail="Debes cambiar tu clave antes de continuar.")
    return identity


def _set_portal_cookie(resp, token: str, kind: str = "empresa"):
    resp.set_cookie(
        key=PERSONA_COOKIE_NAME if kind == "persona" else COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=PORTAL_SESSION_MINUTES * 60,
    )
    return resp


def _sucursal_del_cliente(incidencias_db: Session, sucursal_id: int, current_user: PortalIdentity) -> SucursalBBDD:
    sucursal = incidencias_db.get(SucursalBBDD, sucursal_id)
    if not sucursal or sucursal.rut != current_user.cliente_rut:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")
    return sucursal


# ======================================================
# Login / sesión
# ======================================================

class PortalClienteLoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


def _iniciar_seleccion_empresa(rut_norm: str) -> JSONResponse:
    token = create_access_token({"sub": rut_norm, "kind": PERSONA_PENDIENTE_KIND}, expires_minutes=PORTAL_SESSION_MINUTES)
    resp = JSONResponse({"ok": True, "redirect": "/portal-cliente/seleccionar-empresa"})
    return _set_portal_cookie(resp, token, kind="persona")


@router.post("/api/portal-cliente/login")
def portal_cliente_login(payload: PortalClienteLoginRequest, db: Session = Depends(get_db)):
    from ATC.app.core.rate_limit import is_locked_out, record_failure, record_success

    identificador = payload.username.strip()
    if is_locked_out(identificador):
        raise HTTPException(status_code=429, detail="Demasiados intentos fallidos. Espera unos minutos antes de volver a intentar.")

    # 1) Login de empresa (usuario/clave creados a mano para la cuenta general).
    user = UserService.authenticate(db, payload.username.strip(), payload.password)
    if user and user.is_active and user.cliente_rut:
        record_success(identificador)
        token = create_access_token({"sub": user.username, "kind": "empresa"}, expires_minutes=PORTAL_SESSION_MINUTES)
        resp = JSONResponse({"ok": True, "redirect": "/portal-cliente"})
        return _set_portal_cookie(resp, token)

    # 2) Login individual por RUT (persona autorizada / contacto de emergencia).
    rut_norm = _normalizar_rut(payload.username)
    if rut_norm:
        login = db.query(PortalPersonaLogin).filter(PortalPersonaLogin.rut == rut_norm).first()
        if login:
            if login.is_active and verify_password(payload.password, login.hashed_password):
                # Si la persona figura en 2+ empresas distintas, agrega el
                # paso de elegir cual ver antes de dejarla entrar (pedido
                # explicito, jul 2026). Si solo esta en 0 o 1, sin cambios.
                record_success(identificador)
                empresas = _resolver_empresas_por_rut(db, rut_norm)
                if len(empresas) >= 2:
                    return _iniciar_seleccion_empresa(rut_norm)
                token = create_access_token({"sub": rut_norm, "kind": "persona"}, expires_minutes=PORTAL_SESSION_MINUTES)
                redirect = "/portal-cliente" if login.password_changed else "/portal-cliente/cambiar-clave"
                resp = JSONResponse({"ok": True, "redirect": redirect})
                return _set_portal_cookie(resp, token, kind="persona")
        else:
            # Primer ingreso: debe existir como persona autorizada o contacto
            # de emergencia, y la clave debe ser los primeros 5 digitos del RUT.
            empresas = _resolver_empresas_por_rut(db, rut_norm)
            if empresas and payload.password == _clave_default_desde_rut(rut_norm):
                primera = empresas[0]
                multiples = len(empresas) >= 2
                nuevo = PortalPersonaLogin(
                    rut=rut_norm,
                    nombre=primera["nombre_persona"],
                    hashed_password=hash_password(payload.password),
                    password_changed=False,
                    is_active=True,
                    cliente_rut=None if multiples else primera["cliente_rut"],
                )
                db.add(nuevo)
                db.commit()
                record_success(identificador)
                if multiples:
                    return _iniciar_seleccion_empresa(rut_norm)
                token = create_access_token({"sub": rut_norm, "kind": "persona"}, expires_minutes=PORTAL_SESSION_MINUTES)
                resp = JSONResponse({"ok": True, "redirect": "/portal-cliente/cambiar-clave"})
                return _set_portal_cookie(resp, token, kind="persona")

    record_failure(identificador)
    raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")


class SeleccionarEmpresaRequest(BaseModel):
    cliente_rut: str = Field(min_length=1)


@router.get("/portal-cliente/seleccionar-empresa", response_class=HTMLResponse)
def portal_cliente_seleccionar_empresa_page(request: Request, db: Session = Depends(get_db)):
    pending = _decode_portal_token(request.cookies.get(PERSONA_COOKIE_NAME))
    rut_norm = str(pending.get("sub") or "") if pending and pending.get("kind") == PERSONA_PENDIENTE_KIND else ""
    if not rut_norm:
        return RedirectResponse(url="/portal-cliente", status_code=303)

    empresas = _resolver_empresas_por_rut(db, rut_norm)
    if len(empresas) < 2:
        # Cambio de datos entre el login y este paso (ya no aplica) — reintenta.
        return RedirectResponse(url="/portal-cliente", status_code=303)

    login = db.query(PortalPersonaLogin).filter(PortalPersonaLogin.rut == rut_norm).first()
    return templates.TemplateResponse(
        request,
        "portal_cliente_seleccionar_empresa.html",
        {
            "request": request,
            "display_name": _nombre_visible(login.nombre) if login and login.nombre else "",
            "empresas": empresas,
        },
    )


@router.post("/api/portal-cliente/seleccionar-empresa")
def portal_cliente_seleccionar_empresa(
    payload: SeleccionarEmpresaRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    pending = _decode_portal_token(request.cookies.get(PERSONA_COOKIE_NAME))
    if not pending or pending.get("kind") != PERSONA_PENDIENTE_KIND:
        raise HTTPException(status_code=401, detail="Sesion no encontrada. Vuelve a iniciar sesion.")
    rut_norm = str(pending.get("sub") or "")
    if not rut_norm:
        raise HTTPException(status_code=401, detail="Sesion no encontrada. Vuelve a iniciar sesion.")

    empresas = _resolver_empresas_por_rut(db, rut_norm)
    cliente_rut_elegido = str(payload.cliente_rut or "").strip()
    if cliente_rut_elegido not in {e["cliente_rut"] for e in empresas}:
        raise HTTPException(status_code=400, detail="Empresa invalida.")

    login = db.query(PortalPersonaLogin).filter(PortalPersonaLogin.rut == rut_norm).first()
    if not login:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")
    login.cliente_rut = cliente_rut_elegido
    db.commit()

    token = create_access_token({"sub": rut_norm, "kind": "persona"}, expires_minutes=PORTAL_SESSION_MINUTES)
    redirect = "/portal-cliente" if login.password_changed else "/portal-cliente/cambiar-clave"
    resp = JSONResponse({"ok": True, "redirect": redirect})
    return _set_portal_cookie(resp, token, kind="persona")


class CambiarClaveRequest(BaseModel):
    password: str = Field(min_length=4)


@router.post("/api/portal-cliente/cambiar-clave")
def portal_cliente_cambiar_clave(
    payload: CambiarClaveRequest,
    db: Session = Depends(get_db),
    identity: PortalIdentity = Depends(get_current_user_portal_cliente),
):
    if identity.kind != "persona" or not identity.persona_rut:
        raise HTTPException(status_code=400, detail="Esta cuenta no requiere cambio de clave.")
    login = db.query(PortalPersonaLogin).filter(PortalPersonaLogin.rut == identity.persona_rut).first()
    if not login:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")
    if verify_password(payload.password, login.hashed_password):
        raise HTTPException(status_code=400, detail="La nueva clave no puede ser igual a la actual.")
    login.hashed_password = hash_password(payload.password)
    login.password_changed = True
    db.commit()
    return {"ok": True}


@router.get("/portal-cliente/logout")
def portal_cliente_logout(request: Request):
    resp = RedirectResponse(url="/portal-cliente", status_code=303)
    # Solo borra la cookie propia de "persona"; nunca la compartida con
    # staff/empresa salvo que la sesion actual sea justamente esa (evita
    # cerrar por accidente la sesion de otro usuario en el mismo navegador).
    if request.cookies.get(PERSONA_COOKIE_NAME):
        resp.delete_cookie(PERSONA_COOKIE_NAME)
    else:
        resp.delete_cookie(COOKIE_NAME)
    return resp


@router.get("/portal-cliente/cambiar-clave", response_class=HTMLResponse)
def portal_cliente_cambiar_clave_page(request: Request, db: Session = Depends(get_db)):
    identity = _current_cliente_identity(request, db)
    if not identity:
        return templates.TemplateResponse(request, "portal_cliente_login.html", {"request": request})
    if identity.kind != "persona" or not identity.must_change_password:
        return RedirectResponse(url="/portal-cliente", status_code=303)
    return templates.TemplateResponse(
        request,
        "portal_cliente_cambiar_clave.html",
        {"request": request, "display_name": identity.display_name},
    )


@router.get("/portal-cliente", response_class=HTMLResponse)
def portal_cliente_page(request: Request, db: Session = Depends(get_db)):
    identity = _current_cliente_identity(request, db)
    if not identity:
        return templates.TemplateResponse(request, "portal_cliente_login.html", {"request": request})
    if identity.must_change_password:
        return RedirectResponse(url="/portal-cliente/cambiar-clave", status_code=303)
    cliente = db.query(ClienteBBDD).filter(ClienteBBDD.rut == identity.cliente_rut).first()
    multi_empresa = (
        identity.kind == "persona"
        and len(_resolver_empresas_por_rut(db, identity.persona_rut or "")) >= 2
    )
    return templates.TemplateResponse(
        request,
        "portal_cliente.html",
        {
            "request": request,
            "cliente_nombre": cliente.cliente if cliente else "",
            "cliente_rut": identity.cliente_rut,
            "display_name": identity.display_name,
            "is_persona": identity.kind == "persona",
            "multi_empresa": multi_empresa,
        },
    )


@router.get("/portal-cliente/cambiar-empresa")
def portal_cliente_cambiar_empresa(
    identity: PortalIdentity = Depends(get_current_user_portal_cliente),
):
    # Vuelve a la pantalla de seleccion de empresa sin pedir clave de nuevo
    # (pedido explicito, jul 2026) — solo aplica a personas (RUT), las
    # cuentas "empresa" no tienen mas de una empresa para elegir.
    if identity.kind != "persona" or not identity.persona_rut:
        return RedirectResponse(url="/portal-cliente", status_code=303)
    token = create_access_token({"sub": identity.persona_rut, "kind": PERSONA_PENDIENTE_KIND}, expires_minutes=PORTAL_SESSION_MINUTES)
    resp = RedirectResponse(url="/portal-cliente/seleccionar-empresa", status_code=303)
    return _set_portal_cookie(resp, token, kind="persona")


# ======================================================
# Sucursales del cliente
# ======================================================

@router.get("/api/portal-cliente/sucursales")
def api_portal_cliente_sucursales(
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: PortalIdentity = Depends(get_portal_identity_activa),
):
    rows = (
        incidencias_db.query(SucursalBBDD)
        .filter(SucursalBBDD.rut == current_user.cliente_rut)
        .order_by(SucursalBBDD.nombre_sucursal)
        .all()
    )
    return [
        {
            "id": r.id,
            "nombre_sucursal": r.nombre_sucursal or "",
            "direccion_sucursal": r.direccion_sucursal or "",
            "comuna": r.comuna or "",
            "region": r.region or "",
        }
        for r in rows
    ]


# ======================================================
# Personas autorizadas (crear / editar / deshabilitar)
# ======================================================

class PersonaAutorizadaClienteCreate(BaseModel):
    sucursal_id: int
    nombre: str = ""
    rut: str = ""
    telefono: str = ""
    email: str = ""
    clave_verde: str = ""
    clave_roja: str = ""


class PersonaAutorizadaClienteUpdate(BaseModel):
    nombre: str = ""
    rut: str = ""
    telefono: str = ""
    email: str = ""
    clave_verde: str = ""
    clave_roja: str = ""


def _persona_autorizada_dict(r: SucursalPersonaAutorizada) -> dict:
    return {
        "id": r.id,
        "sucursal_id": r.sucursal_id,
        "nombre": r.nombre or "",
        "rut": r.rut or "",
        "telefono": r.telefono or "",
        "email": r.email or "",
        "clave_verde": r.clave_verde or "",
        "clave_roja": r.clave_roja or "",
        "habilitado": bool(r.habilitado),
    }


def _persona_autorizada_del_cliente(
    incidencias_db: Session, persona_id: int, current_user: PortalIdentity
) -> SucursalPersonaAutorizada:
    row = incidencias_db.get(SucursalPersonaAutorizada, persona_id)
    if not row:
        raise HTTPException(status_code=404, detail="Persona no encontrada.")
    _sucursal_del_cliente(incidencias_db, row.sucursal_id, current_user)
    return row


@router.get("/api/portal-cliente/sucursal/{sucursal_id}/personas-autorizadas")
def api_portal_cliente_list_personas(
    sucursal_id: int,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: PortalIdentity = Depends(get_portal_identity_activa),
):
    _sucursal_del_cliente(incidencias_db, sucursal_id, current_user)
    rows = (
        incidencias_db.query(SucursalPersonaAutorizada)
        .filter(SucursalPersonaAutorizada.sucursal_id == sucursal_id)
        .order_by(SucursalPersonaAutorizada.nombre)
        .all()
    )
    return [_persona_autorizada_dict(r) for r in rows]


@router.post("/api/portal-cliente/personas-autorizadas")
def api_portal_cliente_create_persona(
    payload: PersonaAutorizadaClienteCreate,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: PortalIdentity = Depends(get_portal_identity_activa),
):
    _sucursal_del_cliente(incidencias_db, payload.sucursal_id, current_user)
    nueva = SucursalPersonaAutorizada(
        sucursal_id=payload.sucursal_id,
        nombre=payload.nombre.strip() or None,
        rut=payload.rut.strip() or None,
        telefono=payload.telefono.strip() or None,
        email=payload.email.strip() or None,
        clave_verde=payload.clave_verde.strip() or None,
        clave_roja=payload.clave_roja.strip() or None,
        habilitado=True,
    )
    incidencias_db.add(nueva)
    incidencias_db.commit()
    incidencias_db.refresh(nueva)
    return {"ok": True, "id": nueva.id}


@router.put("/api/portal-cliente/personas-autorizadas/{persona_id}")
def api_portal_cliente_update_persona(
    persona_id: int,
    payload: PersonaAutorizadaClienteUpdate,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: PortalIdentity = Depends(get_portal_identity_activa),
):
    row = _persona_autorizada_del_cliente(incidencias_db, persona_id, current_user)
    row.nombre = payload.nombre.strip() or None
    row.rut = payload.rut.strip() or None
    row.telefono = payload.telefono.strip() or None
    row.email = payload.email.strip() or None
    row.clave_verde = payload.clave_verde.strip() or None
    row.clave_roja = payload.clave_roja.strip() or None
    incidencias_db.commit()
    return {"ok": True}


@router.patch("/api/portal-cliente/personas-autorizadas/{persona_id}/toggle")
def api_portal_cliente_toggle_persona(
    persona_id: int,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: PortalIdentity = Depends(get_portal_identity_activa),
):
    row = _persona_autorizada_del_cliente(incidencias_db, persona_id, current_user)
    row.habilitado = not bool(row.habilitado)
    incidencias_db.commit()
    return {"ok": True, "habilitado": bool(row.habilitado)}


@router.delete("/api/portal-cliente/personas-autorizadas/{persona_id}")
def api_portal_cliente_delete_persona(
    persona_id: int,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: PortalIdentity = Depends(get_portal_identity_activa),
):
    row = _persona_autorizada_del_cliente(incidencias_db, persona_id, current_user)
    incidencias_db.delete(row)
    incidencias_db.commit()
    return {"ok": True}


# ======================================================
# Contactos de emergencia (crear / editar / eliminar)
# ======================================================

class ContactoEmergenciaCreate(BaseModel):
    sucursal_id: int
    nombre: str = ""
    rut: str = ""
    telefono: str = ""
    email: str = ""


class ContactoEmergenciaUpdate(BaseModel):
    nombre: str = ""
    rut: str = ""
    telefono: str = ""
    email: str = ""


def _ensure_contacto_emergencia_orden_column(db: Session) -> None:
    """Agrega columna orden a sucursal_contactos_emergencia si no existe."""
    try:
        db.execute(text("""
            IF NOT EXISTS (
                SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = 'sucursal_contactos_emergencia'
                  AND COLUMN_NAME = 'orden'
            )
            BEGIN
                ALTER TABLE sucursal_contactos_emergencia ADD orden INT NULL
            END
        """))
        db.commit()
    except Exception:
        db.rollback()


def _backfill_orden_contactos(db: Session, sucursal_id: int) -> None:
    """A los contactos de esta sucursal que aun no tengan orden asignado, les da
    uno secuencial (por nombre, que era el orden previo antes de que existiera
    esta columna) para que el drag-and-drop tenga un punto de partida estable."""
    sin_orden = (
        db.query(SucursalContactoEmergencia)
        .filter(
            SucursalContactoEmergencia.sucursal_id == sucursal_id,
            SucursalContactoEmergencia.orden.is_(None),
        )
        .order_by(SucursalContactoEmergencia.nombre, SucursalContactoEmergencia.id)
        .all()
    )
    if not sin_orden:
        return
    siguiente = (
        db.query(SucursalContactoEmergencia)
        .filter(SucursalContactoEmergencia.sucursal_id == sucursal_id, SucursalContactoEmergencia.orden.isnot(None))
        .count()
    )
    for row in sin_orden:
        siguiente += 1
        row.orden = siguiente
    db.commit()


def _contacto_emergencia_dict(r: SucursalContactoEmergencia) -> dict:
    return {
        "id": r.id,
        "sucursal_id": r.sucursal_id,
        "nombre": r.nombre or "",
        "rut": r.rut or "",
        "telefono": r.telefono or "",
        "orden": r.orden,
        "email": r.email or "",
    }


def _contacto_emergencia_del_cliente(
    incidencias_db: Session, contacto_id: int, current_user: PortalIdentity
) -> SucursalContactoEmergencia:
    row = incidencias_db.get(SucursalContactoEmergencia, contacto_id)
    if not row:
        raise HTTPException(status_code=404, detail="Contacto no encontrado.")
    _sucursal_del_cliente(incidencias_db, row.sucursal_id, current_user)
    return row


@router.get("/api/portal-cliente/sucursal/{sucursal_id}/contactos-emergencia")
def api_portal_cliente_list_contactos(
    sucursal_id: int,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: PortalIdentity = Depends(get_portal_identity_activa),
):
    _sucursal_del_cliente(incidencias_db, sucursal_id, current_user)
    _ensure_contacto_emergencia_orden_column(incidencias_db)
    _backfill_orden_contactos(incidencias_db, sucursal_id)
    rows = (
        incidencias_db.query(SucursalContactoEmergencia)
        .filter(SucursalContactoEmergencia.sucursal_id == sucursal_id)
        .order_by(SucursalContactoEmergencia.orden, SucursalContactoEmergencia.id)
        .all()
    )
    return [_contacto_emergencia_dict(r) for r in rows]


@router.post("/api/portal-cliente/contactos-emergencia")
def api_portal_cliente_create_contacto(
    payload: ContactoEmergenciaCreate,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: PortalIdentity = Depends(get_portal_identity_activa),
):
    _sucursal_del_cliente(incidencias_db, payload.sucursal_id, current_user)
    _ensure_contacto_emergencia_orden_column(incidencias_db)
    _backfill_orden_contactos(incidencias_db, payload.sucursal_id)
    max_orden = (
        incidencias_db.query(SucursalContactoEmergencia.orden)
        .filter(SucursalContactoEmergencia.sucursal_id == payload.sucursal_id)
        .order_by(SucursalContactoEmergencia.orden.desc())
        .first()
    )
    nuevo = SucursalContactoEmergencia(
        sucursal_id=payload.sucursal_id,
        nombre=payload.nombre.strip() or None,
        rut=payload.rut.strip() or None,
        telefono=payload.telefono.strip() or None,
        email=payload.email.strip() or None,
        orden=(max_orden[0] if max_orden and max_orden[0] is not None else 0) + 1,
    )
    incidencias_db.add(nuevo)
    incidencias_db.commit()
    incidencias_db.refresh(nuevo)
    return {"ok": True, "id": nuevo.id}


@router.put("/api/portal-cliente/contactos-emergencia/{contacto_id}")
def api_portal_cliente_update_contacto(
    contacto_id: int,
    payload: ContactoEmergenciaUpdate,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: PortalIdentity = Depends(get_portal_identity_activa),
):
    row = _contacto_emergencia_del_cliente(incidencias_db, contacto_id, current_user)
    row.nombre = payload.nombre.strip() or None
    row.rut = payload.rut.strip() or None
    row.telefono = payload.telefono.strip() or None
    row.email = payload.email.strip() or None
    incidencias_db.commit()
    return {"ok": True}


@router.delete("/api/portal-cliente/contactos-emergencia/{contacto_id}")
def api_portal_cliente_delete_contacto(
    contacto_id: int,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: PortalIdentity = Depends(get_portal_identity_activa),
):
    row = _contacto_emergencia_del_cliente(incidencias_db, contacto_id, current_user)
    incidencias_db.delete(row)
    incidencias_db.commit()
    return {"ok": True}


class ContactosEmergenciaReordenar(BaseModel):
    sucursal_id: int
    orden_ids: list[int]


@router.patch("/api/portal-cliente/contactos-emergencia/reordenar")
def api_portal_cliente_reordenar_contactos(
    payload: ContactosEmergenciaReordenar,
    incidencias_db: Session = Depends(get_incidencias_db),
    current_user: PortalIdentity = Depends(get_portal_identity_activa),
):
    _sucursal_del_cliente(incidencias_db, payload.sucursal_id, current_user)
    _ensure_contacto_emergencia_orden_column(incidencias_db)
    rows = {
        r.id: r
        for r in incidencias_db.query(SucursalContactoEmergencia)
        .filter(SucursalContactoEmergencia.sucursal_id == payload.sucursal_id)
        .all()
    }
    if set(payload.orden_ids) != set(rows.keys()):
        raise HTTPException(status_code=400, detail="La lista de orden no coincide con los contactos de esta sucursal.")
    for posicion, contacto_id in enumerate(payload.orden_ids, start=1):
        rows[contacto_id].orden = posicion
    incidencias_db.commit()
    return {"ok": True}
