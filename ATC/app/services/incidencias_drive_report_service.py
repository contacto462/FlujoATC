from __future__ import annotations

import io
import logging
import re
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from googleapiclient.http import MediaIoBaseDownload
from jinja2 import Environment, FileSystemLoader

LOGGER = logging.getLogger(__name__)

from ATC.app.core.incidencias_config import settings
from ATC.app.services.drive_base_service import (
    DriveReportError,
    SCOPES,
    _ATC_ROOT,
    _FOLDER_CACHE,
    _build_clients,
    _build_template_values,
    _clean_filename,
    _copy_template,
    _decode_data_uri,
    _ensure_enabled,
    _export_doc_pdf,
    _find_or_create_folder,
    _find_placeholder_range,
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


def _find_folder(drive, parent_id: str, folder_name: str) -> Optional[str]:
    cache_key = (parent_id, folder_name)
    cached = _FOLDER_CACHE.get(cache_key)
    if cached:
        return cached

    safe_name = folder_name.replace("'", "\\'")
    query = (
        "mimeType='application/vnd.google-apps.folder' "
        f"and name='{safe_name}' and '{parent_id}' in parents and trashed=false"
    )
    response = drive.files().list(
        q=query,
        fields="files(id,name)",
        pageSize=1,
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
    ).execute()
    files = response.get("files", [])
    if not files:
        return None
    folder_id = files[0]["id"]
    _FOLDER_CACHE[cache_key] = folder_id
    return folder_id


def find_ods_drive_folder_id(codigo: str, rut: str, razon_social: str) -> Optional[str]:
    """Busca la carpeta de la ODS en Drive sin crearla.

    Devuelve el folder_id o None si no existe, si Drive esta deshabilitado o si hay error.
    """
    try:
        if not settings.google_drive_enabled:
            return None
        ods_root_id = _safe_text(settings.google_drive_ods_root_folder_id)
        if not ods_root_id:
            return None
        drive, _ = _build_clients()
        cliente_folder_name = _clean_filename(
            f"{_safe_text(rut)} - {_safe_text(razon_social)}",
            fallback=_safe_text(rut) or "cliente",
        )
        cliente_folder_id = _find_folder(drive, ods_root_id, cliente_folder_name)
        if not cliente_folder_id:
            return None
        return _find_folder(drive, cliente_folder_id, _clean_filename(_safe_text(codigo), _safe_text(codigo)))
    except Exception:
        return None


def build_ods_folder_url(folder_id: str) -> str:
    fid = _safe_text(folder_id)
    return f"https://drive.google.com/drive/folders/{fid}" if fid else ""


def find_ods_drive_file_id(
    *,
    codigo: str,
    rut: str,
    razon_social: str,
    servicio: str | None,
    nombre_archivo: str,
) -> Optional[str]:
    """Busca en Drive el archivo de una ODS cuando ya no existe en disco local.

    Recorre la misma estructura de carpetas que usa upload_ods_files_to_drive
    (raiz ODS / {RUT} - {RazonSocial} / {Codigo} / {Servicio}) y devuelve el
    file_id que coincide con nombre_archivo. Si no se conoce el servicio, o no
    hay coincidencia en esa subcarpeta, busca en todas las subcarpetas de la ODS.
    """
    nombre = _safe_text(nombre_archivo)
    if not nombre:
        return None
    try:
        if not settings.google_drive_enabled:
            return None
        ods_root_id = _safe_text(settings.google_drive_ods_root_folder_id)
        if not ods_root_id:
            return None
        drive, _ = _build_clients()
        cliente_folder_name = _clean_filename(
            f"{_safe_text(rut)} - {_safe_text(razon_social)}",
            fallback=_safe_text(rut) or "cliente",
        )
        cliente_folder_id = _find_folder(drive, ods_root_id, cliente_folder_name)
        if not cliente_folder_id:
            return None
        ods_folder_id = _find_folder(drive, cliente_folder_id, _clean_filename(_safe_text(codigo), _safe_text(codigo)))
        if not ods_folder_id:
            return None

        def _buscar_en(folder_id: str) -> Optional[str]:
            safe_name = nombre.replace("'", "\\'")
            query = f"'{folder_id}' in parents and trashed=false and name='{safe_name}'"
            rows = drive.files().list(
                q=query,
                fields="files(id,name)",
                pageSize=5,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            ).execute().get("files", [])
            return _safe_text(rows[0]["id"]) if rows else None

        servicio_limpio = _safe_text(servicio)
        if servicio_limpio:
            servicio_folder_id = _find_folder(drive, ods_folder_id, _clean_filename(servicio_limpio, fallback="General"))
            if servicio_folder_id:
                found = _buscar_en(servicio_folder_id)
                if found:
                    return found

        # Fallback: recorrer todas las subcarpetas de servicio dentro de la ODS.
        subcarpetas = drive.files().list(
            q=f"'{ods_folder_id}' in parents and trashed=false and mimeType='application/vnd.google-apps.folder'",
            fields="files(id,name)",
            pageSize=50,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute().get("files", [])
        for carpeta in subcarpetas:
            fid = _safe_text(carpeta.get("id"))
            if not fid:
                continue
            found = _buscar_en(fid)
            if found:
                return found
        return None
    except Exception:
        return None


def _resolve_ods_upload_path(value: object) -> Path | None:
    raw = _safe_text(value)
    if not raw:
        return None

    path = Path(raw)
    candidates = [path] if path.is_absolute() else [
        Path.cwd() / path,
        _ATC_ROOT / path,
        _ATC_ROOT.parent / path,
    ]

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def upload_ods_files_to_drive(
    codigo: str,
    rut: str,
    razon_social: str,
    files: list[dict],
) -> list[dict]:
    """
    Sube los archivos adjuntos de una ODS a Google Drive.

    Estructura:
        Carpeta raÃ­z ODS /
          {RUT} - {RazÃ³n Social} /
            {Codigo ODS} /
              {Tipo Servicio} /
                archivo.pdf

    Args:
        codigo:       CÃ³digo ODS (ej. "V0001")
        rut:          RUT del cliente (ej. "12.345.678-9")
        razon_social: RazÃ³n social del cliente
        files:        Lista de dicts con claves:
                        "path"    â†’ ruta local relativa al archivo
                        "nombre"  â†’ nombre original del archivo
                        "mime"    â†’ tipo MIME
                        "servicio"â†’ tipo de servicio (Televigilancia, InstalaciÃ³n, etc.)

    Returns:
        Lista de dicts con id, name, webViewLink de cada archivo subido.
    """
    if not settings.google_drive_enabled:
        raise DriveReportError("GOOGLE_DRIVE_ENABLED=false â€” subida ODS omitida")

    ods_root_id = _safe_text(settings.google_drive_ods_root_folder_id)
    if not ods_root_id:
        raise DriveReportError("Falta GOOGLE_DRIVE_ODS_ROOT_FOLDER_ID en .env")

    drive, _ = _build_clients()

    # Nivel 1: {RUT} - {RazÃ³n Social}
    cliente_folder_name = _clean_filename(
        f"{_safe_text(rut)} - {_safe_text(razon_social)}",
        fallback=_safe_text(rut) or "cliente",
    )
    cliente_folder_id = _find_or_create_folder(drive, ods_root_id, cliente_folder_name)

    # Nivel 2: cÃ³digo ODS (ej. "V0001")
    ods_folder_id = _find_or_create_folder(drive, cliente_folder_id, _clean_filename(codigo, codigo))

    results: list[dict] = []
    for f in files:
        local = _resolve_ods_upload_path(f.get("path"))
        if local is None:
            raise DriveReportError(f"No se encontro archivo local ODS: {_safe_text(f.get('path'))}")

        content = local.read_bytes()
        mime = str(f.get("mime") or "") or _guess_mime_and_ext(local.name)[0]
        name = _clean_filename(str(f.get("nombre") or local.name), local.name)

        # Nivel 3: subcarpeta por tipo de servicio
        servicio = _clean_filename(str(f.get("servicio") or "General"), fallback="General")
        servicio_folder_id = _find_or_create_folder(drive, ods_folder_id, servicio)

        info = _upload_bytes(drive, servicio_folder_id, name, content, mime)
        results.append(info)

    return results


def _document_visible_text_len(document: dict[str, Any]) -> int:
    content = document.get("body", {}).get("content", []) or []
    combined = "".join(text for text, _ in _iter_text_runs(content))
    compact = re.sub(r"\s+", "", combined)
    return len(compact)


def _extract_template_analysis(document: dict[str, Any]) -> dict[str, Any]:
    token_map: dict[str, str] = {}
    image_ranges: dict[str, tuple[int, int, str]] = {}
    pattern = re.compile(r"\{\{[^{}]+\}\}")
    for text_value, start_index in _iter_text_runs(document.get("body", {}).get("content", [])):
        for match in pattern.finditer(text_value):
            token = match.group(0)
            normalized = _normalize_text_for_token(token)
            token_map.setdefault(normalized, token)
            if normalized in {
                _normalize_text_for_token("{{Imagen del trabajo 1}}"),
                _normalize_text_for_token("{{Imagen del trabajo 2}}"),
            }:
                image_ranges[normalized] = (
                    start_index + match.start(),
                    start_index + match.end(),
                    token,
                )
    return {"token_map": token_map, "image_ranges": image_ranges}


@lru_cache(maxsize=4)
def _get_template_analysis(template_id: str) -> dict[str, Any]:
    _, docs = _build_clients()
    document = docs.documents().get(documentId=template_id).execute()
    return _extract_template_analysis(document)


def _apply_template_updates(
    docs,
    doc_id: str,
    template_analysis: dict[str, Any],
    values: dict[str, str],
    image_token_to_uri: dict[str, str],
) -> None:
    requests_payload: list[dict[str, Any]] = []

    image_ranges = template_analysis.get("image_ranges", {})
    pending_images: list[tuple[int, int, str, str]] = []
    for token, uri in image_token_to_uri.items():
        normalized = _normalize_text_for_token(token)
        found = image_ranges.get(normalized)
        if not found:
            continue
        pending_images.append((found[0], found[1], found[2], uri))

    pending_images.sort(key=lambda item: item[0], reverse=True)
    for start_idx, end_idx, _token, image_uri in pending_images:
        requests_payload.append(
            {
                "deleteContentRange": {
                    "range": {"startIndex": start_idx, "endIndex": end_idx}
                }
            }
        )
        if image_uri:
            requests_payload.append(
                {
                    "insertInlineImage": {
                        "location": {"index": start_idx},
                        "uri": image_uri,
                        "objectSize": {
                            "height": {"magnitude": 180, "unit": "PT"},
                            "width": {"magnitude": 240, "unit": "PT"},
                        },
                    }
                }
            )
        else:
            requests_payload.append(
                {
                    "insertText": {
                        "location": {"index": start_idx},
                        "text": "Sin imagen",
                    }
                }
            )

    token_map = template_analysis.get("token_map", {})
    for normalized_key, value in values.items():
        actual_token = token_map.get(normalized_key)
        if not actual_token:
            continue
        requests_payload.append(
            {
                "replaceAllText": {
                    "containsText": {"text": actual_token, "matchCase": True},
                    "replaceText": _safe_text(value),
                }
            }
        )

    if requests_payload:
        docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests_payload}).execute()


