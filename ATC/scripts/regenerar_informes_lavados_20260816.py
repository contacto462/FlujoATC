"""
Regenera los informes PDF de lavados generados el 2026-08-16 (columna B de la
hoja Registro, fecha de registro) cuyos PDFs quedaron con placeholders de
imagen sin reemplazar ({{Imagen Antes 1}}, etc.) por un bug en
_find_placeholder_range (ver drive_base_service.py) ya corregido.

No vuelve a subir imagenes a Drive: reconstruye el embed_uri a partir del
file_id ya presente en las URLs guardadas en las columnas G-J de la fila
(Antes 1, Antes 2, Despues 1, Despues 2), y solo regenera el documento/PDF.
"""
from __future__ import annotations

import re
import sys

sys.path = [p for p in sys.path if "PROYECTO-ATC-SERVIDOR" not in p]

from ATC.app.services.lavados_service import (  # noqa: E402
    _values_get,
    _image_embed_uri,
    generar_pdf_lavado,
    _update_pdf_url,
)

TARGET_PREFIX = "2026-08-16"
_FILE_ID_RE = re.compile(r"/d/([-\w]+)")


def _extract_file_id(url: str) -> str:
    url = (url or "").strip()
    match = _FILE_ID_RE.search(url)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([-\w]+)", url)
    return match.group(1) if match else ""


def main() -> None:
    rows = _values_get("Registro!A2:M")
    targets = []
    for idx, row in enumerate(rows, start=2):
        fecha_registro = str(row[1]).strip() if len(row) > 1 else ""
        if fecha_registro.startswith(TARGET_PREFIX):
            targets.append((idx, row))

    print(f"Filas encontradas del {TARGET_PREFIX}: {len(targets)}")

    for sheet_row, row in targets:
        row = row + [""] * (13 - len(row))
        registro_id, _fecha_reg, patente, fecha_servicio, servicio, kilometraje = row[0:6]
        url_antes1, url_antes2, url_despues1, url_despues2 = row[6:10]
        observaciones = row[11]
        pdf_url_actual = row[12]

        urls = {}
        for key, url in (
            ("antes1", url_antes1),
            ("antes2", url_antes2),
            ("despues1", url_despues1),
            ("despues2", url_despues2),
        ):
            file_id = _extract_file_id(str(url))
            urls[key] = {"embed_uri": _image_embed_uri(file_id) if file_id else ""}

        registro = {
            "id": registro_id,
            "patente": patente,
            "fecha": fecha_servicio,
            "servicio": servicio,
            "kilometraje": kilometraje,
            "observaciones": observaciones,
            "urls": urls,
        }

        faltantes = [k for k, v in urls.items() if not v["embed_uri"]]
        print(f"--- fila {sheet_row} id={registro_id} patente={patente} pdf_actual={pdf_url_actual}")
        if faltantes:
            print(f"    AVISO: sin embed_uri para {faltantes} (url vacia o 'Imagen pendiente')")

        try:
            nuevo_pdf_url = generar_pdf_lavado(registro)
        except Exception as exc:  # noqa: BLE001
            print(f"    ERROR regenerando id={registro_id}: {exc}")
            continue

        _update_pdf_url(sheet_row, nuevo_pdf_url)
        print(f"    OK nuevo_pdf_url={nuevo_pdf_url}")


if __name__ == "__main__":
    main()
