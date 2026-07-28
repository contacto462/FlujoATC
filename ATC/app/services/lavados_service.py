from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

from googleapiclient.errors import HttpError

from ATC.app.core.config import settings
from ATC.app.services.drive_base_service import (
    _build_clients,
    _build_sheets_client,
    _clean_filename,
    _copy_template,
    _decode_data_uri,
    _export_doc_pdf,
    _find_or_create_folder,
    _insert_images_on_placeholders,
    _replace_text,
    _safe_text,
    _set_public_read,
    _upload_bytes,
)


class LavadosServiceError(RuntimeError):
    pass


_OPTIONS_CACHE: dict[str, object] = {"ts": 0.0, "data": None}
_OPTIONS_TTL_SECONDS = 120
_REGISTER_LOCK = threading.Lock()
_IMAGE_KEYS = ("antes1", "antes2", "despues1", "despues2")


def _sheet_id() -> str:
    return _safe_text(settings.lavados_sheet_id)


def _root_folder_id() -> str:
    return _safe_text(settings.lavados_drive_folder_id)


def _template_id() -> str:
    return _safe_text(settings.lavados_doc_template_id)


def _ensure_configured() -> None:
    if not _sheet_id():
        raise LavadosServiceError("Falta configurar lavados_sheet_id.")
    if not _root_folder_id():
        raise LavadosServiceError("Falta configurar lavados_drive_folder_id.")
    if not _template_id():
        raise LavadosServiceError("Falta configurar lavados_doc_template_id.")


def _values_get(range_name: str) -> list[list[object]]:
    sheets = _build_sheets_client()
    response = sheets.spreadsheets().values().get(
        spreadsheetId=_sheet_id(),
        range=range_name,
        majorDimension="ROWS",
    ).execute()
    return response.get("values", [])


def obtener_opciones_lavados() -> dict[str, list[str]]:
    _ensure_configured()

    now = time.time()
    cached = _OPTIONS_CACHE.get("data")
    if cached and now - float(_OPTIONS_CACHE.get("ts") or 0) < _OPTIONS_TTL_SECONDS:
        return cached  # type: ignore[return-value]

    bbdd_rows = _values_get("BBDD!A2:J")
    datos_rows = _values_get("Datos!A2:A")

    patentes = []
    seen_patentes: set[str] = set()
    for row in bbdd_rows:
        patente = _safe_text(row[0] if len(row) > 0 else "")
        ultimo_servicio = _safe_text(row[9] if len(row) > 9 else "")
        if patente and not ultimo_servicio and patente not in seen_patentes:
            seen_patentes.add(patente)
            patentes.append(patente)

    servicios = []
    seen_servicios: set[str] = set()
    for row in datos_rows:
        servicio = _safe_text(row[0] if row else "")
        if servicio and servicio not in seen_servicios:
            seen_servicios.add(servicio)
            servicios.append(servicio)

    data = {
        "patentes": patentes,
        "servicios": sorted(servicios, key=str.casefold),
    }
    _OPTIONS_CACHE["ts"] = now
    _OPTIONS_CACHE["data"] = data
    return data


def _invalidate_options_cache() -> None:
    _OPTIONS_CACHE["ts"] = 0.0
    _OPTIONS_CACHE["data"] = None


def _now_santiago() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone or "America/Santiago"))


def _public_drive_file_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def _image_embed_uri(file_id: str) -> str:
    return f"https://drive.google.com/uc?export=view&id={file_id}"


def _get_patente_folder(drive, patente: str) -> str:
    imagenes_id = _find_or_create_folder(drive, _root_folder_id(), "Imagenes")
    return _find_or_create_folder(drive, imagenes_id, _clean_filename(patente or "SIN_PATENTE", "SIN_PATENTE"))


def _upload_image(data_uri: str, patente: str, name: str) -> dict[str, str]:
    if not _safe_text(data_uri):
        return {"url": "", "embed_uri": "", "id": ""}

    drive, _docs = _build_clients()
    folder_id = _get_patente_folder(drive, patente)
    payload, mime_type, ext = _decode_data_uri(data_uri)
    file_name = _clean_filename(name, "imagen") + ext
    uploaded = _upload_bytes(drive, folder_id, file_name, payload, mime_type)
    file_id = uploaded["id"]
    _set_public_read(drive, file_id)
    return {
        "id": file_id,
        "url": uploaded.get("webViewLink") or _public_drive_file_url(file_id),
        "embed_uri": _image_embed_uri(file_id),
    }


