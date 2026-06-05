from __future__ import annotations

import unicodedata

from ATC.app.models.user import User


_TECHNICAL_ONLY_DEPARTMENTS = {
    "tecnico",
    "tecnicos",
}


def _normalize_department(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").strip())
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return without_accents.casefold()


def _split_departments(value: str | None) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    normalized = raw.replace("|", ";").replace(",", ";")
    return [part.strip() for part in normalized.split(";") if part.strip()]


def can_access_bitacora(user: User | None) -> bool:
    if not user:
        return False

    departments = [_normalize_department(item) for item in _split_departments(getattr(user, "department", None))]
    if not departments:
        return True

    return any(item not in _TECHNICAL_ONLY_DEPARTMENTS for item in departments)