def upload_support_images_for_odt(
    *,
    odt: str,
    image_payloads: list[dict[str, object]],
    root_folder_id: str | None = None,
    start_index: int = 1,
) -> dict[str, Any]:
    if not settings.google_drive_enabled:
        raise DriveReportError("GOOGLE_DRIVE_ENABLED=false")

    root_id = _safe_text(root_folder_id) or _safe_text(settings.google_drive_support_folder_id) or _safe_text(
        settings.google_drive_root_folder_id
    )
    if not root_id:
        raise DriveReportError("Falta GOOGLE_DRIVE_SUPPORT_FOLDER_ID")

    drive, _ = _build_clients()

    folder_name = _support_folder_name_from_odt(odt)
    folder_id = _find_or_create_folder(drive, root_id, folder_name)

    def _existing_image_indices(folder_id_value: str) -> set[int]:
        query = f"'{folder_id_value}' in parents and trashed=false and mimeType contains 'image/'"
        rows = drive.files().list(
            q=query,
            fields="files(id,name,mimeType,webViewLink,webContentLink)",
            pageSize=200,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute().get("files", [])
        out: set[int] = set()
        for row in rows:
            name = _safe_text(row.get("name"))
            m = re.search(r"(?i)^imagen\s+(\d+)(?:\.[a-z0-9]+)?$", name)
            if not m:
                continue
            try:
                out.add(int(m.group(1)))
            except Exception:
                continue
        return out

    def _next_free_index(used: set[int], preferred_start: int) -> int:
        idx = max(1, preferred_start)
        while idx in used:
            idx += 1
        return idx

    uploaded_images: list[dict[str, str]] = []
    safe_start = max(1, int(start_index or 1))
    used_indices = _existing_image_indices(folder_id)
    for payload in image_payloads or []:
        slot_index = _next_free_index(used_indices, safe_start)
        used_indices.add(slot_index)
        safe_start = slot_index + 1
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
        # Nota: algunas cuentas bloquean el permiso "anyone".
        # Para visualizacion en la app, devolvemos un proxy local que descarga via la cuenta del sistema.
        uploaded["public_uri"] = f"/api/incidencias/drive-image/{uploaded['id']}"
        uploaded_images.append(uploaded)

    return {
        "folder_id": folder_id,
        "folder_name": folder_name,
        "folder_url": f"https://drive.google.com/drive/folders/{folder_id}",
        "uploaded_images_count": len(uploaded_images),
        "imagenes": [img.get("public_uri", "") for img in uploaded_images if img.get("public_uri")],
    }


def _find_sucursal_folder_id(drive, root_id: str, prefix: str) -> tuple[str, str] | None:
    query = (
        "mimeType='application/vnd.google-apps.folder' "
        f"and '{root_id}' in parents and trashed=false"
    )
    page_token = None
    while True:
        response = drive.files().list(
            q=query,
            fields="nextPageToken, files(id,name)",
            pageSize=200,
            pageToken=page_token,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute()
        for f in response.get("files", []):
            if str(f.get("name") or "").startswith(prefix):
                return f["id"], f.get("name", "")
        page_token = response.get("nextPageToken")
        if not page_token:
            return None


def _find_or_create_sucursal_folder(drive, root_id: str, sucursal_id: int, nombre_sucursal: str) -> str:
    # Carpeta nombrada "{sucursal_id} - {nombre}": el prefijo del id permite
    # ubicar la carpeta aunque la sucursal se haya renombrado en Bitácora
    # despues (se renombra la carpeta existente en vez de crear una nueva y
    # dejar huerfanas las fotos ya subidas) — pedido explicito, ago 2026.
    prefix = f"{sucursal_id} - "
    desired_name = _clean_filename(f"{prefix}{nombre_sucursal}".strip(), fallback=str(sucursal_id))
    existing = _find_sucursal_folder_id(drive, root_id, prefix)
    if existing:
        folder_id, current_name = existing
        if current_name != desired_name:
            drive.files().update(fileId=folder_id, body={"name": desired_name}, fields="id,name").execute()
        return folder_id
    return _find_or_create_folder(drive, root_id, desired_name)


def upload_camaras_monitoreo_fotos(
    *,
    sucursal_id: int,
    nombre_sucursal: str,
    image_payloads: list[dict[str, object]],
) -> dict[str, Any]:
    """Sube fotos individuales de cámaras a Drive, en una subcarpeta por
    sucursal bajo GOOGLE_DRIVE_CAMARAS_MONITOREO_FOLDER_ID. No hay fallback a
    SQL si Drive falla (solo se propaga la excepción) — guardar bytes de
    imagen en SQL fue lo que infló el log de transacciones a 9GB antes."""
    if not settings.google_drive_enabled:
        raise DriveReportError("GOOGLE_DRIVE_ENABLED=false")

    root_id = _safe_text(settings.google_drive_camaras_monitoreo_folder_id)
    if not root_id:
        raise DriveReportError("Falta GOOGLE_DRIVE_CAMARAS_MONITOREO_FOLDER_ID")

    drive, _ = _build_clients()
    folder_id = _find_or_create_sucursal_folder(drive, root_id, sucursal_id, nombre_sucursal)

    urls: dict[str, str] = {}
    for payload in image_payloads or []:
        camara = _safe_text(payload.get("camara"))
        content = payload.get("bytes")
        if not camara or not isinstance(content, (bytes, bytearray)) or not content:
            continue
        filename = _safe_text(payload.get("filename")) or f"{camara}.jpg"
        mime_type = _safe_text(payload.get("mime_type")) or "image/jpeg"
        _, ext = _guess_mime_and_ext(filename, default_mime=mime_type)
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        image_name = _clean_filename(f"{camara} {stamp}{ext}", fallback=f"camara_{stamp}{ext}")
        uploaded = _upload_bytes(drive, folder_id, image_name, bytes(content), mime_type)
        _set_public_read(drive, uploaded["id"])
        urls[camara] = f"/api/incidencias/drive-image/{uploaded['id']}"

    return {"folder_id": folder_id, "urls": urls}


def list_support_images_for_odt(
    *,
    odt: str,
    root_folder_id: str | None = None,
) -> list[str]:
    if not settings.google_drive_enabled:
        return []

    root_id = _safe_text(root_folder_id) or _safe_text(settings.google_drive_support_folder_id) or _safe_text(
        settings.google_drive_root_folder_id
    )
    if not root_id:
        return []

    try:
        drive, _ = _build_clients()
        folder_name = _support_folder_name_from_odt(odt)
        folder_id = _find_or_create_folder(drive, root_id, folder_name)
        query = f"'{folder_id}' in parents and trashed=false and mimeType contains 'image/'"
        rows = drive.files().list(
            q=query,
            fields="files(id,name,mimeType,webViewLink,webContentLink)",
            pageSize=200,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute().get("files", [])
    except Exception:
        return []

    def _slot(row: dict[str, Any]) -> tuple[int, str]:
        name = _safe_text(row.get("name"))
        m = re.search(r"(?i)^imagen\s+(\d+)(?:\.[a-z0-9]+)?$", name)
        if m:
            try:
                return (0, f"{int(m.group(1)):04d}")
            except Exception:
                pass
        return (1, name.lower())

    rows_sorted = sorted(rows, key=_slot)
    out: list[str] = []
    for row in rows_sorted:
        fid = _safe_text(row.get("id"))
        if not fid:
            continue
        url = f"/api/incidencias/drive-image/{fid}"
        if url not in out:
            out.append(url)
    return out[:3]


def download_support_drive_file_bytes(*, file_id: str) -> tuple[bytes, str, str]:
    """
    Descarga un archivo binario desde Google Drive usando las credenciales del sistema.

    Retorna: (bytes, mime_type, filename)
    """
    if not settings.google_drive_enabled:
        raise DriveReportError("GOOGLE_DRIVE_ENABLED=false")

    fid = _safe_text(file_id)
    if not fid:
        raise DriveReportError("file_id invalido")

    # Bajo trafico alto contra la API de Drive aparecen fallos transitorios
    # (timeout, conexion cortada) que en un segundo intento funcionan bien
    # — sin reintento, esos fallos se le mostraban al usuario como si el
    # archivo no existiera (ver obtener_drive_image, mapea excepciones a 404).
    last_exc: Exception | None = None
    for intento in range(3):
        try:
            drive, _ = _build_clients()
            meta = (
                drive.files()
                .get(fileId=fid, fields="id,name,mimeType", supportsAllDrives=True)
                .execute()
            )
            mime_type = _safe_text(meta.get("mimeType")) or "application/octet-stream"
            filename = _safe_text(meta.get("name")) or f"{fid}.bin"

            # Los formatos nativos de Google (Docs/Sheets/Slides) no tienen
            # bytes descargables directos — hay que exportarlos (a PDF).
            if mime_type.startswith("application/vnd.google-apps."):
                content = _export_doc_pdf(drive, fid)
                if not filename.lower().endswith(".pdf"):
                    filename = f"{filename}.pdf"
                return content, "application/pdf", filename

            request = drive.files().get_media(fileId=fid, supportsAllDrives=True)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return fh.getvalue(), mime_type, filename
        except DriveReportError:
            raise
        except Exception as exc:
            last_exc = exc
            if intento < 2:
                time.sleep(1.5 * (intento + 1))
                continue

    raise DriveReportError(f"No se pudo descargar archivo Drive {fid}: {last_exc}") from last_exc


@lru_cache(maxsize=1)
def _protocolos_template_env() -> Environment:
    template_dir = _ATC_ROOT / "app" / "templates" / "reportes"
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _render_protocol_template(template_name: str, context: dict[str, Any]) -> str:
    env = _protocolos_template_env()
    template = env.get_template(template_name)
    return str(template.render(**(context or {}))).strip()


def _resolve_logo_atc_path() -> Path | None:
    candidates = [
        Path.cwd() / "ATC" / "static" / "img" / "logo-atc2.png",
        Path.cwd().parent / "ATC" / "static" / "img" / "logo-atc2.png",
        Path.cwd() / "app" / "static" / "img" / "logo-atc2.png",
        Path.cwd() / "ATC" / "static" / "img" / "logo-atc2.jpg",
        Path.cwd().parent / "ATC" / "static" / "img" / "logo-atc2.jpg",
        Path.cwd() / "app" / "static" / "img" / "logo-atc2.jpg",
        Path.cwd() / "ATC" / "static" / "img" / "logo-atc2.jpeg",
        Path.cwd().parent / "ATC" / "static" / "img" / "logo-atc2.jpeg",
        Path.cwd() / "app" / "static" / "img" / "logo-atc2.jpeg",
        Path.cwd() / "ATC" / "static" / "img" / "logo-atc2.webp",
        Path.cwd().parent / "ATC" / "static" / "img" / "logo-atc2.webp",
        Path.cwd() / "app" / "static" / "img" / "logo-atc2.webp",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _get_or_upload_logo_uri(drive, folder_id: str) -> str:
    try:
        for fname in ("logo-atc2.png", "logo-atc2.jpg", "logo-atc2.jpeg", "logo-atc2.webp"):
            rows = drive.files().list(
                q=f"'{folder_id}' in parents and trashed=false and name='{fname}'",
                fields="files(id,name)",
                pageSize=1,
            ).execute().get("files", [])
            if rows:
                fid = _safe_text(rows[0].get("id"))
                if fid:
                    _set_public_read(drive, fid)
                    return f"https://drive.google.com/uc?export=view&id={fid}"
    except Exception:
        pass

    logo_path = _resolve_logo_atc_path()
    if not logo_path:
        return ""
    try:
        payload = logo_path.read_bytes()
        if logo_path.suffix.lower() == ".png":
            mime = "image/png"
        elif logo_path.suffix.lower() == ".webp":
            mime = "image/webp"
        else:
            mime = "image/jpeg"
        logo_name = "logo-atc2.jpeg" if mime == "image/jpeg" else "logo-atc2.png"
        uploaded = _upload_bytes(drive, folder_id, logo_name, payload, mime)
        _set_public_read(drive, uploaded["id"])
        return f"https://drive.google.com/uc?export=view&id={uploaded['id']}"
    except Exception:
        return ""


def _create_blank_doc(drive, folder_id: str, title: str) -> str:
    created = drive.files().create(
        body={
            "name": title,
            "mimeType": "application/vnd.google-apps.document",
            "parents": [folder_id],
        },
        fields="id",
    ).execute()
    return _safe_text(created.get("id"))


def _insert_report_content(
    docs,
    doc_id: str,
    content: str,
    logo_uri: str = "",
    insert_index: int = 1,
    leading_newlines: int = 0,
) -> None:
    if not doc_id:
        raise DriveReportError("No se pudo crear el documento de informe.")
    clean_content = str(content or "").strip()
    if not clean_content:
        clean_content = "Informe sin contenido."
    docs.documents().batchUpdate(
        documentId=doc_id,
        body={
            "requests": [
                {
                    "insertText": {
                        "location": {"index": max(1, int(insert_index or 1))},
                        "text": ("\n" * max(0, int(leading_newlines or 0))) + clean_content + "\n",
                    }
                }
            ]
        },
    ).execute()

    if logo_uri:
        try:
            docs.documents().batchUpdate(
                documentId=doc_id,
                body={
                    "requests": [
                        {
                            "insertInlineImage": {
                                "location": {"index": 1},
                                "uri": logo_uri,
                                "objectSize": {
                                    "height": {"magnitude": 62, "unit": "PT"},
                                    "width": {"magnitude": 168, "unit": "PT"},
                                },
                            }
                        },
                        {"insertText": {"location": {"index": 2}, "text": "\n\n"}},
                        {
                            "updateParagraphStyle": {
                                "range": {"startIndex": 1, "endIndex": 3},
                                "paragraphStyle": {"alignment": "END"},
                                "fields": "alignment",
                            }
                        },
                    ]
                },
            ).execute()
        except Exception:
            # Si la insercion de imagen falla, mantenemos el informe textual.
            pass


def _replace_tokens_in_template(docs, doc_id: str, replacements: dict[str, str]) -> int:
    requests_payload: list[dict[str, Any]] = []
    for token, value in (replacements or {}).items():
        tk = _safe_text(token)
        if not tk:
            continue
        requests_payload.append(
            {
                "replaceAllText": {
                    "containsText": {"text": tk, "matchCase": True},
                    "replaceText": _safe_text(value),
                }
            }
        )
    if not requests_payload:
        return 0

    response = docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests_payload}).execute()
    total_changed = 0
    for reply in response.get("replies", []) or []:
        replace_meta = reply.get("replaceAllText") or {}
        try:
            total_changed += int(replace_meta.get("occurrencesChanged") or 0)
        except Exception:
            continue
    return total_changed


def _extract_text_range_from_cell(cell: dict[str, Any]) -> tuple[int, int] | None:
    start: int | None = None
    end: int | None = None
    for item in (cell.get("content") or []):
        paragraph = item.get("paragraph")
        if not paragraph:
            continue
        for el in paragraph.get("elements", []):
            s = el.get("startIndex")
            e = el.get("endIndex")
            if not isinstance(s, int) or not isinstance(e, int) or e <= s:
                continue
            start = s if start is None else min(start, s)
            end = e if end is None else max(end, e)
    if start is None or end is None or end <= start:
        return None
    return start, end


def _style_first_table_professional(docs, doc_id: str) -> None:
    try:
        document = docs.documents().get(documentId=doc_id).execute()
    except Exception:
        return

    first_table = None
    table_start = None
    for item in document.get("body", {}).get("content", []):
        if item.get("table"):
            first_table = item.get("table")
            table_start = int(item.get("startIndex", 1))
            break
    if not first_table or table_start is None:
        return

    rows = first_table.get("tableRows", []) or []
    if not rows:
        return
    col_count = len(rows[0].get("tableCells", []) or [])
    if col_count <= 0:
        return

    requests_payload: list[dict[str, Any]] = []

    # Header con color corporativo.
    requests_payload.append(
        {
            "updateTableCellStyle": {
                "tableStartLocation": {"index": table_start},
                "tableRange": {
                    "tableCellLocation": {"rowIndex": 0, "columnIndex": 0},
                    "rowSpan": 1,
                    "columnSpan": col_count,
                },
                "tableCellStyle": {
                    "backgroundColor": {
                        "color": {"rgbColor": {"red": 0.08, "green": 0.29, "blue": 0.43}}
                    },
                    "contentAlignment": "MIDDLE",
                },
                "fields": "backgroundColor,contentAlignment",
            }
        }
    )

    # Cuerpo con fondo limpio.
    if len(rows) > 1:
        requests_payload.append(
            {
                "updateTableCellStyle": {
                    "tableStartLocation": {"index": table_start},
                    "tableRange": {
                        "tableCellLocation": {"rowIndex": 1, "columnIndex": 0},
                        "rowSpan": len(rows) - 1,
                        "columnSpan": col_count,
                    },
                    "tableCellStyle": {
                        "backgroundColor": {
                            "color": {"rgbColor": {"red": 0.98, "green": 0.99, "blue": 1.0}}
                        },
                        "contentAlignment": "MIDDLE",
                    },
                    "fields": "backgroundColor,contentAlignment",
                }
            }
        )

    # Bordes uniformes.
    requests_payload.append(
        {
            "updateTableCellStyle": {
                "tableStartLocation": {"index": table_start},
                "tableRange": {
                    "tableCellLocation": {"rowIndex": 0, "columnIndex": 0},
                    "rowSpan": len(rows),
                    "columnSpan": col_count,
                },
                "tableCellStyle": {
                    "borderTop": {
                        "color": {"color": {"rgbColor": {"red": 0.57, "green": 0.67, "blue": 0.76}}},
                        "width": {"magnitude": 1, "unit": "PT"},
                        "dashStyle": "SOLID",
                    },
                    "borderBottom": {
                        "color": {"color": {"rgbColor": {"red": 0.57, "green": 0.67, "blue": 0.76}}},
                        "width": {"magnitude": 1, "unit": "PT"},
                        "dashStyle": "SOLID",
                    },
                    "borderLeft": {
                        "color": {"color": {"rgbColor": {"red": 0.57, "green": 0.67, "blue": 0.76}}},
                        "width": {"magnitude": 1, "unit": "PT"},
                        "dashStyle": "SOLID",
                    },
                    "borderRight": {
                        "color": {"color": {"rgbColor": {"red": 0.57, "green": 0.67, "blue": 0.76}}},
                        "width": {"magnitude": 1, "unit": "PT"},
                        "dashStyle": "SOLID",
                    },
                },
                "fields": "borderTop,borderBottom,borderLeft,borderRight",
            }
        }
    )

    # Estilo texto header/body.
    for r_idx, row in enumerate(rows):
        for cell in row.get("tableCells", []) or []:
            text_range = _extract_text_range_from_cell(cell)
            if not text_range:
                continue
            start, end = text_range
            if r_idx == 0:
                requests_payload.append(
                    {
                        "updateTextStyle": {
                            "range": {"startIndex": start, "endIndex": end},
                            "textStyle": {
                                "bold": True,
                                "fontSize": {"magnitude": 10.5, "unit": "PT"},
                                "foregroundColor": {
                                    "color": {"rgbColor": {"red": 1.0, "green": 1.0, "blue": 1.0}}
                                },
                            },
                            "fields": "bold,fontSize,foregroundColor",
                        }
                    }
                )
            else:
                requests_payload.append(
                    {
                        "updateTextStyle": {
                            "range": {"startIndex": start, "endIndex": end},
                            "textStyle": {
                                "bold": False,
                                "fontSize": {"magnitude": 10, "unit": "PT"},
                                "foregroundColor": {
                                    "color": {"rgbColor": {"red": 0.12, "green": 0.18, "blue": 0.24}}
                                },
                            },
                            "fields": "bold,fontSize,foregroundColor",
                        }
                    }
                )

    if requests_payload:
        docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests_payload}).execute()


