from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute

from ATC.incidencias.app.main import app as incidencias_app


SKIPPED_PATHS = {
    "/api/client-notes",
    "/api/usuario-actual",
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
        app.add_event_handler("startup", startup_handler)

    for shutdown_handler in incidencias_app.router.on_shutdown:
        app.add_event_handler("shutdown", shutdown_handler)
