from __future__ import annotations

import hashlib
import io
import os
import re
import secrets
import smtplib
import ssl
import threading
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

# ──────────────────────────────────────────────
# Plantilla PDF y ubicación de campos
# ──────────────────────────────────────────────

_CONTRATO_TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "Contrato Diario.pdf"
_ATC_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
_DESTINATARIO_CONTRATO = "fernando.lubiano.m@gmail.com"

_FICHA_PAGE_INDEX = 2  # "FICHA INGRESO TRABAJADOR" es la 3ra página del PDF (0-indexed)
_FICHA_PAGE_SIZE = (612.0, 1008.0)

# (clave, top, bottom, multilinea) — coordenadas tomadas de la tabla real del
# PDF (columna de valores empieza en x=211.7, borde derecho en x=508.4).
_FICHA_ROWS: list[tuple[str, float, float, bool]] = [
    ("rut", 118.6, 131.6, False),
    ("nombres", 132.6, 145.6, False),
    ("apellido_paterno", 146.6, 162.2, False),
    ("apellido_materno", 163.2, 176.2, False),
    ("fecha_nacimiento", 177.2, 191.6, False),
    ("direccion", 192.6, 205.6, False),
    ("comuna", 206.6, 219.6, False),
    ("institucion_salud", 220.6, 263.6, True),
    ("prevision_afp", 264.6, 279.6, False),
    ("estado_civil", 280.6, 296.9, False),
    ("telefono_movil", 297.9, 313.7, False),
    ("telefono_fijo", 314.7, 327.7, False),
    ("contacto_emergencia_nombre", 328.7, 353.7, False),
    ("contacto_emergencia_telefono", 354.7, 370.9, False),
    ("fecha_inicio_funciones", 371.9, 387.5, False),
    ("banco", 388.5, 401.5, False),
    ("tipo_cuenta", 402.5, 416.1, False),
    ("numero_cuenta", 417.1, 431.5, False),
    ("correo_electronico", 432.5, 446.9, False),
]

_FICHA_VALUE_X = 220.0

_FIRMA_X = 115.0


def _wrap_text(value: str, max_chars: int) -> list[str]:
    words = (value or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _draw_ficha_overlay(c: canvas.Canvas, datos: dict[str, str]) -> None:
    page_h = _FICHA_PAGE_SIZE[1]
    for key, top, bottom, multiline in _FICHA_ROWS:
        value = str(datos.get(key) or "").strip()
        if not value:
            continue
        if multiline:
            lines = _wrap_text(value, 48)[:3]
            c.setFont("Helvetica", 8)
            row_h = bottom - top
            step = row_h / (len(lines) + 1)
            for i, line in enumerate(lines, start=1):
                y = page_h - (top + step * i) + 3
                c.drawString(_FICHA_VALUE_X, y, line)
        else:
            c.setFont("Helvetica", 9)
            y = page_h - bottom + 4
            c.drawString(_FICHA_VALUE_X, y, value)


def _draw_firma_overlay(c: canvas.Canvas, datos: dict[str, str], *, verificado_en: str, referencia: str) -> None:
    nombre_completo = " ".join(
        p for p in (datos.get("nombres"), datos.get("apellido_paterno"), datos.get("apellido_materno")) if p
    ).strip()
    c.setFont("Helvetica-Bold", 8)
    c.drawString(_FIRMA_X, 350, "FIRMADO ELECTRÓNICAMENTE — firma electrónica simple (Ley 19.799)")
    c.setFont("Helvetica", 8)
    c.drawString(_FIRMA_X, 323, f"{nombre_completo}  ·  RUT {datos.get('rut', '')}  ·  {verificado_en}")
    c.setFont("Helvetica", 6.5)
    c.drawString(
        _FIRMA_X,
        305,
        f"Verificado por código enviado a {datos.get('correo_electronico', '')}  ·  ref. {referencia}",
    )


def generar_pdf_contrato(datos: dict[str, str], *, verificado_en: str, referencia: str) -> bytes:
    reader = PdfReader(str(_CONTRATO_TEMPLATE_PATH))
    writer = PdfWriter()

    overlay_buffer = io.BytesIO()
    c = canvas.Canvas(overlay_buffer, pagesize=_FICHA_PAGE_SIZE)
    _draw_ficha_overlay(c, datos)
    _draw_firma_overlay(c, datos, verificado_en=verificado_en, referencia=referencia)
    c.save()
    overlay_buffer.seek(0)
    overlay_page = PdfReader(overlay_buffer).pages[0]

    for i, page in enumerate(reader.pages):
        if i == _FICHA_PAGE_INDEX:
            page.merge_page(overlay_page)
        writer.add_page(page)

    out_buffer = io.BytesIO()
    writer.write(out_buffer)
    return out_buffer.getvalue()


# ──────────────────────────────────────────────
# Envío de correo (cuenta contacto@alguientecuida.cl, ya provisionada en .env)
# ──────────────────────────────────────────────

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


def _send_contacto_email(
    *,
    to: list[str],
    subject: str,
    html_body: str,
    plain_body: str,
    attachment: tuple[str, bytes] | None = None,
) -> None:
    cfg = _contacto_smtp_config()
    username = cfg["username"]
    password = cfg["password"]
    if not username or not password:
        raise RuntimeError("CONTACTO_SMTP_USERNAME/CONTACTO_SMTP_PASSWORD no configurados en .env")

    from_addr = cfg["from_addr"] or username
    host = cfg["host"]
    port = int(cfg["port"] or 587)
    use_tls = cfg["use_tls"].lower() not in {"0", "false", "no", "off"}

    msg = EmailMessage()
    msg["From"] = f"{cfg['from_name']} <{from_addr}>"
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="alguientecuida.cl")
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")

    if attachment:
        filename, data = attachment
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=filename)

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=25) as srv:
        srv.ehlo()
        if use_tls:
            srv.starttls(context=ctx)
            srv.ehlo()
        srv.login(username, password)
        srv.send_message(msg)


