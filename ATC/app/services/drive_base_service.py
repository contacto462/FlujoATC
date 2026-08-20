"""Utilidades compartidas de Google Drive/Docs entre drive_report_service e incidencias_drive_report_service."""
from __future__ import annotations

import base64
import io
import mimetypes
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import requests
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from ATC.app.core.config import settings


_ATC_ROOT = Path(__file__).resolve().parents[2]

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]


class DriveReportError(RuntimeError):
    pass


def _safe_text(value: object) -> str:
    return str(value or "").strip()


def _clean_filename(value: str, fallback: str = "archivo") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", _safe_text(value))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or fallback


def _candidate_paths(configured: str, fallback: Path) -> list[Path]:
    candidates: list[Path] = []
    if configured:
        configured_path = Path(configured)
        if configured_path.is_absolute():
            candidates.append(configured_path)
        else:
            candidates.extend([
                Path.cwd() / configured_path,
                _ATC_ROOT / configured_path,
            ])

    candidates.extend([
        Path.cwd() / fallback,
        _ATC_ROOT / fallback,
    ])

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _first_existing_file(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _load_service_account_path() -> Path:
    configured = _safe_text(settings.google_service_account_file)
    existing = _first_existing_file(_candidate_paths(configured, Path("secrets") / "gdrive_service_account.json"))
    if existing:
        return existing

    desktop = Path.home() / "Desktop"
    desktop_candidates: list[Path] = []
    if desktop.exists():
        patterns = ("*service*.json", "*credential*.json", "*google*.json")
        for pattern in patterns:
            desktop_candidates.extend(desktop.glob(pattern))
        desktop_candidates = [p for p in desktop_candidates if p.is_file()]
        seen: set[str] = set()
        unique_candidates: list[Path] = []
        for item in desktop_candidates:
            key = str(item.resolve())
            if key in seen:
                continue
            seen.add(key)
            unique_candidates.append(item)
        desktop_candidates = unique_candidates

    if len(desktop_candidates) == 1:
        return desktop_candidates[0]

    raise DriveReportError(
        "No se encontro el JSON de Service Account. Define GOOGLE_SERVICE_ACCOUNT_FILE o usa secrets/gdrive_service_account.json"
    )


def _load_oauth_client_secret_path() -> Path:
    configured = _safe_text(settings.google_oauth_client_secret_file)
    existing = _first_existing_file(_candidate_paths(configured, Path("secrets") / "google_oauth_client_secret.json"))
    if existing:
        return existing
    raise DriveReportError(
        "No se encontro OAuth client secret. Define GOOGLE_OAUTH_CLIENT_SECRET_FILE o usa secrets/google_oauth_client_secret.json"
    )


def _load_oauth_token_path() -> Path:
    configured = _safe_text(settings.google_oauth_token_file)
    existing = _first_existing_file(_candidate_paths(configured, Path("secrets") / "google_oauth_token.json"))
    if existing:
        return existing

    configured_path = Path(configured) if configured else Path("secrets") / "google_oauth_token.json"
    if configured_path.is_absolute():
        return configured_path
    return _ATC_ROOT / configured_path


def _load_oauth_credentials() -> UserCredentials:
    token_path = _load_oauth_token_path()
    _load_oauth_client_secret_path()

    creds: UserCredentials | None = None
    if token_path.exists():
        creds = UserCredentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    raise DriveReportError(
        "OAuth token invalido o ausente. Ejecuta scripts/google_oauth_setup.py para autorizar tu cuenta."
    )


@lru_cache(maxsize=1)
def _build_google_credentials():
    auth_mode = _safe_text(settings.google_drive_auth_mode).lower() or "service_account"
    if auth_mode == "oauth_user":
        return _load_oauth_credentials()

    creds_path = _load_service_account_path()
    return service_account.Credentials.from_service_account_file(str(creds_path), scopes=SCOPES)


def _build_clients():
    # IMPORTANTE: no cachear (ni compartir entre threads) el objeto http/
    # AuthorizedHttp resultante. httplib2.Http no es thread-safe — mantiene
    # un pool de conexiones interno — y este proceso hace llamadas Drive
    # concurrentes desde threads de background (_generar_drive_para_cierre)
    # y desde requests simultaneos (subida sincronica de fotos, proxy de
    # imagenes). Compartir una sola instancia via @lru_cache causaba
    # corrupcion de memoria nativa (crashes 0xc0000005/0xc0000374 del
    # proceso python.exe bajo carga, ago 2026). Construir un cliente nuevo
    # por llamada es barato (no hace I/O) y elimina el estado compartido.
    #
    # Sin timeout explicito, ademas, una conexion colgada contra la API de
    # Drive (throttling silencioso, corte de red, etc.) deja el thread del
    # server esperando para siempre.
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp

    creds = _build_google_credentials()
    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=30))
    drive = build("drive", "v3", http=http, cache_discovery=False)
    docs = build("docs", "v1", http=http, cache_discovery=False)
    return drive, docs


