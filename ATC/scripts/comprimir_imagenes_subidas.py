"""
Comprime a calidad 60 las imagenes ya subidas en Ticketera, Rendiciones y
Cierre de ODT (jpeg_quality/webp_quality=60 via optimize_image_bytes — el
mismo optimizador que ya se usa para las imagenes NUEVAS que entran, solo
que aca se corre retroactivamente sobre lo que ya estaba guardado).

Carpetas cubiertas (bajo ATC/uploads/):
  - .                (raiz: adjuntos/inline de Ticketera)
  - ticket_replies/  (adjuntos de respuestas de Ticketera)
  - rendiciones/     (comprobantes de Rendiciones)
  - cierres_odt/     (evidencia de Cierre de ODT)

Antes de tocar nada hace una copia de respaldo completa (misma estructura
de carpetas) en ATC/_backup_compresion_<fecha>/, fuera de uploads/ (para
no quedar servida por /uploads) y del repo git.

PNG se re-optimiza sin perdida (no aplica "calidad" a un formato lossless,
asi que baja poco). Fotos animadas/MPO se dejan intactas (mismo criterio
que el optimizador de subidas nuevas).

Ejecutar en el Windows Server:

    python ATC/scripts/comprimir_imagenes_subidas.py [--dry-run]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ATC.app.core.image_optimizer import optimize_image_bytes

ATC_ROOT = Path(__file__).resolve().parents[1]
UPLOADS_DIR = ATC_ROOT / "uploads"

CARPETAS = [
    ("Ticketera (raiz)", UPLOADS_DIR, False),
    ("Ticketera (ticket_replies)", UPLOADS_DIR / "ticket_replies", True),
    ("Rendiciones", UPLOADS_DIR / "rendiciones", True),
    ("Cierre de ODT", UPLOADS_DIR / "cierres_odt", True),
]

EXT_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _listar_imagenes(carpeta: Path, recursivo: bool) -> list[Path]:
    if not carpeta.exists():
        return []
    patron = "**/*" if recursivo else "*"
    return [
        p for p in carpeta.glob(patron)
        if p.is_file() and p.suffix.lower() in EXT_MIME
    ]


def _hacer_backup(archivos_por_carpeta: dict[str, list[Path]], destino: Path) -> None:
    print(f"Copiando respaldo a {destino} ...")
    for nombre, archivos in archivos_por_carpeta.items():
        for archivo in archivos:
            rel = archivo.relative_to(UPLOADS_DIR)
            dest_path = destino / rel
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archivo, dest_path)
    print("Respaldo listo.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="No escribe nada, solo muestra lo que haria")
    parser.add_argument("--skip-backup", action="store_true", help="Omite el respaldo (no recomendado)")
    args = parser.parse_args()

    archivos_por_carpeta: dict[str, list[Path]] = {}
    total_archivos = 0
    for nombre, carpeta, recursivo in CARPETAS:
        archivos = _listar_imagenes(carpeta, recursivo)
        archivos_por_carpeta[nombre] = archivos
        total_archivos += len(archivos)
        print(f"{nombre}: {len(archivos)} imagenes en {carpeta}")

    print(f"\nTotal a procesar: {total_archivos}\n")

    if not args.dry_run and not args.skip_backup:
        backup_dir = ATC_ROOT / f"_backup_compresion_{date.today().isoformat()}"
        if backup_dir.exists():
            print(f"Ya existe un respaldo de hoy en {backup_dir}, no se vuelve a copiar.")
        else:
            _hacer_backup(archivos_por_carpeta, backup_dir)

    ok = 0
    sin_cambio = 0
    errores: list[tuple[str, str]] = []
    total_antes = 0
    total_despues = 0
    inicio = time.time()
    i = 0

    for nombre, archivos in archivos_por_carpeta.items():
        for archivo in archivos:
            i += 1
            try:
                data = archivo.read_bytes()
                mime = EXT_MIME[archivo.suffix.lower()]
                comprimida = optimize_image_bytes(data, content_type=mime, jpeg_quality=60, webp_quality=60)
                total_antes += len(data)
                total_despues += len(comprimida)
                if len(comprimida) < len(data):
                    if not args.dry_run:
                        archivo.write_bytes(comprimida)
                    ok += 1
                else:
                    sin_cambio += 1
                if i % 100 == 0:
                    print(f"[{i}/{total_archivos}] procesadas... ({ok} reducidas, {sin_cambio} sin cambio, {len(errores)} errores)")
            except Exception as exc:
                errores.append((str(archivo), str(exc)))

    duracion = time.time() - inicio
    ahorro_mb = (total_antes - total_despues) / (1024 * 1024)
    print()
    print(f"Listo en {duracion:.0f}s. Reducidas={ok} SinCambio={sin_cambio} Errores={len(errores)}")
    print(f"Total antes: {total_antes/1024/1024:.1f} MB -> despues: {total_despues/1024/1024:.1f} MB (ahorro {ahorro_mb:.1f} MB, {(1 - total_despues/total_antes)*100:.1f}%)" if total_antes else "Sin datos")
    if errores:
        print("Errores:")
        for path, msg in errores[:30]:
            print(f"  {path}: {msg}")


if __name__ == "__main__":
    main()
