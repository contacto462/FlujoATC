from __future__ import annotations

import unicodedata

from ATC.app.models.user import User

def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").strip())
    return "".join(c for c in normalized if not unicodedata.combining(c)).casefold()


def _split_departments(value: str | None) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.replace("|", ";").replace(",", ";").split(";") if p.strip()]


_ALLOWED_DEPARTMENTS = {"bitacora", "televigilante"}


def can_access_bitacora(user: User | None) -> bool:
    """Solo tienen acceso a Bitacora: admin/superadmin (siempre), o usuarios
    que tengan literalmente 'Bitacora' o 'Televigilante' entre sus
    departamentos. Tener otras areas (Tecnicos, RRHH, Guardia, etc.) sin
    ninguno de esos dos no da acceso — antes bastaba con NO estar en una
    lista de exclusiones, lo que dejaba entrar a areas que nunca debieron
    tener acceso."""
    if not user:
        return False
    role = str(getattr(user, "role", None) or "").strip().lower()
    if role in ("admin", "superadmin"):
        return True
    departments = [_normalize(d) for d in _split_departments(getattr(user, "department", None))]
    return any(d in _ALLOWED_DEPARTMENTS for d in departments)


def is_bitacora_admin(user: User | None) -> bool:
    if not user:
        return False
    # role='admin' o 'superadmin' → admin de bitácora
    role = str(getattr(user, "role", None) or "").strip().lower()
    if role in ("admin", "superadmin"):
        return True
    dept = _normalize(str(getattr(user, "department", None) or ""))
    return dept == "administrador"


def can_manage_bitacora_puestos(user: User | None) -> bool:
    if not user:
        return False
    departments = [_normalize(d) for d in _split_departments(getattr(user, "department", None))]
    return any("soporte" in department for department in departments)
