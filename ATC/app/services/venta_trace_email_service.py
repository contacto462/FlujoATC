from __future__ import annotations

import html
import json
import logging
import os
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, parseaddr
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)

CONTACTO_EMAIL = "contacto@alguientecuida.cl"


def _env_file_values() -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = Path(__file__).resolve().parents[2] / ".env"
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except Exception:
        pass
    return values


def _env_get(*keys: str, default: str = "") -> str:
    env_file = _env_file_values()
    for key in keys:
        value = str(os.getenv(key) or env_file.get(key) or "").strip()
        if value:
            return value
    return default


def _bool_env(value: str, default: bool) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "no", "off"}


def _recipient_list(env_key: str, defaults: list[str]) -> list[str]:
    raw = _env_get(env_key)
    items = raw.replace(";", ",").split(",") if raw else defaults
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        email = parseaddr(str(item or "").strip())[1].strip()
        if not email or "@" not in email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(email)
    return out

VENTA_TRACE_RECIPIENTS: dict[str, list[str]] = {
    "registro_ods": _recipient_list("VENTA_REGISTRO_ODS_EMAILS", [
        "administracion@alguientecuida.cl",
        "dromero@alguientecuida.cl",
        "finanzas@alguientecuida.cl",
    ]),
    "oc_requerida": _recipient_list("VENTA_OC_REQUERIDA_EMAILS", ["finanzas@alguientecuida.cl"]),
    "recepcion_admin_cliente": [],  # se envía al email_representante del cliente (dinámico)
    "materiales_bodega": _recipient_list("VENTA_MATERIALES_BODEGA_EMAILS", [
        "gponce@soporteatc.cl",
        "compras@alguientecuida.cl",
    ]),
    "instalacion_finalizada": _recipient_list("VENTA_INSTALACION_FINALIZADA_EMAILS", [
        "administracion@alguientecuida.cl",
        "dromero@alguientecuida.cl",
    ]),
    "puesto_soporte": _recipient_list("VENTA_PUESTO_SOPORTE_EMAILS", [
        "soporte@soporteatc.cl",
    ]),
    "inicio_servicio": _recipient_list("VENTA_INICIO_SERVICIO_EMAILS", [
        "administracion@alguientecuida.cl",
        "dromero@alguientecuida.cl",
        "finanzas@alguientecuida.cl",
    ]),
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _esc(value: Any) -> str:
    return html.escape(_clean(value))


def _esc_cell(value: Any) -> str:
    return _esc(value).replace("\n", "<br>")


def _fecha_hora() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def _smtp_from_email() -> str:
    return parseaddr(
        _env_get("CONTACTO_SMTP_FROM_EMAIL", default=_env_get("CONTACTO_SMTP_USERNAME", "CONTACTO_SMTP_USER"))
    )[1].strip().lower()


def _smtp_from_header() -> str:
    email = _smtp_from_email()
    name = _clean(_env_get("CONTACTO_SMTP_FROM_NAME", default="Alguien Te Cuida"))
    if email != CONTACTO_EMAIL:
        raise ValueError(f"SMTP de contacto no configurado para {CONTACTO_EMAIL}.")
    return f"{name} <{email}>" if name else email


def _plain_from_html(value: str) -> str:
    text_value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value or "")
    text_value = re.sub(r"(?i)</p>|<br\s*/?>|</tr>|</div>|</li>", "\n", text_value)
    text_value = re.sub(r"(?is)<[^>]+>", " ", text_value)
    text_value = html.unescape(text_value)
    text_value = re.sub(r"[ \t]+\n", "\n", text_value)
    text_value = re.sub(r"\n{3,}", "\n\n", text_value)
    return text_value.strip() or "Mensaje sin contenido."