# ──────────────────────────────────────────────
# Código de verificación (firma electrónica simple)
# ──────────────────────────────────────────────

_OTP_LOCK = threading.Lock()
_OTP_STORE: dict[str, dict] = {}
_OTP_TTL_SECONDS = 10 * 60
_OTP_MAX_ATTEMPTS = 5


def _normalizar_email(value: str) -> str:
    return (value or "").strip().lower()


def solicitar_otp(email: str, nombre: str) -> None:
    email_norm = _normalizar_email(email)
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email_norm):
        raise ValueError("Correo inválido.")

    codigo = f"{secrets.randbelow(1_000_000):06d}"
    with _OTP_LOCK:
        _OTP_STORE[email_norm] = {
            "codigo": codigo,
            "expira": time.time() + _OTP_TTL_SECONDS,
            "intentos": 0,
        }

    saludo = f"Hola {nombre.strip()}," if nombre.strip() else "Hola,"
    html_body = f"""
      <div style="font-family:Arial,sans-serif;font-size:14px;color:#2d3436;line-height:1.6;">
        <p>{saludo}</p>
        <p>Tu código de verificación para firmar el Contrato Diario es:</p>
        <p style="font-size:28px;font-weight:800;letter-spacing:4px;margin:18px 0;">{codigo}</p>
        <p>Este código vence en 10 minutos. Si no solicitaste este contrato, ignora este correo.</p>
        <p>Alguien Te Cuida</p>
      </div>"""
    plain_body = f"{saludo}\nTu código de verificación es: {codigo}\nVence en 10 minutos."
    _send_contacto_email(
        to=[email_norm],
        subject="Código de verificación — Contrato Diario",
        html_body=html_body,
        plain_body=plain_body,
    )


def verificar_otp(email: str, codigo: str) -> bool:
    email_norm = _normalizar_email(email)
    codigo = (codigo or "").strip()
    with _OTP_LOCK:
        entry = _OTP_STORE.get(email_norm)
        if not entry:
            return False
        if time.time() > entry["expira"]:
            _OTP_STORE.pop(email_norm, None)
            return False
        entry["intentos"] += 1
        if entry["intentos"] > _OTP_MAX_ATTEMPTS:
            _OTP_STORE.pop(email_norm, None)
            return False
        if not secrets.compare_digest(entry["codigo"], codigo):
            return False
        _OTP_STORE.pop(email_norm, None)
        return True


# ──────────────────────────────────────────────
# Orquestación: verificar OTP → generar PDF → enviar
# ──────────────────────────────────────────────

def procesar_contrato_diario(datos: dict[str, str], codigo_otp: str) -> None:
    email = datos.get("correo_electronico", "")
    if not verificar_otp(email, codigo_otp):
        raise ValueError("Código de verificación inválido o vencido.")

    if not _CONTRATO_TEMPLATE_PATH.exists():
        raise RuntimeError(f"No se encontró la plantilla: {_CONTRATO_TEMPLATE_PATH}")

    ahora = datetime.now()
    verificado_en = ahora.strftime("%d-%m-%Y %H:%M")
    referencia_raw = f"{datos.get('rut', '')}|{email}|{codigo_otp}|{ahora.isoformat()}"
    referencia = hashlib.sha256(referencia_raw.encode("utf-8")).hexdigest()[:10].upper()

    pdf_bytes = generar_pdf_contrato(datos, verificado_en=verificado_en, referencia=referencia)

    nombre_completo = " ".join(
        p for p in (datos.get("nombres"), datos.get("apellido_paterno"), datos.get("apellido_materno")) if p
    ).strip() or "Guardia"
    filename = f"Contrato Diario - {nombre_completo}.pdf"

    html_body = f"""
      <div style="font-family:Arial,sans-serif;font-size:14px;color:#2d3436;line-height:1.6;">
        <p>Se firmó y generó un nuevo Contrato Diario.</p>
        <p>
          <strong>Trabajador:</strong> {nombre_completo}<br>
          <strong>RUT:</strong> {datos.get('rut', '')}<br>
          <strong>Correo:</strong> {email}<br>
          <strong>Firmado el:</strong> {verificado_en}<br>
          <strong>Referencia:</strong> {referencia}
        </p>
        <p>El contrato en PDF va adjunto a este correo.</p>
      </div>"""
    plain_body = (
        f"Contrato Diario firmado.\nTrabajador: {nombre_completo}\nRUT: {datos.get('rut', '')}\n"
        f"Correo: {email}\nFirmado el: {verificado_en}\nReferencia: {referencia}"
    )

    _send_contacto_email(
        to=[_DESTINATARIO_CONTRATO],
        subject=f"Contrato Diario firmado — {nombre_completo}",
        html_body=html_body,
        plain_body=plain_body,
        attachment=(filename, pdf_bytes),
    )
