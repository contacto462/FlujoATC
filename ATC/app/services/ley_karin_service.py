"""Comprobante de Entrega, Acuse de Recibo y Toma de Conocimiento — Protocolo
Ley Karin. Formulario público (sin login) que genera un PDF simple con los
datos ingresados: sin gráficos, sin KPIs, solo el comprobante mismo con el
mismo estilo corporativo del resto de los informes ATC.
"""
from __future__ import annotations

import io
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

_ATC_DIR = Path(__file__).resolve().parents[2]  # .../ATC
_ATC_ENV_PATH = _ATC_DIR / ".env"

_C_DARK = "#0b1424"
_C_PURPLE = "#4b1d78"
_C_ORANGE = "#f4a672"
_C_ORDK = "#c2410c"
_C_BG = "#f7f8fa"
_C_BORDER = "#e5e7eb"
_C_TEXT = "#111827"
_C_SOFT = "#4b5563"
_C_GREY = "#9ca3af"

DOCUMENTOS_LEY_KARIN = [
    "Protocolo de Prevención del Acoso Sexual, Laboral y la Violencia en el Trabajo.",
    "Procedimiento de investigación y sanción de Alguien Te Cuida SpA.",
    "Definiciones, ejemplos y señales de alerta: acoso, violencia, incivismo y sexismo.",
    "Derechos, deberes, confidencialidad, debido proceso y prohibición de represalias.",
    "Medidas preventivas, de protección y de resguardo.",
    "Formulario de denuncia y antecedentes que pueden acompañarse.",
    "Canales internos y externos para orientación o denuncia.",
    "Material impreso o acceso electrónico para consulta posterior.",
]

DECLARACIONES_LEY_KARIN = [
    "Recibí copia o acceso electrónico legible y se me indicó cómo volver a consultar la información.",
    "Recibí una explicación clara y tuve oportunidad de formular preguntas.",
    "Conozco los canales de denuncia y la prohibición de represalias por denunciar o participar de buena fe.",
    "Comprendo que una denuncia inicia una investigación y no determina por sí sola responsabilidad.",
]

# Punto 4 — Registro de la actividad de información o capacitación. Fijo
# para esta capacitación puntual: no aparece en el formulario (nadie lo
# completa a mano), pero sí en el PDF generado.
ACTIVIDAD_FECHA_HORA = "22 de julio de 2026, 08:00 hrs"
ACTIVIDAD_FECHA_DECLARACION = "22-07-2026"  # mismo formato DD-MM-AAAA del resto del documento
ACTIVIDAD_DURACION = "45 minutos"
ACTIVIDAD_MODALIDAD_MEDIO = "Charla presencial"
ACTIVIDAD_NOMBRE_INFORMA = "Bárbara Cárdenas"
ACTIVIDAD_CARGO_INFORMA = "Encargada Desarrollo Organizacional"
ACTIVIDAD_VERIFICACION = "Preguntas dirigidas"


def _fmt_fecha(value: str) -> str:
    """Acepta 'YYYY-MM-DD' (input type=date) y la devuelve como DD-MM-AAAA;
    si no matchea ese formato, devuelve el texto tal cual llegó."""
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return value


