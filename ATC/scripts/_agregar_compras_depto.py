from ATC.app.core.db import SessionLocal
from ATC.app.models.user import User

NOMBRES = ["Hector Rosales", "Gonzalo Ponce"]

db = SessionLocal()
try:
    usuarios = db.query(User).filter(User.is_active == True).all()  # noqa: E712
    for objetivo in NOMBRES:
        candidatos = [
            u for u in usuarios
            if objetivo.strip().lower() in str(u.name or "").strip().lower()
        ]
        if not candidatos:
            print(f"NO ENCONTRADO: {objetivo}")
            continue
        for u in candidatos:
            deptos_actuales = [d.strip() for d in str(u.department or "").split(",") if d.strip()]
            ya_tiene = any(d.strip().lower() == "control de compras" for d in deptos_actuales)
            if ya_tiene:
                print(f"YA TENIA: {u.name} ({u.username}) -> {u.department}")
                continue
            deptos_actuales.append("Control de Compras")
            u.department = ",".join(deptos_actuales)
            print(f"ACTUALIZADO: {u.name} ({u.username}) -> {u.department}")
    db.commit()
finally:
    db.close()
