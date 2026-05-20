from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import parse_qs

from starlette.responses import RedirectResponse
from starlette.types import Receive, Scope, Send


ROOT_DIR = Path(__file__).resolve().parent
ATC_DIR = ROOT_DIR / "ATC"
INC_DIR = ROOT_DIR / "Incidencias"

# ATC has legacy code that reads/writes "uploads" relative to cwd.
# Running the unified app from ATC keeps those paths stable.
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
os.chdir(ATC_DIR)

from ATC.app.main import app as atc_app  # noqa: E402
from Incidencias.app.main import app as incidencias_app  # noqa: E402


INC_STATIC_DIR = INC_DIR / "app" / "static"
INC_UPLOADS_DIR = INC_DIR / "uploads"
ATC_STATIC_DIR = ATC_DIR / "static"
ATC_UPLOADS_DIR = ATC_DIR / "uploads"


INCIDENCIAS_EXACT_PATHS = {
    "/",
    "/api/login",
    "/api/logout",
    "/api/usuario-actual",
    "/api/login/usuarios",
    "/api/catalogo-clientes",
    "/api/formulario",
    "/api/derivaciones",
    "/api/planificacion",
    "/api/debug/db",
}

INCIDENCIAS_PREFIXES = (
    "/venta",
    "/servicio",
    "/api/venta",
    "/api/listas/",
    "/api/sucursal/",
    "/api/tecnicos/",
    "/api/mantencion/",
    "/api/contactos/",
    "/api/contacto-cliente/",
    "/api/clientes-soporte",
    "/api/sync/soporte",
    "/api/tareas",
    "/api/protocolos",
    "/api/coordinacion/",
    "/api/rendiciones",
    "/api/servicio/",
)

INCIDENCIAS_INCIDENCIAS_PATHS = {
    "/api/incidencias/puesto",
    "/api/incidencias/coordinacion",
    "/api/incidencias/imagenes",
    "/api/incidencias/imagenes-tabla",
    "/api/incidencias/upload-image-tabla",
    "/api/incidencias/nueva",
    "/api/incidencias/multiples",
    "/api/incidencias/cerrar",
    "/api/incidencias/finalizar-completo",
    "/api/incidencias/cierre-mantencion",
    "/api/incidencias/en-proceso",
    "/api/incidencias/derivar-tecnico",
    "/api/incidencias/editar-tabla",
    "/api/incidencias/cerrar-encargado",
}


def _path_exists(base: Path, request_path: str, mount: str) -> bool:
    rel = request_path.removeprefix(mount).lstrip("/")
    if not rel:
        return False
    try:
        candidate = (base / rel).resolve()
        return candidate.is_file() and candidate.is_relative_to(base.resolve())
    except Exception:
        return False


def _has_query_param(scope: Scope, name: str) -> bool:
    raw = (scope.get("query_string") or b"").decode("utf-8", errors="ignore")
    return name in parse_qs(raw, keep_blank_values=True)


def _has_cookie(scope: Scope, name: str) -> bool:
    for header_name, header_value in scope.get("headers") or []:
        if header_name.lower() != b"cookie":
            continue
        cookie_text = header_value.decode("latin-1", errors="ignore")
        return any(part.strip().startswith(f"{name}=") for part in cookie_text.split(";"))
    return False


def _target_for_scope(scope: Scope):
    path = scope.get("path") or "/"

    if path == "/login":
        return "unified-login"

    if path.startswith("/shared-static"):
        return incidencias_app

    if path.startswith("/static"):
        if _path_exists(INC_STATIC_DIR, path, "/static"):
            return incidencias_app
        return atc_app

    if path.startswith("/uploads"):
        if _path_exists(INC_UPLOADS_DIR, path, "/uploads"):
            return incidencias_app
        return atc_app

    if path == "/api/client-notes":
        if _has_query_param(scope, "token") or not _has_cookie(scope, "access_token"):
            return incidencias_app
        return atc_app

    if path in INCIDENCIAS_EXACT_PATHS:
        return incidencias_app

    if path in INCIDENCIAS_INCIDENCIAS_PATHS:
        return incidencias_app

    if path.startswith("/api/registros") and path != "/api/registros/tabla":
        return incidencias_app

    if any(path.startswith(prefix) for prefix in INCIDENCIAS_PREFIXES):
        return incidencias_app

    return atc_app


class UnifiedATCApplication:
    def __init__(self):
        self.atc = atc_app
        self.incidencias = incidencias_app
        self._started = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return

        target = _target_for_scope(scope)
        if target == "unified-login":
            response = RedirectResponse(url="/?form=login&next=auto", status_code=303)
            await response(scope, receive, send)
            return

        await target(scope, receive, send)

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await self.atc.router.startup()
                    await self.incidencias.router.startup()
                    self._started = True
                except Exception as exc:
                    await send({"type": "lifespan.startup.failed", "message": str(exc)})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                try:
                    if self._started:
                        await self.incidencias.router.shutdown()
                        await self.atc.router.shutdown()
                except Exception as exc:
                    await send({"type": "lifespan.shutdown.failed", "message": str(exc)})
                    return
                await send({"type": "lifespan.shutdown.complete"})
                return


app = UnifiedATCApplication()