def generar_comprobante_ley_karin_pdf(data: dict) -> bytes:
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, HRFlowable, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    C_DARK = HexColor(_C_DARK)
    C_PURPLE = HexColor(_C_PURPLE)
    C_ORANGE = HexColor(_C_ORANGE)
    C_ORDK = HexColor(_C_ORDK)
    C_BG = HexColor(_C_BG)
    C_BORDER = HexColor(_C_BORDER)
    C_TEXT = HexColor(_C_TEXT)
    C_SOFT = HexColor(_C_SOFT)
    C_GREY = HexColor(_C_GREY)

    nombre = str(data.get("nombre_completo") or "").strip()
    rut = str(data.get("rut") or "").strip()
    cargo = str(data.get("cargo") or "").strip() or "Trabajador ATC"
    correo = str(data.get("correo") or "").strip()
    fecha_doc = _fmt_fecha(data.get("fecha") or "")

    documentos_marcados = set(int(i) for i in (data.get("documentos") or []))
    declaraciones_marcadas = set(int(i) for i in (data.get("declaraciones") or []))

    ahora = datetime.now()
    fecha_emision = ahora.strftime("%d/%m/%Y %H:%M")
    titulo_hdr = "COMPROBANTE DE ENTREGA, ACUSE DE RECIBO Y TOMA DE CONOCIMIENTO"
    subtitulo_hdr = "Protocolo Ley Karin · Procedimiento de investigación · Canales de denuncia"

    W, H = A4
    pad = 1.4 * cm
    HEADER_H = 2.6 * cm
    ACCENT_H = 5
    FOOTER_H = 1.0 * cm
    BODY_TOP = HEADER_H + ACCENT_H + 12
    BODY_BOT = FOOTER_H + 8
    fw = W - 2 * pad

    logo_path = _ATC_DIR / "app" / "static" / "img" / "logo-atc.png"
    if not logo_path.exists():
        logo_path = _ATC_DIR / "static" / "img" / "logo-atc.png"
    logo_w, logo_h = 2.6 * cm, 1.3 * cm

    def draw_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(C_DARK)
        canvas.rect(0, H - HEADER_H, W, HEADER_H, fill=1, stroke=0)
        if logo_path.exists():
            try:
                canvas.drawImage(
                    str(logo_path),
                    pad, H - HEADER_H + (HEADER_H - logo_h) / 2,
                    width=logo_w, height=logo_h,
                    preserveAspectRatio=True, mask="auto",
                )
            except Exception:
                pass
        tx = pad + logo_w + 0.5 * cm
        canvas.setFillColor(white)
        canvas.setFont("Helvetica-Bold", 11.5)
        canvas.drawString(tx, H - HEADER_H + 1.3 * cm, titulo_hdr)
        canvas.setFillColor(HexColor("#d8b4fe"))
        canvas.setFont("Helvetica", 8)
        canvas.drawString(tx, H - HEADER_H + 0.75 * cm, subtitulo_hdr)
        canvas.setFillColor(C_PURPLE)
        canvas.rect(0, H - HEADER_H - ACCENT_H, W, ACCENT_H, fill=1, stroke=0)
        canvas.setFillColor(C_DARK)
        canvas.rect(0, 0, W, FOOTER_H, fill=1, stroke=0)
        canvas.setFillColor(C_GREY)
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(
            W / 2, FOOTER_H / 2 - 3,
            f"Documento generado automáticamente · Alguien Te Cuida SpA · RUT 76.521.007-0 · {fecha_emision}",
        )
        canvas.drawRightString(W - pad, FOOTER_H / 2 - 3, f"Página {doc.page}")
        canvas.restoreState()

    frame = Frame(pad, BODY_BOT, fw, H - BODY_TOP - BODY_BOT,
                  leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0)
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4, pageTemplates=[PageTemplate(id="main", frames=[frame], onPage=draw_page)],
        leftMargin=0, rightMargin=0, topMargin=0, bottomMargin=0,
        title=titulo_hdr, author="Alguien Te Cuida",
    )

    st_sec = ParagraphStyle("sec", fontName="Helvetica-Bold", fontSize=10.5, textColor=C_PURPLE, leading=13, spaceBefore=12, spaceAfter=5)
    st_body = ParagraphStyle("body", fontName="Helvetica", fontSize=9, textColor=C_SOFT, leading=13.5, alignment=TA_JUSTIFY)
    st_intro = ParagraphStyle("intro", fontName="Helvetica-Oblique", fontSize=8.3, textColor=C_SOFT, leading=12, alignment=TA_JUSTIFY)
    st_label = ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=8, textColor=C_GREY, leading=10)
    st_value = ParagraphStyle("value", fontName="Helvetica", fontSize=9.5, textColor=C_TEXT, leading=12)
    st_check = ParagraphStyle("check", fontName="Helvetica", fontSize=8.5, textColor=C_TEXT, leading=12)
    st_decl = ParagraphStyle("decl", fontName="Helvetica", fontSize=9.5, textColor=C_TEXT, leading=16, alignment=TA_JUSTIFY)

    story: list = []

    story.append(Paragraph(
        "Este comprobante acredita que la persona trabajadora recibió y fue informada sobre las materias "
        "indicadas. La firma/declaración no implica renuncia de derechos, aceptación de hechos ni limita el "
        "derecho a denunciar.",
        st_intro,
    ))
    story.append(Spacer(1, 4))

    def campo(etiqueta: str, valor: str):
        return [Paragraph(etiqueta.upper(), st_label), Paragraph(valor or "—", st_value)]

    # ── 1. Identificación ──
    story.append(Paragraph("1. Identificación", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.6, color=C_BORDER, spaceAfter=6))
    t1 = Table(
        [
            [campo("Nombre completo", nombre), campo("RUT", rut)],
            [campo("Cargo", cargo), campo("Correo", correo)],
            [campo("Fecha", fecha_doc), ""],
        ],
        colWidths=[fw / 2, fw / 2],
    )
    t1.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(t1)

    # ── 2. Documentos y materias ──
    story.append(Paragraph("2. Documentos y materias entregadas o informadas", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.6, color=C_BORDER, spaceAfter=6))
    mitad = (len(DOCUMENTOS_LEY_KARIN) + 1) // 2
    col_izq = DOCUMENTOS_LEY_KARIN[:mitad]
    col_der = DOCUMENTOS_LEY_KARIN[mitad:]

    def fila_check(idx: int, texto: str):
        marca = "[X]" if idx in documentos_marcados else "[&nbsp;&nbsp;]"
        return Paragraph(f"{marca}  {texto}", st_check)

    filas_doc = []
    for i in range(mitad):
        izq = fila_check(i, col_izq[i])
        der = fila_check(mitad + i, col_der[i]) if i < len(col_der) else ""
        filas_doc.append([izq, der])
    t2 = Table(filas_doc, colWidths=[fw / 2, fw / 2])
    t2.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(t2)
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "<b>Canales internos:</b> denuncias@alguientecuida.cl · RR.HH. · Desarrollo Organizacional · "
        "Jefatura Directa · Prevención de Riesgos.<br/>"
        "<b>Canales externos:</b> Dirección del Trabajo y organismo administrador de la Ley N° 16.744, "
        "según corresponda.",
        st_body,
    ))

    # ── 3. Declaración de recepción y toma de conocimiento ──
    story.append(Paragraph("3. Declaración de recepción y toma de conocimiento", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.6, color=C_BORDER, spaceAfter=6))
    mitad2 = (len(DECLARACIONES_LEY_KARIN) + 1) // 2
    col_izq2 = DECLARACIONES_LEY_KARIN[:mitad2]
    col_der2 = DECLARACIONES_LEY_KARIN[mitad2:]

    def fila_check2(idx: int, texto: str):
        marca = "[X]" if idx in declaraciones_marcadas else "[&nbsp;&nbsp;]"
        return Paragraph(f"{marca}  {texto}", st_check)

    filas_decl = []
    for i in range(mitad2):
        izq = fila_check2(i, col_izq2[i])
        der = fila_check2(mitad2 + i, col_der2[i]) if i < len(col_der2) else ""
        filas_decl.append([izq, der])
    t3 = Table(filas_decl, colWidths=[fw / 2, fw / 2])
    t3.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(t3)

    # ── 4. Registro de la actividad de información o capacitación ──
    story.append(Paragraph("4. Registro de la actividad de información o capacitación", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.6, color=C_BORDER, spaceAfter=6))
    t4 = Table(
        [
            [campo("Fecha y hora", ACTIVIDAD_FECHA_HORA), campo("Duración", ACTIVIDAD_DURACION)],
            [campo("Modalidad / medio", ACTIVIDAD_MODALIDAD_MEDIO), campo("Nombre de quien informa", ACTIVIDAD_NOMBRE_INFORMA)],
            [campo("Cargo, profesión u oficio", ACTIVIDAD_CARGO_INFORMA), campo("Verificación de comprensión", ACTIVIDAD_VERIFICACION)],
        ],
        colWidths=[fw / 2, fw / 2],
    )
    t4.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(t4)

    # ── 5. Declaración ──
    story.append(Paragraph("5. Declaración de toma de conocimiento", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.6, color=C_BORDER, spaceAfter=8))
    fecha_decl = ACTIVIDAD_FECHA_DECLARACION
    texto_decl = (
        f"Yo, <b>{nombre or '________________________'}</b>, "
        f"RUT <b>{rut or '____________'}</b>, declaro que con fecha "
        f"<b>{fecha_decl or '____________'}</b> recibí y tomé conocimiento íntegro de los documentos, "
        f"materias y canales de denuncia señalados en el presente comprobante, en el marco de la actividad "
        f"de información/capacitación individualizada en el punto 4. Formulo esta declaración en señal de "
        f"conformidad con lo antes expuesto, sin que ello implique renuncia de derechos, aceptación de "
        f"hechos ni limite mi derecho a denunciar."
    )
    story.append(Paragraph(texto_decl, st_decl))

    doc.build(story)
    return buf.getvalue()


