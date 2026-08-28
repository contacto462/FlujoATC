from __future__ import annotations

from typing import Any

import requests

from ATC.app.core.config import settings


class PiriodError(Exception):
    pass


def _api_url(path: str) -> str:
    base = (settings.piriod_api_base_url or "https://api.piriod.com").rstrip("/")
    return f"{base}{path}"


def _headers() -> dict[str, str]:
    api_key = (settings.PIRIOD_API_KEY or "").strip()
    org_id = (settings.PIRIOD_ORGANIZATION_ID or "").strip()
    if not api_key or not org_id:
        raise PiriodError("PIRIOD_API_KEY / PIRIOD_ORGANIZATION_ID no configurados en .env")
    return {
        "Authorization": f"Token {api_key}",
        "x-simple-workspace": org_id,
        "Content-Type": "application/json",
    }


def crear_cliente(
    *,
    nombre: str,
    email: str = "",
    direccion: str = "",
    telefono: str = "",
    rut: str = "",
    giro: str = "",
) -> dict[str, Any]:
    """Crea un Customer en Piriod. Lanza PiriodError si la llamada falla.

    https://piriod.readme.io/reference/create-a-customer
    """
    nombre_limpio = (nombre or "").strip()
    if not nombre_limpio:
        raise PiriodError("nombre requerido para crear cliente en Piriod")

    payload: dict[str, Any] = {
        "name": nombre_limpio,
        "country": "CL",
        "currency": "CLP",
    }
    if email:
        payload["email"] = email
    if direccion:
        payload["address"] = direccion
    if telefono:
        payload["phone"] = telefono
    if rut:
        payload["tax_id"] = rut
    if giro:
        payload["tax_settings"] = {"cl_activity_description": giro}

    try:
        response = requests.post(
            _api_url("/customers/"),
            headers=_headers(),
            json=payload,
            timeout=settings.piriod_timeout_seconds,
        )
    except requests.RequestException as exc:
        raise PiriodError(f"Error de red llamando a Piriod: {exc}") from exc

    if response.status_code >= 400:
        raise PiriodError(f"Piriod respondio {response.status_code}: {response.text[:300]}")

    return response.json()


def listar_todas_suscripciones() -> list[dict[str, Any]]:
    """Trae todas las suscripciones de la organizacion, paginando (la API
    fuerza 20 resultados por pagina sin importar el 'limit' que se pida).

    https://piriod.readme.io/reference/list-subscriptions
    """
    resultados: list[dict[str, Any]] = []
    url = _api_url("/subscriptions/")
    headers = _headers()

    while url:
        try:
            response = requests.get(url, headers=headers, timeout=settings.piriod_timeout_seconds)
        except requests.RequestException as exc:
            raise PiriodError(f"Error de red llamando a Piriod: {exc}") from exc

        if response.status_code >= 400:
            raise PiriodError(f"Piriod respondio {response.status_code}: {response.text[:300]}")

        payload = response.json()
        resultados.extend(payload.get("results") or [])
        siguiente = payload.get("next")
        # La API a veces devuelve el "next" en http:// aunque la llamada
        # original fue https:// — se fuerza https para no salir del canal
        # cifrado en la siguiente pagina.
        url = siguiente.replace("http://", "https://", 1) if siguiente else None

    return resultados
