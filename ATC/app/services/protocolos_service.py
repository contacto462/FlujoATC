from __future__ import annotations

import json
import logging
import re
import smtplib
import threading
import unicodedata
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from typing import Any
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from ATC.app.core.incidencias_config import settings
from ATC.app.core.incidencias_db import SessionLocal
from ATC.app.services.incidencias_drive_report_service import (
    DriveReportError,
    create_protocol_individual_report_pdf,
    create_protocol_weekly_report_pdf,
    download_support_drive_file_bytes,
)
from ATC.app.models.incidencias import (
    ClienteBBDD,
    LoginSession,
    ProtocoloInforme,
    ProtocoloRegistro,
    Registro,
    SucursalBBDD,
    SucursalContactoEmergencia,
    SucursalPersonaAutorizada,
    User,
)
from ATC.app.schemas.incidencias import ProtocoloRegistroCreateRequest

LOGGER = logging.getLogger(__name__)

_APP_DIR  = Path(__file__).resolve().parents[1]   # ATC/app/
_ATC_ROOT = Path(__file__).resolve().parents[2]   # ATC/


def _generar_pdf_protocolo_local(
    kind: str,
    ctx: dict[str, Any],
) -> str | None:
    """Genera un PDF corporativo de protocolo (semanal o individual) con reportlab.

    Devuelve la URL relativa /static/protocolos/informes/<nombre>.pdf,
    o None si falla.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib.colors import HexColor, white
        from reportlab.platypus import (
            BaseDocTemplate, Frame, PageTemplate,
            Table, TableStyle, Paragraph, Spacer, KeepTogether,
        )
        from reportlab.platypus import Image as RLImage
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        import io, uuid

        C_DARK   = HexColor("#0b1424")
        C_ORANGE = HexColor("#f4a672")
        C_ORDK   = HexColor("#c2410c")
        C_BG     = HexColor("#f7f8fa")
        C_BORDER = HexColor("#e5e7eb")
        C_TEXT   = HexColor("#111827")
        C_SOFT   = HexColor("#4b5563")
        C_YELLOW = HexColor("#fde68a")
        C_GREY   = HexColor("#9ca3af")
        C_OK     = HexColor("#15803d")
        C_OK_BG  = HexColor("#f0fdf4")
        C_WARN   = HexColor("#92400e")
        C_WARN_BG= HexColor("#fffbeb")

        W, H = A4
        pad       = 1.4 * cm
        HEADER_H  = 2.6 * cm
        ORANGE_H  = 5
        FOOTER_H  = 1.0 * cm
        BODY_TOP  = HEADER_H + ORANGE_H + 10
        BODY_BOT  = FOOTER_H + 8
        fw        = W - 2 * pad

        es_semanal = str(kind).upper() == "SEMANAL"
        if es_semanal:
            titulo_hdr  = "INFORME SEMANAL DE PROTOCOLOS"
            subtitulo_hdr = f"{ctx.get('cliente','') or ''}  ·  {ctx.get('sucursal','') or ''}"
        else:
            titulo_hdr  = "INFORME TÉCNICO DE PROTOCOLO"
            subtitulo_hdr = f"{ctx.get('cliente','') or ''}  ·  {ctx.get('sucursal','') or ''}"

        fecha_emision = str(ctx.get("fecha_emision") or "")
        logo_path = _ATC_ROOT / "static" / "img" / "logo-atc.png"
        logo_w, logo_h = 3.0 * cm, 1.5 * cm

        # ── Styles ───────────────────────────────────────────────────────
        st_label   = ParagraphStyle("lbl",  fontName="Helvetica-Bold", fontSize=7.5,
                                    textColor=C_SOFT,  leading=10, spaceAfter=1)
        st_value   = ParagraphStyle("val",  fontName="Helvetica",      fontSize=10,
                                    textColor=C_TEXT,  leading=13)
        st_sec     = ParagraphStyle("sec",  fontName="Helvetica-Bold", fontSize=9,
                                    textColor=C_ORDK,  leading=12, spaceBefore=12, spaceAfter=5)
        st_body    = ParagraphStyle("body", fontName="Helvetica",      fontSize=9.5,
                                    textColor=C_SOFT,  leading=14, spaceAfter=4)
        st_th      = ParagraphStyle("th",   fontName="Helvetica-Bold", fontSize=8,
                                    textColor=white,   leading=10)
        st_td      = ParagraphStyle("td",   fontName="Helvetica",      fontSize=8.5,
                                    textColor=C_TEXT,  leading=11)
        st_td_ok   = ParagraphStyle("tdok", fontName="Helvetica-Bold", fontSize=8.5,
                                    textColor=C_OK,    leading=11)
        st_td_no   = ParagraphStyle("tdno", fontName="Helvetica-Bold", fontSize=8.5,
                                    textColor=C_ORDK,  leading=11)
        st_metric_lbl = ParagraphStyle("mlbl", fontName="Helvetica-Bold", fontSize=7,
                                    textColor=C_SOFT,  leading=9, alignment=TA_CENTER)
        st_metric_val = ParagraphStyle("mval", fontName="Helvetica-Bold", fontSize=16,
                                    textColor=C_TEXT,  leading=18, alignment=TA_CENTER)

        # ── Canvas callback ───────────────────────────────────────────────
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
            canvas.setFont("Helvetica-Bold", 15)
            canvas.drawString(tx, H - HEADER_H + 1.35 * cm, titulo_hdr)
            canvas.setFillColor(C_YELLOW)
            canvas.setFont("Helvetica", 9)
            canvas.drawString(tx, H - HEADER_H + 0.52 * cm, subtitulo_hdr)
            canvas.setFillColor(C_ORANGE)
            canvas.rect(0, H - HEADER_H - ORANGE_H, W, ORANGE_H, fill=1, stroke=0)
            canvas.setFillColor(C_DARK)
            canvas.rect(0, 0, W, FOOTER_H, fill=1, stroke=0)
            canvas.setFillColor(C_GREY)
            canvas.setFont("Helvetica", 7)
            canvas.drawCentredString(
                W / 2, FOOTER_H / 2 - 3,
                f"Documento generado automáticamente  ·  Alguien Te Cuida  ·  {fecha_emision}",
            )
            canvas.restoreState()

        frame = Frame(
            pad, BODY_BOT,
            fw, H - BODY_TOP - BODY_BOT,
            leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0,
        )
        page_tmpl = PageTemplate(id="main", frames=[frame], onPage=draw_page)

        buf = io.BytesIO()
        doc = BaseDocTemplate(
            buf, pagesize=A4, pageTemplates=[page_tmpl],
            leftMargin=0, rightMargin=0, topMargin=0, bottomMargin=0,
            title=titulo_hdr, author="Alguien Te Cuida",
        )

        story: list = []

        def field(label: str, value: str):
            return [Paragraph(label, st_label), Paragraph(str(value or "-"), st_value)]

        def section_title(text: str):
            story.append(Paragraph(text, st_sec))

        def hr():
            from reportlab.platypus import HRFlowable
            story.append(HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=6))

        sep = 0.4 * cm
        cw  = (fw - sep) / 2

        def detail_table(rows_data: list[list]) -> None:
            flat = []
            for row in rows_data:
                lft = row[0]
                rgt = row[1] if len(row) > 1 and row[1] else ["", ""]
                flat.append([lft[0], lft[1], Spacer(sep, 1), rgt[0], rgt[1]])
            t = Table(
                flat,
                colWidths=[cw * 0.36, cw * 0.64, sep, cw * 0.36, cw * 0.64],
            )
            t.setStyle(TableStyle([
                ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING",   (0, 0), (-1, -1), 8),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
                ("TOPPADDING",    (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LEFTPADDING",   (2, 0), (2, -1), 0),
                ("RIGHTPADDING",  (2, 0), (2, -1), 0),
                ("TOPPADDING",    (2, 0), (2, -1), 0),
                ("BOTTOMPADDING", (2, 0), (2, -1), 0),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [white, C_BG]),
                ("LINEBELOW",     (0, 0), (-1, -2), 0.5, C_BORDER),
                ("BOX",           (0, 0), (-1, -1), 1, C_BORDER),
            ]))
            story.append(t)

        # ════════════════════════════════════════════════════════════════
        if es_semanal:
            # ── Datos generales ──────────────────────────────────────────
            detail_table([
                [field("CLIENTE",         ctx.get("cliente")),
                 field("SUCURSAL",        ctx.get("sucursal"))],
                [field("PERÍODO INICIO",  ctx.get("periodo_inicio")),
                 field("PERÍODO FIN",     ctx.get("periodo_fin"))],
            ])
            story.append(Spacer(1, 10))

            # ── Métricas (solo total y exitosos) ─────────────────────────
            metrics = [
                ("TOTAL PROTOCOLOS", str(ctx.get("total_registros", "-"))),
                ("PROTOCOLOS EXITOSOS", str(ctx.get("total_exitosos", "-"))),
            ]
            m_col = fw / len(metrics)
            m_data = [[Paragraph(lbl, st_metric_lbl) for lbl, _ in metrics],
                      [Paragraph(val, st_metric_val) for _, val in metrics]]
            m_table = Table(m_data, colWidths=[m_col] * len(metrics))
            m_table.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, -1), C_BG),
                ("BOX",           (0, 0), (-1, -1), 1, C_BORDER),
                ("LINEAFTER",     (0, 0), (-2, -1), 0.5, C_BORDER),
                ("TOPPADDING",    (0, 0), (-1, -1), 14),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
                ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(m_table)
            story.append(Spacer(1, 12))

            # ── Resumen ejecutivo ────────────────────────────────────────
            section_title("RESUMEN EJECUTIVO")
            hr()
            total = ctx.get("total_registros", 0)
            exitosos = ctx.get("total_exitosos", 0)
            sucursal_rpt = ctx.get("sucursal") or ctx.get("cliente") or "su instalación"
            periodo_rpt = f"{ctx.get('periodo_inicio','')} al {ctx.get('periodo_fin','')}".strip(" al")
            resumen = (
                f"Durante el período del {periodo_rpt}, se registraron <b>{total} protocolo(s)</b> de seguridad "
                f"en <b>{sucursal_rpt}</b>, de los cuales <b>{exitosos} resultaron exitosos</b>. "
                f"Cada ejecución quedó documentada con fecha, tipo de protocolo y observaciones formalizadas, "
                f"otorgando trazabilidad completa de las acciones de vigilancia realizadas. "
                f"Este informe certifica el cumplimiento de nuestro compromiso con la seguridad de su instalación "
                f"y constituye evidencia del monitoreo activo que Alguien Te Cuida mantiene en forma continua "
                f"para brindarle la tranquilidad y confianza que su operación merece."
            )
            story.append(Paragraph(resumen, st_body))
            story.append(Spacer(1, 10))

            # ── Detalle cronológico ──────────────────────────────────────
            filas = ctx.get("detalle_filas") or []
            if filas:
                section_title("DETALLE CRONOLÓGICO")
                hr()
                col_widths = [fw * 0.18, fw * 0.16, fw * 0.66]
                thead = [
                    Paragraph("FECHA", st_th),
                    Paragraph("TIPO", st_th),
                    Paragraph("OBSERVACIÓN", st_th),
                ]
                trows = [thead]
                for f_row in filas:
                    trows.append([
                        Paragraph(str(f_row.get("fecha") or "-"), st_td),
                        Paragraph(str(f_row.get("tipo_protocolo") or "-"), st_td),
                        Paragraph(str(f_row.get("observacion") or "-"), st_td),
                    ])
                dtable = Table(trows, colWidths=col_widths, repeatRows=1)
                dtable.setStyle(TableStyle([
                    ("BACKGROUND",    (0, 0), (-1, 0),  C_DARK),
                    ("ROWBACKGROUNDS",(0, 1), (-1, -1), [white, C_BG]),
                    ("LINEBELOW",     (0, 0), (-1, -1), 0.5, C_BORDER),
                    ("BOX",           (0, 0), (-1, -1), 1, C_BORDER),
                    ("LEFTPADDING",   (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
                    ("TOPPADDING",    (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                ]))
                story.append(dtable)

        else:
            # ── Informe individual ────────────────────────────────────────
            detail_table([
                [field("CÓDIGO DE INFORME", ctx.get("codigo_informe")),
                 field("FECHA DE EMISIÓN",  ctx.get("fecha_emision"))],
                [field("CLIENTE",  ctx.get("cliente")),
                 field("SUCURSAL", ctx.get("sucursal"))],
                [field("FECHA DE REGISTRO", ctx.get("fecha_registro")),
                 field("TIPO DE PROTOCOLO", ctx.get("tipo_protocolo"))],
                [field("ENCARGADO", ctx.get("encargado")),
                 field("OPERADOR",  ctx.get("operador"))],
                [field("GRUPO", ctx.get("grupo")),
                 field("PUESTO", ctx.get("puesto"))],
            ])
            story.append(Spacer(1, 10))

            # ── Checklist ────────────────────────────────────────────────
            section_title("CHECKLIST OPERATIVO")
            hr()
            checks = [
                ("Detectado",        ctx.get("detectado")),
                ("Efectivo",         ctx.get("efectivo")),
                ("Sirena",           ctx.get("sirena")),
                ("Voz",              ctx.get("voz")),
                ("Carabineros",      ctx.get("carabineros")),
                ("Alpha 3",          ctx.get("alpha3")),
                ("Informado",        ctx.get("informado")),
                ("Bitácora",         ctx.get("bitacora")),
                ("Protocolo exitoso",ctx.get("protocolo_exitoso")),
            ]
            n_cols = 3
            c_w = fw / n_cols
            check_rows: list[list] = []
            for i in range(0, len(checks), n_cols):
                row = []
                for lbl, val in checks[i:i + n_cols]:
                    es_ok = str(val or "").strip().upper() in ("SI", "SÍ", "YES", "S")
                    st_v = st_td_ok if es_ok else st_td_no
                    row.append(Table(
                        [[Paragraph(lbl, st_label)],
                         [Paragraph(str(val or "-"), st_v)]],
                        colWidths=[c_w - 16],
                    ))
                while len(row) < n_cols:
                    row.append("")
                check_rows.append(row)
            ch_table = Table(check_rows, colWidths=[c_w] * n_cols)
            ch_table.setStyle(TableStyle([
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [white, C_BG]),
                ("BOX",           (0, 0), (-1, -1), 1, C_BORDER),
                ("LINEAFTER",     (0, 0), (-2, -1), 0.5, C_BORDER),
                ("LINEBELOW",     (0, 0), (-1, -2), 0.5, C_BORDER),
                ("LEFTPADDING",   (0, 0), (-1, -1), 8),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
                ("TOPPADDING",    (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(ch_table)
            story.append(Spacer(1, 10))

            # ── Resumen ejecutivo ────────────────────────────────────────
            section_title("RESUMEN EJECUTIVO")
            hr()
            story.append(Paragraph(str(ctx.get("resumen_ejecutivo") or "-"), st_body))
            story.append(Spacer(1, 10))

            # ── Observaciones ────────────────────────────────────────────
            section_title("OBSERVACIÓN FORMALIZADA")
            hr()
            obs = str(ctx.get("observacion_formalizada") or ctx.get("observacion_original") or "-")
            story.append(Paragraph(obs, st_body))

        doc.build(story)

        # Guardar
        dest_dir = _APP_DIR / "static" / "protocolos" / "informes"
        dest_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        uid = uuid.uuid4().hex[:8]
        kind_slug = "sem" if es_semanal else "ind"
        nombre = f"protocolo_{kind_slug}_{ts}_{uid}.pdf"
        (dest_dir / nombre).write_bytes(buf.getvalue())
        return f"/static/protocolos/informes/{nombre}"

    except Exception:
        LOGGER.exception("Error generando PDF protocolo local kind=%s", kind)
        return None


@dataclass
class RangoFechas:
    inicio: datetime
    fin: datetime
    texto_inicio: str
    texto_fin: str
    etiqueta_mes: str = ""
    modo: str = ""


def _normalizar_clave_nombre(valor: str | None) -> str:
    txt = str(valor or "").strip().lower()
    if not txt:
        return ""
    txt = "".join(ch for ch in unicodedata.normalize("NFD", txt) if unicodedata.category(ch) != "Mn")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


ENCARGADO_GRUPO = {
    _normalizar_clave_nombre("Mery Delgado"): "Grupo B",
    _normalizar_clave_nombre("Cristian Olivares"): "Grupo B",
    _normalizar_clave_nombre("HÃ©ctor Rosales"): "Coordinador",
    _normalizar_clave_nombre("AngÃ©lica Guerra"): "Grupo A",
    _normalizar_clave_nombre("Nicolas SantibaÃ±ez"): "Grupo PT",
    _normalizar_clave_nombre("Daisy Vergara"): "Grupo PT",
    _normalizar_clave_nombre("Tahira Riquelme"): "Grupo Diurno",
    _normalizar_clave_nombre("Marian Macho"): "Grupo A",
    _normalizar_clave_nombre("Manuel Mondaca"): "Grupo PT",
}

PROMPT_FORMALIZAR_OBSERVACION = (
    "Eres un corrector ortográfico y redactor formal para informes de seguridad privada.\n"
    "Tu tarea es corregir y formalizar el siguiente texto de observación operacional.\n\n"
    "DEBES hacer siempre:\n"
    "- Corregir TODO error ortográfico sin excepción, incluyendo errores fonéticos "
    "(ej: 'cirena' → 'sirena', 'sugeto' → 'sujeto', 'Ce activa' → 'Se activa', "
    "'efectibo' → 'efectivo', 'biene' → 'viene', 'habia' → 'había').\n"
    "- Agregar todas las tildes que falten en palabras comunes del español.\n"
    "- Redactar en tono formal y técnico, sin simplificar ni omitir ningún dato.\n"
    "- Corregir mayúsculas al inicio de cada oración.\n\n"
    "DEBES conservar exactamente sin cambiar:\n"
    "- Las siglas operativas: OP, GGSS, NVR, DVR, ODT.\n"
    "- La abreviatura: cam, hrs.\n"
    "- Nombres propios de personas (ej: 'Diego Mendez' debe quedar 'Diego Méndez' solo con tilde si aplica, "
    "pero sin cambiar el nombre).\n"
    "- Códigos, números, fechas, horas y nombres de cámaras (ej: 2_3, 2_2).\n\n"
    "NUNCA hagas:\n"
    "- Inventar información que no esté en el texto original.\n"
    "- Resumir, simplificar ni omitir detalles técnicos.\n"
    "- Agregar explicaciones, comillas ni comentarios propios.\n\n"
    "Devuelve únicamente el texto corregido y formalizado, sin ningún texto adicional."
)


class ProtocolosService:
    """MigraciÃ³n base de Control de Protocolos (Apps Script -> Python).

    Objetivo inmediato:
    - dejar registro SQL centralizado
    - automatizar rangos de fechas y resÃºmenes
    - normalizar/redactar observaciones de forma automÃ¡tica (sin hardcode manual en frontend)
    """

    def __init__(self, db: Session):
        self.db = db
        self.tz = ZoneInfo(settings.timezone or "America/Santiago")

    # =========================
    # Utilidades de fecha
    # =========================
    def _fmt(self, dt: datetime, pattern: str = "%d/%m/%Y") -> str:
        return dt.astimezone(self.tz).strftime(pattern)

    def _dt_bounds(self, d: date) -> tuple[datetime, datetime]:
        start = datetime.combine(d, time.min, tzinfo=self.tz)
        end = datetime.combine(d, time.max, tzinfo=self.tz)
        return start, end

    def obtener_rango_hoy(self) -> RangoFechas:
        now = datetime.now(self.tz)
        inicio, fin = self._dt_bounds(now.date())
        return RangoFechas(inicio, fin, self._fmt(inicio), self._fmt(fin))

    def obtener_rango_semana_actual(self) -> RangoFechas:
        now = datetime.now(self.tz)
        weekday = now.weekday()  # lunes=0
        lunes = (now - timedelta(days=weekday)).date()
        domingo = lunes + timedelta(days=6)
        inicio, _ = self._dt_bounds(lunes)
        _, fin = self._dt_bounds(domingo)
        return RangoFechas(inicio, fin, self._fmt(inicio), self._fmt(fin))

    def obtener_rango_semana_anterior(self) -> RangoFechas:
        actual = self.obtener_rango_semana_actual()
        lunes_anterior = (actual.inicio - timedelta(days=7)).date()
        domingo_anterior = lunes_anterior + timedelta(days=6)
        inicio, _ = self._dt_bounds(lunes_anterior)
        _, fin = self._dt_bounds(domingo_anterior)
        return RangoFechas(inicio, fin, self._fmt(inicio), self._fmt(fin))

    def obtener_rango_mes_actual(self) -> RangoFechas:
        now = datetime.now(self.tz)
        first_day = date(now.year, now.month, 1)
        if now.month == 12:
            next_month = date(now.year + 1, 1, 1)
        else:
            next_month = date(now.year, now.month + 1, 1)
        last_day = next_month - timedelta(days=1)
        inicio, _ = self._dt_bounds(first_day)
        _, fin = self._dt_bounds(last_day)
        return RangoFechas(
            inicio,
            fin,
            self._fmt(inicio),
            self._fmt(fin),
            etiqueta_mes=now.strftime("%m/%Y"),
        )

    def obtener_rango_para_diarios(self) -> RangoFechas:
        now = datetime.now(self.tz)
        # Mantiene lÃ³gica Apps Script: lunes -> semana anterior cerrada; resto -> hoy.
        if now.weekday() == 0:
            r = self.obtener_rango_semana_anterior()
            r.modo = "SEMANA_ANTERIOR"
            return r
        r = self.obtener_rango_hoy()
        r.modo = "HOY"
        return r

    def parsear_fecha(self, valor: str | datetime | None) -> datetime | None:
        if valor is None:
            return None
        if isinstance(valor, datetime):
            return valor

        txt = str(valor).strip()
        if not txt:
            return None

        # dd/MM/yyyy [HH:mm[:ss]]
        m = re.match(r"^(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{4})(?:\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?$", txt)
        if m:
            dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hh = int(m.group(4) or 0)
            mi = int(m.group(5) or 0)
            ss = int(m.group(6) or 0)
            try:
                return datetime(yyyy, mm, dd, hh, mi, ss, tzinfo=self.tz)
            except Exception:
                return None

        # fallback ISO
        try:
            dt = datetime.fromisoformat(txt)
            return dt if dt.tzinfo else dt.replace(tzinfo=self.tz)
        except Exception:
            return None

    # =========================
    # IA/normalizaciÃ³n de texto (versiÃ³n backend)
    # =========================
    def _normalizar_si_no(self, value: str | None) -> str:
        txt = str(value or "").strip().lower()
        if txt in {"si", "sí", "s", "yes", "y", "1", "true"}:
            return "SI"
        if txt in {"no", "n", "0", "false"}:
            return "NO"
        return str(value or "").strip().upper()

    def _capitalizar_oraciones(self, text: str) -> str:
        out: list[str] = []
        for part in re.split(r"([.!?]\s+)", text):
            if not part:
                continue
            if re.match(r"[.!?]\s+", part):
                out.append(part)
                continue
            stripped = part.strip()
            if not stripped:
                out.append(part)
                continue
            out.append(stripped[0].upper() + stripped[1:])
        return "".join(out).strip()

    @staticmethod
    def _es_observacion_vacia(txt: str) -> bool:
        if not txt or txt in {"-", "–", "—", "s/i", "sin observaciones", "sin observacion"}:
            return True
        if len(txt) < 4:
            return True
        return False

    def formalizar_observacion(self, observacion: str | None) -> str:
        txt = str(observacion or "").strip()
        if not txt or self._es_observacion_vacia(txt):
            return txt or "-"

        if settings.ia_formalizador_enabled:
            try:
                return self._formalizar_observacion_con_ia(txt)
            except Exception as exc:
                LOGGER.warning("Falla IA formalizacion: %s", exc)
                if settings.ia_formalizador_strict:
                    raise ValueError(f"Error en formalizacion con IA: {exc}") from exc

        return self.formalizar_observacion_mejorada(txt)

    def _preservar_tokens_operativos(self, text: str) -> tuple[str, dict[str, str]]:
        placeholders: dict[str, str] = {}
        reglas = [r"\bOP\b", r"\bcam\b", r"\bhrs\.?(?=\s|,|$)", r"\bGGSS\b"]
        idx = 0
        txt = text

        for patron in reglas:
            def _repl(match: re.Match[str]) -> str:
                nonlocal idx
                key = f"__TOK_OP_{idx}__"
                idx += 1
                placeholders[key] = match.group(0)
                return key

            txt = re.sub(patron, _repl, txt, flags=re.IGNORECASE)
        return txt, placeholders

    def _restaurar_tokens_operativos(self, text: str, placeholders: dict[str, str]) -> str:
        txt = text
        for key, value in placeholders.items():
            txt = txt.replace(key, value)
        return txt

    def formalizar_observacion_mejorada(self, observacion: str | None) -> str:
        txt = str(observacion or "").strip()
        if not txt:
            return ""

        txt = re.sub(r"\s+", " ", txt).strip()
        txt, placeholders = self._preservar_tokens_operativos(txt)

        reemplazos = [
            ("revicion", "revisión"),
            ("revison", "revisión"),
            ("corecto", "correcto"),
            ("conjelada", "congelada"),
            ("imgen", "imagen"),
            ("conecion", "conexión"),
            ("conexion", "conexión"),
            ("monitoro", "monitoreo"),
            ("camara", "cámara"),
            ("camaras", "cámaras"),
            ("tecnico", "técnico"),
            ("tecnicos", "técnicos"),
            ("observacion", "observación"),
            ("mas", "más"),
            ("nvr", "NVR"),
            ("dvr", "DVR"),
            ("cirena", "sirena"),
            ("sugeto", "sujeto"),
            ("efectibo", "efectivo"),
            ("habia", "había"),
            ("actibo", "activo"),
        ]
        for origen, destino in reemplazos:
            txt = re.sub(rf"\b{origen}\b", destino, txt, flags=re.IGNORECASE)

        txt = re.sub(r"\s+([,.;:!?])", r"\1", txt)
        txt = re.sub(r"([,;.!?])(?!\s|$)", r"\1 ", txt)
        txt = re.sub(r"(?<!\d):(?!\s|$)", r": ", txt)
        txt = re.sub(r"\s{2,}", " ", txt).strip()

        txt = self._capitalizar_oraciones(txt)
        if txt and txt[-1] not in ".!?":
            txt += "."
        txt = self._restaurar_tokens_operativos(txt, placeholders)
        txt = re.sub(r"\bhrs\.\s+MÃ¡s\b", "hrs. mÃ¡s", txt, flags=re.IGNORECASE)
        return txt

    def construir_prompt_formalizacion_observacion(self, observacion: str | None) -> str:
        """Prompt base para integrar un modelo IA de correccion/formalizacion."""
        return (
            f"{PROMPT_FORMALIZAR_OBSERVACION}\n\n"
            f"Observacion original:\n{str(observacion or '').strip()}"
        )

    def _extraer_texto_chat_completion(self, payload: dict[str, object]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            partes: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    partes.append(text.strip())
            return "\n".join(partes).strip()
        return ""

    def _formalizar_observacion_con_ia(self, observacion: str) -> str:
        """Corrige ortografia, tildes y redaccion usando Claude (Anthropic SDK)."""
        import anthropic as _anthropic

        api_key = str(settings.anthropic_api_key or "").strip()
        if not api_key:
            raise ValueError("Falta ANTHROPIC_API_KEY para formalizacion con IA.")

        model = str(settings.anthropic_model_formalizador or "claude-haiku-4-5").strip()
        timeout_sec = float(max(5, int(settings.anthropic_timeout_sec or 25)))

        client = _anthropic.Anthropic(api_key=api_key, timeout=timeout_sec)
        message = client.messages.create(
            model=model,
            max_tokens=1024,
            system=PROMPT_FORMALIZAR_OBSERVACION,
            messages=[
                {"role": "user", "content": str(observacion).strip()},
            ],
        )
        out = next((b.text for b in message.content if b.type == "text"), "")
        if not out:
            raise ValueError("Claude no devolvio contenido de texto util.")
        return out.strip()

    # =========================
    # Listas para formulario
    # =========================
    def obtener_listas(self) -> dict[str, object]:
        encargados_set: set[str] = set()
        grupos: dict[str, str] = {}
        clientes_set: set[str] = set()
        sucursales_set: set[str] = set()
        cliente_sucursales: dict[str, set[str]] = defaultdict(set)
        operadores_set: set[str] = set()

        # Desde histÃ³ricos de protocolos
        rows = self.db.scalars(select(ProtocoloRegistro).order_by(ProtocoloRegistro.id.desc()).limit(5000)).all()
        for r in rows:
            if r.encargado:
                encargados_set.add(r.encargado.strip())
                if r.grupo:
                    grupos.setdefault(r.encargado.strip(), r.grupo.strip())

        # Usuarios con sesiÃ³n
        for value in self.db.scalars(select(LoginSession.usuario)).all():
            if value:
                encargados_set.add(str(value).strip())

        # Operador en control_protocolos.html: solo usuarios Televigilante.
        try:
            rows_operadores = self.db.execute(
                text(
                    """
                    SELECT name
                    FROM users
                    WHERE is_activate = 1
                      AND LOWER(LTRIM(RTRIM(COALESCE(departament, '')))) = :department
                      AND LTRIM(RTRIM(COALESCE(name, ''))) <> ''
                    ORDER BY LOWER(LTRIM(RTRIM(name))) ASC
                    """
                ),
                {"department": "televigilante"},
            ).scalars().all()
            for value in rows_operadores:
                nombre = str(value or "").strip()
                if nombre:
                    operadores_set.add(nombre)
        except Exception as exc:
            LOGGER.warning("No fue posible cargar operadores Televigilante desde users: %s", exc)

        # Fuente actual del sistema: bbdd_clientes + bbdd_sucursales.
        try:
            for cliente_row in self.db.scalars(select(ClienteBBDD).order_by(ClienteBBDD.cliente.asc())).all():
                cliente = str(cliente_row.cliente or "").strip()
                if cliente:
                    clientes_set.add(cliente)

            rows_sucursales = (
                self.db.query(SucursalBBDD, ClienteBBDD)
                .outerjoin(ClienteBBDD, SucursalBBDD.rut == ClienteBBDD.rut)
                .all()
            )
            for sucursal, cliente_ref in rows_sucursales:
                cliente = str(
                    sucursal.nombre_empresa
                    or (cliente_ref.cliente if cliente_ref else "")
                    or ""
                ).strip()
                nombre_sucursal = str(sucursal.nombre_sucursal or "").strip()
                if not cliente or not nombre_sucursal:
                    continue
                clientes_set.add(cliente)
                sucursales_set.add(nombre_sucursal)
                cliente_sucursales[cliente].add(nombre_sucursal)
        except Exception as exc:
            LOGGER.warning(
                "No fue posible cargar cliente/sucursal desde bbdd_clientes/bbdd_sucursales: %s",
                exc,
            )

        try:
            if not cliente_sucursales:
                for cliente_row in self.db.scalars(select(ClienteBBDD).order_by(ClienteBBDD.cliente.asc())).all():
                    cliente = str(cliente_row.cliente or "").strip()
                    if cliente:
                        clientes_set.add(cliente)
        except Exception:
            pass

        def _sort(vals: set[str]) -> list[str]:
            return sorted((v for v in vals if v), key=lambda x: x.lower())

        encargados = _sort(encargados_set)
        clientes = _sort(clientes_set)
        sucursales = _sort(sucursales_set)
        operadores = _sort(operadores_set)
        grupos_final = {k: v for k, v in grupos.items() if k and v}

        return {
            "encargados": encargados,
            "grupos": grupos_final,
            "clientes": clientes,
            "sucursales": sucursales,
            "cliente_sucursales": {
                cliente: sorted(vals, key=lambda x: x.lower())
                for cliente, vals in sorted(cliente_sucursales.items(), key=lambda x: x[0].lower())
                if cliente and vals
            },
            "operadores": operadores,
        }

    # =========================
    # Registro
    # =========================
    def _usuario_por_token(self, token: str | None) -> str:
        tk = str(token or "").strip()
        if not tk:
            return ""
        now = datetime.now(timezone.utc)
        sesion = self.db.scalar(
            select(LoginSession).where(LoginSession.token == tk, LoginSession.expires_at > now).limit(1)
        )
        return str(sesion.usuario or "").strip() if sesion else ""

    def _grupo_por_encargado(self, encargado: str) -> str:
        key = _normalizar_clave_nombre(encargado)
        grupo = ENCARGADO_GRUPO.get(key, "").strip()
        if grupo:
            return grupo

        # Fallback: ultimo grupo historico usado por ese encargado.
        ultimo_grupo = self.db.scalar(
            select(ProtocoloRegistro.grupo)
            .where(
                ProtocoloRegistro.encargado.is_not(None),
                func.lower(ProtocoloRegistro.encargado) == str(encargado or "").strip().lower(),
                ProtocoloRegistro.grupo.is_not(None),
                ProtocoloRegistro.grupo != "",
            )
            .order_by(ProtocoloRegistro.id.desc())
            .limit(1)
        )
        return str(ultimo_grupo or "").strip()

    def _dt_db(self, dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(self.tz).replace(tzinfo=None)

    def _valor_si_no(self, valor: str | None) -> str:
        txt = str(valor or "").strip().upper()
        return txt if txt in {"SI", "NO"} else "-"

    def _resumen_ejecutivo_individual(self, row: ProtocoloRegistro) -> str:
        checks = [
            row.detectado,
            row.efectivo,
            row.sirena,
            row.voz,
            row.carabineros,
            row.alpha3,
            row.informado,
            row.bitacora,
            row.protocolo_exitoso,
        ]
        total_si = sum(1 for c in checks if str(c or "").strip().upper() == "SI")
        return (
            f"Se registro un protocolo {row.tipo_protocolo or '-'} para la sucursal {row.sucursal}. "
            f"El checklist operativo obtuvo {total_si} respuestas afirmativas de {len(checks)} variables. "
            f"Este informe consolida antecedentes tecnicos, observacion original y su redaccion formalizada."
        )

    def _guardar_informe_error(self, informe: ProtocoloInforme, error_text: str) -> None:
        informe.estado = "ERROR"
        informe.error_detalle = str(error_text or "Error no especificado")
        self.db.add(informe)
        self.db.commit()

    _INTRUSIVO_RECIPIENTS = [
        "czamora@alguientecuida.cl",
        "glubiano@alguientecuida.cl",
        "ventas@alguientecuida.cl",
        "jefe.cctv@alguientecuida.cl",
    ]

    def _notificar_protocolo_intrusivo(self, row: "ProtocoloRegistro") -> None:
        fecha = self._fmt(row.fecha_registro, "%d/%m/%Y %H:%M")
        cliente = str(row.cliente or "")
        sucursal = str(row.sucursal or "")
        encargado = str(row.encargado or "")
        puesto = str(row.puesto or "")
        obs = str(row.observaciones_formal or row.observaciones_raw or "")

        asunto = f"⚠️ Protocolo Intrusivo registrado – {cliente} / {sucursal}"
        cuerpo = (
            f"Se registró un protocolo intrusivo.\n\n"
            f"Cliente: {cliente}\nSucursal: {sucursal}\n"
            f"Encargado: {encargado}\nPuesto: {puesto}\n"
            f"Fecha: {fecha}\nObservación: {obs}"
        )

        def _tabla_row(label: str, value: str) -> str:
            return (
                f'<div style="margin:6px 0;font-size:14px;line-height:1.6;">'
                f'<span style="color:#636e72;font-weight:600;">{label}:</span> '
                f'<span style="color:#2d3436;">{value or "-"}</span></div>'
            )

        tabla_html = (
            '<div style="background:#ecf0f1;border-left:4px solid #e74c3c;'
            'padding:12px 16px;margin:16px 0;border-radius:4px;">'
            + _tabla_row("Cliente", cliente)
            + _tabla_row("Sucursal", sucursal)
            + _tabla_row("Encargado", encargado)
            + _tabla_row("Puesto", puesto)
            + _tabla_row("Fecha", fecha)
            + _tabla_row("Observación", obs)
            + "</div>"
        )

        html = f"""