def _build_protocol_template_replacements(report_kind: str, ctx: dict[str, Any]) -> dict[str, str]:
    kind = str(report_kind or "").strip().upper()
    fecha = _safe_text(ctx.get("fecha_registro") or ctx.get("fecha_emision"))
    fecha_emision = _safe_text(ctx.get("fecha_emision"))
    sucursal = _safe_text(ctx.get("sucursal"))
    cliente = _safe_text(ctx.get("cliente"))
    tipo = _safe_text(ctx.get("tipo_protocolo"))
    observacion = _safe_text(ctx.get("observacion_formalizada") or ctx.get("observacion_formal") or ctx.get("observacion_original"))

    if kind == "SEMANAL":
        titulo = "INFORME DE PROTOCOLOS SEMANAL"
        inicio = _safe_text(ctx.get("periodo_inicio"))
        fin = _safe_text(ctx.get("periodo_fin"))
        intro = (
            f"Por medio del presente, ponemos a su disposicion el Informe de Protocolos Semanal, "
            f"correspondiente a los procedimientos registrados entre los dias {inicio} y {fin} "
            f"en la sucursal {sucursal}."
        )
        objetivo = (
            "Este informe tiene por objetivo entregar una vision clara y detallada de los eventos, "
            "protocolos ejecutados y observaciones asociadas durante el periodo senalado, "
            "con el fin de mantener una comunicacion transparente y un control adecuado de las operaciones realizadas."
        )
        detalle_filas = ctx.get("detalle_filas")
        if isinstance(detalle_filas, list) and detalle_filas:
            first_row = detalle_filas[0] if isinstance(detalle_filas[0], dict) else {}
            fecha_tabla = _safe_text(first_row.get("fecha")) or (f"{inicio} - {fin}" if inicio or fin else fecha)
            tipo = _safe_text(first_row.get("tipo_protocolo")) or tipo
            observacion = _safe_text(first_row.get("observacion")) or observacion
        else:
            fecha_tabla = f"{inicio} - {fin}" if inicio or fin else fecha
        if not tipo:
            total_p = int(ctx.get("total_preventivo") or 0)
            total_i = int(ctx.get("total_intrusivo") or 0)
            if total_p and total_i:
                tipo = "Mixto"
            elif total_p:
                tipo = "Preventivo"
            elif total_i:
                tipo = "Intrusivo"
            else:
                tipo = "-"
        if not observacion:
            detalle = ctx.get("detalle_lineas") or []
            if isinstance(detalle, list) and detalle:
                observacion = _safe_text(detalle[0])
    else:
        titulo = "INFORME DE PROTOCOLOS DIARIO"
        intro = f"Informe de protocolo diario, protocolo acaecido el dia {fecha} en sucursal {cliente}."
        objetivo = ""
        fecha_tabla = fecha

    replacements = {
        "{{TituloInforme}}": titulo,
        "{{Fecha}}": fecha_emision or fecha,
        "{{CiudadFecha}}": f"Vina del mar, {fecha_emision or fecha}",
        "{{Cliente}}": cliente,
        "{{Sucursal}}": sucursal,
        "{{Suuarsal}}": sucursal,
        "{{sucursal}}": sucursal,
        "{{SUCURSAL}}": sucursal,
        "{{InicioSemana}}": _safe_text(ctx.get("periodo_inicio")),
        "{{FinSemana}}": _safe_text(ctx.get("periodo_fin")),
        "{{TextoIntro}}": intro,
        "{{TextoObjetivo}}": objetivo,
        "{{Fecha registro protocolo}}": fecha_tabla,
        "{{Fecha registro\nprotocolo}}": fecha_tabla,
        "{{Tipo protocolo}}": tipo or "-",
        "{{Observaciones Corregidas}}": observacion or "-",
        # Variantes comunes por si tu template usa mayus/minus diferentes.
        "{{TITULO}}": titulo,
        "{{INTRO}}": intro,
        "{{OBJETIVO}}": objetivo,
        "{{tipo protocolo}}": tipo or "-",
        "{{OBSERVACIONES CORREGIDAS}}": observacion or "-",
    }
    return replacements