def _send_contact_message(to: str, subject: str, html_body: str, cc: list[str] | None = None) -> None:
    username = _env_get("CONTACTO_SMTP_USERNAME", "CONTACTO_SMTP_USER")
    password = _env_get("CONTACTO_SMTP_PASSWORD")
    host = _env_get("CONTACTO_SMTP_HOST", default="smtp.gmail.com")
    if not username or not password or not host:
        raise ValueError("SMTP de contacto incompleto: falta CONTACTO_SMTP_HOST/USERNAME/PASSWORD.")
    if _smtp_from_email() != CONTACTO_EMAIL:
        raise ValueError(f"SMTP de contacto no configurado para {CONTACTO_EMAIL}.")

    msg = EmailMessage()
    msg["From"] = _smtp_from_header()
    msg["To"] = to
    cc_recipients = [item for item in (cc or []) if _clean(item)]
    if cc_recipients:
        msg["Cc"] = ", ".join(cc_recipients)
    msg["Subject"] = (subject or "").replace("\r", " ").replace("\n", " ").strip() or "Notificacion Alguien Te Cuida"
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(_plain_from_html(html_body), subtype="plain", charset="utf-8")
    msg.add_alternative(html_body, subtype="html", charset="utf-8")

    try:
        port = int(_env_get("CONTACTO_SMTP_PORT", default="587"))
    except Exception:
        port = 587
    try:
        timeout = int(_env_get("CONTACTO_SMTP_TIMEOUT_SEC", default="20"))
    except Exception:
        timeout = 20
    use_ssl = _bool_env(_env_get("CONTACTO_SMTP_USE_SSL"), False)
    use_tls = _bool_env(_env_get("CONTACTO_SMTP_USE_TLS"), True)
    if use_ssl:
        with smtplib.SMTP_SSL(host, port or 465, timeout=timeout, context=ssl.create_default_context()) as server:
            server.login(username, password)
            server.send_message(msg)
        return

    with smtplib.SMTP(host, port, timeout=timeout) as server:
        server.ehlo()
        if use_tls:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        server.login(username, password)
        server.send_message(msg)


def _ods_row(db: Session, codigo: str) -> dict[str, Any]:
    row = db.execute(text("""
        SELECT codigo, creado_por, rut_cliente, razon_social, nombre_sucursal,
               direccion_sucursal, tipo_servicio, tipo_plan, observacion,
               numero_camaras_instalar, numero_camaras_desinstalar,
               numero_camaras_vigilar, materiales, consideraciones,
               requiere_oc, montos_a_cobrar, estado, created_at
        FROM venta_comercial
        WHERE LOWER(TRIM(codigo)) = LOWER(TRIM(:codigo))
        ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
    """), {"codigo": codigo}).mappings().first()
    return dict(row or {})


def _materiales_servicio(db: Session, codigo: str) -> str:
    return _clean(db.execute(text("""
        SELECT solicitud_materiales
        FROM venta_servicio_tecnico
        WHERE LOWER(TRIM(odt)) = LOWER(TRIM(:codigo))
        ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
    """), {"codigo": codigo}).scalar_one_or_none())


def _cliente_row(db: Session, rut: str) -> dict[str, Any]:
    row = db.execute(text("""
        SELECT rut, cliente, nombre_representante, email_representante
        FROM bbdd_clientes
        WHERE LOWER(TRIM(rut)) = LOWER(TRIM(:rut))
        ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
    """), {"rut": rut}).mappings().first()
    return dict(row or {})


def _materiales_bodega(db: Session, codigo: str) -> tuple[list[dict[str, str]], str]:
    raw = db.execute(text("""
        SELECT materiales_bodega
        FROM venta_servicio_tecnico
        WHERE LOWER(TRIM(odt)) = LOWER(TRIM(:codigo))
        ORDER BY (SELECT NULL) OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
    """), {"codigo": codigo}).scalar_one_or_none()
    if not raw:
        return [], ""
    try:
        data = json.loads(str(raw))
    except Exception:
        return [], str(raw)
    if not isinstance(data, dict):
        return [], ""
    items_raw = data.get("items") if isinstance(data.get("items"), list) else []
    items: list[dict[str, str]] = []
    for item in items_raw:
        if not isinstance(item, dict):
            continue
        items.append({
            "nombre": _clean(item.get("nombre")),
            "presupuestado": _clean(item.get("presupuestado")),
            "entregado": _clean(item.get("entregado")),
        })
    return items, _clean(data.get("observacion"))


