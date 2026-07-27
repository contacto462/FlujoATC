import re

from ATC.app.core.db import SessionLocal
from ATC.app.core.security import hash_password
from ATC.app.models.user import User

USUARIOS = [
    {"name": "Kevin Ignacio Valenzuela Valenzuela", "rut": "19.328.557-0"},
    {"name": "Christopher Esteban Villegas Ruz", "rut": "17.140.854-7"},
]


def _digitos(rut: str) -> str:
    return re.sub(r"\D+", "", str(rut or ""))


db = SessionLocal()
try:
    for u in USUARIOS:
        digitos = _digitos(u["rut"])
        username = f"{digitos[:-1]}-{digitos[-1]}"
        clave = digitos[:5]
        existente = db.query(User).filter(User.username == username).first()
        if existente:
            print(f"YA EXISTE: {username} ({u['name']})")
            continue
        nuevo = User(
            name=u["name"],
            username=username,
            hashed_password=hash_password(clave),
            department="Tecnicos",
            role="agent",
            is_active=True,
        )
        db.add(nuevo)
        print(f"CREADO: {username} ({u['name']}) clave={clave}")
    db.commit()
finally:
    db.close()