<div style="background:#f5f6fa;padding:40px 0;font-family:'Segoe UI',Arial,sans-serif;color:#2d3436;">
  <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:8px;
              padding:30px;box-shadow:0 2px 10px rgba(0,0,0,0.05);">
    <div style="text-align:center;margin-bottom:18px;">
      <img src="https://i.imgur.com/VgLG9Ei.png" alt="Alguien Te Cuida" style="height:55px;">
    </div>
    <h2 style="text-align:center;color:#e74c3c;font-size:18px;margin:0 0 22px 0;">
      ⚠️ Protocolo Intrusivo Registrado
    </h2>
    <div style="font-size:14px;line-height:1.7;">{tabla_html}</div>
    <p style="margin-top:26px;font-size:14px;line-height:1.6;">
      Saludos cordiales,<br>Alguien Te Cuida
    </p>
    <hr style="border:0;border-top:1px solid #ddd;margin:26px 0;">
    <p style="font-size:12px;color:#999;text-align:center;line-height:1.5;">
      Este correo ha sido generado automáticamente como parte del proceso interno.
    </p>
  </div>
</div>"""

        self._enviar_mail_protocolo(self._INTRUSIVO_RECIPIENTS, asunto, cuerpo, html, usar_contacto=True)

    def _lanzar_generacion_informes_async(
        self,
        *,
        registro_id: int,
        cliente: str,
        sucursal: str,
        lanzar_semanal: bool,
        es_intrusivo: bool = False,
    ) -> None:
        def _worker() -> None:
            db = SessionLocal()
            try:
                service = ProtocolosService(db)
                row = db.scalar(
                    select(ProtocoloRegistro).where(ProtocoloRegistro.id == int(registro_id)).limit(1)
                )
                if row:
                    service._generar_informe_individual(row)
                if lanzar_semanal:
                    service._generar_resumen_semanal_si_corresponde(
                        cliente=str(cliente or "").strip(),
                        sucursal=str(sucursal or "").strip(),
                        forzar=True,
                    )
                if es_intrusivo and row:
                    try:
                        service._notificar_protocolo_intrusivo(row)
                    except Exception:
                        LOGGER.exception(
                            "Fallo el envio de email intrusivo (registro_id=%s).", registro_id
                        )
            except Exception:
                LOGGER.exception(
                    "Fallo la generacion asincronica de informes de protocolo (registro_id=%s).",
                    registro_id,
                )
            finally:
                db.close()

        threading.Thread(
            target=_worker,
            name=f"protocolo-report-{registro_id}",
            daemon=True,
        ).start()

    def _generar_informe_individual(self, row: ProtocoloRegistro) -> dict[str, object]:
        informe = self.db.scalar(
            select(ProtocoloInforme)
            .where(
                ProtocoloInforme.tipo_informe == "INDIVIDUAL",
                ProtocoloInforme.registro_id == row.id,
            )
            .limit(1)
        )
        if informe and str(informe.estado or "").upper() == "OK" and str(informe.pdf_url or "").strip():
            return {
                "status": "ok",
                "url": str(informe.pdf_url or ""),
                "informe_id": informe.id,
            }

        if not informe:
            informe = ProtocoloInforme(
                tipo_informe="INDIVIDUAL",
                estado="PENDIENTE",
                registro_id=row.id,
                cliente=row.cliente,
                sucursal=row.sucursal,
                titulo=f"Informe Protocolo #{row.id}",
            )
            self.db.add(informe)
            self.db.commit()
            self.db.refresh(informe)

        try:
            payload = {
                "codigo_informe": f"PR-IND-{row.id}",
                "fecha_emision": self._fmt(datetime.now(self.tz), "%d/%m/%Y %H:%M"),
                "registro_id": row.id,
                "cliente": row.cliente,
                "sucursal": row.sucursal,
                "fecha_registro": self._fmt(row.fecha_registro, "%d/%m/%Y %H:%M"),
                "encargado": row.encargado or "-",
                "grupo": row.grupo or "-",
                "operador": row.operador or "-",
                "puesto": row.puesto or "-",
                "tipo_protocolo": row.tipo_protocolo or "-",
                "detectado": self._valor_si_no(row.detectado),
                "efectivo": self._valor_si_no(row.efectivo),
                "sirena": self._valor_si_no(row.sirena),
                "voz": self._valor_si_no(row.voz),
                "carabineros": self._valor_si_no(row.carabineros),
                "alpha3": self._valor_si_no(row.alpha3),
                "informado": self._valor_si_no(row.informado),
                "bitacora": self._valor_si_no(row.bitacora),
                "protocolo_exitoso": self._valor_si_no(row.protocolo_exitoso),
                "resumen_ejecutivo": self._resumen_ejecutivo_individual(row),
                "observacion_original": row.observaciones_raw or "-",
                "observacion_formalizada": row.observaciones_formal or row.observaciones_raw or "-",
            }
            local_url = _generar_pdf_protocolo_local("INDIVIDUAL", payload)
            drive_meta: dict = {}
            try:
                drive_meta = dict(create_protocol_individual_report_pdf(context=payload))
            except Exception:
                pass
            informe.estado = "OK"
            informe.pdf_url = local_url or str(drive_meta.get("pdf_web_view_link") or "")
            informe.drive_file_id = str(drive_meta.get("pdf_file_id") or "")
            informe.drive_folder_id = str(drive_meta.get("folder_id") or "")
            informe.drive_folder_name = str(drive_meta.get("folder_name") or "")
            meta = {**drive_meta, "local_pdf_url": local_url or ""}
            informe.metadata_json = json.dumps(meta, ensure_ascii=False)
            informe.error_detalle = None
            self.db.add(informe)
            self.db.commit()
            return {
                "status": "ok",
                "url": str(informe.pdf_url or ""),
                "informe_id": informe.id,
            }
        except DriveReportError as exc:
            self._guardar_informe_error(informe, str(exc))
            return {"status": "error", "error": str(exc), "informe_id": informe.id}
        except Exception as exc:
            self._guardar_informe_error(informe, f"Error generando informe individual: {exc}")
            return {"status": "error", "error": str(exc), "informe_id": informe.id}

    def _filas_semana_anterior_cliente_sucursal(
        self,
        *,
        cliente: str,
        sucursal: str,
    ) -> tuple[RangoFechas, list[ProtocoloRegistro]]:
        rango = self.obtener_rango_semana_anterior()
        inicio_db = self._dt_db(rango.inicio)
        fin_db = self._dt_db(rango.fin)
        rows = self.db.scalars(
            select(ProtocoloRegistro)
            .where(
                ProtocoloRegistro.fecha_registro >= inicio_db,
                ProtocoloRegistro.fecha_registro <= fin_db,
                func.lower(ProtocoloRegistro.cliente) == str(cliente or "").strip().lower(),
                func.lower(ProtocoloRegistro.sucursal) == str(sucursal or "").strip().lower(),
            )
            .order_by(ProtocoloRegistro.fecha_registro.asc())
        ).all()
        return rango, rows

    def _generar_resumen_semanal_si_corresponde(
        self,
        *,
        cliente: str,
        sucursal: str,
        forzar: bool = False,
    ) -> dict[str, object]:
        now = datetime.now(self.tz)
        if (not forzar) and now.weekday() != 0:
            return {"status": "skip", "reason": "solo_lunes"}

        rango, rows = self._filas_semana_anterior_cliente_sucursal(cliente=cliente, sucursal=sucursal)
        # El informe semanal solo considera protocolos preventivos — los
        # intrusivos se notifican aparte (correo inmediato), no en este resumen.
        rows = [r for r in rows if str(r.tipo_protocolo or "").strip().lower() == "preventivo"]
        if not rows:
            return {"status": "skip", "reason": "sin_registros_semana_anterior"}

        inicio_db = self._dt_db(rango.inicio)
        fin_db = self._dt_db(rango.fin)
        informe = self.db.scalar(
            select(ProtocoloInforme)
            .where(
                ProtocoloInforme.tipo_informe == "SEMANAL",
                func.lower(ProtocoloInforme.cliente) == str(cliente or "").strip().lower(),
                func.lower(ProtocoloInforme.sucursal) == str(sucursal or "").strip().lower(),
                ProtocoloInforme.periodo_inicio == inicio_db,
                ProtocoloInforme.periodo_fin == fin_db,
            )
            .limit(1)
        )
        if informe and str(informe.estado or "").upper() == "OK" and str(informe.pdf_url or "").strip():
            return {
                "status": "ok",
                "url": str(informe.pdf_url or ""),
                "informe_id": informe.id,
            }

        if not informe:
            informe = ProtocoloInforme(
                tipo_informe="SEMANAL",
                estado="PENDIENTE",
                registro_id=None,
                cliente=cliente,
                sucursal=sucursal,
                periodo_inicio=inicio_db,
                periodo_fin=fin_db,
                titulo=f"Resumen semanal {rango.texto_inicio} - {rango.texto_fin}",
            )
            self.db.add(informe)
            self.db.commit()
            self.db.refresh(informe)

        total_preventivo = sum(1 for r in rows if str(r.tipo_protocolo or "").strip().lower() == "preventivo")
        total_intrusivo = sum(1 for r in rows if str(r.tipo_protocolo or "").strip().lower() == "intrusivo")
        total_exitosos = sum(1 for r in rows if str(r.protocolo_exitoso or "").strip().upper() == "SI")
        detalle_lineas = []
        detalle_filas: list[dict[str, str]] = []
        for r in rows:
            fecha_item = self._fmt(r.fecha_registro, "%d/%m/%Y %H:%M")
            tipo_item = r.tipo_protocolo or "-"
            # En el reporte semanal la columna "Observación" debe mostrar solo la observación.
            _formal = str(r.observaciones_formal or "").strip()
            _raw    = str(r.observaciones_raw or "").strip()
            # Descartar respuestas vacías del formalizador IA
            if "no hay texto para corregir" in _formal.lower() or self._es_observacion_vacia(_formal):
                _formal = ""
            observacion_item = (_formal or _raw or "-")
            detalle_lineas.append(
                (
                    f"{fecha_item} | "
                    f"{tipo_item} | "
                    f"{observacion_item}"
                )
            )
            detalle_filas.append(
                {
                    "fecha": fecha_item,
                    "sucursal": r.sucursal or sucursal,
                    "tipo_protocolo": tipo_item,
                    "observacion": observacion_item,
                }
            )

        resumen_ejecutivo = (
            f"Durante la semana evaluada se registraron {total_preventivo} protocolos preventivos "
            f"en la sucursal {sucursal}, con {total_exitosos} protocolos exitosos."
        )
        conclusiones = (
            "Se recomienda mantener seguimiento sobre hallazgos recurrentes y validar continuidad operativa "
            "segun observaciones formalizadas registradas en este periodo."
        )

        try:
            payload = {
                "codigo_informe": (
                    f"PR-SEM-{cliente[:12].upper().replace(' ', '')}-{sucursal[:12].upper().replace(' ', '')}"
                ),
                "fecha_emision": self._fmt(datetime.now(self.tz), "%d/%m/%Y %H:%M"),
                "cliente": cliente,
                "sucursal": sucursal,
                "periodo_inicio": rango.texto_inicio,
                "periodo_fin": rango.texto_fin,
                "total_registros": len(rows),
                "total_preventivo": total_preventivo,
                "total_intrusivo": total_intrusivo,
                "total_exitosos": total_exitosos,
                "resumen_ejecutivo": resumen_ejecutivo,
                "detalle_lineas": detalle_lineas,
                "detalle_filas": detalle_filas,
                "conclusiones": conclusiones,
            }
            local_url = _generar_pdf_protocolo_local("SEMANAL", payload)
            drive_meta_s: dict = {}
            try:
                drive_meta_s = dict(create_protocol_weekly_report_pdf(context=payload))
            except Exception:
                pass
            informe.estado = "OK"
            informe.pdf_url = local_url or str(drive_meta_s.get("pdf_web_view_link") or "")
            informe.drive_file_id = str(drive_meta_s.get("pdf_file_id") or "")
            informe.drive_folder_id = str(drive_meta_s.get("folder_id") or "")
            informe.drive_folder_name = str(drive_meta_s.get("folder_name") or "")
            meta_s = {**drive_meta_s, "local_pdf_url": local_url or ""}
            informe.metadata_json = json.dumps(meta_s, ensure_ascii=False)
            informe.error_detalle = None
            self.db.add(informe)
            self.db.commit()
            return {
                "status": "ok",
                "url": str(informe.pdf_url or ""),
                "informe_id": informe.id,
            }
        except DriveReportError as exc:
            self._guardar_informe_error(informe, str(exc))
            return {"status": "error", "error": str(exc), "informe_id": informe.id}
        except Exception as exc:
            self._guardar_informe_error(informe, f"Error generando informe semanal: {exc}")
            return {"status": "error", "error": str(exc), "informe_id": informe.id}

    def generar_resumenes_semanales_pendientes(self, *, forzar: bool = False) -> dict[str, object]:
        now = datetime.now(self.tz)
        if (not forzar) and now.weekday() != 0:
            return {"ok": True, "status": "skip", "reason": "solo_lunes", "procesados": 0}

        rango = self.obtener_rango_semana_anterior()
        inicio_db = self._dt_db(rango.inicio)
        fin_db = self._dt_db(rango.fin)
        rows = self.db.execute(
            select(ProtocoloRegistro.cliente, ProtocoloRegistro.sucursal)
            .where(
                ProtocoloRegistro.fecha_registro >= inicio_db,
                ProtocoloRegistro.fecha_registro <= fin_db,
            )
            .distinct()
        ).all()
        total = 0
        ok = 0
        errores = 0
        detalle: list[dict[str, object]] = []
        for cliente, sucursal in rows:
            c = str(cliente or "").strip()
            s = str(sucursal or "").strip()
            if not c or not s:
                continue
            total += 1
            result = self._generar_resumen_semanal_si_corresponde(cliente=c, sucursal=s, forzar=True)
            if str(result.get("status") or "").lower() == "ok":
                ok += 1
            elif str(result.get("status") or "").lower() == "error":
                errores += 1
            detalle.append({"cliente": c, "sucursal": s, **result})
        return {
            "ok": True,
            "status": "done",
            "periodo": {"inicio": rango.texto_inicio, "fin": rango.texto_fin},
            "procesados": total,
            "generados_ok": ok,
            "errores": errores,
            "detalle": detalle,
        }

    def guardar_registro(self, data: ProtocoloRegistroCreateRequest) -> dict[str, object]:
        cliente = str(data.cliente or "").strip()
        sucursal = str(data.sucursal or "").strip()
        if not cliente or not sucursal:
            raise ValueError("Cliente y sucursal son obligatorios.")

        encargado = self._usuario_por_token(data.token)
        if not encargado:
            raise ValueError("Sesion invalida o expirada. Vuelve a iniciar sesion.")

        tipo_raw = str(data.tipo_protocolo or "").strip()
        tipo_norm = _normalizar_clave_nombre(tipo_raw)
        if tipo_norm not in {"preventivo", "intrusivo"}:
            raise ValueError("Tipo de protocolo invalido. Usa Preventivo o Intrusivo.")
        tipo_protocolo = "Preventivo" if tipo_norm == "preventivo" else "Intrusivo"

        grupo = self._grupo_por_encargado(encargado)
        observ_raw = str(data.observaciones or "").strip()
        observ_formal = self.formalizar_observacion(observ_raw)

        row = ProtocoloRegistro(
            encargado=encargado or None,
            grupo=grupo or None,
            cliente=cliente,
            sucursal=sucursal,
            tipo_protocolo=tipo_protocolo,
            detectado=self._normalizar_si_no(data.detectado),
            efectivo=self._normalizar_si_no(data.efectivo),
            sirena=self._normalizar_si_no(data.sirena),
            voz=self._normalizar_si_no(data.voz),
            carabineros=self._normalizar_si_no(data.carabineros),
            alpha3=self._normalizar_si_no(data.alpha3),
            informado=self._normalizar_si_no(data.informado),
            bitacora=self._normalizar_si_no(data.bitacora),
            protocolo_exitoso=self._normalizar_si_no(data.protocolo_exitoso),
            puesto=str(data.puesto or "").strip() or None,
            operador=str(data.operador or "").strip() or None,
            observaciones_raw=observ_raw or None,
            observaciones_formal=observ_formal or None,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)

        es_lunes = datetime.now(self.tz).weekday() == 0
        self._lanzar_generacion_informes_async(
            registro_id=int(row.id),
            cliente=row.cliente,
            sucursal=row.sucursal,
            lanzar_semanal=es_lunes,
            es_intrusivo=(tipo_protocolo == "Intrusivo"),
        )

        return {
            "ok": True,
            "id": row.id,
            "fecha": self._fmt(row.fecha_registro, "%d/%m/%Y %H:%M:%S"),
            "observacion_formal": row.observaciones_formal or "",
            "informes_async": True,
            "informe_individual": {
                "status": "queued",
                "message": "Generacion de informe individual en segundo plano.",
            },
            "informe_semanal": (
                {
                    "status": "queued",
                    "message": "Generacion de resumen semanal en segundo plano.",
                }
                if es_lunes
                else {
                    "status": "skip",
                    "reason": "solo_lunes",
                }
            ),
        }

    # =========================
    # Consultas / reportes
    # =========================
    def listar_registros(
        self,
        *,
        cliente: str = "",
        sucursal: str = "",
        tipo_protocolo: str = "",
        fecha_desde: str = "",
        fecha_hasta: str = "",
        limit: int = 300,
    ) -> list[dict[str, object]]:
        limit_value = int(limit or 0)
        if limit_value < 0:
            limit_value = 0
        if limit_value > 0:
            limit_value = min(limit_value, 50000)
        stmt = select(ProtocoloRegistro)

        where = []
        if cliente.strip():
            where.append(func.lower(ProtocoloRegistro.cliente) == cliente.strip().lower())
        if sucursal.strip():
            where.append(func.lower(ProtocoloRegistro.sucursal) == sucursal.strip().lower())
        if tipo_protocolo.strip():
            where.append(func.lower(ProtocoloRegistro.tipo_protocolo) == tipo_protocolo.strip().lower())

        dt_desde = self.parsear_fecha(fecha_desde)
        dt_hasta = self.parsear_fecha(fecha_hasta)
        if dt_desde:
            where.append(ProtocoloRegistro.fecha_registro >= dt_desde)
        if dt_hasta:
            where.append(ProtocoloRegistro.fecha_registro <= dt_hasta)

        if where:
            stmt = stmt.where(and_(*where))

        stmt = stmt.order_by(ProtocoloRegistro.id.desc())
        if limit_value > 0:
            stmt = stmt.limit(limit_value)
        rows = self.db.scalars(stmt).all()
        out: list[dict[str, object]] = []
        for r in rows:
            out.append(
                {
                    "id": r.id,
                    "fecha": self._fmt(r.fecha_registro, "%d/%m/%Y %H:%M"),
                    "encargado": r.encargado or "",
                    "grupo": r.grupo or "",
                    "cliente": r.cliente,
                    "sucursal": r.sucursal,
                    "tipo_protocolo": r.tipo_protocolo or "",
                    "detectado": r.detectado or "",
                    "efectivo": r.efectivo or "",
                    "sirena": r.sirena or "",
                    "voz": r.voz or "",
                    "carabineros": r.carabineros or "",
                    "alpha3": r.alpha3 or "",
                    "informado": r.informado or "",
                    "bitacora": r.bitacora or "",
                    "protocolo_exitoso": r.protocolo_exitoso or "",
                    "observaciones": r.observaciones_formal or r.observaciones_raw or "",
                    "observaciones_raw": r.observaciones_raw or "",
                    "observaciones_formal": r.observaciones_formal or "",
                    "operador": r.operador or "",
                    "puesto": r.puesto or "",
                    "created_at": self._fmt(r.created_at, "%d/%m/%Y %H:%M:%S"),
                    "updated_at": self._fmt(r.updated_at, "%d/%m/%Y %H:%M:%S"),
                }
            )
        return out

    def _filas_en_rango(self, inicio: datetime, fin: datetime) -> list[ProtocoloRegistro]:
        return self.db.scalars(
            select(ProtocoloRegistro)
            .where(ProtocoloRegistro.fecha_registro >= inicio, ProtocoloRegistro.fecha_registro <= fin)
            .order_by(ProtocoloRegistro.fecha_registro.asc())
        ).all()

    def generar_resumen(self, *, periodo: str = "diario", fecha_referencia: str = "") -> dict[str, object]:
        periodo_norm = str(periodo or "diario").strip().lower()
        if periodo_norm not in {"diario", "semanal", "mensual"}:
            raise ValueError("Periodo invÃ¡lido. Usa: diario, semanal o mensual.")

        if periodo_norm == "diario":
            rango = self.obtener_rango_para_diarios()
        elif periodo_norm == "semanal":
            rango = self.obtener_rango_semana_anterior()
        else:
            rango = self.obtener_rango_mes_actual()

        # Permite forzar fecha de referencia si viene.
        if fecha_referencia.strip():
            dt_ref = self.parsear_fecha(fecha_referencia.strip())
            if dt_ref:
                if periodo_norm == "diario":
                    inicio, fin = self._dt_bounds(dt_ref.date())
                    rango = RangoFechas(inicio, fin, self._fmt(inicio), self._fmt(fin), modo="FECHA_MANUAL")
                elif periodo_norm == "semanal":
                    ref = dt_ref.astimezone(self.tz)
                    monday = (ref - timedelta(days=ref.weekday())).date()
                    sunday = monday + timedelta(days=6)
                    inicio, _ = self._dt_bounds(monday)
                    _, fin = self._dt_bounds(sunday)
                    rango = RangoFechas(inicio, fin, self._fmt(inicio), self._fmt(fin))
                else:
                    first_day = date(dt_ref.year, dt_ref.month, 1)
                    if dt_ref.month == 12:
                        next_month = date(dt_ref.year + 1, 1, 1)
                    else:
                        next_month = date(dt_ref.year, dt_ref.month + 1, 1)
                    last_day = next_month - timedelta(days=1)
                    inicio, _ = self._dt_bounds(first_day)
                    _, fin = self._dt_bounds(last_day)
                    rango = RangoFechas(
                        inicio,
                        fin,
                        self._fmt(inicio),
                        self._fmt(fin),
                        etiqueta_mes=dt_ref.strftime("%m/%Y"),
                    )

        rows = self._filas_en_rango(rango.inicio, rango.fin)

        # Apps Script semanal: solo "preventivo".
        if periodo_norm == "semanal":
            rows = [r for r in rows if str(r.tipo_protocolo or "").strip().lower() == "preventivo"]

        agrupado: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
        for r in rows:
            agrupado[r.cliente][r.sucursal].append(
                {
                    "fecha": self._fmt(r.fecha_registro, "%d/%m/%Y %H:%M"),
                    "tipo": r.tipo_protocolo or "",
                    "observacion": r.observaciones_formal or r.observaciones_raw or "",
                }
            )

        reportes: list[dict[str, object]] = []
        for cliente, sucursales in sorted(agrupado.items(), key=lambda x: x[0].lower()):
            for sucursal, regs in sorted(sucursales.items(), key=lambda x: x[0].lower()):
                reportes.append(
                    {
                        "cliente": cliente,
                        "sucursal": sucursal,
                        "total_registros": len(regs),
                        "detalle": regs,
                    }
                )

        return {
            "periodo": periodo_norm,
            "rango": {
                "inicio": rango.texto_inicio,
                "fin": rango.texto_fin,
                "etiqueta_mes": rango.etiqueta_mes,
                "modo": rango.modo,
            },
            "total_registros": len(rows),
            "total_reportes": len(reportes),
            "reportes": reportes,
        }

    def conteo_por_puesto_mes(self, *, anio: int, mes: int) -> list[dict[str, int]]:
        if mes < 1 or mes > 12:
            raise ValueError("Mes invÃ¡lido.")
        inicio = datetime(anio, mes, 1, 0, 0, 0, tzinfo=self.tz)
        if mes == 12:
            next_month = datetime(anio + 1, 1, 1, 0, 0, 0, tzinfo=self.tz)
        else:
            next_month = datetime(anio, mes + 1, 1, 0, 0, 0, tzinfo=self.tz)
        fin = next_month - timedelta(microseconds=1)

        rows = self._filas_en_rango(inicio, fin)
        buckets: dict[int, dict[str, int]] = {i: {"puesto": i, "intrusivo": 0, "preventivo": 0, "total": 0} for i in range(1, 31)}
        for r in rows:
            try:
                p = int(str(r.puesto or "").strip())
            except Exception:
                continue
            if p < 1 or p > 30:
                continue
            tipo = str(r.tipo_protocolo or "").strip().lower()
            if tipo == "intrusivo":
                buckets[p]["intrusivo"] += 1
            elif tipo == "preventivo":
                buckets[p]["preventivo"] += 1
            buckets[p]["total"] += 1
        return [buckets[i] for i in range(1, 31)]

    def listar_informes(
        self,
        *,
        cliente: str = "",
        sucursal: str = "",
        tipo_informe: str = "",
        limit: int = 200,
    ) -> list[dict[str, object]]:
        limit_value = max(1, min(int(limit or 200), 5000))
        stmt = select(ProtocoloInforme)
        where = []
        if cliente.strip():
            where.append(func.lower(ProtocoloInforme.cliente) == cliente.strip().lower())
        if sucursal.strip():
            where.append(func.lower(ProtocoloInforme.sucursal) == sucursal.strip().lower())
        if tipo_informe.strip():
            where.append(func.lower(ProtocoloInforme.tipo_informe) == tipo_informe.strip().lower())
        if where:
            stmt = stmt.where(and_(*where))
        stmt = stmt.order_by(ProtocoloInforme.id.desc()).limit(limit_value)
        rows = self.db.scalars(stmt).all()
        out: list[dict[str, object]] = []
        for r in rows:
            try:
                meta = json.loads(r.metadata_json or "{}")
            except Exception:
                meta = {}
            out.append(
                {
                    "id": r.id,
                    "tipo_informe": r.tipo_informe,
                    "estado": r.estado,
                    "registro_id": r.registro_id,
                    "cliente": r.cliente,
                    "sucursal": r.sucursal,
                    "periodo_inicio": self._fmt(r.periodo_inicio, "%d/%m/%Y %H:%M") if r.periodo_inicio else "",
                    "periodo_fin": self._fmt(r.periodo_fin, "%d/%m/%Y %H:%M") if r.periodo_fin else "",
                    "titulo": r.titulo or "",
                    "pdf_url": r.pdf_url or "",
                    "docs_url": str(meta.get("docs_web_view_link") or ""),
                    "drive_file_id": r.drive_file_id or "",
                    "drive_folder_id": r.drive_folder_id or "",
                    "drive_folder_name": r.drive_folder_name or "",
                    "error_detalle": r.error_detalle or "",
                    "created_at": self._fmt(r.created_at, "%d/%m/%Y %H:%M:%S"),
                    "updated_at": self._fmt(r.updated_at, "%d/%m/%Y %H:%M:%S"),
                }
            )
        return out

    def _get_informe(self, informe_id: int) -> ProtocoloInforme:
        informe = self.db.get(ProtocoloInforme, int(informe_id))
        if not informe:
            raise ValueError("Informe no encontrado.")
        return informe

    @staticmethod
    def _email_ok(valor: str | None) -> str:
        email = str(valor or "").strip()
        if not email or "@" not in email:
            return ""
        return email

    def _contactos_para_informe(self, informe: ProtocoloInforme) -> list[dict[str, str]]:
        cliente_norm = _normalizar_clave_nombre(informe.cliente)
        sucursal_norm = _normalizar_clave_nombre(informe.sucursal)
        contactos: list[dict[str, str]] = []
        vistos: set[str] = set()

        def add(nombre: str, email: str, origen: str) -> None:
            email_ok = self._email_ok(email)
            key = email_ok.lower()
            if not email_ok or key in vistos:
                return
            vistos.add(key)
            contactos.append(
                {
                    "nombre": str(nombre or email_ok).strip() or email_ok,
                    "email": email_ok,
                    "origen": origen,
                }
            )

        clientes = self.db.scalars(select(ClienteBBDD)).all()
        cliente_match = next(
            (c for c in clientes if _normalizar_clave_nombre(c.cliente) == cliente_norm),
            None,
        )
        if cliente_match:
            add(cliente_match.nombre_representante or "Representante", cliente_match.email_representante or "", "Cliente")
            add("-", cliente_match.email_facturas or "", "Cliente")
            add("Ejecutivo", cliente_match.ejecutivo_email or "", "Cliente")

        sucursales = self.db.scalars(select(SucursalBBDD)).all()
        sucursal_match = next(
            (
                s
                for s in sucursales
                if _normalizar_clave_nombre(s.nombre_sucursal) == sucursal_norm
                and (
                    not cliente_norm
                    or _normalizar_clave_nombre(s.nombre_empresa) == cliente_norm
                    or (cliente_match and s.rut == cliente_match.rut)
                )
            ),
            None,
        )
        if not sucursal_match:
            sucursal_match = next(
                (s for s in sucursales if _normalizar_clave_nombre(s.nombre_sucursal) == sucursal_norm),
                None,
            )

        if sucursal_match:
            add("-", sucursal_match.email_facturas or "", "Sucursal")
            contactos_emergencia = self.db.scalars(
                select(SucursalContactoEmergencia).where(SucursalContactoEmergencia.sucursal_id == sucursal_match.id)
            ).all()
            for c in contactos_emergencia:
                add(c.nombre or "Contacto emergencia", c.email or "", "Contacto")
            personas = self.db.scalars(
                select(SucursalPersonaAutorizada).where(SucursalPersonaAutorizada.sucursal_id == sucursal_match.id)
            ).all()
            for p in personas:
                add(p.nombre or "Persona autorizada", p.email or "", "Autorizado")

        return contactos

    def obtener_contactos_informe(self, informe_id: int) -> dict[str, object]:
        informe = self._get_informe(informe_id)
        return {
            "id": informe.id,
            "cliente": informe.cliente,
            "sucursal": informe.sucursal,
            "titulo": informe.titulo or "",
            "pdf_url": informe.pdf_url or "",
            "contactos": self._contactos_para_informe(informe),
        }

    @staticmethod
    def _smtp_bool(value: object, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "si", "on"}

    def _enviar_mail_protocolo(
        self,
        destinos: list[str],
        asunto: str,
        cuerpo: str,
        html: str,
        attachment: tuple[bytes, str, str] | None = None,
        logo_path: Path | None = None,
        usar_contacto: bool = False,
    ) -> None:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.image import MIMEImage
        from email.mime.base import MIMEBase
        from email import encoders as _enc

        bcc: list[str] = []

        if usar_contacto:
            from ATC.app.routes.inicio_turno import _contacto_smtp_config

            cfg = _contacto_smtp_config()
            if not cfg.get("enabled"):
                raise ValueError(f"SMTP de contacto no disponible: {cfg.get('reason') or 'no configurado'}")
            host = str(cfg["host"]).strip()
            port = int(cfg["port"])
            username = str(cfg["username"]).strip()
            password = str(cfg["password"])
            from_email = str(cfg["from_email"] or username).strip()
            from_name = str(cfg["from_name"] or "Alguien Te Cuida").strip()
            use_tls = bool(cfg["use_tls"])
            use_ssl = bool(cfg["use_ssl"])
            timeout = int(cfg["timeout"])
        else:
            if not settings.smtp_enabled:
                raise ValueError("El envio automatico de correo esta deshabilitado (SMTP_ENABLED=false).")

            host = str(settings.smtp_host or "").strip()
            port = int(settings.smtp_port or 587)
            username = str(settings.smtp_username or "").strip()
            password = str(settings.smtp_password or "")
            from_email = str(settings.smtp_from_email or username).strip()
            from_name = str(settings.smtp_from_name or "ATC").strip()
            use_tls = self._smtp_bool(settings.smtp_use_tls, True)
            use_ssl = self._smtp_bool(settings.smtp_use_ssl, False)
            timeout = int(settings.smtp_timeout_sec or 20)
            bcc = [
                item.strip()
                for item in str(settings.smtp_bcc_emails or "").replace(";", ",").split(",")
                if self._email_ok(item)
            ]

        if not host or not port or not from_email:
            raise ValueError("SMTP incompleto. Configura SMTP_HOST, SMTP_PORT y SMTP_FROM_EMAIL.")

        # Estructura: mixed > related > alternative + logo_inline
        msg_mixed = MIMEMultipart("mixed")
        msg_mixed["Subject"] = asunto
        msg_mixed["From"] = f"{from_name} <{from_email}>" if from_name else from_email
        msg_mixed["To"] = ", ".join(destinos)
        if bcc:
            msg_mixed["Bcc"] = ", ".join(bcc)

        logo_cid = "logo_atc_protocolo"
        logo_bytes: bytes | None = None
        if logo_path and logo_path.exists():
            try:
                logo_bytes = logo_path.read_bytes()
            except Exception:
                logo_bytes = None

        msg_related = MIMEMultipart("related")
        msg_alt = MIMEMultipart("alternative")
        msg_alt.attach(MIMEText(cuerpo, "plain", "utf-8"))
        msg_alt.attach(MIMEText(html, "html", "utf-8"))
        msg_related.attach(msg_alt)

        if logo_bytes:
            img_part = MIMEImage(logo_bytes, _subtype="png")
            img_part["Content-ID"] = f"<{logo_cid}>"
            img_part.add_header("Content-Disposition", "inline", filename="logo-atc.png")
            msg_related.attach(img_part)

        msg_mixed.attach(msg_related)

        if attachment:
            pdf_bytes, pdf_filename, _ = attachment
            pdf_part = MIMEBase("application", "pdf")
            pdf_part.set_payload(pdf_bytes)
            _enc.encode_base64(pdf_part)
            pdf_part.add_header("Content-Disposition", "attachment", filename=pdf_filename)
            msg_mixed.attach(pdf_part)

        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=timeout) as smtp:
                if username:
                    smtp.login(username, password)
                smtp.sendmail(from_email, destinos + bcc, msg_mixed.as_bytes())
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as smtp:
                smtp.ehlo()
                if use_tls:
                    smtp.starttls()
                    smtp.ehlo()
                if username:
                    smtp.login(username, password)
                smtp.sendmail(from_email, destinos + bcc, msg_mixed.as_bytes())

    def enviar_informe_semanal(self, informe_id: int, payload: dict[str, Any]) -> dict[str, object]:
        informe = self._get_informe(informe_id)
        emails = [
            self._email_ok(item)
            for item in (payload.get("emails") or [])
            if self._email_ok(str(item))
        ]
        vistos: set[str] = set()
        destinos = []
        for email in emails:
            key = email.lower()
            if key not in vistos:
                vistos.add(key)
                destinos.append(email)
        if not destinos:
            raise ValueError("Selecciona al menos un contacto con correo.")
        if not informe.pdf_url:
            raise ValueError("El informe no tiene PDF disponible.")

        asunto = f"ATC | Informe semanal de protocolos - {informe.cliente} / {informe.sucursal}"
        periodo = ""
        if informe.periodo_inicio and informe.periodo_fin:
            periodo = f"{self._fmt(informe.periodo_inicio)} al {self._fmt(informe.periodo_fin)}"

        pdf_attachment: tuple[bytes, str, str] | None = None

        # Preferir SIEMPRE el PDF local (reportlab, estilo corporativo ATC —
        # el mismo que se ve al abrir informe.pdf_url en el navegador). El PDF
        # generado en Drive (Google Docs) usa una plantilla distinta y ya no
        # se debe adjuntar al correo.
        pdf_url_local = str(informe.pdf_url or "").strip()
        if pdf_url_local.startswith("/static/protocolos/informes/"):
            local_path = _APP_DIR / pdf_url_local.lstrip("/")
            if local_path.exists():
                try:
                    pdf_fname = f"Informe_{informe.cliente}_{informe.sucursal}_{periodo or 'periodo'}.pdf".replace("/", "-").replace(" ", "_")
                    pdf_attachment = (local_path.read_bytes(), pdf_fname, "application/pdf")
                except Exception as exc:
                    LOGGER.warning("No se pudo leer el PDF local para adjunto (informe_id=%s): %s", informe.id, exc)

        pdf_file_id = str(informe.drive_file_id or "").strip()
        if not pdf_attachment and pdf_file_id:
            try:
                meta_raw = {}
                try:
                    meta_raw = json.loads(informe.metadata_json or "{}")
                except Exception:
                    pass
                docs_file_id = str(meta_raw.get("docs_file_id") or "").strip()
                if docs_file_id:
                    from ATC.app.services.incidencias_drive_report_service import _build_clients, _export_doc_pdf
                    drive, _ = _build_clients()
                    pdf_bytes = _export_doc_pdf(drive, docs_file_id)
                    pdf_fname = f"Informe_{informe.cliente}_{informe.sucursal}_{periodo or 'periodo'}.pdf".replace("/", "-").replace(" ", "_")
                    pdf_attachment = (pdf_bytes, pdf_fname, "application/pdf")
                else:
                    pdf_bytes, _, pdf_fname = download_support_drive_file_bytes(file_id=pdf_file_id)
                    if not pdf_fname.lower().endswith(".pdf"):
                        pdf_fname = f"Informe_semanal_{informe.id}.pdf"
                    pdf_attachment = (pdf_bytes, pdf_fname, "application/pdf")
            except Exception as exc:
                LOGGER.warning("No se pudo descargar el PDF para adjunto (id=%s): %s", pdf_file_id, exc)

        fecha_envio = self._fmt(datetime.now(self.tz), "%d/%m/%Y %H:%M")
        _logo_path = Path(__file__).resolve().parents[2] / "static" / "img" / "logo-atc.png"
        _logo_cid = "logo_atc_protocolo"
        _logo_src = f"cid:{_logo_cid}" if _logo_path.exists() else ""
        cuerpo = (
            f"Estimado cliente,\n\n"
            f"Adjunto encontrara el informe semanal de protocolos de {informe.sucursal}.\n"
            f"Periodo: {periodo or 'No informado'}\n\n"
            f"Saludos,\nAlguien Te Cuida SpA"
        )
        html = f"""<!DOCTYPE html>