def _get_protocol_template_id(report_kind: str) -> str:
    kind = str(report_kind or "").strip().upper()
    if kind == "INDIVIDUAL":
        return (
            _safe_text(settings.google_doc_template_protocolos_diario_id)
            or _safe_text(settings.google_doc_template_protocolos_id)
        )
    if kind == "SEMANAL":
        return (
            _safe_text(settings.google_doc_template_protocolos_semanal_id)
            or _safe_text(settings.google_doc_template_protocolos_id)
        )
    return _safe_text(settings.google_doc_template_protocolos_id)


def _table_cell_insert_index(cell: dict[str, Any]) -> int | None:
    for item in (cell.get("content") or []):
        paragraph = item.get("paragraph")
        if not paragraph:
            continue
        for el in (paragraph.get("elements") or []):
            s = el.get("startIndex")
            if isinstance(s, int):
                return max(1, s)
    return None


def _get_first_table(document: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    for item in document.get("body", {}).get("content", []) or []:
        table = item.get("table")
        if table:
            return int(item.get("startIndex", 1)), table
    return None


def _set_table_row_values(
    docs,
    doc_id: str,
    *,
    row_index: int,
    values: list[str],
) -> None:
    document = docs.documents().get(documentId=doc_id).execute()
    first_table = _get_first_table(document)
    if not first_table:
        return
    _, table = first_table
    rows = table.get("tableRows", []) or []
    if row_index < 0 or row_index >= len(rows):
        return

    target_row = rows[row_index]
    cells = target_row.get("tableCells", []) or []
    requests_payload: list[dict[str, Any]] = []

    for idx, cell in enumerate(cells):
        insert_idx = _table_cell_insert_index(cell)
        if insert_idx is None:
            continue
        text_value = _safe_text(values[idx] if idx < len(values) else "-")
        requests_payload.append({"insertText": {"location": {"index": insert_idx}, "text": text_value}})

    if requests_payload:
        requests_payload.sort(key=lambda r: r["insertText"]["location"]["index"], reverse=True)
        docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests_payload}).execute()


