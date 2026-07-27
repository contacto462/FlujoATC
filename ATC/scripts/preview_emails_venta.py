"""
Envía un preview de todos los correos del flujo de venta a contacto@alguientecuida.cl.
No depende de la base de datos — usa datos de ejemplo para mostrar el diseño.

Uso desde la raíz del proyecto:
    /Users/fernando/PROYECTO-ATC/.venv-backend/bin/python \
        /Volumes/PROYECTO-ATC-SERVIDOR/ATC/scripts/preview_emails_venta.py
"""
from __future__ import annotations

import smtplib
import ssl
import sys
import os
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate

# ── Configuración ────────────────────────────────────────────────────────────
PREVIEW_TO   = "contacto@alguientecuida.cl"
LOGO_URL     = "https://i.imgur.com/VgLG9Ei.png"

# Lee credenciales SMTP del .env — el último valor de cada clave gana
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_env_path):
    for line in open(_env_path):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip().strip('"').strip("'")

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USERNAME") or os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASSWORD", "")

# ── Datos de ejemplo ─────────────────────────────────────────────────────────
ODS = {
    "codigo":           "V0042",
    "creado_por":       "Andrea Ramírez",
    "rut_cliente":      "76.123.456-7",
    "razon_social":     "Empresa Demo SpA",
    "nombre_sucursal":  "Casa Matriz",
    "direccion_sucursal": "Av. Apoquindo 4700, Las Condes",
    "tipo_servicio":    "Monitoreo 24/7 | Instalación",
    "tipo_plan":        "Plan Pro",
}
CLIENTE = {
    "nombre_representante": "Carlos Pérez",
    "email_representante":  PREVIEW_TO,
}

# ── Helpers HTML ─────────────────────────────────────────────────────────────
def _esc(v) -> str:
    import html as _html
    return _html.escape(str(v or ""))

def _table(rows: list[tuple[str, str]]) -> str:
    content = ""
    for label, value in rows:
        content += (
            f'<div style="margin:6px 0;font-size:14px;line-height:1.6;">'
            f'<span style="color:#636e72;font-weight:600;">{_esc(label)}:</span> '
            f'<span style="color:#2d3436;">{_esc(value) or "-"}</span>'
            f'</div>'
        )
    return (
        '<div style="background:#ecf0f1;border-left:4px solid #e67e22;'
        'padding:12px 16px;margin:16px 0;border-radius:4px;">'
        f'{content}</div>'
    )

def _paragraphs(*items: str) -> str:
    body = ""
    for item in items:
        if not item.strip():
            continue
        body += (
            '<p style="margin:0 0 16px 0;color:#334155;font-family:Arial,sans-serif;'
            f'font-size:14px;line-height:1.72;">{_esc(item).replace(chr(10), "<br>")}</p>'
        )
    return f'<div style="padding:2px 0 4px 0;">{body}</div>'