@lru_cache(maxsize=1)
def _build_sheets_client():
    creds_path = _load_service_account_path()
    creds = service_account.Credentials.from_service_account_file(str(creds_path), scopes=SHEETS_SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


_FOLDER_CACHE: dict[tuple[str, str], str] = {}


def _ensure_enabled() -> None:
    if not settings.google_drive_enabled:
        raise DriveReportError("GOOGLE_DRIVE_ENABLED=false")
    if not _safe_text(settings.google_drive_root_folder_id):
        raise DriveReportError("Falta GOOGLE_DRIVE_ROOT_FOLDER_ID")
    if not _safe_text(settings.google_doc_template_id):
        raise DriveReportError("Falta GOOGLE_DOC_TEMPLATE_ID")


def _find_or_create_folder(drive, parent_id: str, folder_name: str) -> str:
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
    if files:
        folder_id = files[0]["id"]
        _FOLDER_CACHE[cache_key] = folder_id
        return folder_id

    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    created = drive.files().create(body=metadata, fields="id", supportsAllDrives=True).execute()
    folder_id = created["id"]
    _FOLDER_CACHE[cache_key] = folder_id
    return folder_id


def _guess_mime_and_ext(path_or_name: str, default_mime: str = "application/octet-stream") -> tuple[str, str]:
    mime, _ = mimetypes.guess_type(path_or_name)
    safe_mime = mime or default_mime
    ext = mimetypes.guess_extension(safe_mime) or ".bin"
    if ext == ".jpe":
        ext = ".jpg"
    return safe_mime, ext


def _decode_data_uri(data_uri: str) -> tuple[bytes, str, str]:
    match = re.match(r"^data:([^;]+);base64,(.+)$", data_uri, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        raise DriveReportError("Formato data URI invalido")
    mime_type = match.group(1).strip().lower()
    encoded = match.group(2).strip()
    content = base64.b64decode(encoded)
    _, ext = _guess_mime_and_ext(f"file.{mime_type.split('/')[-1]}", default_mime=mime_type)
    return content, mime_type, ext


def _read_image_source(source: str) -> tuple[bytes, str, str]:
    src = _safe_text(source)
    if not src:
        raise DriveReportError("Fuente de imagen vacia")

    if src.startswith("data:image/"):
        return _decode_data_uri(src)

    if src.startswith("/uploads/"):
        local_path = _ATC_ROOT / src.lstrip("/")
        if not local_path.exists():
            raise DriveReportError(f"No se encontro archivo local: {local_path}")
        mime_type, ext = _guess_mime_and_ext(local_path.name, default_mime="image/jpeg")
        return local_path.read_bytes(), mime_type, ext

    if src.startswith("http://") or src.startswith("https://"):
        response = requests.get(src, timeout=30)
        response.raise_for_status()
        mime_type = _safe_text(response.headers.get("content-type")).split(";")[0] or "image/jpeg"
        _, ext = _guess_mime_and_ext(f"file.{mime_type.split('/')[-1]}", default_mime=mime_type)
        return response.content, mime_type, ext

    local_path = Path(src)
    if not local_path.is_absolute():
        local_path = Path.cwd() / local_path
    if not local_path.exists():
        raise DriveReportError(f"No se reconoce fuente de imagen: {src}")
    mime_type, ext = _guess_mime_and_ext(local_path.name, default_mime="image/jpeg")
    return local_path.read_bytes(), mime_type, ext


def _upload_bytes(drive, parent_id: str, file_name: str, payload: bytes, mime_type: str) -> dict[str, str]:
    # La subida "simple" (resumable=False) de la API de Drive falla para
    # archivos mayores a 5MB. Los informes de cierre embeben fotos y superan
    # ese limite con facilidad (ej. ODT I12398: 5.9MB) — la subida fallaba
    # en silencio despues de que las fotos (mas chicas, bajo el limite) ya
    # se hubieran subido, dejando el informe sin subir y sin carpeta
    # registrada. resumable=True no tiene ese tope.
    media = MediaIoBaseUpload(io.BytesIO(payload), mimetype=mime_type, resumable=True)
    body = {"name": file_name, "parents": [parent_id]}
    request = drive.files().create(
        body=body,
        media_body=media,
        fields="id,name,webViewLink,webContentLink",
    )
    created = None
    while created is None:
        _status, created = request.next_chunk()
    return {
        "id": created["id"],
        "name": created.get("name", file_name),
        "webViewLink": created.get("webViewLink", ""),
        "webContentLink": created.get("webContentLink", ""),
    }


def _set_public_read(drive, file_id: str) -> None:
    try:
        drive.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
        ).execute()
    except HttpError:
        pass


def _copy_template(drive, template_id: str, folder_id: str, title: str) -> str:
    copied = drive.files().copy(
        fileId=template_id,
        body={"name": title, "parents": [folder_id]},
        fields="id",
    ).execute()
    return copied["id"]


def _replace_text(docs, doc_id: str, replacements: dict[str, str]) -> None:
    requests_payload = []
    for token, value in replacements.items():
        requests_payload.append(
            {
                "replaceAllText": {
                    "containsText": {"text": token, "matchCase": True},
                    "replaceText": _safe_text(value),
                }
            }
        )
    if requests_payload:
        docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests_payload}).execute()


