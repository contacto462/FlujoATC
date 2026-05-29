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

    add_event_handler = getattr(app, "add_event_handler", None)
    if callable(add_event_handler):
        for startup_handler in incidencias_app.router.on_startup:
            add_event_handler("startup", startup_handler)
        for shutdown_handler in incidencias_app.router.on_shutdown:
            add_event_handler("shutdown", shutdown_handler)
    else:
        # Compatibilidad con versiones donde se prioriza lifespan y no existe add_event_handler.
        app.router.on_startup.extend(incidencias_app.router.on_startup)
        app.router.on_shutdown.extend(incidencias_app.router.on_shutdown)