# ──────────────────────────────────────────────
# Envío del comprobante por correo (cuenta contacto@alguientecuida.cl, ya
# provisionada en .env — mismo patrón que contrato_diario_service.py)
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


def enviar_comprobante_ley_karin_email(destinatario: str, pdf_bytes: bytes, nombre_completo: str) -> None:
    """Manda el comprobante ya generado al correo indicado en el formulario.
    Pensada para correr como BackgroundTask (no bloquea la descarga del PDF);
    cualquier error de envío queda solo logueado por Starlette, no interrumpe
    nada para quien llenó el formulario."""
    destinatario = (destinatario or "").strip()
    if not destinatario:
        return

    cfg = _contacto_smtp_config()
    username = cfg["username"]
    password = cfg["password"]
    if not username or not password:
        raise RuntimeError("CONTACTO_SMTP_USERNAME/CONTACTO_SMTP_PASSWORD no configurados en .env")

    from_addr = cfg["from_addr"] or username
    host = cfg["host"]
    port = int(cfg["port"] or 587)
    use_tls = cfg["use_tls"].lower() not in {"0", "false", "no", "off"}

    nombre = (nombre_completo or "").strip() or "trabajador(a)"
    html_body = f"""
      <div style="font-family:Arial,sans-serif;font-size:14px;color:#2d3436;line-height:1.6;">
        <p>Hola {nombre},</p>
        <p>Adjuntamos tu Comprobante de Entrega, Acuse de Recibo y Toma de Conocimiento —
        Protocolo Ley Karin.</p>
        <p>Alguien Te Cuida</p>
      </div>"""
    plain_body = f"Hola {nombre},\nAdjuntamos tu Comprobante Ley Karin.\nAlguien Te Cuida"

    msg = EmailMessage()
    msg["From"] = f"{cfg['from_name']} <{from_addr}>"
    msg["To"] = destinatario
    msg["Subject"] = "Comprobante Ley Karin — Alguien Te Cuida"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="alguientecuida.cl")
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")

    nombre_ascii = "".join(c if c.isalnum() else "_" for c in f"Comprobante_LeyKarin_{nombre}")[:80]
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=f"{nombre_ascii}.pdf")

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=25) as srv:
        srv.ehlo()
        if use_tls:
            srv.starttls(context=ctx)
            srv.ehlo()
        srv.login(username, password)
        srv.send_message(msg)


