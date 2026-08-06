from __future__ import annotations

import html
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.orm import Session

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from ATC.app.models.compras import SolicitudCompra
from ATC.app.models.user import User
from ATC.app.core.config import settings

if TYPE_CHECKING:
    pass

# ──────────────────────────────────────────────
# Constantes de negocio
# ──────────────────────────────────────────────

GESTORS_COMPRA = [
    "gponce@soporteatc.cl",
    "compras@alguientecuida.cl",
    "finanzas@alguientecuida.cl",
    "contabilidad@alguientecuida.cl",
    "nguzman@alguientecuida.cl",
]

OPERACIONES_COMPRA_EMAILS = [
    "czamora@alguientecuida.cl",
]

CONTABILIDAD_COMPRA_EMAILS = [
    "nguzman@alguientecuida.cl",
    "contabilidad@alguientecuida.cl",
]

GERENCIA_EMAIL = "glubiano@alguientecuida.cl"

LOGO_URL = "https://i.imgur.com/VgLG9Ei.png"

UMBRAL_GERENCIA = 500_000  # CLP

# Áreas que saltan la aprobación de Operaciones (van directo a Contabilidad)
BYPASS_OPS_AREAS = {"operaciones", "administracion", "coordinacion", "finanzas", "rrhh"}

# Usuarios especificos que saltan Operaciones sin importar su departamento
# (pedido explicito, jul 2026: Hector Rosales, sus solicitudes van directo a
# Contabilidad sin pasar por ningun aprobador intermedio).
BYPASS_OPS_EMAILS = {"compras@alguientecuida.cl"}

# Mapa de department-string → area_code (subconjunto relevante para compras)
_DEPT_AREA_MAP: dict[str, str] = {
    "soporte": "soporte",
    "materiales": "materiales",
    "servicio tecnico": "servicio_tecnico",
    "servicio técnico": "servicio_tecnico",
    "tecnicos": "tecnicos",
    "técnicos": "tecnicos",
    "operador": "operaciones",
    "coordinacion": "coordinacion",
    "coordinación": "coordinacion",
    "comercial": "venta",
    "finanzas": "finanzas",
    "administracion": "administracion",
    "administración": "administracion",
    "operaciones": "operaciones",
    "guardia": "guardia",
    "rrhh": "rrhh",
    "recursos humanos": "rrhh",
}

# Directorio donde se guardan los presupuestos adjuntos
_UPLOADS_ROOT = Path(__file__).resolve().parents[2] / "uploads" / "presupuestos"
_ATC_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


# ──────────────────────────────────────────────
# Helpers de área
# ──────────────────────────────────────────────

def _area_codes_for_user(user: User) -> list[str]:
    parts = re.split(r"[;,|]+", (user.department or "").strip())
    codes: list[str] = []
    for p in parts:
        code = _DEPT_AREA_MAP.get(p.strip().casefold())
        if code and code not in codes:
            codes.append(code)
    return codes


def requiere_aprobacion_operaciones(user: User) -> bool:
    """True → la solicitud pasa primero por Operaciones antes de Contabilidad."""
    email = (user.email or user.username or "").strip().casefold()
    if email in BYPASS_OPS_EMAILS:
        return False
    codes = set(_area_codes_for_user(user))
    return not bool(codes & BYPASS_OPS_AREAS)


# ──────────────────────────────────────────────
# Correlativo
# ──────────────────────────────────────────────

def generar_correlativo(db: Session, empresa: str) -> str:
    year = datetime.now(timezone.utc).strftime("%y")  # "26"
    prefix = f"{empresa.upper()}-"
    suffix = f"-{year}"

    rows = db.execute(
        text("SELECT codigo FROM solicitudes_compra WHERE codigo LIKE :pat"),
        {"pat": f"{prefix}%{suffix}"},
    ).fetchall()

    max_n = 0
    for (codigo,) in rows:
        parts = str(codigo).split("-")
        if len(parts) == 3 and parts[0] == empresa.upper() and parts[2] == year:
            try:
                n = int(parts[1])
                if n > max_n:
                    max_n = n
            except ValueError:
                pass

    next_n = str(max_n + 1).zfill(4)
    return f"{empresa.upper()}-{next_n}-{year}"


# ──────────────────────────────────────────────
# Email HTML builder
# ──────────────────────────────────────────────

def _esc(value: object) -> str:
    return html.escape(str(value or ""))


def _fmt_clp(amount: int) -> str:
    return f"${amount:,.0f}".replace(",", ".")


