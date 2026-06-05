"""Firmas de correo por usuario para las respuestas de soporte.

La firma se inserta automaticamente en el editor de respuesta del ticket
segun el usuario que tiene la sesion iniciada. Es texto/HTML limpio (sin
imagenes) para que se vea bien en cualquier cliente de correo.
"""

from __future__ import annotations

import html


# Telefono de central comun a todos.
OFFICE_PHONE = "600 828 8000"

# Direcciones comunes (una por linea en la firma).
ADDRESSES = (
    "1 Oriente 1180, piso 3, Vina del Mar",
    "Av. La Dehesa 1201 Of 523, Lo Barnechea",
)

# Datos por usuario, indexados por nombre en minusculas.
# display: como aparece el nombre en la firma.
# title:   cargo.
# mobile:  celular (None si no se muestra).
_SIGNATURES: dict[str, dict[str, str | None]] = {
    "ronald montilla": {
        "display": "Ronald Montilla A.",
        "title": "Jefe Soporte Tecnico",
        "mobile": "+56 9 9826 9667",
    },
    "fernando lubiano": {
        "display": "Fernando Lubiano.",
        "title": "Analista de Operaciones",
        "mobile": None,
    },
    "felipe mora": {
        "display": "Felipe Mora.",
        "title": "Soporte Tecnico",
        "mobile": "+56 98269667",
    },
    "julissa mella": {
        "display": "Julissa Mella.",
        "title": "Soporte Tecnico",
        "mobile": "+56 98269667",
    },
    "antonio bahamondes": {
        "display": "Antonio Bahamondes.",
        "title": "Soporte Tecnico",
        "mobile": "+56 98269667",
    },
    "sthefan leal": {
        "display": "Sthefan Leal.",
        "title": "Soporte Tecnico",
        "mobile": "+56 98269667",
    },
}


def _resolve_record(name: str) -> dict[str, str | None]:
    key = (name or "").strip().lower()
    record = _SIGNATURES.get(key)
    if record:
        return record

    # Fallback para cualquier otro usuario: usa su propio nombre y un
    # cargo generico, sin celular personal.
    display = f"{name.strip()}." if name and name.strip() else "Soporte ATC"
    return {"display": display, "title": "Soporte Tecnico", "mobile": None}


def signature_html_for_user(user) -> str:
    """Devuelve el HTML de la firma para el usuario dado."""
    name = (getattr(user, "name", "") or "").strip()
    record = _resolve_record(name)

    display = html.escape(str(record.get("display") or "Soporte ATC"))
    title = html.escape(str(record.get("title") or "Soporte Tecnico"))
    mobile = record.get("mobile")

    phone_lines = []
    if mobile:
        phone_lines.append(html.escape(str(mobile)))
    phone_lines.append(html.escape(OFFICE_PHONE))

    phones_html = "".join(
        f'<div>&#9742; {phone}</div>' for phone in phone_lines
    )
    address_html = "".join(
        f'<div>&#128205; {html.escape(addr)}</div>' for addr in ADDRESSES
    )

    return (
        '<div data-atc-signature="1" '
        'style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        'color:#1f2937;line-height:1.6;margin-top:4px;">'
        '<div>Atentamente,</div>'
        f'<div style="margin-top:10px;font-weight:bold;color:#0f172a;">{display}</div>'
        f'<div style="color:#334155;">{title}</div>'
        f'<div style="margin-top:6px;">{phones_html}</div>'
        f'<div style="margin-top:6px;">{address_html}</div>'
        '</div>'
    )
