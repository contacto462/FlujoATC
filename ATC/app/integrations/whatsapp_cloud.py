from __future__ import annotations

from typing import Any, Iterable

import requests

from ATC.app.core.config import settings


GRAPH_API_VERSION = "v18.0"
DEFAULT_LANGUAGE = "es"
REQUEST_TIMEOUT_SECONDS = 15


class WhatsAppError(Exception):
    pass


def _api_url() -> str:
    phone_id = (settings.WA_PHONE_NUMBER_ID or "").strip()
    if not phone_id:
        raise WhatsAppError("WA_PHONE_NUMBER_ID no configurado")
    return f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_id}/messages"


def _headers() -> dict[str, str]:
    token = (settings.WA_ACCESS_TOKEN or "").strip()
    if not token:
        raise WhatsAppError("WA_ACCESS_TOKEN no configurado")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def normalize_phone(raw: str | None, *, default_country: str = "56") -> str:
    # Meta exige E.164 sin "+" ni separadores. En Chile el movil tiene 9 digitos
    # despues del 56. Si llega solo el numero local, le anteponemos el codigo pais.
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not digits:
        raise WhatsAppError("Telefono vacio")
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 9 and default_country:
        digits = f"{default_country}{digits}"
    return digits


def _post(payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        _api_url(),
        headers=_headers(),
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise WhatsAppError(f"WhatsApp error {response.status_code}: {response.text}")
    return response.json()


def send_whatsapp_message(to_phone: str, body: str) -> dict[str, Any]:
    # Texto libre. Solo entrega si el cliente escribio en las ultimas 24h.
    # Fuera de esa ventana, usar send_whatsapp_template.
    payload = {
        "messaging_product": "whatsapp",
        "to": normalize_phone(to_phone),
        "type": "text",
        "text": {"body": body, "preview_url": False},
    }
    return _post(payload)


def _build_template_components(
    *,
    body_params: Iterable[str] | None,
    header_params: Iterable[str] | None,
    buttons: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []

    header_list = list(header_params or [])
    if header_list:
        components.append(
            {
                "type": "header",
                "parameters": [{"type": "text", "text": str(value)} for value in header_list],
            }
        )

    body_list = list(body_params or [])
    if body_list:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": str(value)} for value in body_list],
            }
        )

    for index, button in enumerate(buttons or []):
        sub_type = (button.get("sub_type") or "url").strip()
        parameters = button.get("parameters") or []
        components.append(
            {
                "type": "button",
                "sub_type": sub_type,
                "index": str(button.get("index", index)),
                "parameters": list(parameters),
            }
        )

    return components


def send_whatsapp_template(
    to_phone: str,
    template_name: str,
    *,
    language: str = DEFAULT_LANGUAGE,
    body_params: Iterable[str] | None = None,
    header_params: Iterable[str] | None = None,
    buttons: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # Para mensajes fuera de la ventana de 24h. La plantilla debe estar aprobada
    # por Meta y los parametros respetar el orden de {{1}}, {{2}}, ... en la plantilla.
    if not template_name:
        raise WhatsAppError("template_name requerido")

    components = _build_template_components(
        body_params=body_params,
        header_params=header_params,
        buttons=buttons,
    )

    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": normalize_phone(to_phone),
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
        },
    }
    if components:
        payload["template"]["components"] = components

    return _post(payload)