def build_email_html(
    *,
    titulo: str,
    badge_text: str,
    badge_color: str,
    intro_html: str,
    solicitante_nombre: str,
    solicitante_email: str,
    items: list[dict],
    motivo: str,
    footer_note: str = "",
) -> str:
    items_rows = ""
    total = 0
    for idx, it in enumerate(items, 1):
        label = it.get("texto", "")
        cantidad = int(it.get("cantidad", 1))
        unitario = int(it.get("precio_unitario", 0))
        subtotal = int(it.get("subtotal", 0)) or cantidad * unitario
        total += subtotal
        es_link = it.get("es_link", False)

        display = (
            f'<a href="{_esc(label)}" target="_blank">ÍTEM {idx}</a>'
            if es_link
            else f"<strong>{_esc(label)}</strong>"
        )
        items_rows += f"""
          <li style="margin:6px 0;">
            {display}
            <div style="font-size:13px;color:#555;margin-top:3px;">
              Cantidad: {cantidad} | Unitario: {_fmt_clp(unitario)} | Subtotal: {_fmt_clp(subtotal)}
            </div>
          </li>"""

    items_html = f"""
      <div style="background:#ecf0f1;border-left:4px solid {badge_color};padding:12px 16px;margin:22px 0;font-size:14px;">
        <div style="font-weight:600;margin-bottom:8px;">Ítem(s) solicitado(s):</div>
        <ul style="margin:0;padding-left:18px;">{items_rows}</ul>
        <div style="margin-top:14px;text-align:right;font-size:15px;font-weight:700;">
          Total: {_fmt_clp(total)}
        </div>
      </div>"""

    motivo_html = ""
    if motivo:
        motivo_html = f"""
      <div style="margin:18px 0;font-size:14px;line-height:1.6;">
        <div style="font-weight:600;margin-bottom:6px;">Motivo de la compra:</div>
        <div style="white-space:pre-wrap;">{_esc(motivo)}</div>
      </div>"""

    solicitante_html = f"""
      <div style="margin:18px 0;font-size:14px;line-height:1.6;">
        <div style="font-weight:600;margin-bottom:6px;">Solicitante:</div>
        <div>{_esc(solicitante_nombre)} &lt;{_esc(solicitante_email)}&gt;</div>
      </div>"""

    rechazo_html = ""
    if footer_note:
        rechazo_html = f"""
      <div style="background:#fdecea;border-left:4px solid #c0392b;padding:14px 16px;
                  margin:22px 0;font-size:14px;line-height:1.6;">
        <div style="font-weight:700;margin-bottom:6px;color:#c0392b;">Motivo del rechazo:</div>
        <div>{_esc(footer_note)}</div>
      </div>"""

    return f"""
<div style="background:#f5f6fa;padding:40px 0;font-family:'Segoe UI',Arial,sans-serif;color:#2d3436;">
  <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:8px;
              padding:30px;box-shadow:0 2px 10px rgba(0,0,0,0.05);">
    <div style="text-align:center;margin-bottom:18px;">
      <img src="{LOGO_URL}" alt="Alguien Te Cuida" style="height:55px;">
    </div>
    <div style="text-align:center;margin:6px 0 14px;">
      <h2 style="margin:0;color:#2d3436;font-size:18px;">{_esc(titulo)}</h2>
      <span style="display:inline-block;margin-top:10px;padding:6px 10px;border-radius:999px;
                   font-size:12px;font-weight:700;color:#fff;background:{badge_color};">
        {_esc(badge_text)}
      </span>
    </div>
    <div style="font-size:14px;line-height:1.7;">{intro_html}</div>
    {solicitante_html}
    {items_html}
    {motivo_html}
    {rechazo_html}
    <p style="margin-top:26px;font-size:14px;line-height:1.6;">
      Saludos cordiales,<br>Alguien Te Cuida
    </p>
    <hr style="border:0;border-top:1px solid #ddd;margin:26px 0;">
    <p style="font-size:12px;color:#999;text-align:center;line-height:1.5;">
      Este correo ha sido generado automáticamente como parte del proceso interno de compras.
    </p>
  </div>
</div>"""


# ──────────────────────────────────────────────
# Usuarios de área para notificaciones
# ──────────────────────────────────────────────

