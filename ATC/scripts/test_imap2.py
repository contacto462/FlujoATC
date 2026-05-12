"""
Diagnostico de IMAP2. Ejecutar con el servidor detenido:
    cd ATC
    python scripts/test_imap2.py
"""
import sys, os, imaplib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.email_sync_state import EmailSyncState

print("=== CONFIG IMAP2 ===")
print(f"  HOST     : {settings.IMAP2_HOST}")
print(f"  PORT     : {settings.IMAP2_PORT}")
print(f"  USER     : {settings.IMAP2_USER}")
print(f"  PASSWORD : {'(set)' if settings.IMAP2_PASSWORD else '(VACIO)'}")
print(f"  FOLDER   : {settings.IMAP2_FOLDER}")

if not settings.IMAP2_HOST or not settings.IMAP2_USER or not settings.IMAP2_PASSWORD:
    print("\nERROR: IMAP2 no esta configurado en .env")
    sys.exit(1)

print("\n=== ESTADO EN BD ===")
db = SessionLocal()
try:
    from email.utils import parseaddr
    user_norm = parseaddr(settings.IMAP2_USER or "")[1].strip().lower() or (settings.IMAP2_USER or "").strip().lower()
    folder = (settings.IMAP2_FOLDER or "INBOX").strip()
    key = f"{user_norm}:{folder}"
    print(f"  mailbox_key: {key}")
    state = db.get(EmailSyncState, key)
    if state:
        print(f"  last_uid   : {state.last_uid}")
        print(f"  uid_valid  : {state.uid_validity}")
    else:
        print("  Sin registro (sera tratado como buzon nuevo al arrancar)")
finally:
    db.close()

print("\n=== CONEXION IMAP2 ===")
try:
    mail = imaplib.IMAP4_SSL(settings.IMAP2_HOST, settings.IMAP2_PORT)
    print("  Conexion SSL: OK")
    mail.login(settings.IMAP2_USER, settings.IMAP2_PASSWORD)
    print("  Login: OK")
    status, _ = mail.select(settings.IMAP2_FOLDER)
    print(f"  SELECT {settings.IMAP2_FOLDER}: {status}")

    # UIDNEXT actual
    _, data = mail.status(settings.IMAP2_FOLDER, "(UIDNEXT MESSAGES UNSEEN)")
    raw = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    print(f"  STATUS: {raw.strip()}")

    # Ultimos 5 UIDs
    search_status, uids = mail.uid("search", None, "ALL")
    all_uids = uids[0].split() if uids and uids[0] else []
    last_5 = all_uids[-5:] if len(all_uids) >= 5 else all_uids
    print(f"  Total mensajes en INBOX: {len(all_uids)}")
    print(f"  Ultimos UIDs: {[u.decode() for u in last_5]}")

    mail.logout()
    print("\nDiagnostico completo. La conexion funciona correctamente.")
except imaplib.IMAP4.error as e:
    print(f"  ERROR IMAP: {e}")
    print("  Posibles causas:")
    print("    - IMAP no habilitado en Gmail (Configuracion > Ver todos los ajustes > Reenvio e IMAP)")
    print("    - Contrasena de aplicacion incorrecta")
except Exception as e:
    print(f"  ERROR: {e}")