def _populate_weekly_table_rows_from_detail(docs, doc_id: str, ctx: dict[str, Any]) -> None:
    detalle_filas = ctx.get("detalle_filas")
    if not isinstance(detalle_filas, list) or not detalle_filas:
        return

    normalized_rows: list[dict[str, str]] = []
    for item in detalle_filas:
        if not isinstance(item, dict):
            continue
        normalized_rows.append(
            {
                "fecha": _safe_text(item.get("fecha")),
                "sucursal": _safe_text(item.get("sucursal")) or _safe_text(ctx.get("sucursal")),
                "tipo_protocolo": _safe_text(item.get("tipo_protocolo")) or "-",
                "observacion": _safe_text(item.get("observacion")) or "-",
            }
        )
    if not normalized_rows:
        return

    # La fila base del template (index 1) se completa via replaceAllText (primer protocolo).
    # Desde el segundo protocolo en adelante, insertamos filas nuevas.
    for row_data in normalized_rows[1:]:
        try:
            document = docs.documents().get(documentId=doc_id).execute()
            first_table = _get_first_table(document)
            if not first_table:
                break
            table_start, table = first_table
            row_count = len(table.get("tableRows", []) or [])
            if row_count <= 0:
                break
            insert_below_row = row_count - 1
            docs.documents().batchUpdate(
                documentId=doc_id,
                body={
                    "requests": [
                        {
                            "insertTableRow": {
                                "tableCellLocation": {
                                    "tableStartLocation": {"index": table_start},
                                    "rowIndex": insert_below_row,
                                    "columnIndex": 0,
                                },
                                "insertBelow": True,
                            }
                        }
                    ]
                },
            ).execute()
            _set_table_row_values(
                docs,
                doc_id,
                row_index=insert_below_row + 1,
                values=[
                    row_data["fecha"],
                    row_data["sucursal"],
                    row_data["tipo_protocolo"],
                    row_data["observacion"],
                ],
            )
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "No se pudo insertar fila adicional en tabla semanal (doc=%s): %s",
                doc_id, row_data.get("fecha", "?"),
                exc_info=True,
            )


def _build_protocol_template_texts(report_kind: str, ctx: dict[str, Any]) -> dict[str, str]:
    kind = str(report_kind or "").strip().upper()
    fecha = _safe_text(ctx.get("fecha_registro") or ctx.get("fecha_emision"))
    fecha_emision = _safe_text(ctx.get("fecha_emision") or fecha)
    cliente = _safe_text(ctx.get("cliente"))
    sucursal = _safe_text(ctx.get("sucursal"))
    inicio = _safe_text(ctx.get("periodo_inicio"))
    fin = _safe_text(ctx.get("periodo_fin"))
    tipo = _safe_text(ctx.get("tipo_protocolo"))
    observacion = _safe_text(ctx.get("observacion_formalizada") or ctx.get("observacion_formal") or "")

    if not tipo and kind == "SEMANAL":
        total_p = int(ctx.get("total_preventivo") or 0)
        total_i = int(ctx.get("total_intrusivo") or 0)
        if total_p and total_i:
            tipo = "Mixto"
        elif total_p:
            tipo = "Preventivo"
        elif total_i:
            tipo = "Intrusivo"
        else:
            tipo = "-"

    if not observacion:
        detalle = ctx.get("detalle_lineas") or []
        if isinstance(detalle, list) and detalle:
            observacion = _safe_text(detalle[0])

    if kind == "SEMANAL":
        titulo = "INFORME DE PROTOCOLOS SEMANAL"
        saludo = f"Estimado(a) cliente {cliente},"
        intro = (
            f"Por medio del presente, ponemos a su disposicion el Informe de Protocolos Semanal, "
            f"correspondiente a los procedimientos registrados entre los dias {inicio} y {fin} "
            f"en la sucursal {sucursal}."
        )
        objetivo = (
            "Este informe tiene por objetivo entregar una vision clara y detallada de los eventos, protocolos "
            "ejecutados y observaciones asociadas durante el periodo senalado, con el fin de mantener una "
            "comunicacion transparente y un control adecuado de las operaciones realizadas."
        )
        fecha_tabla = f"{inicio} - {fin}" if inicio or fin else fecha
    else:
        titulo = "INFORME DE PROTOCOLOS DIARIO"
        saludo = cliente
        intro = f"Informe de protocolo diario, protocolo acaecido el dia {fecha} en sucursal {sucursal}."
        objetivo = ""
        fecha_tabla = fecha

    return {
        "titulo": titulo,
        "ciudad_fecha": f"Vina del Mar, {fecha_emision}",
        "saludo": saludo,
        "intro": intro,
        "objetivo": objetivo,
        "fecha_tabla": fecha_tabla,
        "sucursal": sucursal,
        "tipo": tipo or "-",
        "observacion": observacion or "-",
    }


def _insert_protocol_content_and_table(
    docs,
    doc_id: str,
    report_kind: str,
    ctx: dict[str, Any],
) -> None:
    texts = _build_protocol_template_texts(report_kind, ctx)
    bloques = [
        "\n\n\n\n\n",
        texts["titulo"],
        "",
        texts["ciudad_fecha"],
        "",
        texts["saludo"],
        "",
        texts["intro"],
    ]
    if texts["objetivo"]:
        bloques.extend(["", texts["objetivo"]])
    bloques.extend(["", "REVISION DE PROTOCOLOS", ""])
    contenido = "\n".join(bloques).strip() + "\n"

    _insert_report_content(docs, doc_id, contenido, logo_uri="", insert_index=1, leading_newlines=0)

    document = docs.documents().get(documentId=doc_id).execute()
    rev_range = _find_placeholder_range(document, "REVISION DE PROTOCOLOS")
    table_index = (rev_range[1] + 1) if rev_range else max(1, int(document.get("body", {}).get("content", [{}])[-1].get("endIndex", 2)) - 1)

    docs.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": [{"insertTable": {"rows": 2, "columns": 4, "location": {"index": table_index}}}]},
    ).execute()

    document = docs.documents().get(documentId=doc_id).execute()
    table_item = None
    for item in reversed(document.get("body", {}).get("content", [])):
        if item.get("table"):
            table_item = item
            break
    if not table_item:
        return

    rows = table_item.get("table", {}).get("tableRows", []) or []
    if len(rows) < 2:
        return
    detalle_filas = ctx.get("detalle_filas") or []
    first_row = detalle_filas[0] if detalle_filas else {}
    values = [
        ["Fecha", "Sucursal", "Tipo de Protocolo", "Observacion"],
        [
            _safe_text(first_row.get("fecha") or texts["fecha_tabla"]),
            _safe_text(first_row.get("sucursal") or texts["sucursal"]),
            _safe_text(first_row.get("tipo_protocolo") or texts["tipo"]),
            _safe_text(first_row.get("observacion") or texts["observacion"]),
        ],
    ]
    requests_payload: list[dict[str, Any]] = []
    for r_idx, row in enumerate(rows[:2]):
        cells = row.get("tableCells", []) or []
        for c_idx, cell in enumerate(cells[:4]):
            cell_idx = _table_cell_insert_index(cell)
            if cell_idx is None:
                continue
            txt = values[r_idx][c_idx]
            requests_payload.append(
                {"insertText": {"location": {"index": cell_idx}, "text": _safe_text(txt)}}
            )
    if requests_payload:
        requests_payload.sort(key=lambda r: r["insertText"]["location"]["index"], reverse=True)
        docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests_payload}).execute()
    _style_first_table_professional(docs, doc_id)


