from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute

from ATC.incidencias.app.main import app as incidencias_app


SKIPPED_PATHS = {
    "/api/client-notes",
    "/api/usuario-actual",
    "/sso/login",
}


def register_incidencias_module(app: FastAPI) -> None:
    """Register Incidencias/Venta routes and lifecycle hooks in the unified app."""

    for route in incidencias_app.router.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in SKIPPED_PATHS:
            continue
        app.router.routes.append(route)

    for startup_handler in incidencias_app.router.on_startup:
        app.router.on_startup.append(startup_handler)

    for shutdown_handler in incidencias_app.router.on_shutdown:
        app.router.on_shutdown.append(shutdown_handler)
