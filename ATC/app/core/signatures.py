"""Firmas de correo por usuario para las respuestas de soporte.

La firma se inserta automaticamente en el editor de respuesta del ticket
segun el usuario que tiene la sesion iniciada.

El diseno es EXACTAMENTE el mismo de la firma corporativa (tabla con logo,
iconos de redes y banner). La plantilla base se extrajo de una respuesta real
y vive en `signature_template.html`; aqui solo se reemplazan nombre, cargo y
una linea de celular (con el mismo estilo) por usuario.
"""

from __future__ import annotations

import html
from pathlib import Path


_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / "signature_template.html"
_PHONE_LINE_FILE = _BASE_DIR / "signature_phone_line.html"

# Datos por usuario, indexados por nombre en minusculas.
# display: como aparece el nombre (en negrita) en la firma.
# title:   cargo.
# mobile:  celular (None si no se muestra esa linea).
_SIGNATURES: dict[str, dict[str, str | None]] = {
    "ronald montilla": {
        "display": "Ronald Montilla A.",
        "title": "Jefe Soporte Técnico",
        "mobile": "+56 9 9826 9667",
    },
    "fernando lubiano": {
        "display": "Fernando Lubiano.",
        "title": "Analista de Operaciones",
        "mobile": None,
    },
    "felipe mora": {
        "display": "Felipe Mora.",
        "title": "Soporte Técnico",
        "mobile": "+56 98269667",
    },
    "julissa mella": {
        "display": "Julissa Mella.",
        "title": "Soporte Técnico",
        "mobile": "+56 98269667",
    },
    "antonio bahamondes": {
        "display": "Antonio Bahamondes.",
        "title": "Soporte Técnico",
        "mobile": "+56 98269667",
    },
    "sthefan leal": {
        "display": "Sthefan Leal.",
        "title": "Soporte Técnico",
        "mobile": "+56 98269667",
    },
}


def _load(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


# Se cargan una vez al importar el modulo.
_TEMPLATE = _load(_TEMPLATE_FILE)
_PHONE_LINE = _load(_PHONE_LINE_FILE)


def _resolve_record(name: str) -> dict[str, str | None]:
    key = (name or "").strip().lower()
    record = _SIGNATURES.get(key)
    if record:
        return record

    # Fallback para cualquier otro usuario: usa su propio nombre y un cargo
    # generico, sin celular personal.
    display = f"{name.strip()}." if name and name.strip() else "Soporte ATC"
    return {"display": display, "title": "Soporte Técnico", "mobile": None}


def _mobile_block(mobile: str | None) -> str:
    if not mobile or not _PHONE_LINE:
        return ""
    return _PHONE_LINE.replace("{{MOBILE}}", html.escape(str(mobile)))


def signature_html_for_user(user) -> str:
    """Devuelve el HTML de la firma corporativa para el usuario dado."""
    name = (getattr(user, "name", "") or "").strip()
    record = _resolve_record(name)

    display = html.escape(str(record.get("display") or "Soporte ATC"))
    title = html.escape(str(record.get("title") or "Soporte Técnico"))
    mobile = record.get("mobile")

    if not _TEMPLATE:
        # Respaldo minimo si faltara la plantilla.
        return (
            '<div data-atc-signature="1">Atentamente,<br><br>'
            f"<strong>{display}</strong><br>{title}<br>600 828 8000</div>"
        )

    return (
        _TEMPLATE
        .replace("{{NAME}}", display)
        .replace("{{TITLE}}", title)
        .replace("{{MOBILE_BLOCK}}", _mobile_block(mobile))
    )
