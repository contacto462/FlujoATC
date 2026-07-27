"""Informe PDF gerencial por cliente (empresa + sucursal) para Bitácora.

Consume el dict que arma `_informacion_cliente_data` en routes/bitacora.py
(incluidas las listas `_detalle`) y genera un PDF con el mismo estilo
corporativo de los otros informes del proyecto (header azul oscuro con logo,
franja de acento, KPI cards, tablas con zebra y footer paginado).
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

# Tope de filas por tabla de detalle para que el PDF gerencial siga siendo
# legible; el Excel descargable es el que lleva el detalle completo.
_MAX_FILAS_DETALLE = 30


def _trunc(texto: str, largo: int) -> str:
    texto = str(texto or "").strip()
    return texto if len(texto) <= largo else texto[: largo - 1] + "…"


def generar_informe_cliente_pdf(data: dict) -> bytes:
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, HRFlowable, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    C_DARK = HexColor("#0b1424")
    C_ACCENT = HexColor("#de7b36")
    C_BG = HexColor("#f7f8fa")
    C_BORDER = HexColor("#e5e7eb")
    C_TEXT = HexColor("#111827")
    C_SOFT = HexColor("#4b5563")
    C_GREY = HexColor("#9ca3af")
    C_OK = HexColor("#1e9c83")
    C_BAD = HexColor("#c0392b")
    C_WARN = HexColor("#d97706")

    inc, pro, bit = data["incidencias"], data["protocolos"], data["bitacora"]

    if data.get("desde") or data.get("hasta"):
        rango_txt = f"Período: {data.get('desde') or 'inicio'} → {data.get('hasta') or 'hoy'}"
    else:
        rango_txt = "Período: histórico completo"

    ahora = datetime.now()
    fecha_emision = ahora.strftime("%d/%m/%Y %H:%M")
    titulo_hdr = "INFORME DE CLIENTE — ESTADO GENERAL"
    subtitulo_hdr = f"{data['empresa']} · {data['sucursal']}  |  {rango_txt}"

    W, H = A4
    pad = 1.4 * cm
    HEADER_H = 2.7 * cm
    ACCENT_H = 5
    FOOTER_H = 1.0 * cm
    BODY_TOP = HEADER_H + ACCENT_H + 12
    BODY_BOT = FOOTER_H + 8
    fw = W - 2 * pad

    _atc_root = Path(__file__).resolve().parents[2]
    logo_path = _atc_root / "ATC" / "static" / "img" / "logo-atc.png"
    if not logo_path.exists():
        logo_path = _atc_root / "static" / "img" / "logo-atc.png"
    logo_w, logo_h = 2.8 * cm, 1.4 * cm

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
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(tx, H - HEADER_H + 1.35 * cm, titulo_hdr)
        canvas.setFillColor(HexColor("#f6c9a4"))
        canvas.setFont("Helvetica", 8.5)
        canvas.drawString(tx, H - HEADER_H + 0.75 * cm, subtitulo_hdr)
        canvas.setFillColor(C_ACCENT)
        canvas.rect(0, H - HEADER_H - ACCENT_H, W, ACCENT_H, fill=1, stroke=0)
        canvas.setFillColor(C_DARK)
        canvas.rect(0, 0, W, FOOTER_H, fill=1, stroke=0)
        canvas.setFillColor(C_GREY)
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(
            W / 2, FOOTER_H / 2 - 3,
            f"Documento generado automáticamente  ·  Alguien Te Cuida  ·  {fecha_emision}",
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

    st_kpi_num = ParagraphStyle("kpiNum", fontName="Helvetica-Bold", fontSize=20, textColor=C_TEXT, leading=22, alignment=1)
    st_kpi_lbl = ParagraphStyle("kpiLbl", fontName="Helvetica-Bold", fontSize=7, textColor=C_SOFT, leading=9, alignment=1)
    st_sec = ParagraphStyle("sec", fontName="Helvetica-Bold", fontSize=11, textColor=C_ACCENT, leading=14, spaceBefore=14, spaceAfter=6)
    st_body = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, textColor=C_SOFT, leading=14.5, alignment=TA_JUSTIFY, spaceAfter=6)
    st_th = ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8, textColor=white, leading=10)
    st_td = ParagraphStyle("td", fontName="Helvetica", fontSize=8, textColor=C_TEXT, leading=11)
    st_td_soft = ParagraphStyle("tdSoft", fontName="Helvetica", fontSize=7.5, textColor=C_SOFT, leading=10)

    def tabla(headers: list[str], filas: list[list[str]], anchos_rel: list[float]) -> Table:
        data_rows = [[Paragraph(h, st_th) for h in headers]]
        for fila in filas:
            data_rows.append([Paragraph(str(c), st_td) for c in fila])
        t = Table(data_rows, colWidths=[fw * a for a in anchos_rel], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_DARK),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, C_BG]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("BOX", (0, 0), (-1, -1), 1, C_BORDER),
        ]))
        return t

    def seccion(titulo: str):
        story.append(Paragraph(titulo, st_sec))
        story.append(HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=8))

    def nota_tope(total: int):
        if total > _MAX_FILAS_DETALLE:
            story.append(Spacer(1, 3))
            story.append(Paragraph(
                f"Se muestran los {_MAX_FILAS_DETALLE} registros más recientes de {total}. "
                "El detalle completo está disponible en el informe Excel.",
                st_td_soft,
            ))

    story: list = []

    # ── KPI cards ── altura fija en las 2 filas (numero + etiqueta) para que
    # las 4 cards midan siempre lo mismo, sin importar si la etiqueta
    # alcanza a entrar en 1 línea o necesita 2 (reportlab no respeta "\n"
    # suelto, por eso el <br/> explicito para forzar el quiebre siempre).
    def kpi_card(numero: str, etiqueta: str, color) -> Table:
        etiqueta_html = etiqueta.replace("\n", "<br/>")
        t = Table(
            [[Paragraph(numero, st_kpi_num)], [Paragraph(etiqueta_html, st_kpi_lbl)]],
            colWidths=[fw / 4 - 8],
            rowHeights=[36, 32],
        )
        t.setStyle(TableStyle([
            ("TOPPADDING", (0, 0), (-1, 0), 12), ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
            ("TOPPADDING", (0, 1), (-1, 1), 2), ("BOTTOMPADDING", (0, 1), (-1, 1), 12),
            ("VALIGN", (0, 0), (-1, 0), "BOTTOM"),
            ("VALIGN", (0, 1), (-1, 1), "TOP"),
            ("BOX", (0, 0), (-1, -1), 1, C_BORDER),
            ("LINEABOVE", (0, 0), (-1, 0), 3, color),
            ("BACKGROUND", (0, 0), (-1, -1), white),
        ]))
        return t

    kpis = Table(
        [[
            kpi_card(str(inc["total"]), "INCIDENCIAS\nTOTALES", C_ACCENT),
            kpi_card(str(inc["pendientes"]), "INCIDENCIAS\nPENDIENTES", C_BAD if inc["pendientes"] else C_OK),
            kpi_card(str(pro["total_registros"]), "ACTIVACIONES DE\nPROTOCOLO", C_WARN),
            kpi_card(str(bit["total"]), "MOVIMIENTOS DE\nBITÁCORA", C_OK),
        ]],
        colWidths=[fw / 4] * 4,
    )
    kpis.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]))
    story.append(kpis)
    story.append(Spacer(1, 14))

    # ── Resumen ejecutivo ──
    seccion("RESUMEN EJECUTIVO")
    partes = [
        f"La sucursal <b>{data['sucursal']}</b> de <b>{data['empresa']}</b> registra "
        f"<b>{inc['total']} incidencias</b> en el período ({inc['cerradas']} cerradas, "
        f"{inc['pendientes']} pendientes).",
    ]
    if inc["cerradas"]:
        partes.append(f"El tiempo promedio de resolución de las incidencias cerradas es de "
                      f"<b>{inc['resolucion_promedio_dias']} días</b>.")
    if inc["pendientes"]:
        partes.append(f"Las pendientes acumulan una antigüedad promedio de "
                      f"<b>{inc['antiguedad_promedio_dias']} días</b> sin cierre.")
    partes.append(
        f"En protocolos se registran <b>{pro['total_registros']} activaciones</b> "
        f"({pro['preventivos']} preventivas, {pro['intrusivos']} intrusivas), de las cuales "
        f"<b>{pro['exitosos']} fueron exitosas</b> y {pro['no_exitosos']} no exitosas."
    )
    if pro["informes_pendientes"]:
        partes.append(f"Hay <b>{pro['informes_pendientes']} informes de protocolo pendientes</b> "
                      f"de un total de {pro['total_informes']} generados.")
    partes.append(
        f"La bitácora acumula <b>{bit['total']} movimientos</b> "
        f"({bit['ultimos_30_dias']} en los últimos 30 días)."
    )
    story.append(Paragraph(" ".join(partes), st_body))
    story.append(Spacer(1, 6))

    # ── Incidencias ──
    seccion("INCIDENCIAS — DESGLOSE")
    mitades = []
    if inc["por_tipo"]:
        mitades.append(tabla(["Tipo", "Cant."], [[n, c] for n, c in inc["por_tipo"]], [0.34, 0.12]))
    if inc["por_estado"]:
        mitades.append(tabla(["Estado", "Cant."], [[n, c] for n, c in inc["por_estado"]], [0.34, 0.12]))
    if mitades:
        lado_a_lado = Table([[m for m in mitades]], colWidths=[fw / len(mitades)] * len(mitades))
        lado_a_lado.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(lado_a_lado)
    else:
        story.append(Paragraph("Sin incidencias registradas en el período.", st_td_soft))
    story.append(Spacer(1, 8))

    inc_pendientes = [r for r in inc["_detalle"]
                      if str(r["estado"]).strip().lower() not in ("terminado", "repetida")]
    if inc_pendientes:
        story.append(Paragraph("Incidencias pendientes (todas):", st_body))
        story.append(tabla(
            ["ODT", "Fecha", "Tipo", "Derivación", "Estado", "Días abierta"],
            [[r["odt"], (r["fecha_registro"].strftime("%d-%m-%Y") if r["fecha_registro"] else ""),
              _trunc(r["tipo"], 34), _trunc(r["derivacion"], 24), r["estado"],
              r["dias_abierta"] if r["dias_abierta"] is not None else "-"]
             for r in inc_pendientes],
            [0.11, 0.12, 0.28, 0.22, 0.15, 0.12],
        ))
        story.append(Spacer(1, 8))

    inc_cerradas = [r for r in inc["_detalle"]
                    if str(r["estado"]).strip().lower() in ("terminado", "repetida")]
    if inc_cerradas:
        story.append(Paragraph("Incidencias cerradas más recientes:", st_body))
        story.append(tabla(
            ["ODT", "Fecha", "Tipo", "Estado", "Cierre", "Días resol."],
            [[r["odt"], (r["fecha_registro"].strftime("%d-%m-%Y") if r["fecha_registro"] else ""),
              _trunc(r["tipo"], 34), r["estado"],
              (r["fecha_cierre"].strftime("%d-%m-%Y") if r["fecha_cierre"] else "-"),
              r["dias_resolucion"] if r["dias_resolucion"] is not None else "-"]
             for r in inc_cerradas[:_MAX_FILAS_DETALLE]],
            [0.11, 0.12, 0.32, 0.16, 0.15, 0.14],
        ))
        nota_tope(len(inc_cerradas))
    story.append(Spacer(1, 6))

    # ── Protocolos ──
    seccion("PROTOCOLOS — ACTIVACIONES")
    if pro["_detalle"]:
        story.append(tabla(
            ["Fecha", "Tipo", "Exitoso", "Detectado", "Efectivo", "Carabineros", "Puesto", "Operador"],
            [[(r["fecha_registro"].strftime("%d-%m-%Y %H:%M") if r["fecha_registro"] else ""),
              r["tipo"], r["exitoso"], r["detectado"] or "-", r["efectivo"] or "-",
              r["carabineros"] or "-", _trunc(r["puesto"], 14) or "-", _trunc(r["operador"], 22) or "-"]
             for r in pro["_detalle"][:_MAX_FILAS_DETALLE]],
            [0.15, 0.12, 0.09, 0.10, 0.10, 0.12, 0.12, 0.20],
        ))
        nota_tope(len(pro["_detalle"]))
    else:
        story.append(Paragraph("Sin activaciones de protocolo en el período.", st_td_soft))
    story.append(Spacer(1, 6))

    # ── Bitácora ──
    seccion("BITÁCORA — MOVIMIENTOS")
    if bit["por_tipo"]:
        story.append(tabla(["Tipo de movimiento", "Cantidad"],
                           [[n, c] for n, c in bit["por_tipo"]], [0.40, 0.15]))
        story.append(Spacer(1, 8))
    if bit["_detalle"]:
        story.append(Paragraph("Movimientos más recientes:", st_body))
        story.append(tabla(
            ["Fecha", "Tipo", "Operador", "Detalle / Observación"],
            [[(r["fecha"].strftime("%d-%m-%Y %H:%M") if r["fecha"] else ""),
              _trunc(r["tipo"], 22), _trunc(r["operador"], 20),
              _trunc(" — ".join(p for p in (r["detalle"], r["observacion"]) if p), 90)]
             for r in bit["_detalle"][:_MAX_FILAS_DETALLE]],
            [0.15, 0.17, 0.16, 0.52],
        ))
        nota_tope(len(bit["_detalle"]))
    elif not bit["por_tipo"]:
        story.append(Paragraph("Sin movimientos de bitácora en el período.", st_td_soft))

    doc.build(story)
    return buf.getvalue()