<html lang="es" xmlns="http://www.w3.org/1999/xhtml" style="color-scheme:light;">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <meta name="color-scheme" content="light">
  <style>
    @media (prefers-color-scheme:dark){{
      .ew{{background-color:#f0f4f8!important}}
      .ec{{background-color:#ffffff!important;border-color:#dde3ea!important}}
      .eb{{background-color:#ffffff!important}}
      .ef{{background-color:#f8fafc!important}}
      .em{{background-color:#f8fafc!important;border-color:#e5e7eb!important}}
      .bt{{color:#374151!important}}
      .mt{{color:#6b7280!important}}
      .ml{{color:#374151!important}}
      .fn{{color:#374151!important}}
      .fd{{color:#9ca3af!important}}
      .sl{{border-color:#e5e7eb!important}}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background-color:#f0f4f8;-webkit-text-size-adjust:100%;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       class="ew" style="background-color:#f0f4f8;min-width:320px;">
  <tr>
    <td align="center" style="padding:40px 16px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
             class="ec"
             style="max-width:600px;width:100%;background-color:#ffffff;
                    border-radius:8px;overflow:hidden;border:1px solid #dde3ea;">

        <!-- HEADER -->
        <tr>
          <td style="background-color:#0b1424;padding:20px 36px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="vertical-align:middle;">
                  {f'<img src="{_logo_src}" alt="Alguien Te Cuida" height="44" style="display:block;border:0;">' if _logo_src else '<span style="font-family:Arial,sans-serif;font-size:18px;font-weight:700;color:#ffffff;">Alguien Te Cuida</span>'}
                </td>
                <td align="right" style="vertical-align:middle;">
                  <span style="font-family:Arial,sans-serif;font-size:10px;font-weight:600;
                               color:#7a9bb5;letter-spacing:0.1em;text-transform:uppercase;">
                    Control de protocolos
                  </span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- FRANJA TÍTULO -->
        <tr>
          <td style="background-color:#1e3a5f;padding:20px 36px;">
            <p style="margin:0;font-family:Arial,sans-serif;font-size:11px;font-weight:600;
                      color:#93c5fd;letter-spacing:0.1em;text-transform:uppercase;">
              Informe semanal
            </p>
            <p style="margin:6px 0 0;font-family:Arial,sans-serif;font-size:20px;font-weight:700;
                      color:#ffffff;letter-spacing:-0.01em;line-height:1.3;">
              {informe.sucursal}
            </p>
            <p style="margin:3px 0 0;font-family:Arial,sans-serif;font-size:13px;
                      color:#bfdbfe;line-height:1.4;">
              {informe.cliente} &nbsp;·&nbsp; {periodo or 'Periodo no informado'}
            </p>
          </td>
        </tr>

        <!-- CUERPO -->
        <tr>
          <td class="eb" style="padding:28px 36px 8px;background-color:#ffffff;">
            <p class="bt" style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:14px;
                                  line-height:1.65;color:#374151;">
              Estimado cliente,
            </p>
            <p class="bt" style="margin:0 0 24px;font-family:Arial,sans-serif;font-size:14px;
                                  line-height:1.65;color:#374151;">
              Adjunto encontrará el <strong>Informe Semanal de Protocolos</strong> correspondiente
              al período <strong>{periodo or 'informado'}</strong> para la sucursal
              <strong>{informe.sucursal}</strong>.
            </p>
          </td>
        </tr>

        <!-- META -->
        <tr>
          <td class="eb" style="padding:0 36px 28px;background-color:#ffffff;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0"
                   class="em"
                   style="background-color:#f8fafc;border-radius:6px;border:1px solid #e5e7eb;
                          width:100%;">
              <tr>
                <td style="padding:14px 18px;">
                  <p class="mt" style="margin:0 0 6px;font-family:Arial,sans-serif;
                                       font-size:12px;color:#6b7280;">
                    <strong class="ml" style="color:#374151;">Cliente:</strong>
                    &nbsp;{informe.cliente}
                  </p>
                  <p class="mt" style="margin:0 0 6px;font-family:Arial,sans-serif;
                                       font-size:12px;color:#6b7280;">
                    <strong class="ml" style="color:#374151;">Sucursal:</strong>
                    &nbsp;{informe.sucursal}
                  </p>
                  <p class="mt" style="margin:0;font-family:Arial,sans-serif;
                                       font-size:12px;color:#6b7280;">
                    <strong class="ml" style="color:#374151;">Período:</strong>
                    &nbsp;{periodo or 'No informado'}
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td class="ef"
              style="background-color:#f8fafc;border-top:1px solid #e5e7eb;
                     padding:18px 36px;border-radius:0 0 8px 8px;">
            <p class="fn" style="margin:0;font-family:Arial,sans-serif;font-size:12px;
                                  font-weight:600;color:#374151;">
              Alguien Te Cuida SpA
            </p>
            <p class="fd" style="margin:3px 0 0;font-family:Arial,sans-serif;font-size:11px;
                                  color:#9ca3af;">
              Este correo fue generado automáticamente · {fecha_envio}
            </p>
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>
</body>
</html>"""
        self._enviar_mail_protocolo(destinos, asunto, cuerpo, html, attachment=pdf_attachment, logo_path=_logo_path, usar_contacto=True)
        informe.estado = "ENVIADO"
        meta = {}
        try:
            meta = json.loads(informe.metadata_json or "{}")
        except Exception:
            meta = {}
        meta["envio_manual"] = {
            "fecha": datetime.now(timezone.utc).isoformat(),
            "destinos": destinos,
        }
        informe.metadata_json = json.dumps(meta, ensure_ascii=False)
        informe.error_detalle = ""
        self.db.commit()
        return {"ok": True, "enviados": len(destinos), "estado": informe.estado}

    def rechazar_informe_semanal(self, informe_id: int, payload: dict[str, Any]) -> dict[str, object]:
        informe = self._get_informe(informe_id)
        motivo = str(payload.get("motivo") or "").strip()
        informe.estado = "RECHAZADO"
        informe.error_detalle = motivo or "Rechazado: protocolo irrelevante para envio."
        self.db.commit()
        return {"ok": True, "estado": informe.estado}

    def eliminar_informe(self, informe_id: int) -> dict[str, object]:
        informe = self._get_informe(informe_id)
        self.db.delete(informe)
        self.db.commit()
        return {"ok": True, "deleted": int(informe_id)}