# ──────────────────────────────────────────────
# Toma de Conocimiento — Capacitación Ley Karin (jefaturas)
# Documento acotado a UNA capacitación puntual: código ATC-LK-TC-001,
# distinto del comprobante general de arriba (que cubre entrega de
# documentos + canales + declaración de recepción en general).
# ──────────────────────────────────────────────

TC_CAPACITACION_DECLARACIONES = [
    "Promover un ambiente de trabajo respetuoso y prevenir conductas de acoso, violencia, discriminación, "
    "incivilidad y sexismo.",
    "Recibir y derivar oportunamente cualquier denuncia o relato al canal institucional correspondiente, sin "
    "investigar por cuenta propia ni emitir juicios anticipados.",
    "Resguardar la confidencialidad, evitar represalias y prevenir la revictimización de las personas "
    "involucradas.",
    "Colaborar con las medidas de resguardo e instrucciones que determine la empresa durante el procedimiento.",
    "Conocer y aplicar el Protocolo de Prevención, el Procedimiento de Investigación y los canales internos "
    "vigentes de ALGUIEN TE CUIDA SPA.",
]

TC_CAPACITACION_CODIGO = "ATC-LK-TC-001"
TC_CAPACITACION_VERSION = "001"
TC_CAPACITACION_AREA_RESPONSABLE = "Desarrollo Organizacional / RR.HH."


