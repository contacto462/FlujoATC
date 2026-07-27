from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ATC.app.core.config import settings
from ATC.app.services.drive_base_service import (
    DriveReportError,
    _build_clients,
    _build_template_values,
    _clean_filename,
    _copy_template,
    _ensure_enabled,
    _export_doc_pdf,
    _find_or_create_folder,
    _guess_mime_and_ext,
    _insert_images_on_placeholders,
    _iter_text_runs,
    _normalize_text_for_token,
    _read_image_source,
    _replace_text,
    _safe_text,
    _set_public_read,
    _support_folder_name_from_odt,
    _upload_bytes,
)


def _replace_template_tokens(docs, doc_id: str, values: dict[str, str]) -> None:
    document = docs.documents().get(documentId=doc_id).execute()
    found_tokens: set[str] = set()
    pattern = re.compile(r"\{\{[^{}]+\}\}")
    for text_value, _ in _iter_text_runs(document.get("body", {}).get("content", [])):
        for token in pattern.findall(text_value):
            found_tokens.add(token)

    replacements: dict[str, str] = {}
    for token in found_tokens:
        normalized = _normalize_text_for_token(token)
        if normalized in values:
            replacements[token] = values[normalized]

    if replacements:
        _replace_text(docs, doc_id, replacements)


def create_drive_report_for_odt(
    *,
    odt: str,
    sucursal: str,
    cliente: str,
    problema: str,
    direccion: str,
    tecnico: str,
    fecha_cierre: str,
    observacion_cierre: str,
    image_sources: list[str],
) -> dict[str, Any]:
    _ensure_enabled()
    drive, docs = _build_clients()

    root_folder_id = _safe_text(settings.google_drive_root_folder_id)
    template_id = _safe_text(settings.google_doc_template_id)

    safe_sucursal = _clean_filename(sucursal, fallback="Sucursal Sin Nombre")
    folder_id = _find_or_create_folder(drive, root_folder_id, safe_sucursal)

    uploaded_images: list[dict[str, str]] = []
    for index, source in enumerate(image_sources, start=1):
        try:
            content, mime_type, ext = _read_image_source(source)
        except Exception:
            continue

        image_name = _clean_filename(f"ODT_{odt}_IMG_{index:02d}{ext}", fallback=f"ODT_{odt}_IMG_{index:02d}{ext}")
        uploaded = _upload_bytes(drive, folder_id, image_name, content, mime_type)
        _set_public_read(drive, uploaded["id"])
        uploaded["public_uri"] = f"https://drive.google.com/uc?export=view&id={uploaded['id']}"
        uploaded_images.append(uploaded)

    now_stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M")
    doc_title = _clean_filename(f"ODT {odt} - {safe_sucursal} - {now_stamp}", fallback=f"ODT_{odt}_{now_stamp}")
    doc_id = _copy_template(drive, template_id, folder_id, doc_title)

    replacements = _build_template_values(
        {
            "odt": odt,
            "tipo_trabajo": problema,
            "rut_cliente": "-",
            "cliente": cliente,
            "fecha_cierre": fecha_cierre,
            "sucursal": sucursal,
            "direccion": direccion,
            "descripcion": problema,
            "trabajo_realizado": observacion_cierre,
            "tecnico": tecnico,
        }
    )
    _replace_template_tokens(docs, doc_id, replacements)

    img1 = uploaded_images[0]["public_uri"] if len(uploaded_images) >= 1 else ""
    img2 = uploaded_images[1]["public_uri"] if len(uploaded_images) >= 2 else ""
    _insert_images_on_placeholders(
        docs,
        doc_id,
        {
            "{{Imagen del trabajo 1}}": img1,
            "{{Imagen del trabajo 2}}": img2,
        },
    )

    pdf_name = _clean_filename(f"ODT_{odt}_{safe_sucursal}_{now_stamp}.pdf", fallback=f"ODT_{odt}_{now_stamp}.pdf")
    pdf_bytes = _export_doc_pdf(drive, doc_id)
    uploaded_pdf = _upload_bytes(drive, folder_id, pdf_name, pdf_bytes, "application/pdf")

    try:
        drive.files().delete(fileId=doc_id).execute()
    except Exception:
        pass

    return {
        "folder_id": folder_id,
        "folder_name": safe_sucursal,
        "pdf_file_id": uploaded_pdf["id"],
        "pdf_name": uploaded_pdf["name"],
        "pdf_web_view_link": uploaded_pdf.get("webViewLink", ""),
        "uploaded_images_count": len(uploaded_images),
    }


def upload_support_images_for_odt(
    *,
    odt: str,
    image_payloads: list[dict[str, object]],
    root_folder_id: str | None = None,
    start_index: int = 1,
) -> dict[str, Any]:
    if not settings.google_drive_enabled:
        raise DriveReportError("GOOGLE_DRIVE_ENABLED=false")

    root_id = (
        _safe_text(root_folder_id)
        or _safe_text(settings.google_drive_support_folder_id)
        or _safe_text(settings.google_drive_root_folder_id)
    )
    if not root_id:
        raise DriveReportError("Falta GOOGLE_DRIVE_SUPPORT_FOLDER_ID")

    drive, _ = _build_clients()

    folder_name = _support_folder_name_from_odt(odt)
    folder_id = _find_or_create_folder(drive, root_id, folder_name)

    uploaded_images: list[dict[str, str]] = []
    safe_start = max(1, int(start_index or 1))
    for offset, payload in enumerate(image_payloads or [], start=0):
        slot_index = safe_start + offset
        content = payload.get("bytes")
        if not isinstance(content, (bytes, bytearray)) or not content:
            continue
        filename = _safe_text(payload.get("filename")) or f"imagen_{slot_index}"
        mime_type = _safe_text(payload.get("mime_type")) or "image/jpeg"
        _, ext = _guess_mime_and_ext(filename, default_mime=mime_type)
        image_name = _clean_filename(
            f"Imagen {slot_index}{ext}",
            fallback=f"Imagen_{slot_index}{ext}",
        )
        uploaded = _upload_bytes(drive, folder_id, image_name, bytes(content), mime_type)
        _set_public_read(drive, uploaded["id"])
        uploaded["public_uri"] = f"https://drive.google.com/uc?export=view&id={uploaded['id']}"
        uploaded_images.append(uploaded)

    return {
        "folder_id": folder_id,
        "folder_name": folder_name,
        "uploaded_images_count": len(uploaded_images),
        "imagenes": [img.get("public_uri", "") for img in uploaded_images if img.get("public_uri")],
    }