def _emails_area(db: Session, area_code: str) -> list[str]:
    """Emails de usuarios activos con el área dada, según su campo department."""
    users: list[User] = db.query(User).filter(User.is_active == True).all()  # noqa: E712
    out: list[str] = []
    for u in users:
        if area_code in _area_codes_for_user(u) and u.email:
            out.append(u.email)
    return out or []


# ──────────────────────────────────────────────
# Envíos de correo por etapa
# ──────────────────────────────────────────────


def _env_compras() -> dict[str, str]:
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


def _smtp_send(to: list[str], subject: str, html_body: str) -> None:
    """Envía usando contacto@alguientecuida.cl si está configurado."""
    env_file = _env_compras()

    def env_get(*keys: str, default: str = "") -> str:
        for key in keys:
            value = str(os.getenv(key) or env_file.get(key) or "").strip()
            if value:
                return value
        return default

    username = env_get("CONTACTO_SMTP_USERNAME", default=settings.smtp_username or settings.SMTP_USER or "")
    password = env_get("CONTACTO_SMTP_PASSWORD", default=settings.smtp_password or settings.SMTP_PASSWORD or "")
    host = env_get("CONTACTO_SMTP_HOST", default=settings.smtp_host or settings.SMTP_HOST or "smtp.gmail.com")
    try:
        port = int(env_get("CONTACTO_SMTP_PORT", default=str(settings.smtp_port or settings.SMTP_PORT or 587)))
    except Exception:
        port = 587
    from_name = env_get("CONTACTO_SMTP_FROM_NAME", default="Alguien Te Cuida")
    from_addr = env_get("CONTACTO_SMTP_FROM_EMAIL", default=username)
    use_tls = env_get("CONTACTO_SMTP_USE_TLS", default="true").lower() not in {"0", "false", "no", "off"}

    if not username or not password:
        print(f"[compras_service] SMTP no configurado (username={username!r})")
        return

    msg = EmailMessage()
    msg["From"]    = f"{from_name} <{from_addr}>"
    msg["To"]      = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"]    = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="alguientecuida.cl")
    msg.set_content(subject)
    msg.add_alternative(html_body, subtype="html")

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=25) as srv:
            srv.ehlo()
            if use_tls:
                srv.starttls(context=ctx)
                srv.ehlo()
            srv.login(username, password)
            srv.send_message(msg)
    except Exception as exc:
        print(f"[compras_service] SMTP error: {exc}")


def _send(to: list[str], subject: str, body: str, *, reply_to: str | None = None) -> None:
    if not to:
        return

    def _do() -> None:
        _smtp_send(to, subject, body)

    threading.Thread(target=_do, daemon=True).start()


def enviar_email_operaciones(solicitud: SolicitudCompra, base_url: str, db: Session) -> None:
    token = solicitud.decision_token or ""
    aprobar_url = f"{base_url}/compras/decision?token={token}&accion=aprobar"
    rechazar_url = f"{base_url}/compras/decision?token={token}&accion=rechazar"

    intro = f"""
      <p>Se registró una nueva solicitud de compra que requiere aprobación de Operaciones.</p>
      <p><strong>Código:</strong> {_esc(solicitud.codigo)}</p>
      <div style="margin:22px 0;text-align:center;">
        <a href="{aprobar_url}"
           style="background:#2ecc71;color:#fff;padding:10px 18px;text-decoration:none;
                  border-radius:6px;font-weight:600;margin-right:10px;display:inline-block;">
          Aprobar
        </a>
        <a href="{rechazar_url}"
           style="background:#e74c3c;color:#fff;padding:10px 18px;text-decoration:none;
                  border-radius:6px;font-weight:600;display:inline-block;">
          Rechazar
        </a>
      </div>"""

    body = build_email_html(
        titulo="Solicitud pendiente de aprobación",
        badge_text="PENDIENTE OPERACIONES",
        badge_color="#f1c40f",
        intro_html=intro,
        solicitante_nombre=solicitud.solicitante_nombre,
        solicitante_email=solicitud.solicitante_email,
        items=solicitud.items,
        motivo=solicitud.motivo,
    )

    destinatarios = OPERACIONES_COMPRA_EMAILS
    _send(destinatarios, f"Aprobación requerida – {solicitud.codigo}", body)