def _iter_text_runs(content: list[dict[str, Any]]):
    for item in content:
        paragraph = item.get("paragraph")
        if paragraph:
            for element in paragraph.get("elements", []):
                text_run = element.get("textRun")
                if text_run and "content" in text_run:
                    yield text_run.get("content", ""), int(element.get("startIndex", 0))
        table = item.get("table")
        if table:
            for row in table.get("tableRows", []):
                for cell in row.get("tableCells", []):
                    yield from _iter_text_runs(cell.get("content", []))
        toc = item.get("tableOfContents")
        if toc:
            yield from _iter_text_runs(toc.get("content", []))


def _find_placeholder_range(document: dict[str, Any], token: str) -> tuple[int, int] | None:
    body = document.get("body", {})
    content = body.get("content", [])
    runs = list(_iter_text_runs(content))
    if not runs:
        return None

    # Google Docs puede partir un mismo placeholder en varios "text runs" si
    # el texto tiene un formato levemente distinto (negrita, color, celda de
    # tabla, etc.) aunque se vea como una sola palabra. Buscar run por run
    # (como antes) fallaba en silencio en ese caso: se reconstruye el texto
    # completo con un mapa de índice por caracter para poder ubicar el token
    # aunque cruce el límite entre runs.
    full_text_parts: list[str] = []
    index_map: list[int] = []
    for text_value, start_index in runs:
        full_text_parts.append(text_value)
        index_map.extend(start_index + offset for offset in range(len(text_value)))
    full_text = "".join(full_text_parts)

    idx = full_text.find(token)
    if idx < 0:
        return None
    token_start = index_map[idx]
    token_end = index_map[idx + len(token) - 1] + 1
    return token_start, token_end


def _insert_images_on_placeholders(docs, doc_id: str, token_to_uri: dict[str, str]) -> None:
    document = docs.documents().get(documentId=doc_id).execute()
    ranges: list[tuple[int, int, str]] = []
    for token, uri in token_to_uri.items():
        found = _find_placeholder_range(document, token)
        if found:
            ranges.append((found[0], found[1], uri))

    if not ranges:
        return

    ranges.sort(key=lambda item: item[0], reverse=True)
    requests_payload: list[dict[str, Any]] = []
    for start_idx, end_idx, image_uri in ranges:
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

    docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests_payload}).execute()


def _export_doc_pdf(drive, doc_id: str) -> bytes:
    request = drive.files().export_media(fileId=doc_id, mimeType="application/pdf")
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()


def _normalize_text_for_token(value: str) -> str:
    base = _safe_text(value)
    base = base.replace("{", "").replace("}", "")
    base = unicodedata.normalize("NFKD", base)
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    base = base.casefold()
    base = re.sub(r"\s+", " ", base).strip()
    return base


def _build_template_values(payload: dict[str, str]) -> dict[str, str]:
    return {
        "numero de odt": payload.get("odt", ""),
        "tipo de trabajo": payload.get("tipo_trabajo", ""),
        "rut cliente": payload.get("rut_cliente", ""),
        "nombre empresa odt": payload.get("cliente", ""),
        "fecha de cierre": payload.get("fecha_cierre", ""),
        "identificacion sucursal": payload.get("sucursal", ""),
        "direccion trabajos": payload.get("direccion", ""),
        "descripcion trabajo": payload.get("descripcion", ""),
        "trabajo realizado": payload.get("trabajo_realizado", ""),
        "nombre tecnico": payload.get("tecnico", ""),
    }


def _support_folder_name_from_odt(odt: str) -> str:
    raw = _safe_text(odt)
    if not raw:
        return "sin_odt"
    cleaned = re.sub(r"(?i)\bodt\b", "", raw).strip()
    cleaned = cleaned.replace(" ", "")
    if cleaned:
        return _clean_filename(cleaned, fallback="sin_odt")
    return _clean_filename(raw, fallback="sin_odt")
