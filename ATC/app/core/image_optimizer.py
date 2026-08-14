from __future__ import annotations

import io

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except Exception:  # pragma: no cover - production keeps the original image if PIL is unavailable.
    Image = None
    ImageOps = None

    class UnidentifiedImageError(Exception):
        pass


_MIN_BYTES_TO_TRY = 128 * 1024
_MIN_SAVINGS_BYTES = 8 * 1024
_MIN_SAVINGS_RATIO = 0.03


def optimize_image_bytes(
    data: bytes,
    *,
    content_type: str | None = None,
    jpeg_quality: int | None = None,
    webp_quality: int | None = None,
) -> bytes:
    """Return a smaller image when Pillow can do it without resizing.

    The optimizer is intentionally conservative for ticket evidence:
    dimensions are preserved, animated images are left untouched, and the
    optimized bytes are used only when they are meaningfully smaller, except
    when a JPEG carries EXIF orientation: in that case the pixels are
    normalized so browsers and direct file previews do not show it sideways.
    """
    if not data or Image is None or ImageOps is None:
        return data

    normalized_type = (content_type or "").strip().lower()
    if normalized_type in {"image/svg+xml", "image/gif"}:
        return data

    try:
        with Image.open(io.BytesIO(data)) as img:
            image_format = (img.format or "").upper()
            if getattr(img, "is_animated", False):
                return data

            if normalized_type == "image/jpeg" or image_format == "JPEG":
                return _optimize_jpeg(data, img, quality=jpeg_quality)
            if len(data) < _MIN_BYTES_TO_TRY:
                return data
            if normalized_type == "image/png" or image_format == "PNG":
                return _save_if_smaller(data, img, "PNG", optimize=True, compress_level=9)
            if normalized_type == "image/webp" or image_format == "WEBP":
                if webp_quality is None:
                    return _save_if_smaller(data, img, "WEBP", lossless=True, method=6)
                quality = max(60, min(95, int(webp_quality)))
                return _save_if_smaller(data, img, "WEBP", quality=quality, method=6)
    except (OSError, ValueError, UnidentifiedImageError):
        return data

    return data


def _optimize_jpeg(original: bytes, img, *, quality: int | None = None) -> bytes:
    try:
        orientation = int(img.getexif().get(274, 1) or 1)
    except Exception:
        orientation = 1
    orientation_changed = orientation not in {1}
    image = ImageOps.exif_transpose(img)
    if image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")

    if quality is not None:
        quality = max(60, min(95, int(quality)))
        try:
            return _save_if_smaller(
                original,
                image,
                "JPEG",
                quality=quality,
                subsampling="keep",
                optimize=True,
                progressive=True,
                force=orientation_changed,
            )
        except (OSError, ValueError):
            return _save_if_smaller(
                original,
                image,
                "JPEG",
                quality=quality,
                optimize=True,
                progressive=True,
                force=orientation_changed,
            )

    # quality/subsampling="keep" keeps the original JPEG quantization tables
    # where Pillow supports it. If Pillow cannot keep them, prefer the original
    # file over a forced lower-quality re-encode.
    try:
        return _save_if_smaller(
            original,
            image,
            "JPEG",
            quality="keep",
            subsampling="keep",
            optimize=True,
            progressive=True,
            force=orientation_changed,
        )
    except (OSError, ValueError):
        if not orientation_changed:
            return original
        return _save_if_smaller(
            original,
            image,
            "JPEG",
            quality=95,
            optimize=True,
            progressive=True,
            force=True,
        )


def _save_if_smaller(original: bytes, img, image_format: str, force: bool = False, **save_kwargs) -> bytes:
    out = io.BytesIO()
    img.save(out, format=image_format, **save_kwargs)
    optimized = out.getvalue()
    if not optimized:
        return original
    if force:
        return optimized

    saved_bytes = len(original) - len(optimized)
    if saved_bytes < _MIN_SAVINGS_BYTES:
        return original
    if len(optimized) > len(original) * (1 - _MIN_SAVINGS_RATIO):
        return original
    return optimized