def _table(rows: list[tuple[str, Any]]) -> str:
    content = ""
    for label, value in rows:
        content += (
            f"<div style=\"margin:6px 0;font-size:14px;line-height:1.6;\">"
            f"<span style=\"color:#636e72;font-weight:600;\">{_esc(label)}:</span> "
            f"<span style=\"color:#2d3436;\">{_esc_cell(value) or '-'}</span>"
            f"</div>"
        )
    return (
        "<div style=\"background:#ecf0f1;border-left:4px solid #e67e22;"
        "padding:12px 16px;margin:16px 0;border-radius:4px;\">"
        f"{content}"
        "</div>"
    )


def _materiales_text(items: list[dict[str, str]], fallback: str = "") -> str:
    lines: list[str] = []
    for item in items:
        parts = [
            _clean(item.get("nombre")),
            f"solicitado: {_clean(item.get('presupuestado'))}" if _clean(item.get("presupuestado")) else "",
            f"entregado: {_clean(item.get('entregado'))}" if _clean(item.get("entregado")) else "",
        ]
        line = " - ".join(part for part in parts if part)
        if line:
            lines.append(line)
    return "\n".join(lines) or _clean(fallback) or "Sin materiales registrados."


_LOGO_URL = "https://i.imgur.com/VgLG9Ei.png"


def _email_html(*, title: str, sections: list[str]) -> str:
    section_html = "".join(
        f"<div style=\"margin-bottom:14px;\">{section}</div>"
        for section in sections
    )
    return f"""
<div style="background:#f5f6fa;padding:40px 0;font-family:'Segoe UI',Arial,sans-serif;color:#2d3436;">
  <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:8px;
              padding:30px;box-shadow:0 2px 10px rgba(0,0,0,0.05);">
    <div style="text-align:center;margin-bottom:18px;">
      <img src="{_LOGO_URL}" alt="Alguien Te Cuida" style="height:55px;">
    </div>
    <h2 style="text-align:center;color:#2d3436;font-size:18px;margin:0 0 22px 0;">{_esc(title)}</h2>
    <div style="font-size:14px;line-height:1.7;">{section_html}</div>
    <p style="margin-top:26px;font-size:14px;line-height:1.6;">
      Saludos cordiales,<br>Alguien Te Cuida
    </p>
    <hr style="border:0;border-top:1px solid #ddd;margin:26px 0;">
    <p style="font-size:12px;color:#999;text-align:center;line-height:1.5;">
      Este correo ha sido generado automáticamente como parte del proceso interno.
    </p>
  </div>
</div>"""


def _paragraphs(*items: str) -> str:
    body = ""
    for item in items:
        text_value = _clean(item)
        if not text_value:
            continue
        body += (
            '<p style="margin:0 0 16px 0;color:#334155;font-family:Arial,sans-serif;'
            'font-size:14px;line-height:1.72;">'
            f"{_esc(text_value).replace(chr(10), '<br>')}"
            "</p>"
        )
    return (
        '<div style="padding:2px 0 4px 0;">'
        f"{body}"
        "</div>"
    )


def _send(kind: str, subject: str, html_body: str) -> dict[str, Any]:
    recipients = VENTA_TRACE_RECIPIENTS.get(kind, [])
    if not recipients:
        return {"email_sent": False, "email_to": [], "email_error": f"Sin destinatarios configurados para {kind}"}
    to = recipients[0]
    cc = recipients[1:]
    try:
        _send_contact_message(to=to, cc=cc, subject=subject, html_body=html_body)
        return {"email_sent": True, "email_to": recipients, "email_error": ""}
    except Exception as exc:
        errors = f"{', '.join(recipients)}: {exc}"
        _log.warning("No se pudo enviar correo trazabilidad venta %s a %s: %s", kind, ", ".join(recipients), exc)
        return {"email_sent": False, "email_to": [], "email_error": errors}


