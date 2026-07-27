"""Agrega el campo editable "Observaciones de coordinacion" (AcroForm) a los
informes de cierre de ODT ya generados, para sucursales IMQ.

Uso:
    python -m ATC.scripts.migrar_campo_editable_imq --dry-run
    python -m ATC.scripts.migrar_campo_editable_imq --ejecutar

En dry-run no escribe nada: solo reporta cuantos registros hay, si logra
resolver el archivo (local o en Drive) y si ya tiene el campo (para no
reprocesar). En modo --ejecutar, ademas hace backup del PDF local original
(si existe) antes de sobreescribirlo, y actualiza el mismo file_id en Drive
via files().update (no crea copias nuevas, no rompe links existentes).
"""

from __future__ import annotations

import argparse
import io
import re
import sys

from sqlalchemy import text

from ATC.app.core.db import SessionLocal
from ATC.app.core.config import settings
from ATC.app.services.drive_base_service import _build_clients, _upload_bytes  # noqa
from ATC.app.services.incidencias_drive_report_service import download_support_drive_file_bytes
from ATC.app.services.incidencias_service import _UPLOADS_ROOT
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor, white
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, HRFlowable, Flowable
from reportlab.lib.styles import ParagraphStyle

C_BORDER = HexColor("#e5e7eb")
C_TEXT = HexColor("#111827")
C_SOFT = HexColor("#4b5563")
C_ORDK = HexColor("#c2410c")

DRIVE_ID_RE = re.compile(r"/d/([\w-]{10,})|[?&]id=([\w-]{10,})")


class _CampoTextoEditable(Flowable):
    def __init__(self, width, height, name):
        Flowable.__init__(self)
        self.width = width
        self.height = height
        self.name = name

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        self.canv.acroForm.extras["NeedAppearances"] = True
        self.canv.acroForm.textfield(
            name=self.name,
            tooltip="Observaciones de coordinación",
            x=0, y=0, width=self.width, height=self.height,
            borderStyle="inset", borderWidth=1,
            borderColor=C_BORDER, fillColor=white,
            textColor=C_TEXT, fontSize=9,
            fieldFlags="multiline",
            value="",
        )


def _build_overlay_page(field_name: str) -> bytes:
    W, H = A4
    pad = 1.4 * cm
    fw = W - 2 * pad
    buf = io.BytesIO()
    frame = Frame(pad, pad, fw, H - 2 * pad, leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0)
    doc = BaseDocTemplate(buf, pagesize=A4, pageTemplates=[PageTemplate(id="main", frames=[frame])],
                           leftMargin=0, rightMargin=0, topMargin=0, bottomMargin=0)
    st_sec = ParagraphStyle("sec", fontName="Helvetica-Bold", fontSize=11, textColor=C_ORDK, leading=14, spaceAfter=6)
    st_body = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, textColor=C_SOFT, leading=14, spaceAfter=8)
    story = [
        Paragraph("OBSERVACIONES DE COORDINACIÓN", st_sec),
        HRFlowable(width=fw, thickness=0.75, color=C_BORDER, spaceAfter=8),
        Paragraph(
            "Espacio editable agregado retroactivamente. Se puede escribir directamente sobre este PDF (Adobe "
            "Reader, Vista Previa de Mac u otro lector compatible con formularios).",
            st_body,
        ),
        Spacer(1, 6),
        _CampoTextoEditable(fw, 10 * cm, field_name),
    ]
    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


def agregar_campo_editable(pdf_bytes: bytes, field_name: str) -> bytes:
    overlay_bytes = _build_overlay_page(field_name)
    reader_original = PdfReader(io.BytesIO(pdf_bytes), strict=False)
    reader_overlay = PdfReader(io.BytesIO(overlay_bytes))

    writer = PdfWriter()
    writer.append(reader_original)
    writer.append(reader_overlay)
    writer.set_need_appearances_writer(True)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _ya_tiene_campo(pdf_bytes: bytes) -> bool:
    try:
        r = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        campos = r.get_form_text_fields() or {}
        return any(k.startswith("observaciones_coordinacion_") for k in campos)
    except Exception:
        return False


def _drive_file_id_from_url(url: str) -> str:
    m = DRIVE_ID_RE.search(url)
    return (m.group(1) or m.group(2)) if m else ""


