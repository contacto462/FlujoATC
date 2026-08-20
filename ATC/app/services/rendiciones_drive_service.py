"""Subida a Google Drive de boletas e informes PDF de rendiciones.

Reusa el cliente Drive y los helpers genericos de drive_base_service.py
(mismo patron que incidencias_drive_report_service.py para cierre de ODT).
Carpeta raiz: GOOGLE_DRIVE_RENDICIONES_FOLDER_ID, separada de la carpeta de
soporte de incidencias porque son documentos financieros.
"""
from __future__ import annotations

from typing import Any

from ATC.app.core.config import settings
from ATC.app.services.drive_base_service import (
    DriveReportError,
    _build_clients,
    _clean_filename,
    _find_or_create_folder,
    _safe_text,
    _set_public_read,
    _upload_bytes,
)


def _rendiciones_root_id() -> str:
    if not settings.google_drive_enabled:
        raise DriveReportError("GOOGLE_DRIVE_ENABLED=false")
    root_id = _safe_text(settings.google_drive_rendiciones_folder_id)
    if not root_id:
        raise DriveReportError("Falta GOOGLE_DRIVE_RENDICIONES_FOLDER_ID")
    return root_id


def _upload_single_file(*, tecnico: str, subfolder: str, filename: str, content: bytes, mime_type: str) -> dict[str, Any]:
    drive, _ = _build_clients()
    root_id = _rendiciones_root_id()

    safe_tecnico = _clean_filename(tecnico or "Sin tecnico", fallback="Sin_tecnico")
    tecnico_folder_id = _find_or_create_folder(drive, root_id, safe_tecnico)
    folder_id = _find_or_create_folder(drive, tecnico_folder_id, _clean_filename(subfolder, fallback="Rendicion"))

    uploaded = _upload_bytes(drive, folder_id, filename, content, mime_type)
    try:
        _set_public_read(drive, uploaded["id"])
    except Exception:
        pass
    uploaded["public_uri"] = f"/api/incidencias/drive-image/{uploaded['id']}"

    return {
        "folder_id": folder_id,
        "file_id": uploaded["id"],
        "public_uri": uploaded["public_uri"],
    }


def upload_rendicion_boleta_to_drive(*, tecnico: str, rendicion_ref: str, content: bytes, filename: str, mime_type: str) -> dict[str, Any]:
    """Sube la boleta de una rendicion. rendicion_ref es un identificador
    temporal (odt+timestamp+uuid) porque la boleta se sube antes de que
    exista el registro Rendicion en la BBDD."""
    return _upload_single_file(
        tecnico=tecnico,
        subfolder=f"Boleta_{rendicion_ref}",
        filename=filename,
        content=content,
        mime_type=mime_type,
    )


def upload_rendicion_informe_to_drive(*, tecnico: str, rendicion_id: int, pdf_bytes: bytes, filename: str) -> dict[str, Any]:
    return _upload_single_file(
        tecnico=tecnico,
        subfolder=f"Rendicion_{rendicion_id}",
        filename=filename,
        content=pdf_bytes,
        mime_type="application/pdf",
    )