def _wrap(title: str, sections: list[str]) -> str:
    section_html = "".join(
        f'<div style="margin-bottom:14px;">{s}</div>' for s in sections
    )
    return f"""
<div style="background:#f5f6fa;padding:40px 0;font-family:'Segoe UI',Arial,sans-serif;color:#2d3436;">
  <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:8px;
              padding:30px;box-shadow:0 2px 10px rgba(0,0,0,0.05);">
    <div style="text-align:center;margin-bottom:18px;">
      <img src="{LOGO_URL}" alt="Alguien Te Cuida" style="height:55px;">
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

def _base_table() -> str:
    return _table([
        ("RUT Cliente",      ODS["rut_cliente"]),
        ("Razón Social",     ODS["razon_social"]),
        ("Sucursal",         ODS["nombre_sucursal"]),
        ("Dirección",        ODS["direccion_sucursal"]),
        ("Tipo de Servicio", ODS["tipo_servicio"]),
        ("Ejecutivo",        ODS["creado_por"]),
    ])

# ── Envío SMTP ────────────────────────────────────────────────────────────────
def _send(subject: str, html_body: str, to: str = PREVIEW_TO) -> None:
    if not SMTP_USER or not SMTP_PASS:
        print(f"  ⚠️  Sin credenciales SMTP — no se pudo enviar: {subject}")
        return
    msg = EmailMessage()
    msg["From"]    = f"ATC <{SMTP_USER}>"
    msg["To"]      = to
    msg["Subject"] = subject
    msg["Date"]    = formatdate(localtime=True)
    msg.set_content(subject)
    msg.add_alternative(html_body, subtype="html")
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=25) as srv:
            srv.ehlo()
            srv.starttls(context=ctx)
            srv.ehlo()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.send_message(msg)
        print(f"  ✅  Enviado → {to}")
    except Exception as exc:
        print(f"  ❌  Error SMTP: {exc}")

# ── Correos en orden de flujo ─────────────────────────────────────────────────
def main() -> None:
    hoy = datetime.now().strftime("%d/%m/%Y")
    print(f"\nPreview de correos del flujo de venta → {PREVIEW_TO}\n")

    # 1. Registro de nueva ODS
    print("1. Registro de nueva ODS")
    _send(
        subject=f"Notificacion de Registro de Nueva Orden de Servicio - ODS {ODS['codigo']}",
        html_body=_wrap(
            "Notificacion de Registro de Nueva Orden de Servicio",
            [_base_table()],
        ),
    )

    # 2. Alerta Orden de Compra (solo si requiere OC)
    print("2. Alerta: Orden requiere Orden de Compra")
    _send(
        subject=f"Nueva Orden Requiere Orden de Compra – Código {ODS['codigo']}",
        html_body=_wrap(
            "Nueva Orden Requiere Orden de Compra",
            [
                _paragraphs("Se ha registrado una nueva Orden de Servicio que requiere Orden de Compra:"),
                _table([
                    ("Código ODS",       ODS["codigo"]),
                    ("Empresa",          ODS["razon_social"]),
                    ("Sucursal",         ODS["nombre_sucursal"]),
                    ("Dirección",        ODS["direccion_sucursal"]),
                    ("Tipo de Servicio", ODS["tipo_servicio"]),
                    ("Ejecutivo",        ODS["creado_por"]),
                ]),
            ],
        ),
    )

    # 3. Recepción admin → representante del cliente
    print("3. Recepción de solicitud → representante del cliente")
    rep = CLIENTE["nombre_representante"]
    _send(
        subject=f"Recepción de solicitud - {ODS['razon_social']}",
        html_body=_wrap(
            "Recepción de solicitud",
            [
                _paragraphs(
                    f"Estimado/a {rep}:",
                    "Junto con saludar, queremos agradecerle sinceramente la confianza depositada "
                    "en Alguien Te Cuida para la gestión de su servicio.",
                    "Le informamos que su solicitud ha sido recibida exitosamente y actualmente se "
                    "encuentra en proceso de validación administrativa. Esta etapa nos permite "
                    "resguardar la correcta revisión de los antecedentes y asegurar una continuidad "
                    "ordenada, transparente y eficiente del servicio solicitado.",
                    "Entendemos la importancia de contar con este servicio a la brevedad. Por ello, "
                    "nuestro equipo está trabajando con especial dedicación, rigurosidad y sentido de "
                    "urgencia para completar esta validación en el menor tiempo posible, manteniendo "
                    "los estándares de calidad y responsabilidad que nos caracterizan.",
                    "Le mantendremos informado/a sobre el avance del proceso y los próximos pasos a "
                    "seguir a través de este mismo medio.",
                    "Atentamente,\nAlguien Te Cuida",
                )
            ],
        ),
    )

    # 4. Solicitud de materiales a bodega
    print("4. Solicitud de materiales a bodega")
    _send(
        subject=f"Materiales solicitados para Orden de Servicio - ODS {ODS['codigo']}",
        html_body=_wrap(
            "Materiales solicitados para Orden de Servicio",
            [
                _base_table(),
                _table([
                    ("Materiales solicitados", "4x Cámara Domo IP - solicitado: 4 - entregado: 0\n2x Cable UTP Cat6 100m - solicitado: 2 - entregado: 0"),
                ]),
            ],
        ),
    )

    # 5. Instalación finalizada
    print("5. Instalación finalizada")
    _send(
        subject=f"Instalacion Finalizada - ODS {ODS['codigo']}",
        html_body=_wrap(
            "Instalacion Finalizada",
            [
                _table([
                    ("RUT Cliente",      ODS["rut_cliente"]),
                    ("Razón Social",     ODS["razon_social"]),
                    ("Sucursal",         ODS["nombre_sucursal"]),
                    ("Dirección",        ODS["direccion_sucursal"]),
                    ("Tipo de Servicio", ODS["tipo_servicio"]),
                ]),
            ],
        ),
    )

    # 6. Definición de puesto soporte
    print("6. Definición de puesto soporte")
    _send(
        subject=f"Definición de Puesto Cliente {ODS['nombre_sucursal']} - ODS {ODS['codigo']}",
        html_body=_wrap(
            f"Definición de Puesto Cliente {ODS['nombre_sucursal']}",
            [
                _table([
                    ("RUT Cliente",      ODS["rut_cliente"]),
                    ("Razón Social",     ODS["razon_social"]),
                    ("Sucursal",         ODS["nombre_sucursal"]),
                    ("Dirección",        ODS["direccion_sucursal"]),
                    ("Tipo de Servicio", ODS["tipo_servicio"]),
                    ("Requiere Puesto",  "Sí"),
                    ("Número de Puesto", "Central 01"),
                ]),
            ],
        ),
    )

    # 7. Fecha inicio de servicio
    print("7. Fecha inicio de servicio")
    _send(
        subject=f"Fecha Inicio de Servicio {ODS['nombre_sucursal']} - ODS {ODS['codigo']}",
        html_body=_wrap(
            f"Fecha Inicio de Servicio {ODS['nombre_sucursal']}",
            [
                _table([
                    ("RUT Cliente",            ODS["rut_cliente"]),
                    ("Razón Social",           ODS["razon_social"]),
                    ("Sucursal",               ODS["nombre_sucursal"]),
                    ("Dirección",              ODS["direccion_sucursal"]),
                    ("Tipo de Servicio",       ODS["tipo_servicio"]),
                    ("Fecha Inicio de servicio", "01/07/2026"),
                ]),
            ],
        ),
    )

    print(f"\n✅ Preview completo — revisa {PREVIEW_TO}\n")


if __name__ == "__main__":
    main()
