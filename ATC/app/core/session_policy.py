from __future__ import annotations

from datetime import datetime, timedelta

# Usuarios cuya sesion nunca debe cerrarse (paneles de uso permanente, p.ej.
# pantallas fijas que quedan logueadas de forma indefinida). Id 371 = "Panel -2".
USUARIOS_SESION_PERMANENTE: set[int] = {371}

# ~20 anios: no es literalmente infinito, pero en la practica nunca expira.
_SESION_PERMANENTE_DIAS = 365 * 20


def es_sesion_permanente(user_id: int | None) -> bool:
    return user_id is not None and int(user_id) in USUARIOS_SESION_PERMANENTE


def expiracion_sesion(user_id: int | None, expiracion_normal: datetime) -> datetime:
    """Devuelve expiracion_normal, salvo que el usuario tenga sesion permanente,
    en cuyo caso extiende el vencimiento ~20 anios hacia adelante (preserva si
    expiracion_normal es naive o aware)."""
    if es_sesion_permanente(user_id):
        return expiracion_normal + timedelta(days=_SESION_PERMANENTE_DIAS)
    return expiracion_normal


def max_age_cookie_segundos(user_id: int | None, max_age_normal: int) -> int:
    if es_sesion_permanente(user_id):
        return 60 * 60 * 24 * _SESION_PERMANENTE_DIAS
    return max_age_normal
