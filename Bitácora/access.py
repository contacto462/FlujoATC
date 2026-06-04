from __future__ import annotations

from ATC.app.models.user import User


_TECHNICAL_ONLY_DEPARTMENTS = {
    "tecnico",
    "tecnico",
    "técnico",
    "tecnicos",
    "técnicos",
}


def _split_departments(value: str | None) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    normalized = raw.replace("|", ";").replace(",", ";")
    return [part.strip() for part in normalized.split(";") if part.strip()]


def can_access_bitacora(user: User | None) -> bool:
    if not user:
        return False
    if getattr(user, "is_admin", False):
        return True
    departments = [item.casefold() for item in _split_departments(getattr(user, "department", None))]
    if not departments:
        return True
    return any(item not in _TECHNICAL_ONLY_DEPARTMENTS for item in departments)