def _insert_protocol_content_fallback(
    docs,
    doc_id: str,
    report_kind: str,
    ctx: dict[str, Any],
) -> None:
    texts = _build_protocol_template_texts(report_kind, ctx)
    bloques = [
        "\n\n\n\n\n",
        texts["titulo"],
        "",
        texts["ciudad_fecha"],
        "",
        texts["saludo"],
        "",
        texts["intro"],
    ]
    if texts["objetivo"]:
        bloques.extend(["", texts["objetivo"]])
    bloques.extend(["", "REVISION DE PROTOCOLOS", ""])
    contenido = "\n".join(bloques).strip() + "\n"

    _insert_report_content(docs, doc_id, contenido, logo_uri="", insert_index=1, leading_newlines=0)

    document = docs.documents().get(documentId=doc_id).execute()
    rev_range = _find_placeholder_range(document, "REVISION DE PROTOCOLOS")
    table_index = (
        (rev_range[1] + 1)
        if rev_range
        else max(1, int(document.get("body", {}).get("content", [{}])[-1].get("endIndex", 2)) - 1)
    )

    docs.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": [{"insertTable": {"rows": 2, "columns": 4, "location": {"index": table_index}}}]},
    ).execute()

    document = docs.documents().get(documentId=doc_id).execute()
    table_item = None
    for item in reversed(document.get("body", {}).get("content", [])):
        if item.get("table"):
            table_item = item
            break
    if not table_item:
        return

    rows = table_item.get("table", {}).get("tableRows", []) or []
    if len(rows) < 2:
        return
    detalle_filas = ctx.get("detalle_filas") or []
    first_row = detalle_filas[0] if detalle_filas else {}
    values = [
        ["Fecha", "Sucursal", "Tipo de Protocolo", "Observacion"],
        [
            _safe_text(first_row.get("fecha") or texts["fecha_tabla"]),
            _safe_text(first_row.get("sucursal") or texts["sucursal"]),
            _safe_text(first_row.get("tipo_protocolo") or texts["tipo"]),
            _safe_text(first_row.get("observacion") or texts["observacion"]),
        ],
    ]
    requests_payload: list[dict[str, Any]] = []
    for r_idx, row in enumerate(rows[:2]):
        cells = row.get("tableCells", []) or []
        for c_idx, cell in enumerate(cells[:4]):
            cell_idx = _table_cell_insert_index(cell)
            if cell_idx is None:
                continue
            txt = values[r_idx][c_idx]
            requests_payload.append({"insertText": {"location": {"index": cell_idx}, "text": _safe_text(txt)}})
    if requests_payload:
        requests_payload.sort(key=lambda r: r["insertText"]["location"]["index"], reverse=True)
        docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests_payload}).execute()


def _apply_report_styles(
    docs,
    doc_id: str,
    *,
    title_token: str,
    section_tokens: list[str],
    preserve_template_style: bool = False,
) -> None:
    try:
        document = docs.documents().get(documentId=doc_id).execute()
    except Exception:
        return

    requests_payload: list[dict[str, Any]] = []
    body_content = document.get("body", {}).get("content", [])
    doc_end = 1
    for item in body_content:
        try:
            doc_end = max(doc_end, int(item.get("endIndex", doc_end)))
        except Exception:
            continue

    if not preserve_template_style:
        # Intentamos aplicar un fondo suave a la hoja completa para evitar blanco puro.
        # Si la API/cuenta no admite background, caemos a margenes solamente.
        try:
            docs.documents().batchUpdate(
                documentId=doc_id,
                body={
                    "requests": [
                        {
                            "updateDocumentStyle": {
                                "documentStyle": {
                                    "marginTop": {"magnitude": 36, "unit": "PT"},
                                    "marginBottom": {"magnitude": 34, "unit": "PT"},
                                    "marginLeft": {"magnitude": 42, "unit": "PT"},
                                    "marginRight": {"magnitude": 42, "unit": "PT"},
                                    "background": {
                                        "color": {
                                            "rgbColor": {
                                                "red": 0.90,
                                                "green": 0.94,
                                                "blue": 0.98,
                                            }
                                        }
                                    },
                                },
                                "fields": "marginTop,marginBottom,marginLeft,marginRight,background",
                            }
                        }
                    ]
                },
            ).execute()
        except Exception:
            try:
                docs.documents().batchUpdate(
                    documentId=doc_id,
                    body={
                        "requests": [
                            {
                                "updateDocumentStyle": {
                                    "documentStyle": {
                                        "marginTop": {"magnitude": 36, "unit": "PT"},
                                        "marginBottom": {"magnitude": 34, "unit": "PT"},
                                        "marginLeft": {"magnitude": 42, "unit": "PT"},
                                        "marginRight": {"magnitude": 42, "unit": "PT"},
                                    },
                                    "fields": "marginTop,marginBottom,marginLeft,marginRight",
                                }
                            }
                        ]
                    },
                ).execute()
            except Exception:
                return

    if doc_end > 2:
        requests_payload.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": 1, "endIndex": doc_end - 1},
                    "textStyle": {
                        "weightedFontFamily": {"fontFamily": "Calibri"},
                        "fontSize": {"magnitude": 10.5, "unit": "PT"},
                        "foregroundColor": {
                            "color": {"rgbColor": {"red": 0.12, "green": 0.18, "blue": 0.24}}
                        },
                    },
                    "fields": "weightedFontFamily,fontSize,foregroundColor",
                }
            }
        )
        requests_payload.append(
            {
                "updateParagraphStyle": {
                    "range": {"startIndex": 1, "endIndex": doc_end - 1},
                    "paragraphStyle": {"lineSpacing": 120},
                    "fields": "lineSpacing",
                }
            }
        )

    title_range = _find_placeholder_range(document, title_token)
    if title_range:
        requests_payload.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": title_range[0], "endIndex": title_range[1]},
                    "textStyle": {
                        "bold": True,
                        "fontSize": {"magnitude": 20, "unit": "PT"},
                        "foregroundColor": {
                            "color": {
                                "rgbColor": {"red": 0.05, "green": 0.18, "blue": 0.30},
                            }
                        },
                    },
                    "fields": "bold,fontSize,foregroundColor",
                }
            }
        )
        requests_payload.append(
            {
                "updateParagraphStyle": {
                    "range": {"startIndex": title_range[0], "endIndex": title_range[1]},
                    "paragraphStyle": {
                        "alignment": "CENTER",
                        "spaceBelow": {"magnitude": 10, "unit": "PT"},
                        "lineSpacing": 120,
                    },
                    "fields": "alignment,spaceBelow,lineSpacing",
                }
            }
        )

    subtitle_range = _find_placeholder_range(document, "ATC - Alguien Te Cuida - Control Operativo")
    if subtitle_range:
        requests_payload.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": subtitle_range[0], "endIndex": subtitle_range[1]},
                    "textStyle": {
                        "italic": True,
                        "fontSize": {"magnitude": 10, "unit": "PT"},
                        "foregroundColor": {
                            "color": {"rgbColor": {"red": 0.36, "green": 0.41, "blue": 0.46}}
                        },
                    },
                    "fields": "italic,fontSize,foregroundColor",
                }
            }
        )
        requests_payload.append(
            {
                "updateParagraphStyle": {
                    "range": {"startIndex": subtitle_range[0], "endIndex": subtitle_range[1]},
                    "paragraphStyle": {
                        "alignment": "CENTER",
                        "spaceBelow": {"magnitude": 14, "unit": "PT"},
                    },
                    "fields": "alignment,spaceBelow",
                }
            }
        )

    for meta_token in ("Codigo de informe:", "Fecha de emision:", "Registro SQL ID:"):
        meta_range = _find_placeholder_range(document, meta_token)
        if not meta_range:
            continue
        requests_payload.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": meta_range[0], "endIndex": meta_range[1]},
                    "textStyle": {
                        "bold": True,
                        "foregroundColor": {
                            "color": {"rgbColor": {"red": 0.22, "green": 0.29, "blue": 0.35}}
                        },
                    },
                    "fields": "bold,foregroundColor",
                }
            }
        )

    for token in section_tokens:
        found = _find_placeholder_range(document, token)
        if not found:
            continue
        requests_payload.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": found[0], "endIndex": found[1]},
                    "textStyle": {
                        "bold": True,
                        "fontSize": {"magnitude": 11, "unit": "PT"},
                        "backgroundColor": {
                            "color": {"rgbColor": {"red": 0.08, "green": 0.29, "blue": 0.43}}
                        },
                        "foregroundColor": {
                            "color": {"rgbColor": {"red": 1.0, "green": 1.0, "blue": 1.0}}
                        },
                    },
                    "fields": "bold,fontSize,foregroundColor,backgroundColor",
                }
            }
        )
        requests_payload.append(
            {
                "updateParagraphStyle": {
                    "range": {"startIndex": found[0], "endIndex": found[1]},
                    "paragraphStyle": {
                        "spaceAbove": {"magnitude": 10, "unit": "PT"},
                        "spaceBelow": {"magnitude": 7, "unit": "PT"},
                    },
                    "fields": "spaceAbove,spaceBelow",
                }
            }
        )

    if requests_payload:
        docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests_payload}).execute()


def _protocolos_root_folder_id(root_folder_id: str | None) -> str:
    root_id = (
        _safe_text(root_folder_id)
        or _safe_text(settings.google_drive_protocolos_folder_id)
        or _safe_text(settings.google_drive_root_folder_id)
    )
    if not root_id:
        raise DriveReportError("Falta GOOGLE_DRIVE_PROTOCOLOS_FOLDER_ID.")
    return root_id


