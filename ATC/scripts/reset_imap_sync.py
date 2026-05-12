"""
Ejecutar UNA VEZ para resetear el estado IMAP.
Al borrar los registros de email_sync_states, el próximo arranque
detecta los buzones como nuevos y salta al UID actual (sin importar histórico).

Uso:
    cd ATC
    python scripts/reset_imap_sync.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import SessionLocal
from app.models.email_sync_state import EmailSyncState

db = SessionLocal()
try:
    deleted = db.query(EmailSyncState).delete()
    db.commit()
    print(f"OK: Resetados {deleted} registros de email_sync_states.")
    print("Al reiniciar el servidor arrancara desde el correo actual en adelante.")
finally:
    db.close()