def enviar_email_contabilidad(solicitud: SolicitudCompra, base_url: str, db: Session) -> None:
    panel_url = f"{base_url}/compras/panel-contabilidad"
    token = solicitud.decision_token or ""
    aprobar_url = f"{base_url}/compras/decision?token={token}&accion=aprobar"
    rechazar_url = f"{base_url}/compras/decision?token={token}&accion=rechazar"

    intro = f"""
      <p>Nueva solicitud de compra pendiente de revisión contable.</p>
      <p><strong>Código:</strong> {_esc(solicitud.codigo)}</p>
      <div style="margin:22px 0;text-align:center;">
        <a href="{aprobar_url}"
           style="background:#2ecc71;color:#fff;padding:10px 18px;text-decoration:none;
                  border-radius:6px;font-weight:600;margin-right:10px;display:inline-block;">
          Aprobar
        </a>
        <a href="{rechazar_url}"
           style="background:#e74c3c;color:#fff;padding:10px 18px;text-decoration:none;
                  border-radius:6px;font-weight:600;display:inline-block;">
          Rechazar
        </a>
      </div>
      <p>Panel: <a href="{panel_url}">{panel_url}</a></p>"""

    body = build_email_html(
        titulo="Nueva solicitud de compra",
        badge_text="PENDIENTE CONTABILIDAD",
        badge_color="#3498db",
        intro_html=intro,
        solicitante_nombre=solicitud.solicitante_nombre,
        solicitante_email=solicitud.solicitante_email,
        items=solicitud.items,
        motivo=solicitud.motivo,
    )

    destinatarios = CONTABILIDAD_COMPRA_EMAILS
    _send(destinatarios, f"Solicitud pendiente – {solicitud.codigo}", body)


def enviar_email_gerencia(solicitud: SolicitudCompra, base_url: str) -> None:
    token = solicitud.decision_token or ""
    aprobar_url = f"{base_url}/compras/decision?token={token}&accion=aprobar"
    rechazar_url = f"{base_url}/compras/decision?token={token}&accion=rechazar"

    intro = f"""
      <p>La solicitud <strong>{_esc(solicitud.codigo)}</strong> supera los
         <strong>{_fmt_clp(UMBRAL_GERENCIA)}</strong> y requiere aprobación de Gerencia.</p>
      <div style="margin:22px 0;text-align:center;">
        <a href="{aprobar_url}"
           style="background:#2ecc71;color:#fff;padding:10px 18px;text-decoration:none;
                  border-radius:6px;font-weight:600;margin-right:10px;display:inline-block;">
          Aprobar
        </a>
        <a href="{rechazar_url}"
           style="background:#e74c3c;color:#fff;padding:10px 18px;text-decoration:none;
                  border-radius:6px;font-weight:600;display:inline-block;">
          Rechazar
        </a>
      </div>"""

    body = build_email_html(
        titulo="Aprobación de Gerencia requerida",
        badge_text="PENDIENTE GERENCIA",
        badge_color="#8e44ad",
        intro_html=intro,
        solicitante_nombre=solicitud.solicitante_nombre,
        solicitante_email=solicitud.solicitante_email,
        items=solicitud.items,
        motivo=solicitud.motivo,
    )
    _send([GERENCIA_EMAIL], f"Aprobación Gerencia – {solicitud.codigo}", body)


def enviar_email_gestors(solicitud: SolicitudCompra) -> None:
    intro = f"""
      <p>Se ha aprobado una solicitud de compra y está lista para gestionarse.</p>
      <p><strong>Código:</strong> {_esc(solicitud.codigo)}</p>"""

    body = build_email_html(
        titulo="Nueva solicitud de compra",
        badge_text="PENDIENTE",
        badge_color="#6c5ce7",
        intro_html=intro,
        solicitante_nombre=solicitud.solicitante_nombre,
        solicitante_email=solicitud.solicitante_email,
        items=solicitud.items,
        motivo=solicitud.motivo,
    )
    _send(GESTORS_COMPRA, f"Nueva solicitud – {solicitud.codigo}", body)


def enviar_email_rechazo(solicitud: SolicitudCompra, etapa: str, motivo_rechazo: str) -> None:
    intro = f"""
      <p>Hola <strong>{_esc(solicitud.solicitante_nombre)}</strong>,</p>
      <p>Tu solicitud de compra <strong>{_esc(solicitud.codigo)}</strong>
         fue <strong>rechazada por {_esc(etapa)}</strong>.</p>"""

    body = build_email_html(
        titulo="Solicitud de compra rechazada",
        badge_text="RECHAZADA",
        badge_color="#c0392b",
        intro_html=intro,
        solicitante_nombre=solicitud.solicitante_nombre,
        solicitante_email=solicitud.solicitante_email,
        items=solicitud.items,
        motivo=solicitud.motivo,
        footer_note=motivo_rechazo,
    )
    _send([solicitud.solicitante_email], f"Solicitud rechazada – {solicitud.codigo}", body)