def _create_protocol_pdf_report(
    *,
    report_kind: str,
    cliente: str,
    sucursal: str,
    report_title: str,
    pdf_filename: str,
    content: str,
    section_tokens: list[str],
    template_context: dict[str, Any] | None = None,
    root_folder_id: str | None = None,
) -> dict[str, Any]:
    if not settings.google_drive_enabled:
        raise DriveReportError("GOOGLE_DRIVE_ENABLED=false")

    drive, docs = _build_clients()
    root_id = _protocolos_root_folder_id(root_folder_id)
    safe_cliente = _clean_filename(cliente or "Cliente", fallback="Cliente")
    safe_sucursal = _clean_filename(sucursal or "Sucursal", fallback="Sucursal")

    cliente_folder_id = _find_or_create_folder(drive, root_id, safe_cliente)
    sucursal_folder_id = _find_or_create_folder(drive, cliente_folder_id, safe_sucursal)
    informes_folder_id = _find_or_create_folder(drive, sucursal_folder_id, "Informes Protocolos")
    kind_folder_name = "Individuales" if str(report_kind).upper() == "INDIVIDUAL" else "Semanales"
    report_folder_id = _find_or_create_folder(drive, informes_folder_id, kind_folder_name)

    now_stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M")
    doc_title = _clean_filename(f"{report_title} - {now_stamp}", fallback=f"Informe_{now_stamp}")
    template_id = _get_protocol_template_id(report_kind)
    use_template = bool(template_id)
    if use_template:
        doc_id = _copy_template(drive, template_id, report_folder_id, doc_title)
        ctx = dict(template_context or {})
        replacements = _build_protocol_template_replacements(report_kind, ctx)
        replacements_changed = _replace_tokens_in_template(docs, doc_id, replacements)
        if str(report_kind).strip().upper() == "SEMANAL":
            _populate_weekly_table_rows_from_detail(docs, doc_id, ctx)
        doc_snapshot = docs.documents().get(documentId=doc_id).execute()
        visible_len = _document_visible_text_len(doc_snapshot)
        if replacements_changed <= 0 or visible_len < 40:
            # Si el template no trae placeholders legibles por API, evitamos PDF en blanco.
            _insert_protocol_content_fallback(docs, doc_id, report_kind, ctx)
        # Importante: respetar exactamente el layout del template (sin insertar bloques/tablas extra).
    else:
        doc_id = _create_blank_doc(drive, report_folder_id, doc_title)
        logo_uri = _get_or_upload_logo_uri(drive, report_folder_id)
        _insert_report_content(docs, doc_id, content, logo_uri=logo_uri)
        _apply_report_styles(docs, doc_id, title_token=report_title, section_tokens=section_tokens)

    pdf_name = _clean_filename(pdf_filename, fallback=f"Informe_{now_stamp}.pdf")
    if not pdf_name.lower().endswith(".pdf"):
        pdf_name = f"{pdf_name}.pdf"
    pdf_bytes = _export_doc_pdf(drive, doc_id)
    uploaded_pdf = _upload_bytes(drive, report_folder_id, pdf_name, pdf_bytes, "application/pdf")

    docs_web_view_link = f"https://docs.google.com/document/d/{doc_id}/edit"

    return {
        "pdf_file_id": uploaded_pdf["id"],
        "pdf_name": uploaded_pdf.get("name", pdf_name),
        "pdf_web_view_link": uploaded_pdf.get("webViewLink", ""),
        "docs_file_id": doc_id,
        "docs_web_view_link": docs_web_view_link,
        "folder_id": report_folder_id,
        "folder_name": kind_folder_name,
        "cliente_folder_id": cliente_folder_id,
        "sucursal_folder_id": sucursal_folder_id,
    }


def create_protocol_individual_report_pdf(
    *,
    context: dict[str, Any],
    root_folder_id: str | None = None,
) -> dict[str, Any]:
    ctx = dict(context or {})
    report_title = "INFORME DE PROTOCOLOS DIARIO"
    content = _render_protocol_template("protocolo_individual.txt.j2", ctx)
    registro_id = _safe_text(ctx.get("registro_id"))
    sucursal = _safe_text(ctx.get("sucursal"))
    now_stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M")
    pdf_filename = f"Protocolo_{registro_id or 'NA'}_{_clean_filename(sucursal or 'Sucursal')}_{now_stamp}.pdf"
    return _create_protocol_pdf_report(
        report_kind="INDIVIDUAL",
        cliente=_safe_text(ctx.get("cliente")),
        sucursal=sucursal,
        report_title=report_title,
        pdf_filename=pdf_filename,
        content=content,
        section_tokens=[
            "REVISION DE PROTOCOLOS",
        ],
        template_context=ctx,
        root_folder_id=root_folder_id,
    )


def create_protocol_weekly_report_pdf(
    *,
    context: dict[str, Any],
    root_folder_id: str | None = None,
) -> dict[str, Any]:
    ctx = dict(context or {})
    report_title = "INFORME DE PROTOCOLOS SEMANAL"
    content = _render_protocol_template("protocolo_semanal.txt.j2", ctx)
    sucursal = _safe_text(ctx.get("sucursal"))
    periodo_inicio = _safe_text(ctx.get("periodo_inicio")).replace("/", "-")
    periodo_fin = _safe_text(ctx.get("periodo_fin")).replace("/", "-")
    now_stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M")
    pdf_filename = (
        f"Semanal_{_clean_filename(sucursal or 'Sucursal')}_{periodo_inicio}_{periodo_fin}_{now_stamp}.pdf"
    )
    return _create_protocol_pdf_report(
        report_kind="SEMANAL",
        cliente=_safe_text(ctx.get("cliente")),
        sucursal=sucursal,
        report_title=report_title,
        pdf_filename=pdf_filename,
        content=content,
        section_tokens=[
            "REVISION DE PROTOCOLOS",
        ],
        template_context=ctx,
        root_folder_id=root_folder_id,
    )


def resolve_odt_cierre_folder(*, odt: str, sucursal: str, cliente: str) -> tuple[str, str]:
    """Resuelve (idempotente, cachea por nombre) la carpeta Drive
    'Sucursal/ODT {odt}' donde conviven las fotos y el PDF de un cierre.
    Compartida por la subida sincronica de fotos (en el request) y la
    subida en background del PDF, para que ambas terminen en la misma
    carpeta sin coordinarse entre si."""
    _ensure_enabled()
    drive, _ = _build_clients()
    root_folder_id  = _safe_text(settings.google_drive_root_folder_id)
    safe_sucursal   = _clean_filename(sucursal or cliente or "Sucursal", fallback="Sucursal")
    safe_odt_folder = _clean_filename(f"ODT {odt}", fallback=f"ODT_{odt}")
    sucursal_folder_id = _find_or_create_folder(drive, root_folder_id, safe_sucursal)
    folder_id          = _find_or_create_folder(drive, sucursal_folder_id, safe_odt_folder)
    return folder_id, safe_odt_folder


def upload_odt_cierre_images_to_drive(
    *,
    folder_id: str,
    odt: str,
    image_payloads: list[dict[str, object]],
) -> list[str]:
    """Sube fotos ya en memoria (bytes) a una carpeta de cierre ya resuelta
    (ver resolve_odt_cierre_folder). Pensada para subir las fotos de forma
    sincronica, antes de responder el request de cierre, para que
    foto_1/2/3 queden con la URL definitiva de inmediato. Salta en
    silencio las que fallen, igual que el resto del modulo."""
    drive, _ = _build_clients()
    uploaded_urls: list[str] = []
    for idx, payload in enumerate(image_payloads or [], start=1):
        try:
            content = payload.get("bytes")
            if not isinstance(content, (bytes, bytearray)) or not content:
                continue
            mime_type = _safe_text(payload.get("mime_type")) or "image/jpeg"
            base_name = _safe_text(payload.get("filename")) or f"img_{idx}"
            _, ext = _guess_mime_and_ext(base_name, default_mime=mime_type)
            img_name = _clean_filename(
                f"ODT_{odt}_IMG_{idx:02d}{ext}", fallback=f"ODT_{odt}_IMG_{idx:02d}.jpg"
            )
            uploaded_img = _upload_bytes(drive, folder_id, img_name, bytes(content), mime_type)
            try:
                _set_public_read(drive, uploaded_img["id"])
            except Exception:
                pass
            uploaded_urls.append(f"/api/incidencias/drive-image/{uploaded_img['id']}")
        except Exception:
            LOGGER.exception("Foto %s de cierre ODT %s no se pudo subir a Drive", idx, odt)
            continue
    return uploaded_urls