def generar_toma_conocimiento_capacitacion_pdf(data: dict) -> bytes:
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, HRFlowable, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    C_DARK = HexColor(_C_DARK)
    C_PURPLE = HexColor(_C_PURPLE)
    C_ORANGE = HexColor(_C_ORANGE)
    C_BG = HexColor(_C_BG)
    C_BORDER = HexColor(_C_BORDER)
    C_TEXT = HexColor(_C_TEXT)
    C_SOFT = HexColor(_C_SOFT)
    C_GREY = HexColor(_C_GREY)

    nombre = str(data.get("nombre_completo") or "").strip()
    rut = str(data.get("rut") or "").strip()
    cargo = str(data.get("cargo") or "").strip() or "Trabajador ATC"
    modalidad = str(data.get("modalidad") or "").strip() or "—"
    fecha_capacitacion = _fmt_fecha(data.get("fecha_capacitacion") or "")

    ahora = datetime.now()
    fecha_doc = ahora.strftime("%d/%m/%Y")
    fecha_emision = ahora.strftime("%d/%m/%Y %H:%M")
    titulo_hdr = "TOMA DE CONOCIMIENTO — CAPACITACIÓN LEY N.º 21.643 (LEY KARIN)"

    W, H = A4
    pad = 1.4 * cm
    HEADER_H = 2.6 * cm
    ACCENT_H = 5
    FOOTER_H = 1.0 * cm
    BODY_TOP = HEADER_H + ACCENT_H + 12
    BODY_BOT = FOOTER_H + 8
    fw = W - 2 * pad

    logo_path = _ATC_DIR / "app" / "static" / "img" / "logo-atc.png"
    if not logo_path.exists():
        logo_path = _ATC_DIR / "static" / "img" / "logo-atc.png"
    logo_w, logo_h = 2.6 * cm, 1.3 * cm

    def draw_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(C_DARK)
        canvas.rect(0, H - HEADER_H, W, HEADER_H, fill=1, stroke=0)
        if logo_path.exists():
            try:
                canvas.drawImage(
                    str(logo_path),
                    pad, H - HEADER_H + (HEADER_H - logo_h) / 2,
                    width=logo_w, height=logo_h,
                    preserveAspectRatio=True, mask="auto",
                )
            except Exception:
                pass
        tx = pad + logo_w + 0.5 * cm
        canvas.setFillColor(white)
        canvas.setFont("Helvetica-Bold", 11.5)
        canvas.drawString(tx, H - HEADER_H / 2 - 4, titulo_hdr)
        canvas.setFillColor(C_PURPLE)
        canvas.rect(0, H - HEADER_H - ACCENT_H, W, ACCENT_H, fill=1, stroke=0)
        canvas.setFillColor(C_DARK)
        canvas.rect(0, 0, W, FOOTER_H, fill=1, stroke=0)
        canvas.setFillColor(C_GREY)
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(
            W / 2, FOOTER_H / 2 - 3,
            f"Documento generado automáticamente · Alguien Te Cuida SpA · RUT 76.521.007-0 · {fecha_emision}",
        )
        canvas.drawRightString(W - pad, FOOTER_H / 2 - 3, f"Página {doc.page}")
        canvas.restoreState()

    frame = Frame(pad, BODY_BOT, fw, H - BODY_TOP - BODY_BOT,
                  leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0)
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4, pageTemplates=[PageTemplate(id="main", frames=[frame], onPage=draw_page)],
        leftMargin=0, rightMargin=0, topMargin=0, bottomMargin=0,
        title=titulo_hdr, author="Alguien Te Cuida",
    )

    st_sec = ParagraphStyle("sec", fontName="Helvetica-Bold", fontSize=10.5, textColor=C_PURPLE, leading=13, spaceBefore=12, spaceAfter=5)
    st_body = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, textColor=C_SOFT, leading=14.5, alignment=TA_JUSTIFY)
    st_bullet = ParagraphStyle("bullet", fontName="Helvetica", fontSize=9.5, textColor=C_TEXT, leading=14, alignment=TA_JUSTIFY, leftIndent=10, bulletIndent=0)
    st_label = ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=8, textColor=C_GREY, leading=10)
    st_value = ParagraphStyle("value", fontName="Helvetica", fontSize=9.5, textColor=C_TEXT, leading=12)
    st_decl = ParagraphStyle("decl", fontName="Helvetica", fontSize=9.5, textColor=C_TEXT, leading=16, alignment=TA_JUSTIFY)
    st_footnote = ParagraphStyle("footnote", fontName="Helvetica-Oblique", fontSize=8, textColor=C_SOFT, leading=11)

    story: list = []

    def campo(etiqueta: str, valor: str):
        return [Paragraph(etiqueta.upper(), st_label), Paragraph(valor or "—", st_value)]

    # ── Metadatos del documento ──
    t0 = Table(
        [[campo("Código", TC_CAPACITACION_CODIGO), campo("Versión", TC_CAPACITACION_VERSION)],
         [campo("Fecha", fecha_doc), campo("Área responsable", TC_CAPACITACION_AREA_RESPONSABLE)]],
        colWidths=[fw / 2, fw / 2],
    )
    t0.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, C_BORDER),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
    ]))
    story.append(t0)
    story.append(Spacer(1, 6))

    # ── 1. Identificación de la persona capacitada ──
    story.append(Paragraph("1. Identificación de la persona capacitada", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.6, color=C_BORDER, spaceAfter=6))
    t1 = Table(
        [
            [campo("Nombre completo", nombre), campo("RUT", rut)],
            [campo("Cargo", cargo), campo("Fecha de capacitación o charla", fecha_capacitacion)],
            [campo("Modalidad", modalidad), ""],
        ],
        colWidths=[fw / 2, fw / 2],
    )
    t1.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(t1)

    # ── 2. Declaración de toma de conocimiento ──
    story.append(Paragraph("2. Declaración de toma de conocimiento", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.6, color=C_BORDER, spaceAfter=6))
    story.append(Paragraph(
        "Declaro haber participado en la capacitación sobre la Ley N.º 21.643 (Ley Karin) y haber recibido "
        "información clara respecto de la prevención, investigación y sanción del acoso laboral, acoso sexual "
        "y violencia en el trabajo, así como de las responsabilidades que me corresponden en mi calidad de "
        "jefatura.",
        st_body,
    ))
    story.append(Spacer(1, 6))
    for texto in TC_CAPACITACION_DECLARACIONES:
        story.append(Paragraph(f"•&nbsp;&nbsp;{texto}", st_bullet))
        story.append(Spacer(1, 3))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Declaro haber tenido la oportunidad de realizar preguntas y comprendo que este documento acredita mi "
        "participación y toma de conocimiento, sin reemplazar la lectura y cumplimiento de los documentos "
        "internos vigentes.",
        st_body,
    ))

    # ── Declaración personalizada (reemplaza a la firma) ──
    story.append(Spacer(1, 10))
    texto_decl = (
        f"Yo, <b>{nombre or '________________________'}</b>, RUT <b>{rut or '____________'}</b>, "
        f"declaro que con fecha <b>{fecha_capacitacion or '____________'}</b> participé en la capacitación "
        f"sobre la Ley N.º 21.643 (Ley Karin) individualizada en este documento y que tomé conocimiento "
        f"íntegro de las materias y responsabilidades señaladas en el punto 2. Formulo esta declaración en "
        f"señal de conformidad con lo antes expuesto, sin que ello implique renuncia de derechos ni limite "
        f"mi derecho a denunciar."
    )
    story.append(Paragraph(texto_decl, st_decl))

    story.append(Spacer(1, 18))
    story.append(Paragraph(
        "Conservar este documento en la carpeta de respaldo de capacitación y cumplimiento Ley Karin.",
        st_footnote,
    ))

    doc.build(story)
    return buf.getvalue()