def _send_to_representante(kind: str, representative_email: str, subject: str, html_body: str) -> dict[str, Any]:
    configured_test_recipients = VENTA_TRACE_RECIPIENTS.get(kind)
    recipients = configured_test_recipients or [representative_email]
    recipients = [recipient for recipient in recipients if _clean(recipient)]
    if not recipients:
        return {"email_sent": False, "email_to": [], "email_error": "Representante legal sin email registrado"}
    to = recipients[0]
    cc = recipients[1:]
    try:
        _send_contact_message(to=to, cc=cc, subject=subject, html_body=html_body)
        return {"email_sent": True, "email_to": recipients, "email_error": ""}
    except Exception as exc:
        errors = f"{', '.join(recipients)}: {exc}"
        _log.warning("No se pudo enviar correo %s a %s: %s", kind, ", ".join(recipients), exc)
        return {"email_sent": False, "email_to": [], "email_error": errors}


def _base_sections(ods: dict[str, Any]) -> list[str]:
    return [
        _table([
            ("RUT Cliente", ods.get("rut_cliente")),
            ("Razón Social", ods.get("razon_social")),
            ("Sucursal", ods.get("nombre_sucursal") or ods.get("razon_social")),
            ("Dirección", ods.get("direccion_sucursal")),
            ("Tipo de Servicio", ods.get("tipo_servicio")),
            ("Ejecutivo", ods.get("creado_por")),
        ])
    ]


def _sections_sin_ejecutivo(ods: dict[str, Any]) -> list[str]:
    return [
        _table([
            ("RUT Cliente", ods.get("rut_cliente")),
            ("Razón Social", ods.get("razon_social")),
            ("Sucursal", ods.get("nombre_sucursal") or ods.get("razon_social")),
            ("Dirección", ods.get("direccion_sucursal")),
            ("Tipo de servicio", ods.get("tipo_servicio")),
        ])
    ]


def notify_ods_registered(db: Session, codigo: str) -> dict[str, Any]:
    ods = _ods_row(db, codigo)
    if not ods:
        return {"email_sent": False, "email_to": [], "email_error": "ODS no encontrada"}
    title = "Notificacion de Registro de Nueva Orden de Servicio"
    sections = _base_sections(ods)
    body = _email_html(title=title, sections=sections)
    return _send("registro_ods", f"{title} - ODS {ods.get('codigo') or codigo}", body)


def notify_recepcion_administracion_cliente(db: Session, codigo: str) -> dict[str, Any]:
    ods = _ods_row(db, codigo)
    if not ods:
        return {"email_sent": False, "email_to": [], "email_error": "ODS no encontrada"}
    cliente = _cliente_row(db, _clean(ods.get("rut_cliente")))
    representante = _clean(cliente.get("nombre_representante")) or "Representante legal"
    email_representante = _clean(cliente.get("email_representante"))
    title = "Recepción de solicitud"
    sections = [
        _paragraphs(
            f"Estimado/a {representante}:",
            (
                "Junto con saludar, queremos agradecerle sinceramente la confianza depositada "
                "en Alguien Te Cuida para la gestión de su servicio."
            ),
            (
                "Le informamos que su solicitud ha sido recibida exitosamente y actualmente se "
                "encuentra en proceso de validación administrativa. Esta etapa nos permite "
                "resguardar la correcta revisión de los antecedentes y asegurar una continuidad "
                "ordenada, transparente y eficiente del servicio solicitado."
            ),
            (
                "Entendemos la importancia de contar con este servicio a la brevedad. Por ello, "
                "nuestro equipo está trabajando con especial dedicación, rigurosidad y sentido de "
                "urgencia para completar esta validación en el menor tiempo posible, manteniendo "
                "los estándares de calidad y responsabilidad que nos caracterizan."
            ),
            (
                "Le mantendremos informado/a sobre el avance del proceso y los próximos pasos a "
                "seguir a través de este mismo medio."
            ),
            "Atentamente,\nAlguien Te Cuida",
        )
    ]
    body = _email_html(title=title, sections=sections)
    return _send_to_representante(
        "recepcion_admin_cliente",
        email_representante,
        f"{title} - {ods.get('razon_social') or ods.get('codigo') or codigo}",
        body,
    )