def upload_odt_cierre_pdf_to_drive(
    *,
    folder_id: str,
    odt: str,
    sucursal: str,
    pdf_bytes: bytes,
) -> dict[str, Any]:
    """Sube solo el PDF de cierre a una carpeta ya resuelta. Usada cuando
    las fotos ya se subieron por separado (ver upload_odt_cierre_images_to_drive)
    y solo falta el informe."""
    drive, _ = _build_clients()
    safe_sucursal = _clean_filename(sucursal or "Sucursal", fallback="Sucursal")
    now_stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M")
    pdf_name  = _clean_filename(
        f"ODT_{odt}_{safe_sucursal}_{now_stamp}.pdf", fallback=f"ODT_{odt}_{now_stamp}.pdf"
    )
    uploaded = _upload_bytes(drive, folder_id, pdf_name, pdf_bytes, "application/pdf")
    return {
        "pdf_file_id":       uploaded["id"],
        "pdf_web_view_link": uploaded.get("webViewLink", ""),
        "pdf_content_link":  uploaded.get("webContentLink", ""),
    }


def upload_odt_cierre_to_drive(
    *,
    pdf_local_path: str = "",
    pdf_bytes: bytes | None = None,
    odt: str,
    sucursal: str,
    cliente: str,
    image_sources: list[str],
) -> dict[str, Any]:
    """Sube el PDF de cierre de ODT y las imágenes a una carpeta Drive.

    El PDF puede venir ya en memoria (`pdf_bytes`, evita escribirlo a disco
    local) o como ruta a un archivo ya guardado (`pdf_local_path`, usado por
    los reintentos/regeneración que parten de un PDF que sí quedó en disco).
    `image_sources` son URLs/data-uris (no bytes en memoria) — para ese caso
    usar upload_odt_cierre_images_to_drive. No usa template de Google Docs —
    solo sube archivos.
    """
    _ensure_enabled()
    drive, _ = _build_clients()

    folder_id, safe_odt_folder = resolve_odt_cierre_folder(odt=odt, sucursal=sucursal, cliente=cliente)
    safe_sucursal = _clean_filename(sucursal or cliente or "Sucursal", fallback="Sucursal")

    uploaded_image_urls: list[str] = []
    for idx, source in enumerate(image_sources or [], start=1):
        try:
            content, mime_type, ext = _read_image_source(source)
            img_name = _clean_filename(
                f"ODT_{odt}_IMG_{idx:02d}{ext}", fallback=f"ODT_{odt}_IMG_{idx:02d}.jpg"
            )
            uploaded_img = _upload_bytes(drive, folder_id, img_name, content, mime_type)
            try:
                _set_public_read(drive, uploaded_img["id"])
            except Exception:
                pass
            uploaded_image_urls.append(f"/api/incidencias/drive-image/{uploaded_img['id']}")
        except Exception:
            LOGGER.exception("Foto %s (fuente %r) de cierre ODT %s no se pudo subir a Drive", idx, source, odt)
            continue

    # El informe se sube en su propio try/except: si falla (red, cuota, etc.)
    # igual devolvemos folder_id para que el llamador registre la carpeta.
    # Antes una falla aca perdia tambien el folder_id de las fotos ya
    # subidas, dejando el cierre sin ningun rastro de Drive para reintentar.
    pdf_file_id = ""
    pdf_web_view_link = ""
    pdf_content_link = ""
    pdf_error = ""
    try:
        if pdf_bytes is not None:
            pdf_payload = pdf_bytes
        else:
            pdf_path = Path(pdf_local_path)
            if not pdf_path.exists():
                raise DriveReportError(f"PDF local no encontrado: {pdf_local_path}")
            pdf_payload = pdf_path.read_bytes()

        now_stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M")
        pdf_name  = _clean_filename(
            f"ODT_{odt}_{safe_sucursal}_{now_stamp}.pdf", fallback=f"ODT_{odt}_{now_stamp}.pdf"
        )
        uploaded = _upload_bytes(drive, folder_id, pdf_name, pdf_payload, "application/pdf")
        pdf_file_id = uploaded["id"]
        pdf_web_view_link = uploaded.get("webViewLink", "")
        pdf_content_link = uploaded.get("webContentLink", "")
    except Exception as exc:
        LOGGER.exception("No se pudo subir el informe PDF a Drive para ODT %s", odt)
        pdf_error = str(exc)

    return {
        "folder_id":         folder_id,
        "folder_name":       safe_odt_folder,
        "pdf_file_id":       pdf_file_id,
        "pdf_web_view_link": pdf_web_view_link,
        "pdf_content_link":  pdf_content_link,
        "pdf_error":         pdf_error,
        "imagenes":          uploaded_image_urls,
        "imagenes_guardadas": len(uploaded_image_urls),
    }


def upload_cierre_apertura_image_to_drive(
    *,
    client_id: str,
    client_name: str,
    content: bytes,
    filename: str,
    mime_type: str = "image/png",
) -> dict[str, Any]:
    """Sube una foto de apertura/cierre de sucursal a Drive, en una subcarpeta
    por cliente bajo GOOGLE_DRIVE_CIERRE_APERTURA_FOLDER_ID."""
    if not settings.google_drive_enabled:
        raise DriveReportError("GOOGLE_DRIVE_ENABLED=false")

    root_id = _safe_text(settings.google_drive_cierre_apertura_folder_id)
    if not root_id:
        raise DriveReportError("Falta GOOGLE_DRIVE_CIERRE_APERTURA_FOLDER_ID")

    drive, _ = _build_clients()

    safe_client = _clean_filename(client_name or client_id, fallback=client_id or "Cliente")
    folder_id = _find_or_create_folder(drive, root_id, safe_client)

    uploaded = _upload_bytes(drive, folder_id, filename, content, mime_type)
    try:
        _set_public_read(drive, uploaded["id"])
    except Exception:
        pass
    uploaded["public_uri"] = f"/api/incidencias/drive-image/{uploaded['id']}"

    return {
        "folder_id": folder_id,
        "folder_name": safe_client,
        "file_id": uploaded["id"],
        "public_uri": uploaded["public_uri"],
    }


def retry_odt_cierre_drive_upload(
    *,
    pdf_local_path: str,
    odt: str,
    sucursal: str,
    cliente: str,
    image_sources: list[str],
) -> dict[str, Any]:
    """Completa un cierre de ODT que quedo a medias en Drive: reusa la
    carpeta ya creada (idempotente) y sube SOLO lo que falte — nunca vuelve
    a subir una foto o el informe que ya estan ahi. Pensado para el reintento
    automatico periodico, para que un fallo transitorio (red, cuota, un
    archivo grande, etc.) no deje el cierre sin informe para siempre."""
    _ensure_enabled()
    drive, _ = _build_clients()

    root_folder_id  = _safe_text(settings.google_drive_root_folder_id)
    safe_sucursal   = _clean_filename(sucursal or cliente or "Sucursal", fallback="Sucursal")
    safe_odt_folder = _clean_filename(f"ODT {odt}", fallback=f"ODT_{odt}")

    sucursal_folder_id = _find_or_create_folder(drive, root_folder_id, safe_sucursal)
    folder_id          = _find_or_create_folder(drive, sucursal_folder_id, safe_odt_folder)

    existentes = drive.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id,name,mimeType)",
        pageSize=100,
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
    ).execute().get("files", [])
    nombres_existentes = {f["name"] for f in existentes}
    ya_tiene_pdf = any(f.get("mimeType") == "application/pdf" for f in existentes)

    uploaded_image_urls: list[str] = []
    for idx, source in enumerate(image_sources or [], start=1):
        try:
            content, mime_type, ext = _read_image_source(source)
            img_name = _clean_filename(
                f"ODT_{odt}_IMG_{idx:02d}{ext}", fallback=f"ODT_{odt}_IMG_{idx:02d}.jpg"
            )
            if img_name in nombres_existentes:
                continue
            uploaded_img = _upload_bytes(drive, folder_id, img_name, content, mime_type)
            try:
                _set_public_read(drive, uploaded_img["id"])
            except Exception:
                pass
            uploaded_image_urls.append(f"/api/incidencias/drive-image/{uploaded_img['id']}")
        except Exception:
            LOGGER.exception("Reintento: foto %s de cierre ODT %s no se pudo subir a Drive", idx, odt)
            continue

    pdf_file_id = ""
    pdf_error = ""
    if ya_tiene_pdf:
        pdf_file_id = "ya-existia"
    else:
        try:
            pdf_path = Path(pdf_local_path)
            if not pdf_path.exists():
                raise DriveReportError(f"PDF local no encontrado: {pdf_local_path}")
            now_stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M")
            pdf_name  = _clean_filename(
                f"ODT_{odt}_{safe_sucursal}_{now_stamp}.pdf", fallback=f"ODT_{odt}_{now_stamp}.pdf"
            )
            uploaded = _upload_bytes(drive, folder_id, pdf_name, pdf_path.read_bytes(), "application/pdf")
            pdf_file_id = uploaded["id"]
        except Exception as exc:
            LOGGER.exception("Reintento: no se pudo subir el informe PDF a Drive para ODT %s", odt)
            pdf_error = str(exc)

    return {
        "folder_id": folder_id,
        "pdf_subido": bool(pdf_file_id),
        "pdf_error": pdf_error,
        "fotos_subidas": len(uploaded_image_urls),
    }