def enviar_toma_conocimiento_capacitacion_email(destinatario: str, pdf_bytes: bytes, nombre_completo: str) -> None:
    """Manda la Toma de Conocimiento de Capacitación Ley Karin ya generada al
    correo indicado en el formulario. Corre como BackgroundTask (no bloquea
    la descarga del PDF); cualquier error de envío queda solo logueado."""
    destinatario = (destinatario or "").strip()
    if not destinatario:
        return

    cfg = _contacto_smtp_config()
    username = cfg["username"]
    password = cfg["password"]
    if not username or not password:
        raise RuntimeError("CONTACTO_SMTP_USERNAME/CONTACTO_SMTP_PASSWORD no configurados en .env")

    from_addr = cfg["from_addr"] or username
    host = cfg["host"]
    port = int(cfg["port"] or 587)
    use_tls = cfg["use_tls"].lower() not in {"0", "false", "no", "off"}

    nombre = (nombre_completo or "").strip() or "trabajador(a)"
    html_body = f"""
      <div style="font-family:Arial,sans-serif;font-size:14px;color:#2d3436;line-height:1.6;">
        <p>Hola {nombre},</p>
        <p>Adjuntamos tu Toma de Conocimiento — Capacitación Ley N.º 21.643 (Ley Karin).</p>
        <p>Alguien Te Cuida</p>
      </div>"""
    plain_body = f"Hola {nombre},\nAdjuntamos tu Toma de Conocimiento — Capacitación Ley Karin.\nAlguien Te Cuida"

    msg = EmailMessage()
    msg["From"] = f"{cfg['from_name']} <{from_addr}>"
    msg["To"] = destinatario
    msg["Subject"] = "Toma de Conocimiento — Capacitación Ley Karin — Alguien Te Cuida"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="alguientecuida.cl")
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")

    nombre_ascii = "".join(c if c.isalnum() else "_" for c in f"TomaConocimiento_Capacitacion_LeyKarin_{nombre}")[:80]
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=f"{nombre_ascii}.pdf")

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=25) as srv:
        srv.ehlo()
        if use_tls:
            srv.starttls(context=ctx)
            srv.ehlo()
        srv.login(username, password)
        srv.send_message(msg)
