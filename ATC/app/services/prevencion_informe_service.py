from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

_ATC_ROOT = Path(__file__).resolve().parents[2]

# Mismos colores que la pagina web de Estatus Gestion / resto de reportes ATC.
_C_DARK = "#0b1424"
_C_ORANGE = "#f4a672"
_C_ORDK = "#c2410c"
_C_BG = "#f7f8fa"
_C_BORDER = "#e5e7eb"
_C_TEXT = "#111827"
_C_SOFT = "#4b5563"
_C_GREY = "#9ca3af"
_C_OK = "#1e9c83"
_C_WARN = "#d97706"
_C_BAD = "#c0392b"
_C_NONE = "#94a3b8"


def _estado_de(avance) -> str:
    if avance is None:
        return "none"
    if avance >= 90:
        return "ok"
    if avance >= 50:
        return "warn"
    return "bad"


def generar_informe_estatus_gestion_pdf(secciones: list[dict]) -> bytes:
    """Genera el PDF "Estatus de Gestion - Prevencion de Riesgos" con el mismo
    estilo corporativo (header navy + barra naranja, tarjetas KPI, donut,
    barras, tabla) usado en el resto de los informes de gestion de ATC."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.colors import HexColor, white
    from reportlab.platypus import (
        BaseDocTemplate, Frame, PageTemplate, PageBreak,
        Table, TableStyle, Paragraph, Spacer, HRFlowable,
    )
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.graphics.shapes import Drawing, Circle, String
    from reportlab.graphics.charts.piecharts import Pie
    from reportlab.graphics.charts.barcharts import HorizontalBarChart

    C_DARK, C_ORANGE, C_ORDK = HexColor(_C_DARK), HexColor(_C_ORANGE), HexColor(_C_ORDK)
    C_BG, C_BORDER, C_TEXT, C_SOFT, C_GREY = (
        HexColor(_C_BG), HexColor(_C_BORDER), HexColor(_C_TEXT), HexColor(_C_SOFT), HexColor(_C_GREY)
    )
    C_OK, C_WARN, C_BAD, C_NONE = HexColor(_C_OK), HexColor(_C_WARN), HexColor(_C_BAD), HexColor(_C_NONE)

    # ── Métricas ──────────────────────────────────────────────────────────
    todos = [it for s in secciones for it in s["items"]]
    total_items = len(todos)
    con_avance = [it["avance"] for it in todos if it["avance"] is not None]
    promedio_general = round(sum(con_avance) / len(con_avance), 1) if con_avance else 0.0

    conteo = {"ok": 0, "warn": 0, "bad": 0, "none": 0}
    for it in todos:
        conteo[_estado_de(it["avance"])] += 1

    resumen_secciones = []
    for s in secciones:
        vals = [it["avance"] for it in s["items"] if it["avance"] is not None]
        promedio = round(sum(vals) / len(vals), 1) if vals else None
        resumen_secciones.append({"seccion": s["seccion"], "promedio": promedio, "n": len(s["items"])})
    resumen_secciones.sort(key=lambda r: (r["promedio"] is None, r["promedio"] if r["promedio"] is not None else 0))

    pendientes = sorted(
        todos,
        key=lambda it: (it["avance"] is not None, it["avance"] if it["avance"] is not None else -1),
    )
    pendientes = [it for it in pendientes if it["avance"] is None or it["avance"] < 90][:25]

    ahora = datetime.now()
    fecha_emision = ahora.strftime("%d/%m/%Y %H:%M")
    titulo_hdr = "ESTATUS DE GESTIÓN — PREVENCIÓN DE RIESGOS"
    subtitulo_hdr = "Avance documental, capacitaciones, protocolos y matriz de riesgos"

    W, H = A4
    pad = 1.4 * cm
    HEADER_H = 2.7 * cm
    ORANGE_H = 5
    FOOTER_H = 1.0 * cm
    BODY_TOP = HEADER_H + ORANGE_H + 12
    BODY_BOT = FOOTER_H + 8
    fw = W - 2 * pad

    logo_path = _ATC_ROOT / "ATC" / "static" / "img" / "logo-atc.png"
    if not logo_path.exists():
        logo_path = _ATC_ROOT / "static" / "img" / "logo-atc.png"
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
        canvas.setFont("Helvetica-Bold", 14)
        canvas.drawString(tx, H - HEADER_H + 1.35 * cm, titulo_hdr)
        canvas.setFillColor(HexColor("#fde68a"))
        canvas.setFont("Helvetica", 8.5)
        canvas.drawString(tx, H - HEADER_H + 0.75 * cm, subtitulo_hdr)
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
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(W - pad, FOOTER_H / 2 - 3, f"Página {doc.page}")
        canvas.restoreState()

    frame = Frame(
        pad, BODY_BOT, fw, H - BODY_TOP - BODY_BOT,
        leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0,
    )
    page_tmpl = PageTemplate(id="main", frames=[frame], onPage=draw_page)
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4, pageTemplates=[page_tmpl],
        leftMargin=0, rightMargin=0, topMargin=0, bottomMargin=0,
        title=titulo_hdr, author="Alguien Te Cuida",
    )

    st_kpi_num = ParagraphStyle("kpiNum", fontName="Helvetica-Bold", fontSize=22, textColor=C_TEXT, leading=24, alignment=1)
    st_kpi_lbl = ParagraphStyle("kpiLbl", fontName="Helvetica-Bold", fontSize=7.5, textColor=C_SOFT, leading=10, alignment=1)
    st_sec = ParagraphStyle("sec", fontName="Helvetica-Bold", fontSize=11, textColor=C_ORDK, leading=14, spaceBefore=14, spaceAfter=6)
    st_body = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, textColor=C_SOFT, leading=14.5, alignment=TA_JUSTIFY, spaceAfter=6)
    st_th = ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8, textColor=white, leading=10)
    st_td = ParagraphStyle("td", fontName="Helvetica", fontSize=8, textColor=C_TEXT, leading=11)
    st_td_soft = ParagraphStyle("tdSoft", fontName="Helvetica", fontSize=7.5, textColor=C_SOFT, leading=10)

    story: list = []

    # ── KPIs ──────────────────────────────────────────────────────────────
    def kpi_card(numero: str, etiqueta: str, color) -> Table:
        t = Table([[Paragraph(numero, st_kpi_num)], [Paragraph(etiqueta, st_kpi_lbl)]], colWidths=[fw / 4 - 8])
        t.setStyle(TableStyle([
            ("TOPPADDING", (0, 0), (-1, 0), 12), ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
            ("TOPPADDING", (0, 1), (-1, 1), 2), ("BOTTOMPADDING", (0, 1), (-1, 1), 12),
            ("BOX", (0, 0), (-1, -1), 1, C_BORDER),
            ("LINEABOVE", (0, 0), (-1, 0), 3, color),
            ("BACKGROUND", (0, 0), (-1, -1), white),
        ]))
        return t

    kpis = Table(
        [[
            kpi_card(str(total_items), "TOTAL ÍTEMS\nDE GESTIÓN", C_DARK),
            kpi_card(str(conteo["ok"]), "AL DÍA\n(≥ 90%)", C_OK),
            kpi_card(str(conteo["warn"] + conteo["bad"] + conteo["none"]), "PENDIENTES /\nEN PROCESO", C_BAD),
            kpi_card(f"{promedio_general}%", "AVANCE\nGENERAL", C_WARN),
        ]],
        colWidths=[fw / 4] * 4,
    )
    kpis.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]))
    story.append(kpis)
    story.append(Spacer(1, 14))

    # ── Resumen general ──────────────────────────────────────────────────
    story.append(Paragraph("RESUMEN GENERAL", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=8))
    n_pend = conteo["warn"] + conteo["bad"] + conteo["none"]
    pct_pend = round((n_pend / total_items) * 100, 1) if total_items else 0
    story.append(Paragraph(
        f"El seguimiento de gestión de Prevención de Riesgos contempla <b>{total_items}</b> ítems entre documentación, "
        f"capacitaciones, protocolos y matriz de riesgos. El avance general es de <b>{promedio_general}%</b>: "
        f"<b>{conteo['ok']}</b> ítems están al día (≥90%), mientras que <b>{n_pend}</b> ({pct_pend}%) siguen "
        f"pendientes o en proceso, incluyendo <b>{conteo['none']}</b> sin dato ingresado aún.",
        st_body,
    ))
    story.append(Spacer(1, 6))

    # ── Donut de estado ──────────────────────────────────────────────────
    dwg = Drawing(fw, 5.4 * cm)
    pie = Pie()
    pie.x, pie.y = fw / 2 - 3.4 * cm, 0.2 * cm
    pie.width, pie.height = 5.4 * cm, 5.4 * cm
    valores = [conteo["ok"], conteo["warn"], conteo["bad"], conteo["none"]]
    colores_pie = [C_OK, C_WARN, C_BAD, C_NONE]
    labels = ["Al día", "En proceso", "Pendiente", "Sin dato"]
    idx_no_cero = [i for i, v in enumerate(valores) if v > 0]
    pie.data = [valores[i] for i in idx_no_cero] or [1]
    for slot, orig_i in enumerate(idx_no_cero):
        pie.slices[slot].fillColor = colores_pie[orig_i]
        pie.slices[slot].strokeColor = white
        pie.slices[slot].strokeWidth = 1.5
    dwg.add(pie)
    centro_x, centro_y = pie.x + pie.width / 2, pie.y + pie.height / 2
    dwg.add(Circle(centro_x, centro_y, 1.85 * cm, fillColor=white, strokeColor=white))
    dwg.add(String(centro_x, centro_y + 4, str(total_items), fontName="Helvetica-Bold", fontSize=20, fillColor=C_TEXT, textAnchor="middle"))
    dwg.add(String(centro_x, centro_y - 14, "ítems totales", fontName="Helvetica", fontSize=8, fillColor=C_SOFT, textAnchor="middle"))
    story.append(dwg)

    leyenda_cells = []
    for lbl, col in zip(labels, colores_pie):
        leyenda_cells.append(Table([[""]], colWidths=[9], rowHeights=[9], style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), col)])))
        leyenda_cells.append(Paragraph(lbl, st_td_soft))
    leyenda = Table([leyenda_cells], colWidths=None)
    leyenda.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story.append(Table([[leyenda]], colWidths=[fw], style=TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")])))
    story.append(Spacer(1, 4))

    # ── Avance promedio por área ─────────────────────────────────────────
    story.append(Paragraph("AVANCE PROMEDIO POR ÁREA", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=8))
    story.append(Paragraph("Promedio de avance de cada área, ordenadas de menor a mayor cumplimiento.", st_td_soft))
    story.append(Spacer(1, 6))

    bar_h = max(3.2 * cm, len(resumen_secciones) * 0.62 * cm)
    bdwg = Drawing(fw, bar_h)
    chart = HorizontalBarChart()
    chart.x, chart.y = 3.6 * cm, 6
    chart.width, chart.height = fw - 4.4 * cm, bar_h - 16
    datos_barra = [r["promedio"] if r["promedio"] is not None else 0 for r in resumen_secciones]
    chart.data = [datos_barra]
    chart.categoryAxis.categoryNames = [
        (r["seccion"][:26] + "…") if len(r["seccion"]) > 27 else r["seccion"] for r in resumen_secciones
    ]
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 6.6
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 100
    chart.valueAxis.valueStep = 20
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 6.5
    chart.bars[0].fillColor = C_WARN
    for i, r in enumerate(resumen_secciones):
        p = r["promedio"] or 0
        chart.bars[(0, i)].fillColor = C_BAD if p < 50 else (C_WARN if p < 90 else C_OK)
    chart.barLabels.fontName = "Helvetica-Bold"
    chart.barLabels.fontSize = 6.8
    chart.barLabelFormat = "%0.0f%%"
    chart.barLabels.dx = 14
    chart.categoryAxis.strokeColor = C_BORDER
    chart.valueAxis.strokeColor = C_BORDER
    bdwg.add(chart)
    story.append(bdwg)

    # ── Página 2: tabla de pendientes ────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("ÍTEMS PENDIENTES O EN PROCESO (menor avance primero)", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=8))
    story.append(Paragraph(
        "Documentos, capacitaciones y protocolos que aún no alcanzan el 90% de avance, ordenados por menor cumplimiento.",
        st_td_soft,
    ))
    story.append(Spacer(1, 6))

    filas_tabla = [[
        Paragraph("DOCUMENTO / GESTIÓN", st_th), Paragraph("RESPONSABLE", st_th),
        Paragraph("AVANCE", st_th), Paragraph("OBSERVACIONES", st_th),
    ]]
    for it in pendientes:
        pct = it["avance"]
        pct_txt = "Sin dato" if pct is None else f"{pct}%"
        color_hex = _C_NONE if pct is None else (_C_BAD if pct < 50 else _C_WARN)
        filas_tabla.append([
            Paragraph(it["documento"], st_td),
            Paragraph(it["responsable"] or "—", st_td_soft),
            Paragraph(f'<font color="{color_hex}"><b>{pct_txt}</b></font>', st_td),
            Paragraph(it["observaciones"] or "—", st_td_soft),
        ])

    tabla = Table(filas_tabla, colWidths=[fw * 0.40, fw * 0.20, fw * 0.12, fw * 0.28], repeatRows=1)
    estilos_tabla = [
        ("BACKGROUND", (0, 0), (-1, 0), C_DARK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, C_BG]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, C_BORDER),
        ("BOX", (0, 0), (-1, -1), 1, C_BORDER),
    ]
    tabla.setStyle(TableStyle(estilos_tabla))
    story.append(tabla)
    if not pendientes:
        story.append(Spacer(1, 8))
        story.append(Paragraph("No hay ítems pendientes: todo el seguimiento está al día. 🎉", st_body))

    # ── Conclusión ───────────────────────────────────────────────────────
    story.append(Spacer(1, 16))
    story.append(Paragraph("CONCLUSIÓN", st_sec))
    story.append(HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=8))
    con_promedio = [r for r in resumen_secciones if r["promedio"] is not None]
    peor = con_promedio[0] if con_promedio else None
    mejor = con_promedio[-1] if con_promedio else None
    frase_areas = ""
    if peor and mejor and peor["seccion"] != mejor["seccion"]:
        frase_areas = (
            f" El área con menor avance es <b>{peor['seccion']}</b> "
            f"({peor['promedio']}%), mientras que "
            f"<b>{mejor['seccion']}</b> presenta el mayor cumplimiento "
            f"({mejor['promedio']}%)."
        )
    story.append(Paragraph(
        f"Del total de {total_items} ítems de gestión de Prevención de Riesgos, el avance general alcanza "
        f"<b>{promedio_general}%</b>. {n_pend} ítems ({pct_pend}%) requieren seguimiento adicional para "
        f"alcanzar el cumplimiento total.{frase_areas}",
        st_body,
    ))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()