def _next_registro_id() -> int:
    rows = _values_get("Registro!A2:A")
    ids: list[int] = []
    for row in rows:
        value = _safe_text(row[0] if row else "")
        try:
            ids.append(int(float(value)))
        except ValueError:
            continue
    return max(ids) + 1 if ids else 1


def _append_registro(row: list[object]) -> int:
    sheets = _build_sheets_client()
    response = sheets.spreadsheets().values().append(
        spreadsheetId=_sheet_id(),
        range="Registro!A:M",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        includeValuesInResponse=False,
        body={"values": [row]},
    ).execute()
    updated_range = _safe_text(response.get("updates", {}).get("updatedRange"))
    match = re.search(r"!A(\d+):", updated_range)
    if not match:
        match = re.search(r"!(?:Registro!)?A?(\d+)", updated_range)
    return int(match.group(1)) if match else 0


def _actualizar_bbdd(patente: str, servicio: str) -> None:
    rows = _values_get("BBDD!A2:J")
    target_row = 0
    for idx, row in enumerate(rows, start=2):
        if _safe_text(row[0] if row else "") == patente:
            target_row = idx
            break
    if not target_row:
        return

    sheets = _build_sheets_client()
    sheets.spreadsheets().values().update(
        spreadsheetId=_sheet_id(),
        range=f"BBDD!J{target_row}",
        valueInputOption="USER_ENTERED",
        body={"values": [[servicio]]},
    ).execute()


def guardar_registro_lavado(data: dict[str, object]) -> dict[str, object]:
    _ensure_configured()

    patente = _safe_text(data.get("patente"))
    fecha = _safe_text(data.get("fecha"))
    servicio = _safe_text(data.get("servicio"))
    kilometraje = _safe_text(data.get("kilometraje"))
    observaciones = _safe_text(data.get("observaciones"))

    if not patente:
        raise LavadosServiceError("Falta patente.")
    if not fecha:
        raise LavadosServiceError("Falta fecha de servicio.")
    if not servicio:
        raise LavadosServiceError("Debes seleccionar al menos un servicio.")
    if not kilometraje:
        raise LavadosServiceError("Falta kilometraje.")

    timestamp = int(time.time() * 1000)
    image_payloads = {
        "antes1": ("Antes_1_" + str(timestamp), _safe_text(data.get("imgAntes1"))),
        "antes2": ("Antes_2_" + str(timestamp), _safe_text(data.get("imgAntes2"))),
        "despues1": ("Despues_1_" + str(timestamp), _safe_text(data.get("imgDespues1"))),
        "despues2": ("Despues_2_" + str(timestamp), _safe_text(data.get("imgDespues2"))),
    }
    urls = {
        key: {"url": "Imagen pendiente" if image_payloads[key][1] else "", "embed_uri": "", "id": ""}
        for key in _IMAGE_KEYS
    }

    with _REGISTER_LOCK:
        new_id = _next_registro_id()
        row = [
            new_id,
            _now_santiago().strftime("%Y-%m-%d %H:%M:%S"),
            patente,
            fecha,
            servicio,
            kilometraje,
            urls["antes1"]["url"],
            urls["antes2"]["url"],
            urls["despues1"]["url"],
            urls["despues2"]["url"],
            "Carpeta pendiente",
            observaciones,
            "PDF pendiente",
        ]
        sheet_row = _append_registro(row)
        _actualizar_bbdd(patente, servicio)
        _invalidate_options_cache()

    return {
        "ok": True,
        "id": new_id,
        "fila": sheet_row,
        "patente": patente,
        "fecha": fecha,
        "servicio": servicio,
        "kilometraje": kilometraje,
        "observaciones": observaciones,
        "urls": urls,
        "image_payloads": image_payloads,
        "mensaje": f"Registro guardado correctamente con ID: {new_id}. Las imagenes y el informe se procesaran automaticamente.",
    }


def _update_registro_links(sheet_row: int, urls: dict[str, dict[str, str]], folder_url: str) -> None:
    if sheet_row <= 0:
        return
    sheets = _build_sheets_client()
    sheets.spreadsheets().values().update(
        spreadsheetId=_sheet_id(),
        range=f"Registro!G{sheet_row}:K{sheet_row}",
        valueInputOption="USER_ENTERED",
        body={
            "values": [[
                urls.get("antes1", {}).get("url", ""),
                urls.get("antes2", {}).get("url", ""),
                urls.get("despues1", {}).get("url", ""),
                urls.get("despues2", {}).get("url", ""),
                folder_url,
            ]]
        },
    ).execute()


