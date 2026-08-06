"""Informe PDF de análisis de "Información Puestos" para Bitácora.

Consume el dict que arma `_informe_puestos_dataset` en routes/bitacora.py:
la lista `puestos` (protocolos preventivos/intrusivos, movimientos de
bitácora, sucursales, métricas normalizadas por sucursal, índice de carga
combinado) más, por puesto, `intrusivos_detalle`: por cada protocolo
intrusivo, la lista de incidencias activas en el momento, el operador,
el desenlace (protocolo_exitoso), la hora aproximada y — solo para los
puestos que se narran en detalle (el top por intrusivos) — movimientos de
bitácora del puesto completo y del operador puntual, ambos en una ventana
de +/-30min alrededor del evento (el operador cubre un puesto entero como
asignación normal, así que lo relevante no es en cuántas sucursales tuvo
actividad — eso es simplemente su pega de siempre — sino cuánta bitácora
escribió él mismo justo en ese momento, como señal de qué tan ocupado
estaba). También trae
`franja_horaria`: distribución de intrusivos vs. todos los protocolos por
franja horaria del día.

Es un informe de ANÁLISIS, no de datos crudos (esos ya están en el Excel):
prosa que compara los puestos con más intrusiones contra los que menos,
cruzando movimientos de bitácora normalizados por sucursal, operador
responsable, desenlace del evento e incidencias activas al momento de cada
intrusivo — mostrando el caso a caso además de los promedios, porque son
pocos eventos puntuales y no ameritan conclusiones causales fuertes — más
un gráfico de barras agrupadas (preventivos / intrusivos / movimientos de
bitácora) por los 29 puestos.

Nota sobre horas: `fecha_registro` de cada protocolo es la hora en que se
REGISTRÓ el parte (a veces al día siguiente), no necesariamente la hora
real de la intrusión; cuando el texto de la observación menciona una hora
("a las 22:22 hrs") se usa esa como aproximación de la hora del día real.
El informe incluye además una sección de demostración (con datos
ilustrativos, no reales) de lo que será el análisis de tiempo de
detección/reacción una vez se registren la hora real de la intrusión y la
hora de detección del operador.
"""
from __future__ import annotations

import calendar
import io
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from statistics import mean

_MESES_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}

_TOP_N = 6