def _buscar_drive_file_id_por_odt(drive, odt: str) -> str:
    """Para informes 'formato nuevo' (pdf_url local): el PDF tambien se subio
    a Drive con nombre 'ODT_{odt}_...'. Lo ubicamos por nombre."""
    safe_odt = odt.replace("'", "")
    res = drive.files().list(
        q=f"name contains 'ODT_{safe_odt}_' and mimeType = 'application/pdf' and trashed=false",
        pageSize=5,
        fields="files(id,name)",
    ).execute()
    files = res.get("files", [])
    return files[0]["id"] if files else ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ejecutar", action="store_true", help="Aplica los cambios. Sin este flag es dry-run.")
    args = parser.parse_args()
    ejecutar = args.ejecutar

    db = SessionLocal()
    filas = db.execute(
        text(
            "SELECT odt, cliente, pdf_url FROM incidencias "
            "WHERE COALESCE(pdf_url, '') != '' AND cliente LIKE 'IMQ%' ORDER BY odt"
        )
    ).all()
    print(f"Total registros IMQ con pdf_url: {len(filas)}")

    drive, _ = _build_clients()

    backup_dir = _UPLOADS_ROOT / "odt" / "informes_backup_pre_editable"
    if ejecutar:
        backup_dir.mkdir(parents=True, exist_ok=True)

    stats = {"ok": 0, "ya_tenia": 0, "sin_archivo": 0, "error": 0}

    for odt, cliente, pdf_url in filas:
        odt = str(odt or "").strip()
        pdf_url = str(pdf_url or "").strip()
        field_name = f"observaciones_coordinacion_{odt.replace('/', '-').replace(' ', '_')}"

        try:
            if pdf_url.startswith("/uploads/odt/informes/"):
                local_path = _UPLOADS_ROOT / "odt" / "informes" / pdf_url.split("/")[-1]
                if not local_path.exists():
                    print(f"[SIN ARCHIVO LOCAL] {odt} — {pdf_url}")
                    stats["sin_archivo"] += 1
                    continue
                original_bytes = local_path.read_bytes()
                drive_file_id = _buscar_drive_file_id_por_odt(drive, odt)
                origen = "local"
            else:
                drive_file_id = _drive_file_id_from_url(pdf_url)
                if not drive_file_id:
                    print(f"[SIN FILE_ID DRIVE] {odt} — {pdf_url}")
                    stats["sin_archivo"] += 1
                    continue
                original_bytes, _mime, _name = download_support_drive_file_bytes(file_id=drive_file_id)
                local_path = None
                origen = "drive"

            if _ya_tiene_campo(original_bytes):
                print(f"[YA TIENE CAMPO] {odt} ({origen})")
                stats["ya_tenia"] += 1
                continue

            resultado = agregar_campo_editable(original_bytes, field_name)

            if not ejecutar:
                print(f"[DRY-RUN OK] {odt} ({origen}) drive_file_id={drive_file_id or '-'} "
                      f"bytes {len(original_bytes)} -> {len(resultado)}")
                stats["ok"] += 1
                continue

            # --- ejecucion real ---
            backup_name = local_path.name if local_path is not None else f"drive_{odt}_{drive_file_id}.pdf"
            backup_path = backup_dir / backup_name
            if not backup_path.exists():
                backup_path.write_bytes(original_bytes)

            if local_path is not None:
                local_path.write_bytes(resultado)

            if drive_file_id:
                from googleapiclient.http import MediaIoBaseUpload

                media = MediaIoBaseUpload(io.BytesIO(resultado), mimetype="application/pdf", resumable=True)
                request = drive.files().update(fileId=drive_file_id, media_body=media, fields="id,name")
                resp = None
                while resp is None:
                    _status, resp = request.next_chunk()
                print(f"[APLICADO] {odt} ({origen}) drive_file_id={drive_file_id}")
            else:
                print(f"[APLICADO SOLO LOCAL, SIN DRIVE] {odt} ({origen})")

            stats["ok"] += 1

        except Exception as exc:
            print(f"[ERROR] {odt}: {exc!r}")
            stats["error"] += 1

    print("\n--- resumen ---")
    for k, v in stats.items():
        print(f"{k}: {v}")
    db.close()


if __name__ == "__main__":
    main()