def notify_materiales_bodega(db: Session, codigo: str) -> dict[str, Any]:
    ods = _ods_row(db, codigo)
    if not ods:
        return {"email_sent": False, "email_to": [], "email_error": "ODS no encontrada"}
    items, observacion = _materiales_bodega(db, codigo)
    fallback = _materiales_servicio(db, codigo) or _clean(ods.get("materiales"))
    sections = _base_sections(ods)
    _ = observacion
    sections.append(_table([("Materiales solicitados (Lista tipeada)", _materiales_text(items, fallback=fallback))]))
    title = "Materiales solicitados para Orden de Servicio"
    body = _email_html(title=title, sections=sections)
    return _send("materiales_bodega", f"{title} - ODS {ods.get('codigo') or codigo}", body)


def notify_instalacion_finalizada(db: Session, codigo: str) -> dict[str, Any]:
    ods = _ods_row(db, codigo)
    if not ods:
        return {"email_sent": False, "email_to": [], "email_error": "ODS no encontrada"}
    title = "Instalacion Finalizada"
    body = _email_html(title=title, sections=_sections_sin_ejecutivo(ods))
    return _send("instalacion_finalizada", f"{title} - ODS {ods.get('codigo') or codigo}", body)


def notify_puesto_soporte(db: Session, codigo: str, requiere_puesto: str, numero_puesto: str) -> dict[str, Any]:
    ods = _ods_row(db, codigo)
    if not ods:
        return {"email_sent": False, "email_to": [], "email_error": "ODS no encontrada"}
    sucursal = _clean(ods.get("nombre_sucursal") or ods.get("razon_social"))
    title = f"Definición de Puesto Cliente {sucursal}".strip()
    sections = [
        _table([
            ("RUT Cliente", ods.get("rut_cliente")),
            ("Razón Social", ods.get("razon_social")),
            ("Sucursal", sucursal),
            ("Dirección", ods.get("direccion_sucursal")),
            ("Tipo de servicio", ods.get("tipo_servicio")),
            ("Requiere Puesto", requiere_puesto),
            ("Número de Puesto", numero_puesto),
        ])
    ]
    body = _email_html(title=title, sections=sections)
    return _send("puesto_soporte", f"{title} - ODS {ods.get('codigo') or codigo}", body)


def notify_inicio_servicio(db: Session, codigo: str, fecha_inicio: str) -> dict[str, Any]:
    ods = _ods_row(db, codigo)
    if not ods:
        return {"email_sent": False, "email_to": [], "email_error": "ODS no encontrada"}
    sucursal = _clean(ods.get("nombre_sucursal") or ods.get("razon_social"))
    title = f"Fecha Inicio de Servicio {sucursal}".strip()
    sections = [
        _table([
            ("RUT Cliente", ods.get("rut_cliente")),
            ("Razón Social", ods.get("razon_social")),
            ("Sucursal", sucursal),
            ("Dirección", ods.get("direccion_sucursal")),
            ("Tipo de servicio", ods.get("tipo_servicio")),
            ("Fecha Inicio de servicio", fecha_inicio),
        ])
    ]
    body = _email_html(title=title, sections=sections)
    return _send("inicio_servicio", f"{title} - ODS {ods.get('codigo') or codigo}", body)


def notify_oc_requerida(db: Session, codigo: str) -> dict[str, Any]:
    ods = _ods_row(db, codigo)
    if not ods:
        return {"email_sent": False, "email_to": [], "email_error": "ODS no encontrada"}
    title = "Nueva Orden Requiere Orden de Compra"
    sections = [
        _paragraphs("Se ha registrado una nueva Orden de Servicio que requiere Orden de Compra:"),
        _table([
            ("Código ODS", ods.get("codigo") or codigo),
            ("Empresa", ods.get("razon_social")),
            ("Sucursal", ods.get("nombre_sucursal") or ods.get("razon_social")),
            ("Dirección", ods.get("direccion_sucursal")),
            ("Tipo de Servicio", ods.get("tipo_servicio")),
            ("Ejecutivo", ods.get("creado_por")),
        ]),
    ]
    body = _email_html(title=title, sections=sections)
    return _send("oc_requerida", f"Nueva Orden Requiere Orden de Compra – Código {ods.get('codigo') or codigo}", body)
