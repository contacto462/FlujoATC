"""
Crea (o resetea la contraseña de) un usuario interno de staff en dbo.users.

No confundir con ATC/scripts/crear_usuario_cliente.py (ese es para el Portal
Cliente externo, con cliente_rut). Este es para personal de ATC: soporte,
supervisores, guardias, etc. — cualquier valor de `department` reconocido en
DEPARTMENT_AREA_MAP (ATC/app/routes/web.py) para que el login lo enrute solo.

Uso:
    python -m ATC.scripts.crear_usuario_staff \
        --name "Susy Jovanna Chamorro Cárcamo" \
        --username "12018082-7" \
        --password "12018" \
        --department "supervisores"

    # Si el usuario ya existe y quieres actualizar clave/nombre/departamento:
    python -m ATC.scripts.crear_usuario_staff --name "..." --username "..." --password "..." --department "..." --reset
"""
from __future__ import annotations

import argparse

from ATC.app.core.db import SessionLocal
from ATC.app.core.security import hash_password
from ATC.app.models.user import User


def main() -> None:
    parser = argparse.ArgumentParser(description="Crea o resetea un usuario interno de staff.")
    parser.add_argument("--name", required=True, help="Nombre completo a mostrar.")
    parser.add_argument("--username", required=True, help="Usuario de acceso (habitualmente el RUT).")
    parser.add_argument("--password", required=True, help="Contraseña de acceso.")
    parser.add_argument("--department", required=True, help="Departamento(s), ej. 'supervisores'. Separar varios con ';'.")
    parser.add_argument("--role", default="agent", choices=["agent", "admin", "superadmin"], help="Rol (por defecto: agent).")
    parser.add_argument("--email", default=None, help="Email opcional.")
    parser.add_argument("--reset", action="store_true", help="Si el usuario ya existe, actualiza sus datos en vez de fallar.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        existente = db.query(User).filter(User.username == args.username.strip()).first()
        if existente and not args.reset:
            raise SystemExit(
                f"Ya existe un usuario '{args.username}' (id={existente.id}, name={existente.name!r}, "
                f"department={existente.department!r}). Usa --reset si quieres actualizarlo."
            )

        if existente:
            existente.name = args.name
            existente.hashed_password = hash_password(args.password)
            existente.department = args.department
            existente.role = args.role
            existente.is_active = True
            if args.email:
                existente.email = args.email
            db.commit()
            print(f"Actualizado: usuario '{existente.username}' (id={existente.id}) -> department={existente.department!r}, role={existente.role!r}.")
            return

        nuevo = User(
            name=args.name,
            username=args.username.strip(),
            hashed_password=hash_password(args.password),
            role=args.role,
            is_active=True,
            department=args.department,
            email=args.email,
        )
        db.add(nuevo)
        db.commit()
        db.refresh(nuevo)
        print(f"Creado: usuario '{nuevo.username}' (id={nuevo.id}) -> {nuevo.name!r}, department={nuevo.department!r}, role={nuevo.role!r}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