def _plural(n: float, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def _etiqueta_periodo(desde: str, hasta: str) -> str:
    """'2026-07-01'/'2026-07-31' -> 'del mes de julio'; si no calza con un mes
    calendario completo, o falta alguna fecha, cae a un rango legible."""
    if not desde or not hasta:
        return "histórico completo"
    try:
        d_ini = date.fromisoformat(desde.strip())
        d_fin = date.fromisoformat(hasta.strip())
    except ValueError:
        return "período seleccionado"
    es_mes_completo = (
        d_ini.year == d_fin.year
        and d_ini.month == d_fin.month
        and d_ini.day == 1
        and d_fin.day == calendar.monthrange(d_fin.year, d_fin.month)[1]
    )
    if es_mes_completo:
        return f"del mes de {_MESES_ES[d_ini.month]}"
    return f"del {d_ini.strftime('%d/%m/%Y')} al {d_fin.strftime('%d/%m/%Y')}"


def _lista_texto(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " y " + items[-1]


def _nivel(valor: float, promedio: float) -> str:
    if promedio <= 0:
        return "sin comparación posible"
    ratio = valor / promedio
    if ratio >= 1.5:
        return "muy por encima del promedio de todos los puestos"
    if ratio >= 1.15:
        return "por encima del promedio"
    if ratio <= 0.6:
        return "muy por debajo del promedio"
    if ratio <= 0.85:
        return "por debajo del promedio"
    return "en línea con el promedio"


def _enriquecer(puestos: list[dict]) -> list[dict]:
    salida = []
    for p in puestos:
        salida.append({**p, "protocolos_total": p["protocolos_preventivos"] + p["protocolos_intrusivos"]})
    return salida


def _grafico_barras(puestos: list[dict], fw: float):
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.charts.legends import Legend
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib.colors import HexColor

    C_PREV = HexColor("#de7b36")
    C_INTR = HexColor("#c0392b")
    C_MOV = HexColor("#1e9c83")

    ordenado = sorted(puestos, key=lambda p: p["puesto"])
    preventivos = [p["protocolos_preventivos"] for p in ordenado]
    intrusivos = [p["protocolos_intrusivos"] for p in ordenado]
    movimientos = [p["movimientos_bitacora"] for p in ordenado]
    nombres = [str(p["puesto"]) for p in ordenado]

    # Los movimientos de bitácora ocurren órdenes de magnitud más seguido que
    # los protocolos (son entradas/salidas y avisos, no eventos de seguridad):
    # sin reescalar, las barras de protocolos quedarían invisibles al lado.
    # Se reescala para que el grafico siga siendo comparable y legible; el
    # valor real de cada puesto ya está en el análisis de texto.
    max_proto = max([*preventivos, *intrusivos, 1])
    max_mov = max([*movimientos, 0])
    factor = max(1, round(max_mov / (max_proto * 4))) if max_mov > max_proto * 4 else 1
    movimientos_escala = [round(v / factor) for v in movimientos] if factor > 1 else movimientos
    etiqueta_mov = f"Movimientos de bitácora (÷{factor})" if factor > 1 else "Movimientos de bitácora"

    alto = 235
    d = Drawing(fw, alto)

    chart = VerticalBarChart()
    chart.x = 28
    chart.y = 34
    chart.width = fw - 40
    chart.height = alto - 70
    chart.data = [preventivos, intrusivos, movimientos_escala]
    chart.barSpacing = 0.6

    chart.strokeColor = None
    chart.bars.strokeWidth = 0
    chart.bars[0].fillColor = C_PREV
    chart.bars[1].fillColor = C_INTR
    chart.bars[2].fillColor = C_MOV

    chart.valueAxis.valueMin = 0
    chart.valueAxis.labels.fontSize = 6.5
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.strokeColor = HexColor("#cbd5e1")
    chart.valueAxis.gridStrokeColor = HexColor("#e5e7eb")
    chart.valueAxis.visibleGrid = True

    chart.categoryAxis.categoryNames = nombres
    chart.categoryAxis.labels.fontSize = 6
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.strokeColor = HexColor("#cbd5e1")

    d.add(chart)

    legend = Legend()
    legend.x = fw - 175
    legend.y = alto - 6
    legend.dx = 7
    legend.dy = 7
    legend.dxTextSpace = 4
    legend.deltay = 9
    legend.fontName = "Helvetica"
    legend.fontSize = 7.5
    legend.alignment = "right"
    legend.colorNamePairs = [
        (C_PREV, "Protocolos preventivos"),
        (C_INTR, "Protocolos intrusivos"),
        (C_MOV, etiqueta_mov),
    ]
    d.add(legend)

    return d, factor


def generar_informe_puestos_pdf(data: dict) -> bytes:
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
    C_BORDER = HexColor("#e5e7eb")
    C_TEXT = HexColor("#111827")
    C_SOFT = HexColor("#4b5563")
    C_GREY = HexColor("#9ca3af")
    C_OK = HexColor("#1e9c83")
    C_BAD = HexColor("#c0392b")
    C_WARN = HexColor("#d97706")

    puestos = _enriquecer(data.get("puestos") or [])

    if data.get("desde") or data.get("hasta"):
        rango_txt = f"Período: {data.get('desde') or 'inicio'} → {data.get('hasta') or 'hoy'}"
    else:
        rango_txt = "Período: histórico completo"
    etiqueta_periodo = _etiqueta_periodo(data.get("desde") or "", data.get("hasta") or "")

    ahora = datetime.now()
    fecha_emision = ahora.strftime("%d/%m/%Y %H:%M")
    titulo_hdr = "INFORME DE PUESTOS — ANÁLISIS DE INTRUSIONES"
    subtitulo_hdr = f"Puestos de monitoreo 1-29  |  {rango_txt}"

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
    st_sub = ParagraphStyle("sub", fontName="Helvetica-Bold", fontSize=9.5, textColor=C_TEXT, leading=13, spaceBefore=8, spaceAfter=3)
    st_body = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, textColor=C_SOFT, leading=14.5, alignment=TA_JUSTIFY, spaceAfter=6)
    st_body_r = ParagraphStyle("bodyR", parent=st_body, textColor=C_TEXT)
    st_caption = ParagraphStyle("caption", fontName="Helvetica-Oblique", fontSize=7.5, textColor=C_GREY, leading=10)
    st_th = ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=7.5, textColor=white, leading=9.5)
    st_td = ParagraphStyle("td", fontName="Helvetica", fontSize=7.5, textColor=C_TEXT, leading=9.5)
    st_td_bad = ParagraphStyle("tdBad", parent=st_td, textColor=C_BAD, fontName="Helvetica-Bold")

    def seccion(titulo: str):
        story.append(Paragraph(titulo, st_sec))
        story.append(HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=8))

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

    story: list = []

    total_protocolos = sum(p["protocolos_total"] for p in puestos)
    total_intrusivos = sum(p["protocolos_intrusivos"] for p in puestos)
    total_movimientos = sum(p["movimientos_bitacora"] for p in puestos)
    total_incidencias = sum(p["incidencias"] for p in puestos)

    kpis = Table(
        [[
            kpi_card(str(total_protocolos), "PROTOCOLOS\nTOTALES", C_ACCENT),
            kpi_card(str(total_intrusivos), "PROTOCOLOS\nINTRUSIVOS", C_WARN),
            kpi_card(str(total_movimientos), "MOVIMIENTOS DE\nBITÁCORA", C_OK),
            kpi_card(str(total_incidencias), "INCIDENCIAS\nTOTALES", C_ACCENT),
        ]],
        colWidths=[fw / 4] * 4,
    )
    kpis.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]))
    story.append(kpis)
    story.append(Spacer(1, 14))

    seccion("PROTOCOLOS Y MOVIMIENTOS DE BITÁCORA POR PUESTO")
    grafico, factor_escala = _grafico_barras(puestos, fw)
    story.append(grafico)
    if factor_escala > 1:
        story.append(Paragraph(
            f"Los movimientos de bitácora se muestran divididos por {factor_escala} para poder compararlos "
            "junto a los protocolos en el mismo gráfico; el valor real de cada puesto está en el análisis "
            "de más abajo.",
            st_caption,
        ))
    story.append(Spacer(1, 6))

    # ── Análisis ──
    con_intrusivos = [p for p in puestos if p["protocolos_intrusivos"] > 0]
    sin_intrusivos = [p for p in puestos if p["protocolos_intrusivos"] == 0]
    ordenado_desc = sorted(puestos, key=lambda p: -p["protocolos_intrusivos"])
    top = [p for p in ordenado_desc if p["protocolos_intrusivos"] > 0][:_TOP_N]

    prom_mov_global = mean([p["movimientos_bitacora"] for p in puestos]) if puestos else 0
    prom_suc_global = mean([p["sucursales"] for p in puestos]) if puestos else 0
    prom_mov_con = mean([p["movimientos_bitacora"] for p in con_intrusivos]) if con_intrusivos else 0
    prom_mov_sin = mean([p["movimientos_bitacora"] for p in sin_intrusivos]) if sin_intrusivos else 0
    prom_suc_con = mean([p["sucursales"] for p in con_intrusivos]) if con_intrusivos else 0
    prom_suc_sin = mean([p["sucursales"] for p in sin_intrusivos]) if sin_intrusivos else 0

    seccion("PUESTOS CON MÁS INTRUSIONES — ANÁLISIS")

    intro = (
        f"De los {len(puestos)} puestos de monitoreo, <b>{len(con_intrusivos)}</b> registraron al menos un "
        f"protocolo intrusivo en el período, acumulando <b>{total_intrusivos}</b> activaciones intrusivas en "
        f"total. Los otros <b>{len(sin_intrusivos)}</b> puestos no registraron ninguna."
    )
    if con_intrusivos and sin_intrusivos:
        intro += (
            f" En promedio, los puestos con intrusivos registran <b>{prom_mov_con:.1f} movimientos de "
            f"bitácora</b> en el período, frente a <b>{prom_mov_sin:.1f}</b> en los puestos sin intrusivos, "
            f"y cubren en promedio <b>{prom_suc_con:.1f} sucursales</b> cada uno, frente a "
            f"<b>{prom_suc_sin:.1f}</b> en los puestos sin intrusivos."
        )
    story.append(Paragraph(intro, st_body))

    cautela = (
        f"<b>Cautela estadística:</b> {total_intrusivos} intrusivos repartidos en {len(con_intrusivos)} "
        f"puestos son eventos puntuales, por lo que los promedios de esta sección son una "
        f"referencia general, no una conclusión final. Por eso a continuación se detalla cada evento "
        f"intrusivo por separado (fecha, operador, incidencias activas y desenlace), no solo el agregado."
    )
    story.append(Paragraph(cautela, st_body))
    story.append(Spacer(1, 4))

    for p in top:
        nombre_h = f"Puesto {p['puesto']} — {p['protocolos_intrusivos']} intrusivo(s)"
        story.append(Paragraph(nombre_h, st_sub))

        suc_txt = _plural(p["sucursales"], "sucursal", "sucursales")
        mov_txt = _plural(p["movimientos_bitacora"], "movimiento de bitácora", "movimientos de bitácora")
        prev_txt = _plural(p["protocolos_preventivos"], "protocolo preventivo", "protocolos preventivos")
        intr_txt = _plural(p["protocolos_intrusivos"], "protocolo intrusivo", "protocolos intrusivos")
        texto = (
            f"Se activaron <b>{p['protocolos_preventivos']} {prev_txt}</b> y "
            f"<b>{p['protocolos_intrusivos']} {intr_txt}</b>. El puesto agrupa "
            f"<b>{p['sucursales']} {suc_txt}</b> y acumula <b>{p['movimientos_bitacora']} {mov_txt}</b> en el "
            f"período, {_nivel(p['movimientos_bitacora'], prom_mov_global)} ({etiqueta_periodo}). "
            f"Índice de carga combinado: <b>{p['indice_carga']:.0f}/100</b>."
        )
        story.append(Paragraph(texto, st_body_r))

        detalle_suc = sorted(
            p.get("detalle_sucursales") or [],
            key=lambda s: (
                -(s["protocolos_preventivos"] + s["protocolos_intrusivos"] + s["movimientos_bitacora"]),
                s["sucursal"],
            ),
        )
        if detalle_suc:
            filas_suc = [[
                Paragraph("Sucursal", st_th), Paragraph("Protocolos preventivos", st_th),
                Paragraph("Protocolos intrusivos", st_th), Paragraph("Movimientos de bitácora", st_th),
            ]]
            for s in detalle_suc:
                filas_suc.append([
                    Paragraph(s["sucursal"], st_td),
                    Paragraph(str(s["protocolos_preventivos"]), st_td),
                    Paragraph(str(s["protocolos_intrusivos"]), st_td),
                    Paragraph(str(s["movimientos_bitacora"]), st_td),
                ])
            tabla_suc = Table(filas_suc, colWidths=[0.40 * fw, 0.20 * fw, 0.20 * fw, 0.20 * fw], repeatRows=1)
            tabla_suc.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), C_DARK),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.5, C_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ] + [("BACKGROUND", (0, i), (-1, i), HexColor("#f8f9fb")) for i in range(2, len(filas_suc), 2)]))
            story.append(Spacer(1, 4))
            story.append(tabla_suc)
            story.append(Spacer(1, 4))

        if p.get("operadores_repetidos"):
            ops_txt = _lista_texto([f"{o['operador']} ({o['veces']} eventos)" for o in p["operadores_repetidos"][:4]])
            story.append(Paragraph(
                f"<b>Operador repetido en varios intrusivos:</b> {ops_txt}.",
                st_body_r,
            ))

        if p.get("desenlace_no_exitoso"):
            desenlace_txt = _plural(p["desenlace_no_exitoso"], "intrusivo NO fue contenido", "intrusivos NO fueron contenidos")
            story.append(Paragraph(
                f"<b>Desenlace:</b> {p['desenlace_no_exitoso']} {desenlace_txt} a tiempo según el registro "
                f"(protocolo_exitoso = NO).",
                st_body_r,
            ))

        # ── Detalle por evento intrusivo, como texto ordenado (no tabla, sin
        # colores de semáforo — un bloque corto por evento). Solo se listan
        # los datos de cada evento (incidencias activas, tipo, desenlace);
        # la interpretación de causa queda para quien lea el informe.
        eventos_detalle = p.get("intrusivos_detalle") or []

        if eventos_detalle:
            story.append(Paragraph("Detalle de cada intrusivo (caso a caso):", st_body_r))

        for idx, ev in enumerate(eventos_detalle, start=1):
            activas = ev.get("incidencias_activas") or []
            fecha_txt = ev["fecha"].strftime("%d/%m/%Y %H:%M") if ev.get("fecha") else "-"
            hora = ev.get("hora_aprox")
            hora_txt = ""
            if hora is not None:
                fuente_lbl = "según observación" if ev.get("hora_fuente") == "texto" else "hora de registro"
                hora_txt = f" (~{hora:02d}h, {fuente_lbl})"
            sucursal_txt = ev.get("sucursal") or "-"
            operador_txt = ev.get("operador") or "Sin operador registrado"

            exitoso = ev.get("exitoso", "")
            if exitoso == "NO":
                desenlace_txt = "NO contenido"
            elif exitoso == "SI":
                desenlace_txt = "Contenido"
            else:
                desenlace_txt = "sin dato"

            if activas:
                conteo = Counter(activas)
                resumen = ", ".join(f"{tipo} x{n}" for tipo, n in conteo.most_common())
                inc_txt = f"{len(activas)} ({resumen})"
            else:
                inc_txt = "sin incidencias activas"

            mov_txt = ev.get("movimientos_ventana_30min", "—")
            op_mov_txt = ev.get("movimientos_operador_30min", "—")

            bloque = (
                f"<b>{idx}. {fecha_txt}{hora_txt} — {sucursal_txt}</b><br/>"
                f"Operador: {operador_txt} · Desenlace: {desenlace_txt}<br/>"
                f"Incidencias activas en esa sucursal: {inc_txt}<br/>"
                f"Bitácora del puesto ±30m: {mov_txt} · Bitácora de este operador ±30m: {op_mov_txt}"
            )
            story.append(Paragraph(bloque, st_body_r))
            story.append(Spacer(1, 5))

        story.append(Spacer(1, 6))
        story.append(HRFlowable(width=fw, thickness=0.5, color=C_BORDER))
        story.append(Spacer(1, 10))

    if not top:
        story.append(Paragraph("Ningún puesto registró protocolos intrusivos en el período.", st_body))

    story.append(Spacer(1, 6))

    if sin_intrusivos:
        seccion("PUESTOS SIN INTRUSIONES")
        nombres_sin = _lista_texto([f"Puesto {p['puesto']}" for p in sorted(sin_intrusivos, key=lambda p: p["puesto"])[:12]])
        extra = f" (y {len(sin_intrusivos) - 12} más)" if len(sin_intrusivos) > 12 else ""
        texto_sin = (
            f"{nombres_sin}{extra} no registraron protocolos intrusivos en el período. En promedio, estos "
            f"puestos acumulan {prom_mov_sin:.1f} movimientos de bitácora y cubren {prom_suc_sin:.1f} "
            f"sucursales cada uno."
        )
        story.append(Paragraph(texto_sin, st_body))
        story.append(Spacer(1, 6))

    # ── Índice de carga combinado: ranking ponderado para decidir prioridad
    # de redistribución de sucursales entre puestos (protocolos/sucursal +
    # movimientos/sucursal + incidencias simultáneas promedio, normalizado
    # 0-100 relativo a este mismo informe).
    seccion("ÍNDICE DE CARGA COMBINADO")
    story.append(Paragraph(
        "Ranking ponderado 0-100 por puesto, pensado como insumo para decidir cómo redistribuir sucursales "
        "entre puestos: combina en partes iguales protocolos/sucursal, movimientos de bitácora/sucursal e "
        "incidencias activas simultáneas promedio durante los intrusivos. Es un ranking relativo entre los "
        "puestos de este informe, no una escala absoluta.",
        st_body,
    ))
    ranking = sorted(puestos, key=lambda p: -p["indice_carga"])[:10]
    filas_ranking = [[
        Paragraph("#", st_th), Paragraph("Puesto", st_th), Paragraph("Índice", st_th),
        Paragraph("Protocolos/sucursal", st_th), Paragraph("Movimientos/sucursal", st_th),
        Paragraph("Incid. simult. prom.", st_th),
    ]]
    for i, p in enumerate(ranking, start=1):
        filas_ranking.append([
            Paragraph(str(i), st_td),
            Paragraph(f"Puesto {p['puesto']}", st_td),
            Paragraph(f"{p['indice_carga']:.0f}/100", st_td_bad if i <= 3 else st_td),
            Paragraph(f"{p['protocolos_por_sucursal']:.2f}", st_td),
            Paragraph(f"{p['movimientos_por_sucursal']:.1f}", st_td),
            Paragraph(f"{p['incidencias_simultaneas_promedio']:.1f}", st_td),
        ])
    tabla_ranking = Table(
        filas_ranking, colWidths=[0.06 * fw, 0.16 * fw, 0.14 * fw, 0.22 * fw, 0.22 * fw, 0.20 * fw], repeatRows=1
    )
    tabla_ranking.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_DARK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, C_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ] + [("BACKGROUND", (0, i), (-1, i), HexColor("#f8f9fb")) for i in range(2, len(filas_ranking), 2)]))
    story.append(tabla_ranking)
    story.append(Paragraph(
        "Los 3 valores de índice más altos están destacados en rojo.",
        st_caption,
    ))
    story.append(Spacer(1, 6))

    # ── Distribución horaria: intrusivos vs. todos los
    # protocolos por franja horaria del día.
    franja = data.get("franja_horaria") or {}
    franja_todos = franja.get("todos") or {}
    franja_intr = franja.get("intrusivos") or {}
    orden_franjas = franja.get("orden") or []
    total_todos_franja = sum(franja_todos.values()) or 0
    total_intr_franja = sum(franja_intr.values()) or 0
    mayor_sobre_rep = None

    if total_todos_franja and total_intr_franja:
        seccion("DISTRIBUCIÓN HORARIA DE LOS INTRUSIVOS")
        story.append(Paragraph(
            "Distribución por franja horaria del día de los protocolos intrusivos, comparada con la de todos "
            "los protocolos (preventivos + intrusivos) en el mismo período. "
            "<i>La hora usada es la mencionada en la observación del operador cuando está disponible; si no, "
            "se usa la hora de registro del parte, que en algunos casos es la del día siguiente.</i>",
            st_body,
        ))
        filas_franja = [[
            Paragraph("Franja horaria", st_th), Paragraph("Intrusivos", st_th),
            Paragraph("% de los intrusivos", st_th), Paragraph("Todos los protocolos", st_th),
            Paragraph("% de todos los protocolos", st_th), Paragraph("Proporción", st_th),
        ]]
        mayor_ratio = 0.0
        for nombre in orden_franjas:
            n_todos = franja_todos.get(nombre, 0)
            n_intr = franja_intr.get(nombre, 0)
            pct_todos = 100 * n_todos / total_todos_franja
            pct_intr = 100 * n_intr / total_intr_franja
            ratio = (pct_intr / pct_todos) if pct_todos > 0 else (999 if pct_intr > 0 else 0)
            if pct_todos > 0 and ratio > mayor_ratio:
                mayor_ratio = ratio
                mayor_sobre_rep = nombre
            filas_franja.append([
                Paragraph(nombre, st_td),
                Paragraph(str(n_intr), st_td),
                Paragraph(f"{pct_intr:.0f}%", st_td),
                Paragraph(str(n_todos), st_td),
                Paragraph(f"{pct_todos:.0f}%", st_td),
                Paragraph(f"{ratio:.1f}x" if pct_todos > 0 else "—", st_td),
            ])
        tabla_franja = Table(
            filas_franja,
            colWidths=[0.20 * fw, 0.14 * fw, 0.17 * fw, 0.17 * fw, 0.17 * fw, 0.15 * fw],
            repeatRows=1,
        )
        tabla_franja.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_DARK),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ] + [("BACKGROUND", (0, i), (-1, i), HexColor("#f8f9fb")) for i in range(2, len(filas_franja), 2)]))
        story.append(tabla_franja)
        story.append(Paragraph(
            "Proporción = % de los intrusivos dividido por % de todos los protocolos en esa franja.",
            st_caption,
        ))
        story.append(Spacer(1, 6))

    # ── Demostración: tiempo de detección/reacción. NO hay datos reales
    # todavía — fecha_registro es la hora del PARTE, no la hora real de la
    # intrusión ni la de detección del operador. Esta sección es solo un
    # ejemplo ilustrativo de lo que se podrá calcular una vez se registren
    # esos dos campos; los números son inventados, no deben usarse para
    # ninguna decisión.
    seccion("DEMOSTRACIÓN — TIEMPO DE DETECCIÓN Y REACCIÓN")
    story.append(Paragraph(
        "<b>Sección de ejemplo — datos ilustrativos, no reales.</b> Los protocolos actuales solo registran la "
        "hora en que se escribió el parte, no la hora real en que ocurrió la intrusión ni la hora en que el "
        "operador la detectó. Cuando esos dos campos se empiecen a registrar, esta sección mostrará el tiempo "
        "de reacción por evento, por operador y por franja horaria. Formato que tendrá, con datos de ejemplo:",
        st_body,
    ))
    demo_lineas = [
        "• [EJEMPLO] Puesto 17 — 24/07/2025 — Operador Ejemplo A — intrusión real 00:12, detectada 00:19 → 7 min de reacción.",
        "• [EJEMPLO] Puesto 17 — 19/10/2025 — Operador Ejemplo B — intrusión real 07:48, detectada 07:48 → 0 min (detección inmediata).",
        "• [EJEMPLO] Puesto 14 — 12/05/2026 — Operador Ejemplo A — intrusión real 19:01, detectada 19:14 → 13 min de reacción.",
    ]
    story.append(Paragraph("<br/>".join(demo_lineas), st_caption))
    story.append(Paragraph(
        "Con esos datos reales se podrá además cruzar operador + hora + tiempo de reacción para detectar "
        "patrones (ej. \"los intrusivos con reacción lenta ocurren con tal operador en turno noche\").",
        st_body,
    ))
    story.append(Spacer(1, 6))

    seccion("RESUMEN NUMÉRICO")
    if con_intrusivos:
        peor = ordenado_desc[0]
        conclusion = (
            f"El puesto con más protocolos intrusivos es <b>Puesto {peor['puesto']}</b>, con "
            f"{peor['protocolos_intrusivos']} en el período. "
        )
        if con_intrusivos and sin_intrusivos:
            diferencia = prom_mov_con - prom_mov_sin
            conclusion += (
                f"Los puestos con intrusivos acumulan en promedio {diferencia:+.1f} movimientos de bitácora "
                f"respecto de los que no tuvieron ninguna ({prom_mov_con:.1f} vs. {prom_mov_sin:.1f})."
            )
        if ranking:
            lider = ranking[0]
            conclusion += (
                f" Según el índice de carga combinado, el puesto con el valor más alto es "
                f"<b>Puesto {lider['puesto']}</b> (índice {lider['indice_carga']:.0f}/100)."
            )
        if mayor_sobre_rep:
            conclusion += (
                f" La franja horaria <b>{mayor_sobre_rep}</b> tiene la mayor proporción relativa de intrusivos "
                f"frente a su participación en el total de protocolos (proporción {mayor_ratio:.1f}x)."
            )
        story.append(Paragraph(conclusion, st_body))

    doc.build(story)
    return buf.getvalue()