def enviar_email_cambio_estado(solicitud: SolicitudCompra, nuevo_estado: str) -> None:
    config = {
        "En proceso": (
            "Solicitud en proceso", "EN PROCESO", "#e67e22",
            "Tu solicitud se encuentra <strong>en proceso</strong> de envío.",
        ),
        "Terminado": (
            "Compra finalizada", "TERMINADA", "#2ecc71",
            "Tu solicitud fue <strong>finalizada con éxito</strong>.",
        ),
    }
    if nuevo_estado not in config:
        return

    titulo, badge, color, frase = config[nuevo_estado]
    intro = f"""
      <p>Hola <strong>{_esc(solicitud.solicitante_nombre)}</strong>,</p>
      <p>{frase}</p>
      <p><strong>Código:</strong> {_esc(solicitud.codigo)}</p>"""

    body = build_email_html(
        titulo=titulo,
        badge_text=badge,
        badge_color=color,
        intro_html=intro,
        solicitante_nombre=solicitud.solicitante_nombre,
        solicitante_email=solicitud.solicitante_email,
        items=solicitud.items,
        motivo=solicitud.motivo,
    )
    _send([solicitud.solicitante_email], f"{titulo} – {solicitud.codigo}", body)


# ──────────────────────────────────────────────
# Guardar presupuestos en disco
# ──────────────────────────────────────────────

def guardar_presupuesto(codigo: str, filename: str, data: bytes) -> str:
    """Guarda el archivo y devuelve la ruta relativa para servir en /uploads/..."""
    _UPLOADS_ROOT.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
    dest = _UPLOADS_ROOT / f"{codigo}_{safe_name}"
    dest.write_bytes(data)
    return f"/uploads/presupuestos/{dest.name}"


# ──────────────────────────────────────────────
# Crear solicitud (flujo completo)
# ──────────────────────────────────────────────

def crear_solicitud(
    *,
    db: Session,
    user: User,
    empresa: str,
    centro: str,
    items: list[dict],
    motivo: str,
    archivos: list[tuple[str, bytes]],  # [(filename, bytes), ...]
    base_url: str,
) -> SolicitudCompra:
    total = sum(it.get("subtotal", 0) for it in items)
    codigo = generar_correlativo(db, empresa)

    usa_ops = requiere_aprobacion_operaciones(user)
    estado_inicial = "Pendiente Operaciones" if usa_ops else "Pendiente Contabilidad"

    solicitud = SolicitudCompra(
        codigo=codigo,
        empresa=empresa.upper(),
        centro=centro,
        solicitante_email=user.email or user.username,
        solicitante_nombre=user.name,
        items_json=json.dumps(items, ensure_ascii=False),
        motivo=motivo,
        estado=estado_inicial,
        total_compra=total,
        decision_token=str(uuid.uuid4()),
    )

    # Presupuestos
    rutas = []
    for filename, data in archivos[:3]:
        ruta = guardar_presupuesto(codigo, filename, data)
        rutas.append(ruta)

    if len(rutas) > 0:
        solicitud.presupuesto_1 = rutas[0]
    if len(rutas) > 1:
        solicitud.presupuesto_2 = rutas[1]
    if len(rutas) > 2:
        solicitud.presupuesto_3 = rutas[2]

    db.add(solicitud)
    db.commit()
    db.refresh(solicitud)

    # Notificación inicial
    if usa_ops:
        enviar_email_operaciones(solicitud, base_url, db)
    else:
        enviar_email_contabilidad(solicitud, base_url, db)

    return solicitud


# ──────────────────────────────────────────────
# Procesar decisión desde link de correo
# ──────────────────────────────────────────────

