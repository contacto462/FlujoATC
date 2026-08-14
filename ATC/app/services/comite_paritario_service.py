from __future__ import annotations

import html
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path


_ATC_DIR = Path(__file__).resolve().parents[2]
_ATC_ENV_PATH = _ATC_DIR / ".env"
COMITE_PARITARIO_DESTINATARIO_EMAIL = "do@alguientecuida.cl"


def _env_contacto() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in _ATC_ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except Exception:
        pass
    return values


def _contacto_smtp_config() -> dict[str, str]:
    env_file = _env_contacto()

    def env_get(*keys: str, default: str = "") -> str:
        for key in keys:
            value = str(os.getenv(key) or env_file.get(key) or "").strip()
            if value:
                return value
        return default

    return {
        "username": env_get("CONTACTO_SMTP_USERNAME"),
        "password": env_get("CONTACTO_SMTP_PASSWORD"),
        "host": env_get("CONTACTO_SMTP_HOST", default="smtp.gmail.com"),
        "port": env_get("CONTACTO_SMTP_PORT", default="587"),
        "from_name": env_get("CONTACTO_SMTP_FROM_NAME", default="Alguien Te Cuida"),
        "from_addr": env_get("CONTACTO_SMTP_FROM_EMAIL"),
        "use_tls": env_get("CONTACTO_SMTP_USE_TLS", default="true"),
    }


def enviar_postulacion_comite_paritario_email(data: dict[str, str]) -> None:
    cfg = _contacto_smtp_config()
    username = cfg["username"]
    password = cfg["password"]
    if not username or not password:
        raise RuntimeError("CONTACTO_SMTP_USERNAME/CONTACTO_SMTP_PASSWORD no configurados en .env")

    from_addr = cfg["from_addr"] or username
    host = cfg["host"]
    port = int(cfg["port"] or 587)
    use_tls = cfg["use_tls"].lower() not in {"0", "false", "no", "off"}

    nombre = (data.get("nombre") or "").strip()
    rut = (data.get("rut") or "").strip()
    cargo = (data.get("cargo") or "").strip()
    correo = (data.get("correo") or "").strip()
    fecha = datetime.now().strftime("%d-%m-%Y %H:%M")

    rows = [
        ("Nombre", nombre),
        ("RUT", rut),
        ("Cargo", cargo),
        ("Correo", correo),
        ("Fecha postulacion", fecha),
    ]
    plain_body = "Nueva postulacion al Comite Paritario\n\n" + "\n".join(
        f"{label}: {value}" for label, value in rows
    )
    html_rows = "".join(
        "<tr>"
        f"<th>{html.escape(label)}</th>"
        f"<td>{html.escape(value)}</td>"
        "</tr>"
        for label, value in rows
    )
    html_body = f"""
      <div style="font-family:Arial,sans-serif;font-size:14px;color:#172033;line-height:1.55;">
        <h2 style="margin:0 0 12px;color:#0b1424;">Nueva postulacion al Comite Paritario</h2>
        <table style="border-collapse:collapse;width:100%;max-width:620px;">
          {html_rows}
        </table>
      </div>
      <style>
        th{{text-align:left;background:#f3f6fb;color:#0b1424;padding:9px 11px;border:1px solid #dbe3ef;width:180px;}}
        td{{padding:9px 11px;border:1px solid #dbe3ef;}}
      </style>
    """

    msg = EmailMessage()
    msg["From"] = f"{cfg['from_name']} <{from_addr}>"
    msg["To"] = COMITE_PARITARIO_DESTINATARIO_EMAIL
    msg["Subject"] = f"Postulacion Comite Paritario - {nombre or rut or 'Sin nombre'}"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="alguientecuida.cl")
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=25) as srv:
        srv.ehlo()
        if use_tls:
            srv.starttls(context=ctx)
            srv.ehlo()
        srv.login(username, password)
        srv.send_message(msg)
