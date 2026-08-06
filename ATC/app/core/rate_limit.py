from __future__ import annotations

"""Rate limiting de login en memoria de proceso — la app corre como un unico
proceso uvicorn (run_server.py, ver CLAUDE.md), asi que un dict en memoria es
suficiente y no requiere Redis. Bloquea por identificador de cuenta (usuario o
RUT), no por IP: eso es lo que realmente frena fuerza bruta contra una cuenta
puntual sin importar desde donde ataque (hallazgo de auditoria de seguridad,
ago 2026 — ningun endpoint de login tenia limite de intentos)."""

import threading
import time
import unicodedata

_LOCK = threading.Lock()
_FAILURES: dict[str, list[float]] = {}

MAX_ATTEMPTS = 8
WINDOW_SECONDS = 10 * 60


def _normalize_key(identifier: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(identifier or "").strip())
    return "".join(c for c in normalized if not unicodedata.combining(c)).casefold()


def is_locked_out(identifier: str, *, max_attempts: int = MAX_ATTEMPTS, window_seconds: int = WINDOW_SECONDS) -> bool:
    key = _normalize_key(identifier)
    if not key:
        return False
    now = time.time()
    with _LOCK:
        attempts = [t for t in _FAILURES.get(key, []) if now - t < window_seconds]
        _FAILURES[key] = attempts
        return len(attempts) >= max_attempts


def record_failure(identifier: str) -> None:
    key = _normalize_key(identifier)
    if not key:
        return
    with _LOCK:
        _FAILURES.setdefault(key, []).append(time.time())


def record_success(identifier: str) -> None:
    key = _normalize_key(identifier)
    if not key:
        return
    with _LOCK:
        _FAILURES.pop(key, None)