def procesar_decision(
    *,
    db: Session,
    token: str,
    accion: str,  # "aprobar" | "rechazar"
    obs: str,
    base_url: str,
) -> dict:
    """
    Retorna {"ok": True, "mensaje": ..., "titulo": ..., "color": ...}
    o lanza ValueError con mensaje de error.
    """
    solicitud = (
        db.query(SolicitudCompra).filter(SolicitudCompra.decision_token == token).first()
    )
    if not solicitud:
        raise ValueError("Token inválido o ya utilizado.")

    estado = solicitud.estado

    # ── RECHAZO ────────────────────────────────────────────────────────
    if accion == "rechazar":
        if not obs:
            raise ValueError("Se requiere motivo de rechazo.")

        if estado == "Pendiente Operaciones":
            solicitud.estado = "Rechazado Operaciones"
            etapa = "Operaciones"
        elif estado == "Pendiente Contabilidad":
            solicitud.estado = "Rechazado Contabilidad"
            etapa = "Contabilidad"
        elif estado == "Pendiente Gerencia":
            solicitud.estado = "Rechazado Gerencia"
            etapa = "Gerencia"
        else:
            raise ValueError(f"Estado '{estado}' no permite rechazo por este medio.")

        solicitud.observaciones = obs
        solicitud.decision_token = str(uuid.uuid4())  # invalidar token
        db.commit()

        enviar_email_rechazo(solicitud, etapa, obs)

        return {
            "ok": True,
            "titulo": "Solicitud rechazada",
            "mensaje": f"La solicitud {solicitud.codigo} fue rechazada. El solicitante fue notificado.",
            "color": "#c0392b",
        }

    # ── APROBACIÓN ─────────────────────────────────────────────────────
    if accion == "aprobar":
        if estado == "Pendiente Operaciones":
            solicitud.estado = "Pendiente Contabilidad"
            solicitud.decision_token = str(uuid.uuid4())
            db.commit()
            db.refresh(solicitud)
            enviar_email_contabilidad(solicitud, base_url, db)
            return {
                "ok": True,
                "titulo": "Enviado a Contabilidad",
                "mensaje": f"{solicitud.codigo} aprobado por Operaciones. Ahora en revisión de Contabilidad.",
                "color": "#3498db",
            }

        if estado == "Pendiente Contabilidad":
            if solicitud.total_compra > UMBRAL_GERENCIA:
                solicitud.estado = "Pendiente Gerencia"
                solicitud.decision_token = str(uuid.uuid4())
                db.commit()
                db.refresh(solicitud)
                enviar_email_gerencia(solicitud, base_url)
                return {
                    "ok": True,
                    "titulo": "Enviado a Gerencia",
                    "mensaje": f"{solicitud.codigo} supera {_fmt_clp(UMBRAL_GERENCIA)}. Pendiente aprobación de Gerencia.",
                    "color": "#8e44ad",
                }
            else:
                solicitud.estado = "Pendiente"
                solicitud.decision_token = str(uuid.uuid4())
                db.commit()
                db.refresh(solicitud)
                enviar_email_gestors(solicitud)
                return {
                    "ok": True,
                    "titulo": "Solicitud aprobada",
                    "mensaje": f"{solicitud.codigo} aprobado por Contabilidad. Los gestores de compra fueron notificados.",
                    "color": "#2ecc71",
                }

        if estado == "Pendiente Gerencia":
            solicitud.estado = "Pendiente"
            solicitud.decision_token = str(uuid.uuid4())
            db.commit()
            db.refresh(solicitud)
            enviar_email_gestors(solicitud)
            return {
                "ok": True,
                "titulo": "Solicitud aprobada",
                "mensaje": f"{solicitud.codigo} aprobado por Gerencia. Los gestores de compra fueron notificados.",
                "color": "#2ecc71",
            }

        raise ValueError(f"Estado '{estado}' no admite aprobación por este medio.")

    raise ValueError(f"Acción desconocida: {accion}")


# ──────────────────────────────────────────────
# Cambiar estado desde panel
# ──────────────────────────────────────────────

def cambiar_estado_panel(
    *,
    db: Session,
    solicitud_id: int,
    nuevo_estado: str,
    obs: str = "",
) -> SolicitudCompra:
    solicitud = db.get(SolicitudCompra, solicitud_id)
    if not solicitud:
        raise ValueError("Solicitud no encontrada.")

    if nuevo_estado == "Rechazado":
        if not obs:
            raise ValueError("Se requiere motivo de rechazo.")
        solicitud.estado = "Rechazado"
        solicitud.observaciones = obs
        db.commit()
        enviar_email_rechazo(solicitud, "el equipo de compras", obs)

    elif nuevo_estado in ("Pendiente", "En proceso", "Terminado"):
        solicitud.estado = nuevo_estado
        db.commit()
        if nuevo_estado in ("En proceso", "Terminado"):
            enviar_email_cambio_estado(solicitud, nuevo_estado)

    else:
        raise ValueError(f"Estado no soportado: {nuevo_estado}")

    db.refresh(solicitud)
    return solicitud