def _update_pdf_url(sheet_row: int, value: str) -> None:
    if sheet_row <= 0:
        return
    sheets = _build_sheets_client()
    sheets.spreadsheets().values().update(
        spreadsheetId=_sheet_id(),
        range=f"Registro!M{sheet_row}",
        valueInputOption="USER_ENTERED",
        body={"values": [[value]]},
    ).execute()


def _upload_images_for_record(registro: dict[str, object]) -> tuple[dict[str, dict[str, str]], str]:
    patente = _safe_text(registro.get("patente")) or "SIN_PATENTE"
    image_payloads = registro.get("image_payloads") if isinstance(registro.get("image_payloads"), dict) else {}
    urls = {key: {"url": "", "embed_uri": "", "id": ""} for key in _IMAGE_KEYS}

    futures = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for key in _IMAGE_KEYS:
            job = image_payloads.get(key) if isinstance(image_payloads, dict) else None
            if not isinstance(job, tuple) and not isinstance(job, list):
                continue
            name = _safe_text(job[0] if len(job) > 0 else "")
            payload = _safe_text(job[1] if len(job) > 1 else "")
            if payload:
                futures[pool.submit(_upload_image, payload, patente, name)] = key

        for future in as_completed(futures):
            urls[futures[future]] = future.result()

    drive, _docs = _build_clients()
    folder_id = _get_patente_folder(drive, patente)
    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
    return urls, folder_url


def generar_pdf_lavado(registro: dict[str, object]) -> str:
    _ensure_configured()

    drive, docs = _build_clients()
    patente = _safe_text(registro.get("patente")) or "SIN_PATENTE"
    folder_id = _get_patente_folder(drive, patente)
    registro_id = int(registro.get("id") or 0)

    doc_id = _copy_template(drive, _template_id(), folder_id, f"Informe_{registro_id}")
    fecha_registro = _now_santiago().strftime("%d-%m-%Y")
    urls = registro.get("urls") if isinstance(registro.get("urls"), dict) else {}

    try:
        _replace_text(
            docs,
            doc_id,
            {
                "{{ID}}": str(registro_id),
                "{{Fecha Registro}}": fecha_registro,
                "{{Patente}}": patente,
                "{{Fecha Servicio}}": _safe_text(registro.get("fecha")) or "Sin fecha",
                "{{Servicio}}": _safe_text(registro.get("servicio")) or "Sin servicio",
                "{{Kilometraje}}": _safe_text(registro.get("kilometraje")) or "0",
                "{{Observaciones}}": _safe_text(registro.get("observaciones")) or "Sin observaciones",
            },
        )
        _insert_images_on_placeholders(
            docs,
            doc_id,
            {
                "{{Imagen Antes 1}}": _safe_text(urls.get("antes1", {}).get("embed_uri") if urls else ""),
                "{{Imagen Antes 2}}": _safe_text(urls.get("antes2", {}).get("embed_uri") if urls else ""),
                "{{Imagen Despues 1}}": _safe_text(urls.get("despues1", {}).get("embed_uri") if urls else ""),
                "{{Imagen Despues 2}}": _safe_text(urls.get("despues2", {}).get("embed_uri") if urls else ""),
            },
        )
        pdf_bytes = _export_doc_pdf(drive, doc_id)
        pdf_file = _upload_bytes(drive, folder_id, f"Informe_{registro_id}.pdf", pdf_bytes, "application/pdf")
        pdf_id = pdf_file["id"]
        _set_public_read(drive, pdf_id)
        return pdf_file.get("webViewLink") or _public_drive_file_url(pdf_id)
    finally:
        try:
            drive.files().update(fileId=doc_id, body={"trashed": True}, fields="id").execute()
        except HttpError:
            pass


def procesar_registro_lavado_background(registro: dict[str, object]) -> None:
    sheet_row = int(registro.get("fila") or 0)
    try:
        urls, folder_url = _upload_images_for_record(registro)
        _update_registro_links(sheet_row, urls, folder_url)
        registro["urls"] = urls
        pdf_url = generar_pdf_lavado(registro)
        _update_pdf_url(sheet_row, pdf_url)
    except Exception as exc:
        _update_pdf_url(sheet_row, f"ERROR PDF: {exc}")
