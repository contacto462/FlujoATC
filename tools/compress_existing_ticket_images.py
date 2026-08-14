from __future__ import annotations

import argparse
import mimetypes
import shutil
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ATC.app.core.image_optimizer import optimize_image_bytes

UPLOADS_ROOT = ROOT / "ATC" / "uploads"
LEGACY_UPLOADS_ROOT = ROOT / "uploads"
BACKUP_ROOT = UPLOADS_ROOT / ".image_optimizer_backups"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _is_inside_backup(path: Path) -> bool:
    return any(part == ".image_optimizer_backups" for part in path.parts)


def _iter_images_under(root: Path, *, recursive: bool) -> list[Path]:
    if not root.exists():
        return []
    iterator = root.rglob("*") if recursive else root.iterdir()
    return [
        path
        for path in iterator
        if path.is_file()
        and path.suffix.lower() in IMAGE_SUFFIXES
        and not _is_inside_backup(path)
    ]


def iter_images(scope: str) -> list[Path]:
    paths: list[Path] = []

    if scope == "ticketera":
        ticket_replies = UPLOADS_ROOT / "ticket_replies"
        paths.extend(_iter_images_under(ticket_replies, recursive=True))

        # Historically, inbound email inline images were stored directly in
        # ATC/uploads. Do not recurse into ODT/rendiciones/cierres folders here.
        paths.extend(_iter_images_under(UPLOADS_ROOT, recursive=False))
    elif scope == "atc-uploads":
        paths.extend(_iter_images_under(UPLOADS_ROOT, recursive=True))
    elif scope == "legacy-uploads":
        paths.extend(_iter_images_under(LEGACY_UPLOADS_ROOT, recursive=True))
    elif scope == "all":
        paths.extend(_iter_images_under(UPLOADS_ROOT, recursive=True))
        paths.extend(_iter_images_under(LEGACY_UPLOADS_ROOT, recursive=True))
    else:
        raise ValueError(f"scope invalido: {scope}")

    return sorted(set(paths))


def optimize_path(
    path: Path,
    *,
    dry_run: bool,
    backup_session: Path,
    jpeg_quality: int | None,
    webp_quality: int | None,
) -> tuple[int, int, bool]:
    original = path.read_bytes()
    content_type = mimetypes.guess_type(path.name)[0] or ""
    optimized = optimize_image_bytes(
        original,
        content_type=content_type,
        jpeg_quality=jpeg_quality,
        webp_quality=webp_quality,
    )
    original_size = len(original)
    optimized_size = len(optimized)

    if optimized_size >= original_size:
        return original_size, original_size, False

    if dry_run:
        return original_size, optimized_size, True

    relative = path.relative_to(UPLOADS_ROOT)
    backup_path = backup_session / relative
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)

    tmp_path = path.with_name(f".{path.name}.optimized_tmp")
    tmp_path.write_bytes(optimized)
    tmp_path.replace(path)
    return original_size, optimized_size, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="replace files after creating backups")
    parser.add_argument(
        "--scope",
        choices=("ticketera", "atc-uploads", "legacy-uploads", "all"),
        default="ticketera",
    )
    parser.add_argument("--jpeg-quality", type=int, default=None)
    parser.add_argument("--webp-quality", type=int, default=None)
    args = parser.parse_args()

    dry_run = not args.apply
    backup_session = BACKUP_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")

    total = 0
    optimized_count = 0
    original_total = 0
    final_total = 0

    for path in iter_images(args.scope):
        total += 1
        original_size, final_size, changed = optimize_path(
            path,
            dry_run=dry_run,
            backup_session=backup_session,
            jpeg_quality=args.jpeg_quality,
            webp_quality=args.webp_quality,
        )
        original_total += original_size
        final_total += final_size
        if changed:
            optimized_count += 1

    saved = original_total - final_total
    mode = "DRY_RUN" if dry_run else "APPLIED"
    print(f"mode={mode}")
    print(f"scanned={total}")
    print(f"optimizable={optimized_count}")
    print(f"before_bytes={original_total}")
    print(f"after_bytes={final_total}")
    print(f"saved_bytes={saved}")
    if not dry_run and optimized_count:
        print(f"backup={backup_session}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
